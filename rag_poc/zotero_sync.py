import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"
ZOTERO_SYNC_REPORT_PATH = INDEX_DIR / "zotero_sync_report.json"
PAPERS_DIR = ROOT / "papers"
ZOTERO_PDF_DIR = PAPERS_DIR / "zotero"

DEFAULT_API_BASE_URL = "https://api.zotero.org"
DEFAULT_LIMIT = 25
DEFAULT_PDF_WORKERS = 1
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


def default_pdf_workers() -> int:
    value = env("ZOTERO_PDF_WORKERS", str(DEFAULT_PDF_WORKERS))
    try:
        return max(1, int(value))
    except ValueError as exc:
        raise ZoteroError("ZOTERO_PDF_WORKERS must be an integer.") from exc


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


def zotero_request(
    path: str,
    params: dict | None = None,
    *,
    accept: str = "application/json",
    timeout: int = 60,
) -> tuple[bytes, dict]:
    query = urllib.parse.urlencode(params or {})
    url = f"{api_base_url()}{path}"
    if query:
        url = f"{url}?{query}"

    headers = {
        "Accept": accept,
        "User-Agent": "kohdalab-paperbot/0.1",
        "Zotero-API-Version": "3",
    }
    api_key = env("ZOTERO_API_KEY")
    if api_key:
        headers["Zotero-API-Key"] = api_key

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read(), dict(res.headers.items())
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


def zotero_get(path: str, params: dict | None = None) -> tuple[list, dict]:
    body, headers = zotero_request(path, params, accept="application/json")
    return json.loads(body.decode("utf-8")), headers


def zotero_get_file(path: str) -> tuple[bytes, dict]:
    return zotero_request(path, accept="application/octet-stream", timeout=180)


def total_results(headers: dict) -> int | None:
    value = headers.get("Total-Results") or headers.get("total-results")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def library_version(headers: dict) -> int:
    value = headers.get("Last-Modified-Version") or headers.get("last-modified-version")
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def fetch_top_items(limit: int | None, since: int | None = None) -> tuple[list[dict], int]:
    items = []
    start = 0
    total = None
    latest_version = 0

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
                **({"since": since} if since else {}),
            },
        )
        latest_version = max(latest_version, library_version(headers))
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

    return items, latest_version


def fetch_deleted_items(since: int) -> tuple[set[str], int]:
    body, headers = zotero_request(
        f"{library_base_path()}/deleted",
        {"since": since},
        accept="application/json",
    )
    data = json.loads(body.decode("utf-8"))
    deleted = set(data.get("items") or []) if isinstance(data, dict) else set()
    return deleted, library_version(headers)


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


def normalize_doi(doi: str) -> str:
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return re.sub(r"\s+", "", doi)


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).casefold()
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def make_dedupe_key(title: str, year: str, doi: str) -> str:
    doi_norm = normalize_doi(doi)
    if doi_norm:
        return f"doi:{doi_norm}"

    title_norm = normalize_title(title)
    if title_norm:
        return f"title:{title_norm}|year:{year}"

    return ""


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attachment_mtime_seconds(mtime: str | int | None) -> float | None:
    if not mtime:
        return None
    try:
        return int(mtime) / 1000
    except (TypeError, ValueError):
        return None


def is_pdf_attachment(item: dict) -> bool:
    data = item.get("data") or {}
    if data.get("itemType") != "attachment":
        return False

    content_type = (data.get("contentType") or "").lower()
    filename = (data.get("filename") or "").lower()
    title = (data.get("title") or "").lower()
    return (
        content_type == "application/pdf"
        or filename.endswith(".pdf")
        or title.endswith(".pdf")
    )


def attachment_filename(attachment: dict) -> str:
    data = attachment.get("data") or {}
    filename = data.get("filename") or data.get("title") or attachment.get("key") or "paper.pdf"
    filename = filename.strip()
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return filename


