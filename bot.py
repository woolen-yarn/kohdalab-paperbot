import logging
import logging.handlers
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
PAPERS_DIR = PROJECT_ROOT / "rag_poc" / "papers"
INDEX_DB_PATH = PROJECT_ROOT / "rag_poc" / "index" / "chunks.sqlite3"
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


MAX_INLINE_SOURCES_CHARS = 6000


def format_rag_message(answer: str, sources: str) -> str:
    clean_answer = answer.strip() or "LLMが空の回答を返しました。Sourcesを確認してください。"
    clean_sources = sources.strip() or "No sources."
    if len(clean_sources) > MAX_INLINE_SOURCES_CHARS:
        clean_sources = (
            clean_sources[:MAX_INLINE_SOURCES_CHARS].rstrip()
            + "\n... sources truncated. Send `sources` for the full list."
        )
    return (
        f"*Answer / 回答*\n{clean_answer}\n\n"
        f"*Sources / 根拠*\n```{clean_sources}```"
    )


def ask_rag(question: str) -> RagResult:
    started = time.monotonic()
    answer, contexts = answer_question(question)
    sources = format_sources(contexts)
    source_ids = format_source_ids(contexts)
    duration = time.monotonic() - started
    message = format_rag_message(answer, sources)
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
            "*PaperBot commands / コマンド*",
            "`help` / `ヘルプ`  Show this help / このヘルプを表示",
            "`model` / `モデル`  Show LLM and embedding settings / LLM・embedding設定を表示",
            "`status` / `状態`  Show DB counts and Ollama health / DB件数とOllama疎通を表示",
            "`sources` / `根拠`  Show the previous source list / 直前のSourcesを表示",
            "`recent` / `最近`  Show recent local PDFs / 最近のPDFを表示",
            "",
            "Slash-style text such as `/status` may be intercepted by Slack, so plain `status` is recommended.",
            "Slackが `/status` を吸収することがあるので、通常は `status` と送ってください。",
            "",
            "Ask normally in Japanese or English. / 日本語・英語どちらでも質問できます。",
            "Example: `Persistent Spin Helixについて一文で教えて`",
            "Example: `Explain Persistent Spin Helix in one sentence.`",
        ]
    )


def command_model() -> str:
    return "\n".join(
        [
            "*Current model settings / 現在のモデル設定*",
            f"`OLLAMA_BASE_URL`: `{os.environ.get('OLLAMA_BASE_URL', 'default')}`",
            f"`OLLAMA_CHAT_MODEL`: `{CHAT_MODEL}`",
            f"`OLLAMA_EMBED_MODEL`: `{EMBED_MODEL}`",
            f"`PAPERBOT_TOP_K`: `{TOP_K}`",
            f"`PAPERBOT_SHORT_TOP_K`: `{SHORT_TOP_K}`",
            f"`PAPERBOT_DEEP_TOP_K`: `{DEEP_TOP_K}`",
        ]
    )


def scalar(conn: sqlite3.Connection, sql: str, default=0):
    try:
        value = conn.execute(sql).fetchone()[0]
    except (sqlite3.Error, TypeError):
        return default
    return default if value is None else value


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (name,),
        ).fetchone()
    )


def ollama_status() -> str:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://10.32.145.143:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=3) as res:
            body = res.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"`unreachable` ({exc})"

    model_names = set(re.findall(r'"name"\s*:\s*"([^"]+)"', body))
    chat_ok = CHAT_MODEL in model_names
    embed_ok = EMBED_MODEL in model_names
    return (
        f"`ok` chat=`{CHAT_MODEL}` {'ok' if chat_ok else 'missing'} / "
        f"embed=`{EMBED_MODEL}` {'ok' if embed_ok else 'missing'}"
    )


