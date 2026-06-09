import logging
import logging.handlers
import os
import re
import time
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"


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


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("paperbot")
    logger.setLevel(os.environ.get("PAPERBOT_LOG_LEVEL", "INFO").upper())
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "paperbot.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


load_env_file(PROJECT_ROOT / ".env")

from rag_poc.ask import answer_question, format_sources

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

logger = setup_logging()
app = App(token=SLACK_BOT_TOKEN)


def ask_rag(question: str) -> tuple[str, str, float]:
    started = time.monotonic()
    answer, contexts = answer_question(question)
    sources = format_sources(contexts)
    duration = time.monotonic() - started
    message = f"{answer}\n\n*Sources*\n```{sources}```"
    return message, sources, duration


def reply_with_rag(client, channel: str, thread_ts: str, question: str, user: str, surface: str) -> None:
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="PDFを検索しています...",
    )

    try:
        answer, sources, duration = ask_rag(question)
    except Exception as e:
        logger.exception(
            "rag_error user=%s surface=%s channel=%s question=%r",
            user,
            surface,
            channel,
            question,
        )
        answer = f"RAG処理でエラーが出ました: `{e}`"
    else:
        logger.info(
            "rag_answer user=%s surface=%s channel=%s duration_sec=%.2f question=%r sources=%r",
            user,
            surface,
            channel,
            duration,
            question,
            sources,
        )

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=answer[:39000],
    )


@app.event("app_mention")
def handle_mention(event, client):
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    text = event.get("text", "")
    question = re.sub(r"<@[^>]+>", "", text).strip()
    user = event.get("user", "unknown")

    if not question:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="質問を書いてください。例: `@PaperBot PSHとは何ですか？`",
        )
        return

    reply_with_rag(client, channel, thread_ts, question, user, "mention")

@app.event("message")
def handle_dm(event, client):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return
    if event.get("subtype"):
        return

    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]
    question = event.get("text", "").strip()
    user = event.get("user", "unknown")

    if not question:
        return

    reply_with_rag(client, channel, thread_ts, question, user, "dm")


def main() -> None:
    logger.info("paperbot_start ollama_base_url=%s", os.environ.get("OLLAMA_BASE_URL", "default"))
    SocketModeHandler(app, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
