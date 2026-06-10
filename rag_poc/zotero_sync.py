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
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"
PAPERS_DIR = ROOT / "papers"
ZOTERO_PDF_DIR = PAPERS_DIR / "zotero"

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


def download_unique_pdfs(
    conn: sqlite3.Connection,
    unique_papers: list[dict],
    *,
    dry_run: bool = False,
    force: bool = False,
    verbose: bool = False,
) -> dict:
    report = {
        "checked": 0,
        "downloaded": 0,
        "unchanged": 0,
        "no_pdf": 0,
        "failed": 0,
    }

    for paper in unique_papers:
        paper_key = paper["zotero_key"]
        report["checked"] += 1
        title = paper["title"] or paper_key
        if verbose:
            print(f"PDF {paper_key}: {title}")

        try:
            children = fetch_children(paper_key)
            attachment = select_pdf_attachment(children)
            if not attachment:
                if verbose:
                    print(f"PDF {paper_key}: no PDF attachment")
                report["no_pdf"] += 1
                if not dry_run:
                    upsert_attachment_status(
                        conn,
                        attachment=None,
                        parent_key=paper_key,
                        status="no_pdf",
                    )
                    update_paper_pdf_status(
                        conn,
                        paper_key=paper_key,
                        status="no_pdf",
                    )
                continue

            data = attachment.get("data") or {}
            attachment_key = data.get("key") or attachment.get("key")
            if not attachment_key:
                raise ZoteroError("PDF attachment has no key.")
            remote_md5 = (data.get("md5") or "").lower()
            remote_mtime = attachment_mtime_seconds(data.get("mtime"))
            dest = zotero_pdf_path(paper_key, attachment_key)
            rel_path = dest.relative_to(PAPERS_DIR).as_posix()

            if dry_run:
                print(f"PDF {paper_key}: would download {attachment_filename(attachment)} -> {rel_path}")
                continue

            if should_skip_pdf(dest, remote_md5, force):
                if verbose:
                    print(f"PDF {paper_key}: unchanged {rel_path}")
                report["unchanged"] += 1
                local_md5 = file_md5(dest) if dest.exists() else remote_md5
                upsert_attachment_status(
                    conn,
                    attachment=attachment,
                    parent_key=paper_key,
                    path=rel_path,
                    status="unchanged",
                )
                update_paper_pdf_status(
                    conn,
                    paper_key=paper_key,
                    attachment_key=attachment_key,
                    path=rel_path,
                    md5=local_md5,
                    status="downloaded",
                )
                continue

            local_md5, _headers = download_attachment_file(attachment_key, dest)
            if remote_mtime is not None:
                os.utime(dest, (remote_mtime, remote_mtime))
            print(f"PDF {paper_key}: downloaded {rel_path}")
            report["downloaded"] += 1
            upsert_attachment_status(
                conn,
                attachment=attachment,
                parent_key=paper_key,
                path=rel_path,
                status="downloaded",
            )
            update_paper_pdf_status(
                conn,
                paper_key=paper_key,
                attachment_key=attachment_key,
                path=rel_path,
                md5=local_md5,
                status="downloaded",
            )
        except (OSError, ZoteroError) as exc:
            print(f"PDF {paper_key}: failed: {exc}")
            report["failed"] += 1
            if not dry_run:
                upsert_attachment_status(
                    conn,
                    attachment=None,
                    parent_key=paper_key,
                    status="failed",
                    error=str(exc),
                )
                update_paper_pdf_status(
                    conn,
                    paper_key=paper_key,
                    status="failed",
                )

        time.sleep(0.2)

    return report


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
        "--verbose-pdfs",
        action="store_true",
        help="Print unchanged/no-PDF entries during PDF sync.",
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

    if args.dry_run:
        duplicates = assign_duplicates(papers, {})
        skipped = len(items) - len(papers)
        unique_count = len(papers) - len(duplicates)
        unique_papers = [paper for paper in papers if not paper["is_duplicate"]]
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
            )
            print(f"PDF dry run: {pdf_report}")
        print("Dry run: SQLite was not updated.")
        return

    conn = init_db(INDEX_DB_PATH)
    try:
        duplicates = assign_duplicates(papers, load_primary_by_dedupe_key(conn))
        skipped = len(items) - len(papers)
        unique_count = len(papers) - len(duplicates)
        unique_papers = [paper for paper in papers if not paper["is_duplicate"]]
        print(f"Fetched {len(items)} Zotero top-level items.")
        print(
            f"Paper-like items: {len(papers)} / unique: {unique_count} / "
            f"duplicates: {len(duplicates)} / skipped: {skipped}"
        )
        print_sample(papers)

        for paper in papers:
            upsert_paper(conn, paper)

        if args.download_pdfs:
            pdf_report = download_unique_pdfs(
                conn,
                unique_papers,
                force=args.force_pdf_download,
                verbose=args.verbose_pdfs,
            )
            print(f"PDF sync: {pdf_report}")

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
