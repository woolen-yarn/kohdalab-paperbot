import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

try:
    from .ollama_client import OllamaError, generate
except ImportError:
    from ollama_client import OllamaError, generate


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

DEFAULT_TERMS = {
    "persistent spin helix": 9,
    "spin helix": 7,
    "rashba": 4,
    "dresselhaus": 4,
    "spin-orbit": 4,
    "spin orbit": 4,
    "spin diffusion": 5,
    "spin lifetime": 4,
    "spintronics": 3,
    "semiconductor": 2,
    "two-dimensional electron gas": 4,
    "2deg": 4,
    "gaas": 2,
    "ingaas": 2,
    "time-resolved kerr": 6,
    "trkr": 6,
    "optical spectroscopy": 3,
    "structured light": 4,
    "spatial light modulator": 4,
    "crsbr": 6,
    "gate-controlled": 3,
    "gallium telluride": 4,
    "wse2": 3,
    "ws2": 3,
    "mos2": 2,
    "magnon": 5,
    "2d magnet": 5,
    "van der waals magnet": 5,
    "spin exciton": 5,
    "exciton spin": 5,
    "valley spin": 4,
}


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


load_env_file(PROJECT_ROOT / ".env")


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_datetime(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_arxiv_id(url: str) -> str:
    arxiv_id = url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"v\d+$", "", arxiv_id)


