import argparse
import email.utils
import html
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
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
CROSSREF_API_URL = "https://api.crossref.org/works"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)

DEFAULT_RSS_FEEDS = [
    {
        "id": "aps_prl",
        "group": "pr",
        "journal": "Physical Review Letters",
        "url": "https://feeds.aps.org/rss/recent/prl.xml",
    },
    {
        "id": "aps_prb",
        "group": "pr",
        "journal": "Physical Review B",
        "url": "https://feeds.aps.org/rss/recent/prb.xml",
    },
    {
        "id": "aps_prapplied",
        "group": "pr",
        "journal": "Physical Review Applied",
        "url": "https://feeds.aps.org/rss/recent/prapplied.xml",
    },
    {
        "id": "aps_prx",
        "group": "pr_ext",
        "journal": "Physical Review X",
        "url": "https://feeds.aps.org/rss/recent/prx.xml",
    },
    {
        "id": "aps_prresearch",
        "group": "pr_ext",
        "journal": "Physical Review Research",
        "url": "https://feeds.aps.org/rss/recent/prresearch.xml",
    },
    {
        "id": "aps_prmaterials",
        "group": "pr_ext",
        "journal": "Physical Review Materials",
        "url": "https://feeds.aps.org/rss/recent/prmaterials.xml",
    },
    {
        "id": "aps_rmp",
        "group": "pr_ext",
        "journal": "Reviews of Modern Physics",
        "url": "https://feeds.aps.org/rss/recent/rmp.xml",
    },
    {
        "id": "nature_physics",
        "group": "nature",
        "journal": "Nature Physics",
        "url": "https://www.nature.com/nphys.rss",
    },
    {
        "id": "nature_communications",
        "group": "nature",
        "journal": "Nature Communications",
        "url": "https://www.nature.com/ncomms.rss",
    },
    {
        "id": "communications_physics",
        "group": "nature",
        "journal": "Communications Physics",
        "url": "https://www.nature.com/commsphys.rss",
    },
    {
        "id": "nature_materials",
        "group": "nature_ext",
        "journal": "Nature Materials",
        "url": "https://www.nature.com/nmat.rss",
    },
    {
        "id": "nature_nanotechnology",
        "group": "nature_ext",
        "journal": "Nature Nanotechnology",
        "url": "https://www.nature.com/nnano.rss",
    },
    {
        "id": "nature_electronics",
        "group": "nature_ext",
        "journal": "Nature Electronics",
        "url": "https://www.nature.com/natelectron.rss",
    },
    {
        "id": "nature_photonics",
        "group": "nature_ext",
        "journal": "Nature Photonics",
        "url": "https://www.nature.com/nphoton.rss",
    },
    {
        "id": "aip_apl",
        "group": "aip",
        "journal": "Applied Physics Letters",
        "url": "https://pubs.aip.org/action/showFeed?type=etoc&feed=rss&jc=apl",
    },
]

RSS_CROSSREF_FALLBACKS = {
    "aip_apl": {
        "group": "aip",
        "journal": "Applied Physics Letters",
        "query": "spin-orbit spintronics semiconductor Rashba Dresselhaus TRKR",
        "issn": "0003-6951",
    },
    "prx": {
        "group": "pr_ext",
        "journal": "Physical Review X",
        "query": "spin-orbit spintronics semiconductor quantum materials optical spectroscopy",
        "issn": "2160-3308",
    },
    "prresearch": {
        "group": "pr_ext",
        "journal": "Physical Review Research",
        "query": "spin-orbit spintronics semiconductor Rashba exciton magnon",
        "issn": "2643-1564",
    },
    "prmaterials": {
        "group": "pr_ext",
        "journal": "Physical Review Materials",
        "query": "spin-orbit semiconductor two-dimensional materials magnetism exciton",
        "issn": "2475-9953",
    },
    "rmp": {
        "group": "pr_ext",
        "journal": "Reviews of Modern Physics",
        "query": "spintronics spin-orbit semiconductor two-dimensional materials magnetism",
        "issn": "0034-6861",
    },
    "nano_letters": {
        "group": "nano_2d",
        "journal": "Nano Letters",
        "query": "spin-orbit spintronics two-dimensional materials exciton valley semiconductor",
        "issn": "1530-6992",
    },
    "acs_nano": {
        "group": "nano_2d",
        "journal": "ACS Nano",
        "query": "two-dimensional materials spin exciton valley magnetism photonics",
        "issn": "1936-086X",
    },
    "acs_photonics": {
        "group": "nano_2d",
        "journal": "ACS Photonics",
        "query": "spin optical spectroscopy exciton valley semiconductor photonics",
        "issn": "2330-4022",
    },
    "acs_ami": {
        "group": "nano_2d",
        "journal": "ACS Applied Materials & Interfaces",
        "query": "spintronics two-dimensional semiconductor optical materials exciton",
        "issn": "1944-8252",
    },
    "two_d_materials": {
        "group": "nano_2d",
        "journal": "2D Materials",
        "query": "two-dimensional semiconductor spin-orbit exciton valley magnetism",
        "issn": "2053-1583",
    },
    "science_advances": {
        "group": "broad_high",
        "journal": "Science Advances",
        "query": "spin-orbit spintronics semiconductor two-dimensional materials exciton magnon",
        "issn": "2375-2548",
    },
    "advanced_materials": {
        "group": "broad_high",
        "journal": "Advanced Materials",
        "query": "spintronics two-dimensional materials semiconductor exciton magnetism photonics",
        "issn": "1521-4095",
    },
    "advanced_science": {
        "group": "broad_high",
        "journal": "Advanced Science",
        "query": "spin-orbit spintronics two-dimensional materials exciton valley magnetism",
        "issn": "2198-3844",
    },
}

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


