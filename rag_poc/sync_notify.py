import argparse
import json
import os
import sys
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
ZOTERO_SYNC_REPORT_PATH = INDEX_DIR / "zotero_sync_report.json"
INGEST_REPORT_PATH = INDEX_DIR / "ingest_report.json"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def notify_mode() -> str:
    return os.environ.get("SYNC_NOTIFY_MODE", "errors_only").strip().lower()


def notify_channel() -> str:
    return os.environ.get("SYNC_NOTIFY_CHANNEL", "").strip()


def should_notify_success(zotero: dict, ingest: dict) -> bool:
    mode = notify_mode()
    if mode in {"", "silent", "off", "none"}:
        return False
    if mode in {"errors_only", "error", "errors"}:
        return False
    if mode in {"verbose", "always", "all"}:
        return True

    pdf_report = zotero.get("pdf_report") or {}
    changed_counts = [
        int(zotero.get("paper_like_items") or 0),
        int(zotero.get("deleted_items") or 0),
        int(pdf_report.get("downloaded") or 0),
        int(pdf_report.get("failed") or 0),
        int(ingest.get("newly_indexed_pdfs") or 0),
        int(ingest.get("removed_pdfs") or 0),
    ]
    return any(count > 0 for count in changed_counts)


def success_message(zotero: dict, ingest: dict) -> str:
    pdf_report = zotero.get("pdf_report") or {}
    return "\n".join(
        [
            "*PaperBot sync completed / 同期完了*",
            f"Zotero: mode=`{zotero.get('sync_mode', 'unknown')}` "
            f"changed_papers=`{zotero.get('paper_like_items', 0)}` "
            f"deleted=`{zotero.get('deleted_items', 0)}` "
            f"version=`{zotero.get('latest_version', 'unknown')}`",
            f"PDF sync: checked=`{pdf_report.get('checked', 0)}` "
            f"downloaded=`{pdf_report.get('downloaded', 0)}` "
            f"skipped_known=`{pdf_report.get('skipped_known', 0)}` "
            f"failed=`{pdf_report.get('failed', 0)}`",
            f"Ingest: new=`{ingest.get('newly_indexed_pdfs', 0)}` "
            f"unchanged=`{ingest.get('unchanged_pdfs', 0)}` "
            f"removed=`{ingest.get('removed_pdfs', 0)}` "
            f"chunks_added=`{ingest.get('chunks_added', 0)}` "
            f"total_chunks=`{ingest.get('total_chunks', 0)}`",
        ]
    )


def failure_message(exit_code: int, message: str) -> str:
    return "\n".join(
        [
            "*PaperBot sync failed / 同期失敗*",
            f"exit_code=`{exit_code}`",
            message.strip() or "No error message captured.",
        ]
    )


def post_to_slack(text: str) -> bool:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = notify_channel()
    if not token or not channel:
        print("Slack sync notification disabled: SLACK_BOT_TOKEN or SYNC_NOTIFY_CHANNEL is not set.")
        return False

    try:
        WebClient(token=token).chat_postMessage(channel=channel, text=text)
    except SlackApiError as exc:
        error = exc.response.get("error", "unknown_error")
        print(f"Slack sync notification failed: {error}", file=sys.stderr)
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send PaperBot sync notifications to Slack.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("success", help="Notify successful sync according to SYNC_NOTIFY_MODE.")

    failure = subparsers.add_parser("failure", help="Notify failed sync.")
    failure.add_argument("--exit-code", type=int, default=1)
    failure.add_argument("--message", default="")

    return parser.parse_args()


def main() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    args = parse_args()

    if args.command == "failure":
        if notify_mode() in {"silent", "off", "none"}:
            return
        post_to_slack(failure_message(args.exit_code, args.message))
        return

    zotero = load_json(ZOTERO_SYNC_REPORT_PATH)
    ingest = load_json(INGEST_REPORT_PATH)
    if should_notify_success(zotero, ingest):
        post_to_slack(success_message(zotero, ingest))


if __name__ == "__main__":
    main()
