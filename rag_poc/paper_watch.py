import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

try:
    from .ollama_client import OllamaError, embed, generate
except ImportError:
    from ollama_client import OllamaError, embed, generate


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"
LAB_PROFILE_PATH = INDEX_DIR / "lab_profile.json"

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
    ensure_paper_watch_columns(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_watch_posted_score
        ON paper_watch_items(posted_at, score, published_at)
        """
    )
    return conn


def ensure_paper_watch_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(paper_watch_items)").fetchall()
    }
    columns = {
        "term_score": "REAL NOT NULL DEFAULT 0",
        "rag_score": "REAL NOT NULL DEFAULT 0",
        "rag_source": "TEXT",
        "rag_page_start": "INTEGER",
        "rag_page_end": "INTEGER",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE paper_watch_items ADD COLUMN {name} {column_type}")


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


def paper_watch_embed_model() -> str:
    return os.environ.get(
        "PAPER_WATCH_EMBED_MODEL",
        os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
    )


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def parse_authors(authors_json: str) -> list[str]:
    try:
        authors = json.loads(authors_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(authors, list):
        return []
    return [str(author) for author in authors if author]


def compact_lab_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    return f"{authors[0]} et al."


def load_metadata_by_source(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute(
            """
            SELECT pdf_path, title, authors_json, year, journal
            FROM papers
            WHERE pdf_path IS NOT NULL
              AND pdf_path != ''
              AND COALESCE(is_duplicate, 0) = 0
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    metadata = {}
    for pdf_path, title, authors_json, year, journal in rows:
        metadata[pdf_path] = {
            "title": title or "",
            "authors": parse_authors(authors_json),
            "year": year or "",
            "journal": journal or "",
        }
    return metadata


def format_rag_source_label(source: str, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    title = metadata.get("title", "")
    if not title:
        return source

    parts = [title]
    year = metadata.get("year", "")
    authors = compact_lab_authors(metadata.get("authors", []))
    journal = metadata.get("journal", "")
    if year:
        parts.append(f"({year})")
    if authors:
        parts.append(f"- {authors}")
    if journal:
        parts.append(f"- {journal}")
    return " ".join(parts)


def load_rag_reference_chunks(
    conn: sqlite3.Connection,
    *,
    max_chunks: int,
    chunks_per_source: int,
) -> list[dict]:
    if max_chunks <= 0 or chunks_per_source <= 0:
        return []

    metadata_by_source = load_metadata_by_source(conn)
    try:
        rows = conn.execute(
            """
            SELECT source, page_start, page_end, text, embedding_json
            FROM chunks
            ORDER BY source, page_start, page_end, rowid
            """
        )
    except sqlite3.OperationalError:
        return []

    chunks = []
    counts_by_source: dict[str, int] = {}
    for source, page_start, page_end, text, embedding_json in rows:
        if counts_by_source.get(source, 0) >= chunks_per_source:
            continue
        try:
            vector = json.loads(embedding_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(vector, list) or not vector:
            continue

        counts_by_source[source] = counts_by_source.get(source, 0) + 1
        metadata = metadata_by_source.get(source, {})
        chunks.append(
            {
                "source": source,
                "source_label": format_rag_source_label(source, metadata),
                "page_start": page_start,
                "page_end": page_end,
                "text": text,
                "embedding": vector,
            }
        )
        if len(chunks) >= max_chunks:
            break
    return chunks


def candidate_embedding_text(entry: dict) -> str:
    return "\n\n".join(
        [
            entry.get("title", ""),
            compact_authors(entry.get("authors", [])),
            entry.get("summary", ""),
        ]
    ).strip()


def best_rag_match(query_vector: list[float], chunks: list[dict]) -> tuple[float, dict | None]:
    best_score = -1.0
    best_chunk = None
    for chunk in chunks:
        score = cosine(query_vector, chunk["embedding"])
        if score > best_score:
            best_score = score
            best_chunk = chunk
    return max(0.0, best_score), best_chunk


def apply_rag_scores(
    entries: list[dict],
    conn: sqlite3.Connection,
    *,
    enabled: bool,
) -> None:
    for entry in entries:
        entry.setdefault("term_score", entry.get("score", 0.0))
        entry.setdefault("rag_score", 0.0)
        entry.setdefault("rag_source", "")
        entry.setdefault("rag_source_label", "")
        entry.setdefault("rag_page_start", None)
        entry.setdefault("rag_page_end", None)

    if not enabled:
        return

    reference_chunks = load_rag_reference_chunks(
        conn,
        max_chunks=env_int("PAPER_WATCH_RAG_MAX_CHUNKS", 1200),
        chunks_per_source=env_int("PAPER_WATCH_RAG_CHUNKS_PER_SOURCE", 2),
    )
    if not reference_chunks:
        print("Paper Watch RAG score skipped: no indexed PDF chunks found.")
        return

    candidate_limit = env_int("PAPER_WATCH_RAG_CANDIDATE_LIMIT", 30)
    min_term_score = env_float("PAPER_WATCH_RAG_MIN_TERM_SCORE", 1.0)
    rag_weight = env_float("PAPER_WATCH_RAG_WEIGHT", 8.0)
    model = paper_watch_embed_model()

    eligible_entries = [
        entry for entry in entries if entry.get("term_score", 0.0) >= min_term_score
    ]
    eligible_entries.sort(
        key=lambda item: (item.get("term_score", 0.0), item["published_at"]),
        reverse=True,
    )

    for entry in eligible_entries[:candidate_limit]:
        try:
            query_vector = embed(candidate_embedding_text(entry), model, timeout=180)
        except OllamaError as exc:
            print(
                f"Paper Watch RAG score failed for {entry['external_id']}: {exc}",
                file=sys.stderr,
            )
            continue

        rag_score, nearest = best_rag_match(query_vector, reference_chunks)
        entry["rag_score"] = rag_score
        entry["score"] = entry["term_score"] + (rag_score * rag_weight)
        if nearest:
            entry["rag_source"] = nearest["source"]
            entry["rag_source_label"] = nearest["source_label"]
            entry["rag_page_start"] = nearest["page_start"]
            entry["rag_page_end"] = nearest["page_end"]


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
            term_score, rag_score, rag_source, rag_page_start, rag_page_end,
            first_seen_at, last_seen_at, posted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(source, external_id) DO UPDATE SET
            title = excluded.title,
            authors_json = excluded.authors_json,
            summary = excluded.summary,
            url = excluded.url,
            published_at = excluded.published_at,
            updated_at = excluded.updated_at,
            score = excluded.score,
            reasons_json = excluded.reasons_json,
            term_score = excluded.term_score,
            rag_score = excluded.rag_score,
            rag_source = excluded.rag_source,
            rag_page_start = excluded.rag_page_start,
            rag_page_end = excluded.rag_page_end,
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
            entry.get("term_score", entry["score"]),
            entry.get("rag_score", 0.0),
            entry.get("rag_source") or None,
            entry.get("rag_page_start"),
            entry.get("rag_page_end"),
            now,
            now,
        ),
    )
    return existing is None


def select_candidates(conn: sqlite3.Connection, min_score: float, limit: int) -> list[dict]:
    metadata_by_source = load_metadata_by_source(conn)
    rows = conn.execute(
        """
        SELECT source, external_id, title, authors_json, summary, url,
               published_at, score, reasons_json,
               term_score, rag_score, rag_source, rag_page_start, rag_page_end
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
        rag_source = row[11] or ""
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
                "term_score": row[9],
                "rag_score": row[10],
                "rag_source": rag_source,
                "rag_source_label": format_rag_source_label(
                    rag_source,
                    metadata_by_source.get(rag_source, {}),
                ) if rag_source else "",
                "rag_page_start": row[12],
                "rag_page_end": row[13],
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


def paper_watch_translation_model() -> str:
    return os.environ.get(
        "PAPER_WATCH_TRANSLATION_MODEL",
        os.environ.get("PAPERBOT_TRANSLATION_MODEL", ""),
    ).strip()


def paper_watch_translation_enabled() -> bool:
    model = paper_watch_translation_model()
    default = bool(model)
    return bool(model) and env_bool("PAPER_WATCH_TRANSLATION_ENABLED", default)


@lru_cache(maxsize=1)
def load_lab_profile_context() -> str:
    if not LAB_PROFILE_PATH.exists():
        return "not available"
    try:
        profile = json.loads(LAB_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "not available"

    categories = profile.get("categories", {})
    labels = {
        "materials": "Materials",
        "methods": "Methods",
        "physics": "Physics",
    }
    lines = []
    for key, label in labels.items():
        entries = categories.get(key, [])[:8]
        names = [entry.get("label", "") for entry in entries if entry.get("label")]
        if names:
            lines.append(f"{label}: {', '.join(names)}")
    return "\n".join(lines) if lines else "not available"


def fallback_intro(item: dict, *, reason: str = "failed") -> str:
    if reason == "disabled":
        en_line = "EN: Bilingual introduction is disabled; please open the linked paper."
        ja_line = "JA: 日英紹介文は無効化されています。リンク先の論文を確認してください。"
    else:
        en_line = "EN: Bilingual introduction failed; please open the linked paper."
        ja_line = "JA: 日英紹介文の生成に失敗しました。リンク先の論文を確認してください。"
    return "\n".join(
        [
            en_line,
            ja_line,
        ]
    )


def strip_intro_label(text: str, label: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(rf"^{re.escape(label)}\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip().strip('"').strip("'").strip()


def build_english_intro_prompt(item: dict) -> str:
    reasons = ", ".join(item["reasons"]) if item["reasons"] else "profile match"
    lab_profile = load_lab_profile_context()
    rag_hint = "not used"
    if item.get("rag_score", 0) > 0 and item.get("rag_source_label"):
        page = item.get("rag_page_start") or "?"
        rag_hint = (
            f"nearest indexed lab PDF similarity={item['rag_score']:.3f}; "
            f"nearest PDF={item['rag_source_label']} p.{page}"
        )
    return f"""You are KohdaLab's Paper Watch assistant.
Based only on the title and abstract below, write an English paper introduction.
Do not invent results, numbers, materials, or methods that are not in the abstract.
Keep technical terms such as Rashba, Dresselhaus, spin-orbit, exciton, magnon, TRKR, PSH, and 2DEG in English.
Use the relevance hints only to judge likely lab relevance; do not claim findings from the nearest lab PDF unless they also appear in the abstract.

Output exactly one concise English sentence explaining why this may be relevant to the lab.
Do not include a label such as "EN:".

Profile match terms: {reasons}
RAG relevance hint: {rag_hint}
Lab profile from indexed PDFs:
{lab_profile}

Title:
{item['title']}

Abstract:
{item['summary']}
"""


def build_intro_translation_prompt(item: dict, english_intro: str) -> str:
    return f"""Translate the English paper introduction into natural Japanese.
Do not add, remove, or change scientific claims.
Preserve technical terms and proper nouns in English when appropriate.
Keep Rashba, Dresselhaus, spin-orbit, exciton, magnon, TRKR, PSH, 2DEG, and material names as-is.
Output exactly one Japanese sentence. Do not include a label such as "JA:".

Title:
{item['title']}

English introduction:
{english_intro}

Japanese introduction:
"""


def bilingual_intro(item: dict, *, enabled: bool) -> str:
    if not enabled:
        return fallback_intro(item, reason="disabled")
    try:
        english_intro = strip_intro_label(
            generate(build_english_intro_prompt(item), summary_model(), timeout=180),
            "EN",
        )
    except OllamaError:
        return fallback_intro(item)
    if not english_intro:
        return fallback_intro(item)

    japanese_intro = ""
    if paper_watch_translation_enabled():
        try:
            japanese_intro = strip_intro_label(
                generate(
                    build_intro_translation_prompt(item, english_intro),
                    paper_watch_translation_model(),
                    timeout=180,
                ),
                "JA",
            )
        except OllamaError:
            japanese_intro = ""

    if not japanese_intro:
        japanese_intro = "日本語紹介文の生成に失敗しました。リンク先の論文を確認してください。"

    return f"EN: {english_intro}\nJA: {japanese_intro}"


def build_message(
    items: list[dict],
    *,
    terms: dict[str, float] | None = None,
    include_intro: bool = True,
    use_rag_score: bool = False,
    include_abstract: bool = False,
    verbose: bool = False,
) -> str:
    lines = ["*Paper Watch / 新着論文紹介*"]
    if terms and verbose:
        lines.append(f"profile=`{profile_label(terms)}`")
        if use_rag_score:
            weight = env_float("PAPER_WATCH_RAG_WEIGHT", 8.0)
            lines.append(f"scoring=`term score + RAG similarity x {weight:g}`")
        else:
            lines.append("scoring=`profile term match`")
    for index, item in enumerate(items, start=1):
        reasons = ", ".join(item["reasons"]) if item["reasons"] else "profile match"
        score_parts = [
            f"`{item['source']}:{item['external_id']}`",
            f"score=`{item['score']:.1f}`",
            f"term=`{item.get('term_score', item['score']):.1f}`",
        ]
        if use_rag_score:
            score_parts.append(f"rag=`{item.get('rag_score', 0.0):.3f}`")
        score_parts.append(f"reasons=`{reasons}`")
        lines.extend(["", f"*{index}. {item['title']}*", f"{compact_authors(item['authors'])}"])
        if verbose:
            lines.append(" ".join(score_parts))
        lines.extend([bilingual_intro(item, enabled=include_intro), item["url"]])
        if include_abstract:
            lines.append(f"Abstract: {truncate(item['summary'], 360)}")
        if use_rag_score and item.get("rag_source_label"):
            page_start = item.get("rag_page_start") or "?"
            page_end = item.get("rag_page_end") or page_start
            lines.append(
                "Nearest lab PDF / 近い研究室PDF: "
                f"{truncate(item['rag_source_label'], 180)} pp.{page_start}-{page_end}"
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
    parser.add_argument("--include-abstract", action="store_true", help="Include abstracts in Slack output.")
    parser.add_argument("--verbose-message", action="store_true", help="Include profile and score details in Slack output.")
    parser.add_argument(
        "--no-rag-score",
        action="store_true",
        help="Disable abstract-to-RAG-index similarity scoring.",
    )
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
        term_score, reasons = score_entry(entry, terms)
        entry["term_score"] = term_score
        entry["score"] = term_score
        entry["rag_score"] = 0.0
        entry["rag_source"] = ""
        entry["rag_source_label"] = ""
        entry["rag_page_start"] = None
        entry["rag_page_end"] = None
        entry["reasons"] = reasons
        entries.append(entry)

    conn = init_db(INDEX_DB_PATH)
    try:
        use_rag_score = env_bool("PAPER_WATCH_USE_RAG_SCORE", True) and not args.no_rag_score
        apply_rag_scores(entries, conn, enabled=use_rag_score)
        new_count = 0
        for entry in entries:
            if upsert_entry(conn, entry):
                new_count += 1
        candidates = select_candidates(conn, args.min_score, args.post_limit)
        if candidates:
            include_intro = env_bool("PAPER_WATCH_BILINGUAL_INTRO", True) and not args.no_summary
            include_abstract = (
                env_bool("PAPER_WATCH_INCLUDE_ABSTRACT", False) or args.include_abstract
            )
            verbose_message = (
                env_bool("PAPER_WATCH_VERBOSE_MESSAGE", False) or args.verbose_message
            )
            message = build_message(
                candidates,
                terms=terms,
                include_intro=include_intro,
                use_rag_score=use_rag_score,
                include_abstract=include_abstract,
                verbose=verbose_message,
            )
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
