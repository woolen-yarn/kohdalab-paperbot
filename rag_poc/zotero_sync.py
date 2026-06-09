import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"

DEFAULT_API_BASE_URL = "https://api.zotero.org"
DEFAULT_LIMIT = 25
REQUEST_BATCH_SIZE = 100
SKIPPED_ITEM_TYPES = {"attachment", "note"}


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


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def default_sync_limit() -> int:
    value = env("ZOTERO_SYNC_LIMIT", str(DEFAULT_LIMIT))
    try:
        return int(value)
    except ValueError as exc:
        raise ZoteroError("ZOTERO_SYNC_LIMIT must be an integer.") from exc


class ZoteroError(RuntimeError):
    pass


def library_base_path() -> str:
    library_type = env("ZOTERO_LIBRARY_TYPE", "group").lower()
    library_id = env("ZOTERO_LIBRARY_ID")
    if not library_id:
        raise ZoteroError(
            "ZOTERO_LIBRARY_ID is not set. For a Group Library, set the numeric "
            "group ID in .env."
        )

    if library_type == "group":
        return f"/groups/{library_id}"
    if library_type == "user":
        return f"/users/{library_id}"

    raise ZoteroError("ZOTERO_LIBRARY_TYPE must be either 'group' or 'user'.")


def api_base_url() -> str:
    return env("ZOTERO_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def zotero_get(path: str, params: dict | None = None) -> tuple[list, dict]:
    query = urllib.parse.urlencode(params or {})
    url = f"{api_base_url()}{path}"
    if query:
        url = f"{url}?{query}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "kohdalab-paperbot/0.1",
    }
    api_key = env("ZOTERO_API_KEY")
    if api_key:
        headers["Zotero-API-Key"] = api_key

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            body = res.read().decode("utf-8")
            return json.loads(body), dict(res.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {401, 403}:
            raise ZoteroError(
                f"Zotero API denied access ({exc.code}). Check ZOTERO_API_KEY "
                "and Group Library permissions."
            ) from exc
        raise ZoteroError(f"Zotero API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ZoteroError(f"Zotero API connection failed: {exc}") from exc


def total_results(headers: dict) -> int | None:
    value = headers.get("Total-Results") or headers.get("total-results")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def fetch_top_items(limit: int | None) -> list[dict]:
    items = []
    start = 0
    total = None

    while True:
        batch_limit = REQUEST_BATCH_SIZE
        if limit is not None:
            remaining = limit - len(items)
            if remaining <= 0:
                break
            batch_limit = min(batch_limit, remaining)

        data, headers = zotero_get(
            f"{library_base_path()}/items/top",
            {
                "format": "json",
                "include": "data",
                "sort": "dateModified",
                "direction": "desc",
                "start": start,
                "limit": batch_limit,
            },
        )
        if total is None:
            total = total_results(headers)

        if not data:
            break

        items.extend(data)
        start += len(data)

        if total is not None and start >= total:
            break
        if len(data) < batch_limit:
            break

        time.sleep(0.2)

    return items


def creator_name(creator: dict) -> str:
    if creator.get("name"):
        return creator["name"].strip()
    parts = [creator.get("firstName", "").strip(), creator.get("lastName", "").strip()]
    return " ".join(part for part in parts if part)


def item_year(data: dict) -> str:
    date = data.get("date", "") or ""
    match = re.search(r"(18|19|20)\d{2}", date)
    return match.group(0) if match else ""


def item_journal(data: dict) -> str:
    for key in (
        "publicationTitle",
        "journalAbbreviation",
        "proceedingsTitle",
        "conferenceName",
        "publisher",
    ):
        if data.get(key):
            return data[key]
    return ""


def normalize_item(item: dict) -> dict | None:
    data = item.get("data") or {}
    item_type = data.get("itemType", "")
    if item_type in SKIPPED_ITEM_TYPES:
        return None

    creators = data.get("creators") or []
    authors = [
        name
        for creator in creators
        if creator.get("creatorType") == "author" and (name := creator_name(creator))
    ]
    tags = [tag.get("tag", "") for tag in data.get("tags") or [] if tag.get("tag")]

    return {
        "zotero_key": data.get("key") or item.get("key") or "",
        "version": int(data.get("version") or item.get("version") or 0),
        "item_type": item_type,
        "title": data.get("title", ""),
        "authors": authors,
        "year": item_year(data),
        "journal": item_journal(data),
        "doi": data.get("DOI", ""),
        "url": data.get("url", ""),
        "abstract": data.get("abstractNote", ""),
        "tags": tags,
        "date_added": data.get("dateAdded", ""),
        "date_modified": data.get("dateModified", ""),
        "zotero_json": data,
    }


def init_db(path: Path) -> sqlite3.Connection:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            zotero_key TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            year TEXT,
            journal TEXT,
            doi TEXT,
            url TEXT,
            abstract TEXT,
            tags_json TEXT NOT NULL,
            date_added TEXT,
            date_modified TEXT,
            synced_at TEXT NOT NULL,
            zotero_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_modified ON papers(date_modified)")
    return conn


def upsert_paper(conn: sqlite3.Connection, paper: dict) -> None:
    synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """
        INSERT INTO papers (
            zotero_key, version, item_type, title, authors_json, year, journal,
            doi, url, abstract, tags_json, date_added, date_modified, synced_at,
            zotero_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(zotero_key) DO UPDATE SET
            version = excluded.version,
            item_type = excluded.item_type,
            title = excluded.title,
            authors_json = excluded.authors_json,
            year = excluded.year,
            journal = excluded.journal,
            doi = excluded.doi,
            url = excluded.url,
            abstract = excluded.abstract,
            tags_json = excluded.tags_json,
            date_added = excluded.date_added,
            date_modified = excluded.date_modified,
            synced_at = excluded.synced_at,
            zotero_json = excluded.zotero_json
        """,
        (
            paper["zotero_key"],
            paper["version"],
            paper["item_type"],
            paper["title"],
            json.dumps(paper["authors"], ensure_ascii=False),
            paper["year"],
            paper["journal"],
            paper["doi"],
            paper["url"],
            paper["abstract"],
            json.dumps(paper["tags"], ensure_ascii=False),
            paper["date_added"],
            paper["date_modified"],
            synced_at,
            json.dumps(paper["zotero_json"], ensure_ascii=False),
        ),
    )


def print_sample(papers: list[dict]) -> None:
    if not papers:
        return

    print("Recent papers:")
    for paper in papers[:5]:
        authors = ", ".join(paper["authors"][:2])
        if len(paper["authors"]) > 2:
            authors += ", et al."
        year = paper["year"] or "n.d."
        title = paper["title"] or "(untitled)"
        print(f"  - {year} {title}")
        if authors:
            print(f"    {authors}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Zotero Group/User Library metadata into the local SQLite index."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=default_sync_limit(),
        help="Maximum top-level Zotero items to fetch. Default: ZOTERO_SYNC_LIMIT or 25.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch every top-level Zotero item instead of stopping at --limit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print a sample without writing to SQLite.",
    )
    return parser.parse_args()


def main() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    try:
        args = parse_args()
        limit = None if args.all else args.limit
        items = fetch_top_items(limit)
        papers = [paper for item in items if (paper := normalize_item(item))]
    except ZoteroError as exc:
        raise SystemExit(f"Zotero sync failed: {exc}") from exc

    skipped = len(items) - len(papers)
    print(f"Fetched {len(items)} Zotero top-level items.")
    print(f"Paper-like items: {len(papers)} / skipped: {skipped}")
    print_sample(papers)

    if args.dry_run:
        print("Dry run: SQLite was not updated.")
        return

    conn = init_db(INDEX_DB_PATH)
    try:
        for paper in papers:
            upsert_paper(conn, paper)
        conn.commit()
    finally:
        conn.close()

    print(f"Saved {len(papers)} papers to SQLite.")
    print(f"DB: {INDEX_DB_PATH}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