def command_status() -> str:
    lines = ["*PaperBot status / 状態*"]
    lines.append(f"Ollama: {ollama_status()}")

    if not INDEX_DB_PATH.exists():
        lines.append(f"SQLite: `missing` `{INDEX_DB_PATH}`")
        return "\n".join(lines)

    conn = sqlite3.connect(INDEX_DB_PATH)
    try:
        lines.append(f"SQLite: `ok` `{INDEX_DB_PATH.name}`")

        if table_exists(conn, "papers"):
            papers = scalar(conn, "SELECT COUNT(*) FROM papers")
            unique = (
                scalar(conn, "SELECT COUNT(*) FROM unique_papers")
                if table_exists(conn, "unique_papers")
                else 0
            )
            duplicates = scalar(
                conn,
                "SELECT COUNT(*) FROM papers WHERE COALESCE(is_duplicate, 0) = 1",
            )
            last_sync = scalar(conn, "SELECT MAX(synced_at) FROM papers", "n/a")
            downloaded = scalar(conn, "SELECT COUNT(*) FROM papers WHERE pdf_status = 'downloaded'")
            no_pdf = scalar(conn, "SELECT COUNT(*) FROM papers WHERE pdf_status = 'no_pdf'")
            failed_pdf = scalar(conn, "SELECT COUNT(*) FROM papers WHERE pdf_status = 'failed'")
            lines.append(
                f"Zotero: papers=`{papers}` unique=`{unique}` duplicates=`{duplicates}` "
                f"last_sync=`{last_sync}`"
            )
            lines.append(
                f"Zotero PDFs: downloaded=`{downloaded}` no_pdf=`{no_pdf}` failed=`{failed_pdf}`"
            )

        if table_exists(conn, "zotero_sync_state"):
            row = conn.execute(
                "SELECT version, synced_at FROM zotero_sync_state ORDER BY synced_at DESC LIMIT 1"
            ).fetchone()
            if row:
                lines.append(f"Zotero sync state: version=`{row[0]}` synced_at=`{row[1]}`")

        if table_exists(conn, "chunks"):
            chunks = scalar(conn, "SELECT COUNT(*) FROM chunks")
            lines.append(f"RAG chunks: `{chunks}`")

        if table_exists(conn, "pdf_documents"):
            indexed = scalar(
                conn,
                "SELECT COUNT(*) FROM pdf_documents WHERE status = 'indexed' AND chunk_count > 0",
            )
            zero_text = scalar(conn, "SELECT COUNT(*) FROM pdf_documents WHERE status = 'zero_text'")
            duplicate_pdf = scalar(conn, "SELECT COUNT(*) FROM pdf_documents WHERE status = 'duplicate'")
            last_index = scalar(conn, "SELECT MAX(indexed_at) FROM pdf_documents", "n/a")
            lines.append(
                f"Indexed PDFs: indexed=`{indexed}` zero_text=`{zero_text}` "
                f"duplicates=`{duplicate_pdf}` last_index=`{last_index}`"
            )
    finally:
        conn.close()

    zotero_dir = PAPERS_DIR / "zotero"
    zotero_pdf_count = len(list(zotero_dir.rglob("*.pdf"))) if zotero_dir.exists() else 0
    lines.append(f"Local Zotero PDFs: `{zotero_pdf_count}`")
    return "\n".join(lines)


def command_sources(channel: str, user: str) -> str:
    result = LAST_RESULTS.get((channel, user))
    if not result:
        return (
            "No previous answer in this DM/channel yet.\n"
            "まだこのDM/チャンネルでは回答履歴がありません。先に質問を送ってください。"
        )
    return (
        f"*Last question / 直前の質問*\n{result.question}\n\n"
        f"*Model*: `{result.model}` / `{result.duration:.2f}s`\n\n"
        f"*Sources / 根拠*\n```{result.sources}```"
    )


def command_recent() -> str:
    papers = latest_papers()
    if not papers:
        return (
            "No PDFs found under `rag_poc/papers` yet.\n"
            "NAS上の `rag_poc/papers` にPDFがまだ見つかりません。"
        )
    lines = ["*Recent PDFs / 最近のPDF*"]
    for i, path in enumerate(papers, start=1):
        source = path.relative_to(PAPERS_DIR).as_posix()
        lines.append(f"{i}. {source}")
    return "\n".join(lines)


def handle_command(text: str, channel: str, user: str) -> str | None:
    command = text.strip().split(maxsplit=1)[0].lower()
    if command in {"/help", "help", "ヘルプ", "使い方"}:
        return command_help()
    if command in {"/model", "model", "モデル"}:
        return command_model()
    if command in {"/status", "status", "stat", "状態", "ステータス"}:
        return command_status()
    if command in {"/sources", "sources", "source", "根拠", "出典"}:
        return command_sources(channel, user)
    if command in {"/recent", "recent", "最近"}:
        return command_recent()
    if command.startswith("/"):
        return (
            "Unknown command. Send `help`.\n"
            "未知のコマンドです。`help` を見てください。"
        )
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

    post_message(client, channel, "Searching PDFs... / PDFを検索しています...", thread_ts)

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
        answer = f"RAG error / RAG処理エラー: `{e}`"
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
            text=(
                "Please include a question. / 質問を書いてください。\n"
                "Example: `@PaperBot What is PSH?`\n"
                "例: `@PaperBot PSHとは何ですか？`"
            ),
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
