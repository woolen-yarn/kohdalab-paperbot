import logging
import logging.handlers
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
PAPERS_DIR = PROJECT_ROOT / "rag_poc" / "papers"
LAST_RESULTS: dict[tuple[str, str], "RagResult"] = {}


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

from rag_poc.ask import (
    CHAT_MODEL,
    DEEP_TOP_K,
    EMBED_MODEL,
    SHORT_TOP_K,
    TOP_K,
    answer_question,
    format_source_ids,
    format_sources,
)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

logger = setup_logging()
app = App(token=SLACK_BOT_TOKEN)


@dataclass(frozen=True)
class RagResult:
    message: str
    answer: str
    sources: str
    source_ids: str
    duration: float
    model: str
    question: str


def format_rag_message(answer: str, source_ids: str) -> str:
    clean_answer = answer.strip() or "LLMが空の回答を返しました。Sourcesを確認してください。"
    clean_source_ids = source_ids.strip() or "No sources."
    return (
        f"*Answer*\n{clean_answer}\n\n"
        f"*Sources*: {clean_source_ids}\n"
        "詳細は `sources` と送ってください。"
    )


def ask_rag(question: str) -> RagResult:
    started = time.monotonic()
    answer, contexts = answer_question(question)
    sources = format_sources(contexts)
    source_ids = format_source_ids(contexts)
    duration = time.monotonic() - started
    message = format_rag_message(answer, source_ids)
    return RagResult(
        message=message,
        answer=answer,
        sources=sources,
        source_ids=source_ids,
        duration=duration,
        model=CHAT_MODEL,
        question=question,
    )


def latest_papers(limit: int = 8) -> list[Path]:
    if not PAPERS_DIR.exists():
        return []
    papers = [path for path in PAPERS_DIR.rglob("*.pdf") if path.is_file()]
    papers.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return papers[:limit]


def command_help() -> str:
    return "\n".join(
        [
            "*PaperBot commands*",
            "`/help`  このヘルプを表示",
            "`/model`  現在のLLM/embedding設定を表示",
            "`/sources`  直前の回答で使ったSourcesを再表示",
            "`/recent`  NAS上の最近のPDFを表示",
            "",
            "通常の質問はそのまま送ってください。例: `Persistent Spin Helixについて一文で教えて`",
        ]
    )


def command_model() -> str:
    return "\n".join(
        [
            "*Current model settings*",
            f"`OLLAMA_BASE_URL`: `{os.environ.get('OLLAMA_BASE_URL', 'default')}`",
            f"`OLLAMA_CHAT_MODEL`: `{CHAT_MODEL}`",
            f"`OLLAMA_EMBED_MODEL`: `{EMBED_MODEL}`",
            f"`PAPERBOT_TOP_K`: `{TOP_K}`",
            f"`PAPERBOT_SHORT_TOP_K`: `{SHORT_TOP_K}`",
            f"`PAPERBOT_DEEP_TOP_K`: `{DEEP_TOP_K}`",
        ]
    )


def command_sources(channel: str, user: str) -> str:
    result = LAST_RESULTS.get((channel, user))
    if not result:
        return "まだこのDMで回答履歴がありません。先に質問を送ってください。"
    return (
        f"*Last question*\n{result.question}\n\n"
        f"*Model*: `{result.model}` / `{result.duration:.2f}s`\n\n"
        f"*Sources*\n```{result.sources}```"
    )


def command_recent() -> str:
    papers = latest_papers()
    if not papers:
        return "NAS上の `rag_poc/papers` にPDFがまだ見つかりません。"
    lines = ["*Recent PDFs*"]
    for i, path in enumerate(papers, start=1):
        source = path.relative_to(PAPERS_DIR).as_posix()
        lines.append(f"{i}. {source}")
    return "\n".join(lines)


def handle_command(text: str, channel: str, user: str) -> str | None:
    command = text.strip().split(maxsplit=1)[0].lower()
    if command in {"/help", "help"}:
        return command_help()
    if command in {"/model", "model"}:
        return command_model()
    if command in {"/sources", "sources"}:
        return command_sources(channel, user)
    if command in {"/recent", "recent"}:
        return command_recent()
    if command.startswith("/"):
        return "未知のコマンドです。`/help` を見てください。"
    return None


def post_message(client, channel: str, text: str, thread_ts: str | None = None) -> None:
    payload = {
        "channel": channel,
        "text": text,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    client.chat_postMessage(**payload)


def reply_with_rag(
    client,
    channel: str,
    thread_ts: str | None,
    question: str,
    user: str,
    surface: str,
) -> None:
    command_response = handle_command(question, channel, user)
    if command_response is not None:
        logger.info(
            "command user=%s surface=%s channel=%s command=%r model=%s",
            user,
            surface,
            channel,
            question,
            CHAT_MODEL,
        )
        post_message(client, channel, command_response, thread_ts)
        return

    post_message(client, channel, "PDFを検索しています...", thread_ts)

    try:
        result = ask_rag(question)
    except Exception as e:
        logger.exception(
            "rag_error user=%s surface=%s channel=%s model=%s question=%r",
            user,
            surface,
            channel,
            CHAT_MODEL,
            question,
        )
        answer = f"RAG処理でエラーが出ました: `{e}`"
    else:
        LAST_RESULTS[(channel, user)] = result
        logger.info(
            (
                "rag_answer user=%s surface=%s channel=%s model=%s embed_model=%s "
                "duration_sec=%.2f answer_chars=%d sources_chars=%d source_ids=%r question=%r sources=%r"
            ),
            user,
            surface,
            channel,
            result.model,
            EMBED_MODEL,
            result.duration,
            len(result.answer),
            len(result.sources),
            result.source_ids,
            question,
            result.sources,
        )
        answer = result.message

    post_message(client, channel, answer[:39000], thread_ts)


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
    question = event.get("text", "").strip()
    user = event.get("user", "unknown")

    if not question:
        return

    reply_with_rag(client, channel, None, question, user, "dm")


def main() -> None:
    logger.info(
        "paperbot_start ollama_base_url=%s chat_model=%s embed_model=%s top_k=%s",
        os.environ.get("OLLAMA_BASE_URL", "default"),
        CHAT_MODEL,
        EMBED_MODEL,
        TOP_K,
    )
    SocketModeHandler(app, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