def zotero_pdf_path(paper_key: str, attachment_key: str) -> Path:
    return ZOTERO_PDF_DIR / f"{paper_key}_{attachment_key}.pdf"


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
    title = data.get("title", "")
    year = item_year(data)
    doi = data.get("DOI", "")

    return {
        "zotero_key": data.get("key") or item.get("key") or "",
        "version": int(data.get("version") or item.get("version") or 0),
        "item_type": item_type,
        "title": title,
        "authors": authors,
        "year": year,
        "journal": item_journal(data),
        "doi": doi,
        "doi_norm": normalize_doi(doi),
        "title_norm": normalize_title(title),
        "dedupe_key": make_dedupe_key(title, year, doi),
        "is_duplicate": 0,
        "duplicate_of": "",
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
            doi_norm TEXT,
            title_norm TEXT,
            dedupe_key TEXT,
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            duplicate_of TEXT,
            pdf_attachment_key TEXT,
            pdf_path TEXT,
            pdf_md5 TEXT,
            pdf_status TEXT,
            pdf_downloaded_at TEXT,
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
    ensure_papers_columns(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS zotero_attachments (
            attachment_key TEXT PRIMARY KEY,
            parent_key TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            link_mode TEXT,
            content_type TEXT,
            filename TEXT,
            md5 TEXT,
            mtime TEXT,
            path TEXT,
            status TEXT NOT NULL,
            error TEXT,
            downloaded_at TEXT,
            zotero_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS zotero_sync_state (
            library_path TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi_norm ON papers(doi_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_dedupe_key ON papers(dedupe_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_duplicate ON papers(is_duplicate)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_modified ON papers(date_modified)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_pdf_status ON papers(pdf_status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_zotero_attachments_parent ON zotero_attachments(parent_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_zotero_attachments_status ON zotero_attachments(status)"
    )
    conn.execute(
        """
        CREATE VIEW IF NOT EXISTS unique_papers AS
        SELECT * FROM papers
        WHERE COALESCE(is_duplicate, 0) = 0
        """
    )
    return conn


def get_sync_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM zotero_sync_state WHERE library_path = ?",
        (library_base_path(),),
    ).fetchone()
    return int(row[0]) if row else 0


def set_sync_version(conn: sqlite3.Connection, version: int) -> None:
    if version <= 0:
        return
    conn.execute(
        """
        INSERT INTO zotero_sync_state (library_path, version, synced_at)
        VALUES (?, ?, ?)
        ON CONFLICT(library_path) DO UPDATE SET
            version = MAX(zotero_sync_state.version, excluded.version),
            synced_at = excluded.synced_at
        """,
        (
            library_base_path(),
            version,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ),
    )


def delete_papers(conn: sqlite3.Connection, zotero_keys: set[str]) -> int:
    if not zotero_keys:
        return 0
    conn.executemany(
        "DELETE FROM papers WHERE zotero_key = ?",
        [(key,) for key in zotero_keys],
    )
    conn.executemany(
        "DELETE FROM zotero_attachments WHERE parent_key = ?",
        [(key,) for key in zotero_keys],
    )
    return len(zotero_keys)


def ensure_papers_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(papers)").fetchall()
    }
    columns = {
        "doi_norm": "TEXT",
        "title_norm": "TEXT",
        "dedupe_key": "TEXT",
        "is_duplicate": "INTEGER NOT NULL DEFAULT 0",
        "duplicate_of": "TEXT",
        "pdf_attachment_key": "TEXT",
        "pdf_path": "TEXT",
        "pdf_md5": "TEXT",
        "pdf_status": "TEXT",
        "pdf_downloaded_at": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {column_type}")


def load_primary_by_dedupe_key(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT dedupe_key, zotero_key
        FROM papers
        WHERE dedupe_key IS NOT NULL
          AND dedupe_key != ''
          AND COALESCE(is_duplicate, 0) = 0
        ORDER BY date_added, zotero_key
        """
    ).fetchall()

    primary_by_key = {}
    for dedupe_key, zotero_key in rows:
        primary_by_key.setdefault(dedupe_key, zotero_key)
    return primary_by_key


def attach_existing_pdf_state(conn: sqlite3.Connection, papers: list[dict]) -> None:
    try:
        rows = conn.execute(
            """
            SELECT zotero_key, pdf_status, pdf_path, pdf_attachment_key, pdf_md5
            FROM papers
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return

    state_by_key = {
        row[0]: {
            "pdf_status": row[1] or "",
            "pdf_path": row[2] or "",
            "pdf_attachment_key": row[3] or "",
            "pdf_md5": row[4] or "",
        }
        for row in rows
    }
    for paper in papers:
        paper.update(state_by_key.get(paper["zotero_key"], {}))


def load_unique_pdf_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT zotero_key, title, pdf_status, pdf_path, pdf_attachment_key, pdf_md5
        FROM unique_papers
        ORDER BY date_modified DESC, zotero_key
        """
    ).fetchall()
    return [
        {
            "zotero_key": row[0],
            "title": row[1] or row[0],
            "pdf_status": row[2] or "",
            "pdf_path": row[3] or "",
            "pdf_attachment_key": row[4] or "",
            "pdf_md5": row[5] or "",
        }
        for row in rows
    ]


def assign_duplicates(
    papers: list[dict], primary_by_key: dict[str, str]
) -> list[dict]:
    duplicates = []
    for paper in papers:
        paper["is_duplicate"] = 0
        paper["duplicate_of"] = ""

        dedupe_key = paper["dedupe_key"]
        if not dedupe_key:
            continue

        primary = primary_by_key.get(dedupe_key)
        if primary and primary != paper["zotero_key"]:
            paper["is_duplicate"] = 1
            paper["duplicate_of"] = primary
            duplicates.append(paper)
            continue

        primary_by_key[dedupe_key] = paper["zotero_key"]

    return duplicates


def upsert_attachment_status(
    conn: sqlite3.Connection,
    *,
    attachment: dict | None,
    parent_key: str,
    path: str = "",
    status: str,
    error: str = "",
) -> None:
    data = (attachment or {}).get("data") or {}
    attachment_key = data.get("key") or (attachment or {}).get("key") or f"{parent_key}:none"
    conn.execute(
        """
        INSERT INTO zotero_attachments (
            attachment_key, parent_key, version, link_mode, content_type, filename,
            md5, mtime, path, status, error, downloaded_at, zotero_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(attachment_key) DO UPDATE SET
            parent_key = excluded.parent_key,
            version = excluded.version,
            link_mode = excluded.link_mode,
            content_type = excluded.content_type,
            filename = excluded.filename,
            md5 = excluded.md5,
            mtime = excluded.mtime,
            path = excluded.path,
            status = excluded.status,
            error = excluded.error,
            downloaded_at = excluded.downloaded_at,
            zotero_json = excluded.zotero_json
        """,
        (
            attachment_key,
            parent_key,
            int(data.get("version") or (attachment or {}).get("version") or 0),
            data.get("linkMode", ""),
            data.get("contentType", ""),
            attachment_filename(attachment or {}) if attachment else "",
            data.get("md5", ""),
            str(data.get("mtime", "")),
            path,
            status,
            error,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            json.dumps(data, ensure_ascii=False),
        ),
    )


def update_paper_pdf_status(
    conn: sqlite3.Connection,
    *,
    paper_key: str,
    attachment_key: str = "",
    path: str = "",
    md5: str = "",
    status: str,
) -> None:
    conn.execute(
        """
        UPDATE papers
        SET
            pdf_attachment_key = ?,
            pdf_path = ?,
            pdf_md5 = ?,
            pdf_status = ?,
            pdf_downloaded_at = ?
        WHERE zotero_key = ?
        """,
        (
            attachment_key,
            path,
            md5,
            status,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            paper_key,
        ),
    )


def upsert_paper(conn: sqlite3.Connection, paper: dict) -> None:
    synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """
        INSERT INTO papers (
            zotero_key, version, item_type, title, authors_json, year, journal,
            doi, doi_norm, title_norm, dedupe_key, is_duplicate, duplicate_of,
            url, abstract, tags_json, date_added, date_modified, synced_at, zotero_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(zotero_key) DO UPDATE SET
            version = excluded.version,
            item_type = excluded.item_type,
            title = excluded.title,
            authors_json = excluded.authors_json,
            year = excluded.year,
            journal = excluded.journal,
            doi = excluded.doi,
            doi_norm = excluded.doi_norm,
            title_norm = excluded.title_norm,
            dedupe_key = excluded.dedupe_key,
            is_duplicate = excluded.is_duplicate,
            duplicate_of = excluded.duplicate_of,
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
            paper["doi_norm"],
            paper["title_norm"],
            paper["dedupe_key"],
            paper["is_duplicate"],
            paper["duplicate_of"],
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


def write_sync_report(report: dict) -> None:
    ZOTERO_SYNC_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_children(item_key: str) -> list[dict]:
    data, _ = zotero_get(
        f"{library_base_path()}/items/{item_key}/children",
        {
            "format": "json",
            "include": "data",
        },
    )
    return data


def select_pdf_attachment(children: list[dict]) -> dict | None:
    pdfs = [item for item in children if is_pdf_attachment(item)]
    if not pdfs:
        return None

    def score(item: dict) -> tuple[int, int, str]:
        data = item.get("data") or {}
        link_mode = data.get("linkMode", "")
        content_type = data.get("contentType", "")
        has_md5 = 1 if data.get("md5") else 0
        imported = 1 if link_mode in {"imported_file", "imported_url"} else 0
        exact_pdf = 1 if content_type == "application/pdf" else 0
        return (imported + exact_pdf, has_md5, data.get("dateModified", ""))

    return sorted(pdfs, key=score, reverse=True)[0]


def should_skip_pdf(path: Path, remote_md5: str, force: bool) -> bool:
    if force or not path.exists():
        return False
    if not remote_md5:
        return True
    return file_md5(path) == remote_md5.lower()


def download_attachment_file(attachment_key: str, dest: Path) -> tuple[str, dict]:
    body, headers = zotero_get_file(f"{library_base_path()}/items/{attachment_key}/file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return file_md5(dest), headers


def is_known_pdf_state(
    paper: dict,
    *,
    dry_run: bool,
    force: bool,
    refresh_metadata: bool,
) -> bool:
    pdf_status = paper.get("pdf_status") or ""
    pdf_path = paper.get("pdf_path") or ""
    if dry_run or force or refresh_metadata:
        return False
    if pdf_status == "downloaded" and pdf_path and (PAPERS_DIR / pdf_path).exists():
        return True
    return pdf_status == "no_pdf"


def sync_one_pdf(
    paper: dict,
    *,
    dry_run: bool,
    force: bool,
) -> dict:
    paper_key = paper["zotero_key"]
    try:
        children = fetch_children(paper_key)
        attachment = select_pdf_attachment(children)
        if not attachment:
            return {
                "status": "no_pdf",
                "paper_key": paper_key,
                "attachment": None,
                "path": "",
                "md5": "",
                "error": "",
            }

        data = attachment.get("data") or {}
        attachment_key = data.get("key") or attachment.get("key")
        if not attachment_key:
            raise ZoteroError("PDF attachment has no key.")

        remote_md5 = (data.get("md5") or "").lower()
        remote_mtime = attachment_mtime_seconds(data.get("mtime"))
        dest = zotero_pdf_path(paper_key, attachment_key)
        rel_path = dest.relative_to(PAPERS_DIR).as_posix()

        if dry_run:
            return {
                "status": "dry_run",
                "paper_key": paper_key,
                "attachment": attachment,
                "path": rel_path,
                "md5": remote_md5,
                "error": "",
            }

        if should_skip_pdf(dest, remote_md5, force):
            local_md5 = file_md5(dest) if dest.exists() else remote_md5
            return {
                "status": "unchanged",
                "paper_key": paper_key,
                "attachment": attachment,
                "path": rel_path,
                "md5": local_md5,
                "error": "",
            }

        local_md5, _headers = download_attachment_file(attachment_key, dest)
        if remote_mtime is not None:
            os.utime(dest, (remote_mtime, remote_mtime))
        return {
            "status": "downloaded",
            "paper_key": paper_key,
            "attachment": attachment,
            "path": rel_path,
            "md5": local_md5,
            "error": "",
        }
    except (OSError, ZoteroError) as exc:
        return {
            "status": "failed",
            "paper_key": paper_key,
            "attachment": None,
            "path": "",
            "md5": "",
            "error": str(exc),
        }


def apply_pdf_result(conn: sqlite3.Connection, result: dict, *, dry_run: bool) -> None:
    if dry_run:
        return

    status = result["status"]
    paper_key = result["paper_key"]
    attachment = result.get("attachment")

    if status == "no_pdf":
        upsert_attachment_status(
            conn,
            attachment=None,
            parent_key=paper_key,
            status="no_pdf",
        )
        update_paper_pdf_status(conn, paper_key=paper_key, status="no_pdf")
        return

    if status in {"unchanged", "downloaded"}:
        data = (attachment or {}).get("data") or {}
        attachment_key = data.get("key") or (attachment or {}).get("key") or ""
        upsert_attachment_status(
            conn,
            attachment=attachment,
            parent_key=paper_key,
            path=result["path"],
            status=status,
        )
        update_paper_pdf_status(
            conn,
            paper_key=paper_key,
            attachment_key=attachment_key,
            path=result["path"],
            md5=result["md5"],
            status="downloaded",
        )
        return

    if status == "failed":
        upsert_attachment_status(
            conn,
            attachment=None,
            parent_key=paper_key,
            status="failed",
            error=result["error"],
        )
        update_paper_pdf_status(conn, paper_key=paper_key, status="failed")


def download_unique_pdfs(
    conn: sqlite3.Connection,
    unique_papers: list[dict],
    *,
    dry_run: bool = False,
    force: bool = False,
    verbose: bool = False,
    refresh_metadata: bool = False,
    workers: int = DEFAULT_PDF_WORKERS,
) -> dict:
    report = {
        "checked": 0,
        "skipped_known": 0,
        "downloaded": 0,
        "unchanged": 0,
        "no_pdf": 0,
        "failed": 0,
    }

    pending = []
    for paper in unique_papers:
        paper_key = paper["zotero_key"]
        title = paper["title"] or paper_key
        if is_known_pdf_state(
            paper,
            dry_run=dry_run,
            force=force,
            refresh_metadata=refresh_metadata,
        ):
            report["skipped_known"] += 1
            if verbose:
                print(f"PDF {paper_key}: skipped known {paper.get('pdf_status') or ''}")
            continue

        if verbose:
            print(f"PDF {paper_key}: {title}")
        pending.append(paper)

    report["checked"] = len(pending)
    if not pending:
        return report

    worker_count = max(1, min(workers, len(pending)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(sync_one_pdf, paper, dry_run=dry_run, force=force)
            for paper in pending
        ]
        for future in as_completed(futures):
            result = future.result()
            status = result["status"]
            paper_key = result["paper_key"]
            if status in report:
                report[status] += 1
            apply_pdf_result(conn, result, dry_run=dry_run)

            if status == "dry_run":
                attachment = result.get("attachment") or {}
                print(
                    f"PDF {paper_key}: would download "
                    f"{attachment_filename(attachment)} -> {result['path']}"
                )
            elif status == "downloaded":
                print(f"PDF {paper_key}: downloaded {result['path']}")
            elif status == "failed":
                print(f"PDF {paper_key}: failed: {result['error']}")
            elif verbose and status == "unchanged":
                print(f"PDF {paper_key}: unchanged {result['path']}")
            elif verbose and status == "no_pdf":
                print(f"PDF {paper_key}: no PDF attachment")

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Zotero Group/User Library metadata into the local SQLite index."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Fetch only the most recent N top-level Zotero items. Manual check only.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Force a full metadata refresh instead of using Zotero incremental sync.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print a sample without writing to SQLite.",
    )
    parser.add_argument(
        "--download-pdfs",
        action="store_true",
        help="Download PDF child attachments for unique paper items only.",
    )
    parser.add_argument(
        "--force-pdf-download",
        action="store_true",
        help="Re-download PDFs even when the local copy appears unchanged.",
    )
    parser.add_argument(
        "--pdf-workers",
        type=int,
        default=default_pdf_workers(),
        help="Parallel Zotero PDF checks/downloads. Default: ZOTERO_PDF_WORKERS or 1.",
    )
    parser.add_argument(
        "--verbose-pdfs",
        action="store_true",
        help="Print unchanged/no-PDF entries during PDF sync.",
    )
    parser.add_argument(
        "--refresh-pdf-metadata",
        action="store_true",
        help="Check Zotero child attachments even for papers already marked downloaded/no_pdf.",
    )
    return parser.parse_args()


def main() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    conn: sqlite3.Connection | None = None
    try:
        args = parse_args()

        if args.dry_run:
            dry_limit = None if args.all else (args.limit or DEFAULT_LIMIT)
            items, latest_version = fetch_top_items(dry_limit)
            papers = [paper for item in items if (paper := normalize_item(item))]
            duplicates = assign_duplicates(papers, {})
            skipped = len(items) - len(papers)
            unique_count = len(papers) - len(duplicates)
            unique_papers = [paper for paper in papers if not paper["is_duplicate"]]
            print(f"Dry-run mode. Latest Zotero library version: {latest_version or 'unknown'}")
            print(f"Fetched {len(items)} Zotero top-level items.")
            print(
                f"Paper-like items: {len(papers)} / unique: {unique_count} / "
                f"duplicates: {len(duplicates)} / skipped: {skipped}"
            )
            print_sample(papers)
            if args.download_pdfs:
                pdf_report = download_unique_pdfs(
                    sqlite3.connect(":memory:"),
                    unique_papers,
                    dry_run=True,
                    force=args.force_pdf_download,
                    verbose=args.verbose_pdfs,
                    refresh_metadata=True,
                    workers=args.pdf_workers,
                )
                print(f"PDF dry run: {pdf_report}")
            print("Dry run: SQLite was not updated.")
            return

        conn = init_db(INDEX_DB_PATH)
        previous_version = get_sync_version(conn)
        incremental = bool(previous_version and not args.all and args.limit is None)
        since = previous_version if incremental else None
        limit = args.limit
        items, latest_version = fetch_top_items(limit, since=since)
        papers = [paper for item in items if (paper := normalize_item(item))]
        latest_version = max(latest_version, *(paper["version"] for paper in papers), 0)
    except ZoteroError as exc:
        if conn is not None:
            conn.close()
        raise SystemExit(f"Zotero sync failed: {exc}") from exc

    try:
        deleted_keys: set[str] = set()
        if incremental:
            deleted_keys, deleted_version = fetch_deleted_items(previous_version)
            latest_version = max(latest_version, deleted_version)

        duplicates = assign_duplicates(papers, load_primary_by_dedupe_key(conn))
        skipped = len(items) - len(papers)
        unique_count = len(papers) - len(duplicates)
        unique_papers = [paper for paper in papers if not paper["is_duplicate"]]
        sync_mode = "incremental" if incremental else "full" if args.all else "limited" if args.limit else "initial-full"
        print(
            f"Zotero sync mode: {sync_mode} "
            f"(previous_version={previous_version or 'none'}, latest_version={latest_version or 'unknown'})"
        )
        print(f"Fetched {len(items)} Zotero top-level items.")
        print(
            f"Paper-like items: {len(papers)} / unique: {unique_count} / "
            f"duplicates: {len(duplicates)} / skipped: {skipped}"
        )
        print_sample(papers)

        for paper in papers:
            upsert_paper(conn, paper)

        deleted_count = delete_papers(conn, deleted_keys)
        if deleted_count:
            print(f"Deleted Zotero items removed from DB: {deleted_count}")

        if args.download_pdfs:
            if args.refresh_pdf_metadata or args.force_pdf_download:
                pdf_candidates = load_unique_pdf_candidates(conn)
            else:
                pdf_candidates = unique_papers
            attach_existing_pdf_state(conn, pdf_candidates)
            pdf_report = download_unique_pdfs(
                conn,
                pdf_candidates,
                force=args.force_pdf_download,
                verbose=args.verbose_pdfs,
                refresh_metadata=args.refresh_pdf_metadata,
                workers=args.pdf_workers,
            )
            print(f"PDF sync: {pdf_report}")
        else:
            pdf_report = {}

        if args.limit is None:
            set_sync_version(conn, latest_version)
        write_sync_report(
            {
                "sync_mode": sync_mode,
                "previous_version": previous_version,
                "latest_version": latest_version,
                "fetched_items": len(items),
                "paper_like_items": len(papers),
                "unique_papers": unique_count,
                "duplicates": len(duplicates),
                "skipped_items": skipped,
                "deleted_items": deleted_count,
                "pdf_report": pdf_report,
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        conn.commit()
    finally:
        if conn is not None:
            conn.close()

    print(f"Saved {len(papers)} papers to SQLite.")
    print(f"DB: {INDEX_DB_PATH}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