def init_db(path: Path) -> sqlite3.Connection:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_watch_items (
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            score REAL NOT NULL,
            reasons_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            posted_at TEXT,
            PRIMARY KEY (source, external_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_watch_posted_score
        ON paper_watch_items(posted_at, score, published_at)
        """
    )
    return conn


def profile_terms() -> dict[str, float]:
    raw = os.environ.get("PAPER_WATCH_TERMS", "").strip()
    if not raw:
        return dict(DEFAULT_TERMS)

    terms = {}
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            term, weight = item.rsplit(":", 1)
            try:
                terms[term.strip().lower()] = float(weight)
            except ValueError:
                terms[term.strip().lower()] = 1.0
        else:
            terms[item.lower()] = 1.0
    return terms or dict(DEFAULT_TERMS)


def arxiv_query(terms: dict[str, float]) -> str:
    configured = os.environ.get("PAPER_WATCH_ARXIV_QUERY", "").strip()
    if configured:
        return configured

    core_terms = sorted(terms, key=terms.get, reverse=True)[:18]
    return " OR ".join(f'all:"{term}"' for term in core_terms)


def profile_label(terms: dict[str, float], limit: int = 10) -> str:
    return ", ".join(sorted(terms, key=terms.get, reverse=True)[:limit])


def fetch_arxiv_entries(query: str, max_results: int) -> list[dict]:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "kohdalab-paperbot/0.1"})
    with urllib.request.urlopen(req, timeout=60) as res:
        body = res.read()

    root = ET.fromstring(body)
    entries = []
    for entry in root.findall("atom:entry", ATOM_NS):
        url_text = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        authors = [
            compact_whitespace(author.findtext("atom:name", default="", namespaces=ATOM_NS))
            for author in entry.findall("atom:author", ATOM_NS)
        ]
        authors = [author for author in authors if author]
        entries.append(
            {
                "source": "arxiv",
                "external_id": normalize_arxiv_id(url_text),
                "title": compact_whitespace(entry.findtext("atom:title", default="", namespaces=ATOM_NS)),
                "authors": authors,
                "summary": compact_whitespace(entry.findtext("atom:summary", default="", namespaces=ATOM_NS)),
                "url": url_text,
                "published_at": parse_datetime(entry.findtext("atom:published", default="", namespaces=ATOM_NS)),
                "updated_at": parse_datetime(entry.findtext("atom:updated", default="", namespaces=ATOM_NS)),
            }
        )
    return entries


def score_entry(entry: dict, terms: dict[str, float]) -> tuple[float, list[str]]:
    text = f"{entry['title']} {entry['summary']}".lower()
    score = 0.0
    reasons = []
    for term, weight in terms.items():
        if term in text:
            score += weight
            reasons.append(term)
    return score, reasons[:8]


def upsert_entry(conn: sqlite3.Connection, entry: dict) -> bool:
    now = utc_now()
    existing = conn.execute(
        """
        SELECT posted_at
        FROM paper_watch_items
        WHERE source = ? AND external_id = ?
        """,
        (entry["source"], entry["external_id"]),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO paper_watch_items (
            source, external_id, title, authors_json, summary, url,
            published_at, updated_at, score, reasons_json,
            first_seen_at, last_seen_at, posted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(source, external_id) DO UPDATE SET
            title = excluded.title,
            authors_json = excluded.authors_json,
            summary = excluded.summary,
            url = excluded.url,
            published_at = excluded.published_at,
            updated_at = excluded.updated_at,
            score = excluded.score,
            reasons_json = excluded.reasons_json,
            last_seen_at = excluded.last_seen_at
        """,
        (
            entry["source"],
            entry["external_id"],
            entry["title"],
            json.dumps(entry["authors"], ensure_ascii=False),
            entry["summary"],
            entry["url"],
            entry["published_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            entry["updated_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            entry["score"],
            json.dumps(entry["reasons"], ensure_ascii=False),
            now,
            now,
        ),
    )
    return existing is None


def select_candidates(conn: sqlite3.Connection, min_score: float, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT source, external_id, title, authors_json, summary, url,
               published_at, score, reasons_json
        FROM paper_watch_items
        WHERE posted_at IS NULL
          AND score >= ?
        ORDER BY score DESC, published_at DESC
        LIMIT ?
        """,
        (min_score, limit),
    ).fetchall()
    items = []
    for row in rows:
        items.append(
            {
                "source": row[0],
                "external_id": row[1],
                "title": row[2],
                "authors": json.loads(row[3]),
                "summary": row[4],
                "url": row[5],
                "published_at": row[6],
                "score": row[7],
                "reasons": json.loads(row[8]),
            }
        )
    return items


def mark_posted(conn: sqlite3.Connection, items: list[dict]) -> None:
    now = utc_now()
    conn.executemany(
        """
        UPDATE paper_watch_items
        SET posted_at = ?
        WHERE source = ? AND external_id = ?
        """,
        [(now, item["source"], item["external_id"]) for item in items],
    )


def compact_authors(authors: list[str]) -> str:
    if not authors:
        return "Unknown authors"
    if len(authors) <= 3:
        return ", ".join(authors)
    return f"{', '.join(authors[:3])}, et al."


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def summary_model() -> str:
    return os.environ.get(
        "PAPER_WATCH_SUMMARY_MODEL",
        os.environ.get("OLLAMA_CHAT_MODEL", "gpt-oss:20b"),
    )


def fallback_intro(item: dict, *, reason: str = "failed") -> str:
    if reason == "disabled":
        ja_line = "JA: 日本語紹介文は無効化されています。Abstractを確認してください。"
    else:
        ja_line = "JA: 日本語紹介文はLLM生成に失敗したため省略しました。Abstractを確認してください。"
    return "\n".join(
        [
            f"EN: {truncate(item['summary'], 260)}",
            ja_line,
        ]
    )


def build_intro_prompt(item: dict) -> str:
    reasons = ", ".join(item["reasons"]) if item["reasons"] else "profile match"
    return f"""You are KohdaLab's Paper Watch assistant.
Based only on the title and abstract below, write a bilingual paper introduction.
Do not invent results, numbers, materials, or methods that are not in the abstract.
Keep technical terms such as Rashba, Dresselhaus, spin-orbit, exciton, magnon, TRKR, PSH, and 2DEG in English.

Format exactly:
EN: one concise English sentence explaining why this may be relevant to the lab.
JA: one concise Japanese sentence explaining why this may be relevant to the lab.

Profile match terms: {reasons}

Title:
{item['title']}

Abstract:
{item['summary']}
"""


def bilingual_intro(item: dict, *, enabled: bool) -> str:
    if not enabled:
        return fallback_intro(item, reason="disabled")
    try:
        text = generate(build_intro_prompt(item), summary_model(), timeout=180).strip()
    except OllamaError:
        return fallback_intro(item)
    if "EN:" not in text or "JA:" not in text:
        return fallback_intro(item)
    return text


def build_message(
    items: list[dict],
    *,
    terms: dict[str, float] | None = None,
    include_intro: bool = True,
) -> str:
    lines = ["*Paper Watch / 新着論文紹介*"]
    if terms:
        lines.append(f"profile=`{profile_label(terms)}`")
        lines.append("scoring=`profile term match`; RAG relevance is not used yet.")
    for index, item in enumerate(items, start=1):
        reasons = ", ".join(item["reasons"]) if item["reasons"] else "profile match"
        lines.extend(
            [
                "",
                f"*{index}. {item['title']}*",
                f"{compact_authors(item['authors'])}",
                f"`{item['source']}:{item['external_id']}` score=`{item['score']:.1f}` reasons=`{reasons}`",
                bilingual_intro(item, enabled=include_intro),
                item["url"],
                f"Abstract: {truncate(item['summary'], 360)}",
            ]
        )
    return "\n".join(lines)


def post_to_slack(text: str) -> bool:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("PAPER_WATCH_CHANNEL", "").strip()
    if not token or not channel:
        print("Paper Watch Slack post skipped: SLACK_BOT_TOKEN or PAPER_WATCH_CHANNEL is not set.")
        return False
    try:
        WebClient(token=token).chat_postMessage(channel=channel, text=text)
    except SlackApiError as exc:
        error = exc.response.get("error", "unknown_error")
        print(f"Paper Watch Slack post failed: {error}", file=sys.stderr)
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find and post relevant new papers.")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates without posting.")
    parser.add_argument("--notify-empty", action="store_true", help="Post even when no papers matched.")
    parser.add_argument("--max-results", type=int, default=env_int("PAPER_WATCH_MAX_RESULTS", 80))
    parser.add_argument("--post-limit", type=int, default=env_int("PAPER_WATCH_POST_LIMIT", 5))
    parser.add_argument("--min-score", type=float, default=env_float("PAPER_WATCH_MIN_SCORE", 6.0))
    parser.add_argument("--lookback-days", type=int, default=env_int("PAPER_WATCH_LOOKBACK_DAYS", 14))
    parser.add_argument("--no-summary", action="store_true", help="Skip LLM-generated bilingual intros.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    terms = profile_terms()
    query = arxiv_query(terms)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.lookback_days)

    entries = []
    for entry in fetch_arxiv_entries(query, args.max_results):
        if entry["published_at"] < cutoff:
            continue
        score, reasons = score_entry(entry, terms)
        entry["score"] = score
        entry["reasons"] = reasons
        entries.append(entry)

    conn = init_db(INDEX_DB_PATH)
    try:
        new_count = 0
        for entry in entries:
            if upsert_entry(conn, entry):
                new_count += 1
        candidates = select_candidates(conn, args.min_score, args.post_limit)
        if candidates:
            include_intro = env_bool("PAPER_WATCH_BILINGUAL_INTRO", True) and not args.no_summary
            message = build_message(candidates, terms=terms, include_intro=include_intro)
            print(message)
            if not args.dry_run and post_to_slack(message):
                mark_posted(conn, candidates)
        elif args.notify_empty:
            message = "Paper Watch / 新着論文紹介: no new matching papers found."
            print(message)
            if not args.dry_run:
                post_to_slack(message)
        else:
            print(
                "Paper Watch: "
                f"fetched={len(entries)} new_seen={new_count} candidates=0 "
                f"min_score={args.min_score}"
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