def normalize_doi(value: str) -> str:
    doi = compact_whitespace(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.strip().rstrip(".,;)]}")


def extract_doi(*texts: str) -> str:
    for text in texts:
        match = DOI_RE.search(text or "")
        if match:
            return normalize_doi(match.group(0))
    return ""


def normalize_title_for_dedupe(title: str) -> str:
    normalized = compact_whitespace(html.unescape(title)).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return compact_whitespace(normalized)


def entry_dedupe_key(entry: dict) -> str:
    doi = normalize_doi(entry.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    title = entry_title_key(entry)
    return f"title:{title}" if title else f"{entry['source']}:{entry['external_id']}"


def entry_title_key(entry: dict) -> str:
    return normalize_title_for_dedupe(entry.get("title", ""))


def strip_markup(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return compact_whitespace(text)


def parse_feed_datetime(value: str) -> datetime:
    value = compact_whitespace(value)
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return parse_datetime(value)
    except ValueError:
        return datetime.now(timezone.utc)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if local_name(child.tag) == name]


def child_text(element: ET.Element, *names: str) -> str:
    for name in names:
        for child in direct_children(element, name):
            text = "".join(child.itertext())
            if compact_whitespace(text):
                return compact_whitespace(text)
    return ""


def child_attr(element: ET.Element, name: str, attr: str) -> str:
    for child in direct_children(element, name):
        value = child.attrib.get(attr, "")
        if value:
            return compact_whitespace(value)
    return ""


def parse_crossref_date(item: dict) -> datetime:
    for key in ("published-online", "published-print", "published", "created", "indexed"):
        value = item.get(key) or {}
        date_parts = value.get("date-parts") or []
        if not date_parts or not date_parts[0]:
            continue
        parts = list(date_parts[0])
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return datetime(year, month, day, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def crossref_author_names(item: dict) -> list[str]:
    authors = []
    for author in item.get("author", [])[:10]:
        given = compact_whitespace(author.get("given", ""))
        family = compact_whitespace(author.get("family", ""))
        name = compact_whitespace(f"{given} {family}")
        if name:
            authors.append(name)
    return authors


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
            term_score REAL NOT NULL DEFAULT 0,
            rag_score REAL NOT NULL DEFAULT 0,
            rag_source TEXT,
            rag_page_start INTEGER,
            rag_page_end INTEGER,
            doi TEXT,
            dedupe_key TEXT,
            title_key TEXT,
            source_detail TEXT,
            journal TEXT,
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
        "doi": "TEXT",
        "dedupe_key": "TEXT",
        "title_key": "TEXT",
        "source_detail": "TEXT",
        "journal": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE paper_watch_items ADD COLUMN {name} {column_type}")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_watch_dedupe_posted
        ON paper_watch_items(dedupe_key, posted_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_watch_title_posted
        ON paper_watch_items(title_key, posted_at)
        """
    )
    rows = conn.execute(
        """
        SELECT source, external_id, title
        FROM paper_watch_items
        WHERE title_key IS NULL OR title_key = ''
        """
    ).fetchall()
    if rows:
        conn.executemany(
            """
            UPDATE paper_watch_items
            SET title_key = ?
            WHERE source = ? AND external_id = ?
            """,
            [
                (normalize_title_for_dedupe(title), source, external_id)
                for source, external_id, title in rows
            ],
        )


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


def paper_watch_sources() -> set[str]:
    raw = os.environ.get("PAPER_WATCH_SOURCES", "arxiv,crossref,rss")
    return {source.strip().lower() for source in raw.split(",") if source.strip()}


def rss_groups() -> set[str]:
    raw = os.environ.get("PAPER_WATCH_RSS_GROUPS", "pr,nature,aip")
    return {group.strip().lower() for group in raw.split(",") if group.strip()}


def rss_feeds() -> list[dict]:
    configured = os.environ.get("PAPER_WATCH_RSS_FEEDS", "").strip()
    if not configured:
        return [dict(feed) for feed in DEFAULT_RSS_FEEDS]

    feeds = []
    for index, raw_feed in enumerate(configured.split(";"), start=1):
        parts = [compact_whitespace(part) for part in raw_feed.split("|", 3)]
        if len(parts) != 4:
            print(
                f"RSS feed config skipped at position {index}: expected id|group|journal|url",
                file=sys.stderr,
            )
            continue
        feed_id, group, journal, url = parts
        if feed_id and group and journal and url:
            feeds.append(
                {
                    "id": feed_id.lower(),
                    "group": group.lower(),
                    "journal": journal,
                    "url": url,
                }
            )
    return feeds


def crossref_queries(terms: dict[str, float]) -> list[str]:
    configured = os.environ.get("PAPER_WATCH_CROSSREF_QUERIES", "").strip()
    if configured:
        queries = [compact_whitespace(query) for query in configured.split(";")]
    else:
        queries = [
            "persistent spin helix Rashba Dresselhaus spin-orbit semiconductor",
            "time-resolved Kerr spin diffusion exciton spin magnon van der Waals magnet",
        ]

    max_queries = max(0, env_int("PAPER_WATCH_CROSSREF_MAX_QUERIES", 2))
    return [query for query in queries if query][:max_queries]


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
                "source_detail": "arxiv",
                "journal": "arXiv",
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


def crossref_user_agent() -> str:
    email = os.environ.get("PAPER_WATCH_CONTACT_EMAIL", "").strip()
    if email:
        return f"kohdalab-paperbot/0.1 (mailto:{email})"
    return "kohdalab-paperbot/0.1"


def crossref_url(query: str, *, rows: int, lookback_days: int, issn: str = "") -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    filters = [f"from-created-date:{since}", "type:journal-article"]
    if issn:
        filters.append(f"issn:{issn}")
    params = {
        "query.bibliographic": query,
        "filter": ",".join(filters),
        "sort": "created",
        "order": "desc",
        "rows": rows,
    }
    email = os.environ.get("PAPER_WATCH_CONTACT_EMAIL", "").strip()
    if email:
        params["mailto"] = email
    return f"{CROSSREF_API_URL}?{urllib.parse.urlencode(params)}"


def normalize_crossref_item(item: dict, *, source_detail: str = "crossref") -> dict | None:
    title = compact_whitespace(" ".join(item.get("title") or []))
    if not title:
        return None

    doi = normalize_doi(item.get("DOI", ""))
    external_id = doi or normalize_title_for_dedupe(title)
    if not external_id:
        return None

    published_at = parse_crossref_date(item)
    updated_at = parse_datetime(item.get("indexed", {}).get("date-time", "")) if item.get("indexed", {}).get("date-time") else published_at
    abstract = strip_markup(item.get("abstract", ""))
    journal = compact_whitespace(" ".join(item.get("container-title") or []))
    summary_parts = [abstract]
    if journal:
        summary_parts.append(f"Journal: {journal}.")
    if doi:
        summary_parts.append(f"DOI: {doi}.")
    summary = compact_whitespace(" ".join(part for part in summary_parts if part))
    if not summary:
        summary = title

    url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
    return {
        "source": "crossref",
        "source_detail": source_detail,
        "journal": journal,
        "external_id": external_id,
        "doi": doi,
        "dedupe_key": f"doi:{doi}" if doi else f"title:{normalize_title_for_dedupe(title)}",
        "title": title,
        "authors": crossref_author_names(item),
        "summary": summary,
        "url": url,
        "published_at": published_at,
        "updated_at": updated_at,
    }


def fetch_crossref_entries(
    queries: list[str],
    *,
    rows: int,
    lookback_days: int,
    issn: str = "",
    source_detail: str = "crossref",
) -> list[dict]:
    if not queries or rows <= 0:
        return []

    entries = []
    sleep_seconds = max(0.0, env_float("PAPER_WATCH_CROSSREF_SLEEP_SECONDS", 1.0))
    for index, query in enumerate(queries):
        if index > 0 and sleep_seconds:
            time.sleep(sleep_seconds)

        url = crossref_url(query, rows=rows, lookback_days=lookback_days, issn=issn)
        req = urllib.request.Request(url, headers={"User-Agent": crossref_user_agent()})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                payload = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 400 <= exc.code < 500:
                print(f"Crossref fetch stopped after HTTP {exc.code}; backing off.", file=sys.stderr)
                break
            print(f"Crossref fetch failed with HTTP {exc.code}; skipping query.", file=sys.stderr)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            print(f"Crossref fetch failed; skipping query: {exc}", file=sys.stderr)
            continue

        items = payload.get("message", {}).get("items", [])
        for item in items:
            entry = normalize_crossref_item(item, source_detail=source_detail)
            if entry:
                entries.append(entry)
    return entries


def rss_author_names(element: ET.Element) -> list[str]:
    authors = []
    for name in ("creator", "author"):
        for child in direct_children(element, name):
            author = child_text(child, "name") or compact_whitespace("".join(child.itertext()))
            if author:
                authors.append(author)
    seen = set()
    unique_authors = []
    for author in authors:
        key = author.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_authors.append(author)
    return unique_authors[:10]


def rss_link(element: ET.Element) -> str:
    link = child_text(element, "link")
    if link:
        return link
    return child_attr(element, "link", "href")


def rss_item_elements(root: ET.Element) -> list[ET.Element]:
    elements = [element for element in root.iter() if local_name(element.tag) == "item"]
    if elements:
        return elements
    return [element for element in root.iter() if local_name(element.tag) == "entry"]


def normalize_rss_item(item: ET.Element, feed: dict) -> dict | None:
    title = strip_markup(child_text(item, "title"))
    if not title:
        return None

    link = rss_link(item)
    guid = child_text(item, "guid", "id")
    description = child_text(item, "description", "summary", "abstract")
    content = child_text(item, "encoded", "content")
    summary = strip_markup(" ".join(part for part in (description, content) if part)) or title
    date_text = child_text(item, "pubDate", "published", "updated", "date")
    published_at = parse_feed_datetime(date_text)
    updated_at = published_at

    doi_text = child_text(item, "doi", "identifier")
    doi = extract_doi(doi_text, guid, link, title, description, content)
    if not doi and doi_text.lower().startswith(("doi:", "10.")):
        doi = normalize_doi(doi_text)
    journal = feed["journal"]
    summary_parts = [summary, f"Journal: {journal}."]
    if doi:
        summary_parts.append(f"DOI: {doi}.")
    summary = compact_whitespace(" ".join(summary_parts))

    title_key = normalize_title_for_dedupe(title)
    external_id = doi or guid or link or f"{feed['id']}:{title_key}"
    external_id = compact_whitespace(external_id)[:220]
    if not external_id:
        return None

    return {
        "source": "rss",
        "source_detail": feed["id"],
        "journal": journal,
        "external_id": external_id,
        "doi": doi,
        "dedupe_key": f"doi:{doi}" if doi else f"title:{title_key}",
        "title_key": title_key,
        "title": title,
        "authors": rss_author_names(item),
        "summary": summary,
        "url": link or (f"https://doi.org/{doi}" if doi else ""),
        "published_at": published_at,
        "updated_at": updated_at,
    }


def fetch_rss_entries(feeds: list[dict], *, max_items_per_feed: int) -> list[dict]:
    if not feeds or max_items_per_feed <= 0:
        return []

    entries = []
    sleep_seconds = max(0.0, env_float("PAPER_WATCH_RSS_SLEEP_SECONDS", 1.0))
    user_agent = os.environ.get("PAPER_WATCH_RSS_USER_AGENT", crossref_user_agent())
    for index, feed in enumerate(feeds):
        if index > 0 and sleep_seconds:
            time.sleep(sleep_seconds)

        req = urllib.request.Request(feed["url"], headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                body = res.read()
            root = ET.fromstring(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 400 <= exc.code < 500:
                print(
                    f"RSS feed skipped: {feed['id']} HTTP {exc.code}. "
                    "No retry in this run.",
                    file=sys.stderr,
                )
            else:
                print(f"RSS feed failed: {feed['id']} HTTP {exc.code}.", file=sys.stderr)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as exc:
            print(f"RSS feed failed: {feed['id']} {exc}", file=sys.stderr)
            continue

        for item in rss_item_elements(root)[:max_items_per_feed]:
            entry = normalize_rss_item(item, feed)
            if entry:
                entries.append(entry)
    return entries


def rss_crossref_fallback_entries(
    selected_groups: set[str],
    rss_entries: list[dict],
    feeds: list[dict],
    *,
    lookback_days: int,
) -> list[dict]:
    if not env_bool("PAPER_WATCH_RSS_CROSSREF_FALLBACK", True):
        return []

    feed_group_by_id = {feed["id"]: feed["group"] for feed in feeds}
    groups_with_entries = {
        feed_group_by_id.get(entry.get("source_detail", ""))
        for entry in rss_entries
        if entry.get("source") == "rss"
    }
    groups_with_entries.discard(None)

    rows = env_int("PAPER_WATCH_RSS_CROSSREF_FALLBACK_ROWS", 10)
    max_journals = max(0, env_int("PAPER_WATCH_RSS_CROSSREF_FALLBACK_MAX_JOURNALS", 6))
    sleep_seconds = max(0.0, env_float("PAPER_WATCH_CROSSREF_SLEEP_SECONDS", 1.0))
    fallback_entries = []
    fallback_count = 0
    for group in sorted(selected_groups):
        if group in groups_with_entries:
            continue
        fallbacks = [
            (fallback_id, fallback)
            for fallback_id, fallback in RSS_CROSSREF_FALLBACKS.items()
            if fallback["group"] == group
        ]
        if not fallbacks:
            continue

        remaining = max_journals - fallback_count if max_journals else 0
        if remaining <= 0:
            break
        selected_fallbacks = fallbacks[:remaining]
        print(
            f"RSS group {group} had no entries; trying "
            f"{len(selected_fallbacks)} conservative Crossref fallback journal(s)."
        )
        for fallback_id, fallback in selected_fallbacks:
            if fallback_count > 0 and sleep_seconds:
                time.sleep(sleep_seconds)
            fallback_entries.extend(
                fetch_crossref_entries(
                    [fallback["query"]],
                    rows=rows,
                    lookback_days=lookback_days,
                    issn=fallback.get("issn", ""),
                    source_detail=f"crossref:{fallback_id}",
                )
            )
            fallback_count += 1
    return fallback_entries


def score_entry(entry: dict, terms: dict[str, float]) -> tuple[float, list[str]]:
    text = f"{entry['title']} {entry['summary']}".lower()
    score = 0.0
    reasons = []
    for term, weight in terms.items():
        if term in text:
            score += weight
            reasons.append(term)
    return score, reasons[:8]


def ensure_entry_identity(entry: dict) -> None:
    entry["doi"] = normalize_doi(entry.get("doi", ""))
    entry["title_key"] = entry.get("title_key") or entry_title_key(entry)
    entry["dedupe_key"] = entry.get("dedupe_key") or entry_dedupe_key(entry)
    entry["source_detail"] = compact_whitespace(entry.get("source_detail", "")) or entry["source"]
    entry["journal"] = compact_whitespace(entry.get("journal", ""))


def entry_identity_keys(entry: dict) -> list[str]:
    keys = [entry["dedupe_key"]]
    if entry.get("title_key"):
        keys.append(f"title:{entry['title_key']}")
    return list(dict.fromkeys(key for key in keys if key))


def preferred_entry(existing: dict, entry: dict) -> dict:
    entry_has_doi = bool(entry.get("doi"))
    existing_has_doi = bool(existing.get("doi"))
    entry_summary_len = len(entry.get("summary", ""))
    existing_summary_len = len(existing.get("summary", ""))
    if entry_has_doi and not existing_has_doi:
        return entry
    if existing_has_doi and not entry_has_doi:
        return existing
    if entry_summary_len > existing_summary_len:
        return entry
    if existing_summary_len > entry_summary_len:
        return existing
    return entry if entry["published_at"] > existing["published_at"] else existing


def dedupe_entries(entries: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for entry in entries:
        ensure_entry_identity(entry)
        keys = entry_identity_keys(entry)
        existing = next((by_key[key] for key in keys if key in by_key), None)
        if not existing:
            for key in keys:
                by_key[key] = entry
            continue

        preferred = preferred_entry(existing, entry)
        for key in set(keys + entry_identity_keys(existing)):
            by_key[key] = preferred

    unique_entries = []
    seen = set()
    for entry in by_key.values():
        identity = (entry["source"], entry["external_id"])
        if identity in seen:
            continue
        seen.add(identity)
        unique_entries.append(entry)
    return unique_entries


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
            doi, dedupe_key, title_key, source_detail, journal,
            first_seen_at, last_seen_at, posted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
            doi = excluded.doi,
            dedupe_key = excluded.dedupe_key,
            title_key = excluded.title_key,
            source_detail = excluded.source_detail,
            journal = excluded.journal,
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
            entry.get("doi") or None,
            entry.get("dedupe_key") or entry_dedupe_key(entry),
            entry.get("title_key") or entry_title_key(entry),
            entry.get("source_detail") or None,
            entry.get("journal") or None,
            now,
            now,
        ),
    )
    return existing is None


def select_candidates(
    conn: sqlite3.Connection,
    min_score: float,
    limit: int,
    current_entries: list[dict],
) -> list[dict]:
    current_keys = [
        (entry["source"], entry["external_id"])
        for entry in current_entries
        if entry.get("source") and entry.get("external_id")
    ]
    if not current_keys:
        return []

    current_filters = " OR ".join(
        ["(candidate.source = ? AND candidate.external_id = ?)"] * len(current_keys)
    )
    current_params = [value for key in current_keys for value in key]
    metadata_by_source = load_metadata_by_source(conn)
    fetch_limit = max(limit * 5, limit)
    rows = conn.execute(
        f"""
        SELECT source, external_id, title, authors_json, summary, url,
               published_at, score, reasons_json,
               term_score, rag_score, rag_source, rag_page_start, rag_page_end,
               doi, dedupe_key, title_key, source_detail, journal
        FROM paper_watch_items AS candidate
        WHERE candidate.posted_at IS NULL
          AND candidate.score >= ?
          AND ({current_filters})
          AND NOT EXISTS (
              SELECT 1
              FROM paper_watch_items AS posted
              WHERE posted.posted_at IS NOT NULL
                AND posted.dedupe_key IS NOT NULL
                AND posted.dedupe_key = candidate.dedupe_key
          )
          AND NOT EXISTS (
              SELECT 1
              FROM paper_watch_items AS posted
              WHERE posted.posted_at IS NOT NULL
                AND posted.title_key IS NOT NULL
                AND posted.title_key = candidate.title_key
          )
        ORDER BY score DESC, published_at DESC
        LIMIT ?
        """,
        (min_score, *current_params, fetch_limit),
    ).fetchall()
    items = []
    seen_dedupe_keys = set()
    for row in rows:
        dedupe_key = row[15] or ""
        title_key = row[16] or ""
        identity_key = dedupe_key or (f"title:{title_key}" if title_key else "")
        title_identity_key = f"title:{title_key}" if title_key else ""
        if identity_key and identity_key in seen_dedupe_keys:
            continue
        if title_identity_key and title_identity_key in seen_dedupe_keys:
            continue
        for key in (identity_key, title_identity_key):
            if key:
                seen_dedupe_keys.add(key)
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
                "doi": row[14] or "",
                "dedupe_key": dedupe_key,
                "title_key": title_key,
                "source_detail": row[17] or "",
                "journal": row[18] or "",
            }
        )
        if len(items) >= limit:
            break
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


def source_label(item: dict) -> str:
    if item.get("source") == "arxiv":
        return "arXiv"
    if item.get("source") == "crossref":
        return item.get("journal") or "Crossref"
    if item.get("source") == "rss":
        return item.get("journal") or item.get("source_detail") or "RSS"
    return item.get("source", "unknown")


def relevance_grade(item: dict) -> str:
    score = float(item.get("score", 0.0))
    rag_score = float(item.get("rag_score", 0.0))
    term_score = float(item.get("term_score", score))
    if score >= 14 or rag_score >= 0.78 or term_score >= 10:
        return "S"
    if score >= 9 or rag_score >= 0.70 or term_score >= 6:
        return "A"
    return "B"


def compact_reasons(item: dict) -> str:
    reasons = item.get("reasons") or []
    if not reasons:
        return "profile match"
    return ", ".join(reasons[:3])


def nearest_pdf_label(item: dict) -> str:
    if not item.get("rag_source_label"):
        return ""
    page_start = item.get("rag_page_start") or "?"
    page_end = item.get("rag_page_end") or page_start
    return f"{truncate(item['rag_source_label'], 90)} pp.{page_start}-{page_end}"


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
        "applications": "Applications",
        "journals": "Journals",
        "authors": "Authors",
    }
    lines = []
    for key, label in labels.items():
        entries = categories.get(key, [])[:6]
        names = [entry.get("label", "") for entry in entries if entry.get("label")]
        if names:
            lines.append(f"{label}: {', '.join(names)}")
    return "\n".join(lines) if lines else "not available"


def fallback_intro(item: dict, *, reason: str = "failed") -> str:
    if reason == "disabled":
        en_line = "EN: Bilingual technical note is disabled; please open the linked paper."
        ja_line = "JA: 日英解説は無効化されています。リンク先の論文を確認してください。"
    else:
        en_line = "EN: Bilingual technical note failed; please open the linked paper."
        ja_line = "JA: 日英解説の生成に失敗しました。リンク先の論文を確認してください。"
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
    cleaned = cleaned.replace("**", "")
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
    return f"""You are an expert research assistant for KohdaLab.
Read the title and abstract of the paper.
Write a concise technical commentary in English for researchers.

Your explanation should answer:
1. What problem is being addressed?
2. What material, device, or physical system is studied?
3. What experimental, theoretical, or computational methods are used?
4. What is the main physical insight or discovery?
5. What is genuinely new compared with prior work?
6. Why might this result matter for future research or applications?

Requirements:
- Write a single coherent explanation.
- Do not simply rewrite the abstract.
- Emphasize the physics and scientific significance.
- Mention important materials and measurement techniques when relevant.
- Use 100-180 words total, including the final relevance section.
- Assume the reader is a graduate student or researcher in physics, materials science, or electrical engineering.
- If information is not available from the abstract, do not speculate.
- Keep technical terms such as Rashba, Dresselhaus, spin-orbit, exciton, magnon, TRKR, PSH, and 2DEG in English.
- Use the relevance hints only to judge likely lab relevance; do not claim findings from the nearest lab PDF unless they also appear in the abstract.
- Do not include a label such as "EN:".
- Do not use markdown bullets, numbered lists, bold text, or extra headings.

Finally, add a short section:
Relevance to our research:
(1 sentence, maximum 35 words)

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
    return f"""Translate the English technical commentary into natural Japanese.
Do not add, remove, or change scientific claims.
Preserve technical terms and proper nouns in English when appropriate.
Keep Rashba, Dresselhaus, spin-orbit, exciton, magnon, TRKR, PSH, 2DEG, and material names as-is.
Keep the same structure, including the final section:
Relevance to our research:
Translate that heading as:
研究室との関連:
Write natural Japanese for graduate students or researchers.
Do not include a label such as "JA:".

Title:
{item['title']}

English introduction:
{english_intro}

Japanese introduction:
"""


def bilingual_intro(item: dict, *, enabled: bool) -> str:
    if not enabled:
        return ""
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
        japanese_intro = "日本語解説の生成に失敗しました。リンク先の論文を確認してください。"

    return f"EN: {english_intro}\nJA: {japanese_intro}"


def slack_link(url: str, label: str = "open") -> str:
    if not url:
        return ""
    return f"<{url}|{label}>"


def score_summary(item: dict, *, use_rag_score: bool) -> str:
    parts = [
        f"score={item['score']:.1f}",
        f"term={item.get('term_score', item['score']):.1f}",
    ]
    if use_rag_score:
        parts.append(f"rag={item.get('rag_score', 0.0):.3f}")
    return " ".join(parts)


def build_item_message(
    item: dict,
    *,
    include_intro: bool = True,
    use_rag_score: bool = False,
    include_abstract: bool = False,
    verbose: bool = False,
) -> str:
    reasons = compact_reasons(item)
    grade = relevance_grade(item)
    source = source_label(item)
    lines = [
        f"*Paper Watch*  `[{grade}] {source}`",
        f"*{item['title']}*",
        compact_authors(item["authors"]),
        f"match: {reasons}",
    ]

    nearest = nearest_pdf_label(item) if use_rag_score else ""
    if nearest:
        lines.append(f"near: {nearest}")
    if verbose:
        lines.append(
            f"`{score_summary(item, use_rag_score=use_rag_score)}` "
            f"`{item['source']}:{item['external_id']}`"
        )

    intro = bilingual_intro(item, enabled=include_intro)
    if intro:
        lines.append(intro)
    if include_abstract:
        lines.append(f"abstract: {truncate(item['summary'], 260)}")

    link = slack_link(item.get("url", ""))
    if link:
        lines.append(f"link: {link}")
    return "\n".join(lines)


def build_messages(
    items: list[dict],
    *,
    include_intro: bool = True,
    use_rag_score: bool = False,
    include_abstract: bool = False,
    verbose: bool = False,
) -> list[str]:
    return [
        build_item_message(
            item,
            include_intro=include_intro,
            use_rag_score=use_rag_score,
            include_abstract=include_abstract,
            verbose=verbose,
        )
        for item in items
    ]


def build_message(
    items: list[dict],
    *,
    terms: dict[str, float] | None = None,
    include_intro: bool = True,
    use_rag_score: bool = False,
    include_abstract: bool = False,
    verbose: bool = False,
) -> str:
    del terms
    return "\n\n---\n\n".join(
        build_messages(
            items,
            include_intro=include_intro,
            use_rag_score=use_rag_score,
            include_abstract=include_abstract,
            verbose=verbose,
        )
    )


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
    parser.add_argument("--sources", default="", help="Comma-separated sources, e.g. arxiv,crossref,rss.")
    parser.add_argument("--rss-groups", default="", help="Comma-separated RSS groups, e.g. pr,nature,aip.")
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
    sources = (
        {source.strip().lower() for source in args.sources.split(",") if source.strip()}
        if args.sources
        else paper_watch_sources()
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.lookback_days)

    entries = []
    if "arxiv" in sources:
        query = arxiv_query(terms)
        try:
            entries.extend(fetch_arxiv_entries(query, args.max_results))
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as exc:
            print(f"arXiv fetch failed; continuing with other sources: {exc}", file=sys.stderr)

    if "crossref" in sources:
        rows = env_int("PAPER_WATCH_CROSSREF_ROWS", 10)
        entries.extend(
            fetch_crossref_entries(
                crossref_queries(terms),
                rows=rows,
                lookback_days=args.lookback_days,
            )
        )

    if "rss" in sources:
        selected_groups = (
            {group.strip().lower() for group in args.rss_groups.split(",") if group.strip()}
            if args.rss_groups
            else rss_groups()
        )
        feeds = [feed for feed in rss_feeds() if feed["group"] in selected_groups]
        rss_entries = fetch_rss_entries(
            feeds,
            max_items_per_feed=env_int("PAPER_WATCH_RSS_MAX_ITEMS_PER_FEED", 20),
        )
        entries.extend(rss_entries)
        entries.extend(
            rss_crossref_fallback_entries(
                selected_groups,
                rss_entries,
                feeds,
                lookback_days=args.lookback_days,
            )
        )

    entries = dedupe_entries(entries)
    filtered_entries = []
    for entry in entries:
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
        filtered_entries.append(entry)
    entries = filtered_entries

    conn = init_db(INDEX_DB_PATH)
    try:
        use_rag_score = env_bool("PAPER_WATCH_USE_RAG_SCORE", True) and not args.no_rag_score
        apply_rag_scores(entries, conn, enabled=use_rag_score)
        new_count = 0
        for entry in entries:
            if upsert_entry(conn, entry):
                new_count += 1
        candidates = select_candidates(conn, args.min_score, args.post_limit, entries)
        if candidates:
            include_intro = env_bool("PAPER_WATCH_BILINGUAL_INTRO", True) and not args.no_summary
            include_abstract = (
                env_bool("PAPER_WATCH_INCLUDE_ABSTRACT", False) or args.include_abstract
            )
            verbose_message = (
                env_bool("PAPER_WATCH_VERBOSE_MESSAGE", False) or args.verbose_message
            )
            messages = build_messages(
                candidates,
                include_intro=include_intro,
                use_rag_score=use_rag_score,
                include_abstract=include_abstract,
                verbose=verbose_message,
            )
            print("\n\n---\n\n".join(messages))
            if not args.dry_run:
                for item, message in zip(candidates, messages):
                    if not post_to_slack(message):
                        break
                    mark_posted(conn, [item])
                    conn.commit()
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
