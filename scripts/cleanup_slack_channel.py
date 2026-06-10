from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete PaperBot messages from a Slack channel."
    )
    parser.add_argument(
        "--channel",
        default=os.environ.get("PAPER_WATCH_CHANNEL", "#paper"),
        help="Slack channel name such as #paper, or a channel ID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of recent PaperBot messages to inspect/delete.",
    )
    parser.add_argument(
        "--older-than-hours",
        type=float,
        default=0.0,
        help="Only delete messages older than this many hours. Default: no age filter.",
    )
    parser.add_argument(
        "--include-replies",
        action="store_true",
        help="Also inspect/delete PaperBot replies in threads.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to sleep between delete API calls.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete messages. Without this flag, the command is a dry run.",
    )
    return parser.parse_args()


def slack_client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("SLACK_BOT_TOKEN is not set.")
    return WebClient(token=token)


def resolve_channel_id(client: WebClient, channel: str) -> str:
    value = channel.strip()
    if not value:
        raise SystemExit("Channel is empty.")
    if value[0] in {"C", "G", "D"} and not value.startswith("#"):
        return value

    name = value[1:] if value.startswith("#") else value
    cursor = None
    while True:
        response = client.conversations_list(
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=1000,
            cursor=cursor,
        )
        for item in response.get("channels", []):
            if item.get("name") == name:
                return item["id"]
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    raise SystemExit(
        f"Could not resolve channel {channel!r}. Pass the channel ID instead."
    )


def bot_identity(client: WebClient) -> tuple[str, str]:
    response = client.auth_test()
    user_id = response.get("user_id", "")
    bot_id = response.get("bot_id", "")
    return user_id, bot_id


def is_own_bot_message(message: dict, *, user_id: str, bot_id: str) -> bool:
    if user_id and message.get("user") == user_id:
        return True
    if bot_id and message.get("bot_id") == bot_id:
        return True
    return False


def message_title(message: dict) -> str:
    text = " ".join((message.get("text") or "").split())
    if not text:
        text = message.get("subtype", "message")
    return text[:120]


def iso_from_ts(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return "unknown-time"


def collect_messages(
    client: WebClient,
    *,
    channel_id: str,
    user_id: str,
    bot_id: str,
    limit: int,
    older_than_hours: float,
    include_replies: bool,
) -> list[dict]:
    cutoff = None
    if older_than_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)

    matches: list[dict] = []
    cursor = None
    while len(matches) < limit:
        response = client.conversations_history(
            channel=channel_id,
            limit=min(200, max(1, limit - len(matches))),
            cursor=cursor,
        )
        for message in response.get("messages", []):
            if not is_own_bot_message(message, user_id=user_id, bot_id=bot_id):
                continue
            if cutoff is not None:
                created_at = datetime.fromtimestamp(float(message["ts"]), tz=timezone.utc)
                if created_at >= cutoff:
                    continue
            matches.append(message)
            if len(matches) >= limit:
                break

            if include_replies and int(message.get("reply_count") or 0) > 0:
                matches.extend(
                    collect_replies(
                        client,
                        channel_id=channel_id,
                        thread_ts=message["ts"],
                        user_id=user_id,
                        bot_id=bot_id,
                        remaining=limit - len(matches),
                        cutoff=cutoff,
                    )
                )
                if len(matches) >= limit:
                    break

        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return matches[:limit]


def collect_replies(
    client: WebClient,
    *,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    bot_id: str,
    remaining: int,
    cutoff: datetime | None,
) -> list[dict]:
    matches: list[dict] = []
    cursor = None
    while len(matches) < remaining:
        response = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=min(200, remaining - len(matches)),
            cursor=cursor,
        )
        for message in response.get("messages", [])[1:]:
            if not is_own_bot_message(message, user_id=user_id, bot_id=bot_id):
                continue
            if cutoff is not None:
                created_at = datetime.fromtimestamp(float(message["ts"]), tz=timezone.utc)
                if created_at >= cutoff:
                    continue
            matches.append(message)
            if len(matches) >= remaining:
                break
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return matches


def delete_messages(
    client: WebClient,
    *,
    channel_id: str,
    messages: list[dict],
    sleep_seconds: float,
) -> int:
    deleted = 0
    for message in messages:
        try:
            client.chat_delete(channel=channel_id, ts=message["ts"])
        except SlackApiError as exc:
            error = exc.response.get("error", "unknown_error")
            print(f"delete failed ts={message['ts']} error={error}", file=sys.stderr)
            continue
        deleted += 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return deleted


def main() -> None:
    args = parse_args()
    client = slack_client()
    channel_id = resolve_channel_id(client, args.channel)
    user_id, bot_id = bot_identity(client)

    messages = collect_messages(
        client,
        channel_id=channel_id,
        user_id=user_id,
        bot_id=bot_id,
        limit=args.limit,
        older_than_hours=args.older_than_hours,
        include_replies=args.include_replies,
    )

    mode = "DELETE" if args.delete else "DRY RUN"
    print(f"{mode}: channel={args.channel} id={channel_id} bot_messages={len(messages)}")
    for index, message in enumerate(messages, start=1):
        print(
            f"{index:03d} ts={message['ts']} "
            f"time={iso_from_ts(message['ts'])} text={message_title(message)}"
        )

    if not args.delete:
        print("No messages deleted. Re-run with --delete to delete the listed messages.")
        return

    deleted = delete_messages(
        client,
        channel_id=channel_id,
        messages=messages,
        sleep_seconds=args.sleep,
    )
    print(f"Deleted {deleted}/{len(messages)} messages.")


if __name__ == "__main__":
    main()
