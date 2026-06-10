import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import fitz

try:
    from .ollama_client import OllamaError, embed
except ImportError:
    from ollama_client import OllamaError, embed


ROOT = Path(__file__).resolve().parent
PAPERS_DIR = ROOT / "papers"
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"
INGEST_REPORT_PATH = INDEX_DIR / "ingest_report.json"

EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
CHUNK_CHARS = int(os.environ.get("PAPERBOT_CHUNK_CHARS", "1800"))
CHUNK_OVERLAP = int(os.environ.get("PAPERBOT_CHUNK_OVERLAP", "250"))

REFERENCES_HEADING_RE = re.compile(
    r"(?im)^\s*(references|references and notes|bibliography)\s*$"
)
REFERENCE_LINE_RE = re.compile(r"(?m)^\s*\[\d+\]")


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_pages(path: Path) -> list[dict]:
    doc = fitz.open(path)
    pages = []
    for idx, page in enumerate(doc, start=1):
        text = normalize_text(page.get_text("text"))
        if text:
            pages.append({"page": idx, "text": text})
    return pages


def is_reference_page(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    reference_lines = len(REFERENCE_LINE_RE.findall(text))
    if reference_lines >= 3:
        return True

    body = "\n".join(lines[2:]) if len(lines) > 2 else "\n".join(lines)
    return bool(REFERENCE_LINE_RE.match(body)) and "doi:" not in text.lower()


def remove_references(pages: list[dict]) -> list[dict]:
    trimmed = []
    for page in pages:
        text = page["text"]
        heading = REFERENCES_HEADING_RE.search(text)
        if heading:
            before = text[: heading.start()].strip()
            if before:
                trimmed.append({"page": page["page"], "text": before})
            break

        if is_reference_page(text):
            break

        trimmed.append(page)

    return trimmed


def chunk_pages(pages: list[dict]) -> list[dict]:
    chunks = []
    current = ""
    start_page = None
    end_page = None

    for page in pages:
        page_num = page["page"]
        text = page["text"]
        if start_page is None:
            start_page = page_num
        end_page = page_num

        if current:
            current += "\n\n"
        current += text

        while len(current) >= CHUNK_CHARS:
            chunk_text = current[:CHUNK_CHARS].strip()
            chunks.append(
                {
                    "text": chunk_text,
                    "page_start": start_page,
                    "page_end": end_page,
                }
            )
            current = current[CHUNK_CHARS - CHUNK_OVERLAP :]
            start_page = page_num

    if current.strip():
        chunks.append(
            {
                "text": current.strip(),
                "page_start": start_page or 1,
                "page_end": end_page or start_page or 1,
            }
        )

    return chunks


def iter_pdfs(source_prefix: str = "") -> list[Path]:
    return sorted(
        path
        for path in PAPERS_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() == ".pdf"
        and source_name(path).startswith(source_prefix)
    )


def source_name(path: Path) -> str:
    return path.relative_to(PAPERS_DIR).as_posix()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def init_index_db(path: Path, rebuild: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_documents (
            source TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            duplicate_of TEXT,
            error TEXT,
            indexed_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_sha256 ON chunks(sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pdf_documents_sha256 ON pdf_documents(sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pdf_documents_status ON pdf_documents(status)")

    if rebuild:
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM pdf_documents")

    return conn


def backfill_pdf_documents(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO pdf_documents (
            source, sha256, file_size, mtime_ns, chunk_count, status,
            duplicate_of, error, indexed_at
        )
        SELECT
            source,
            sha256,
            0,
            0,
            COUNT(*),
            'indexed',
            NULL,
            NULL,
            ?
        FROM chunks
        GROUP BY source, sha256
        """,
        (utc_now(),),
    )


def delete_sources(conn: sqlite3.Connection, sources: list[str]) -> None:
    if not sources:
        return

    placeholders = ",".join("?" for _ in sources)
    conn.execute(f"DELETE FROM chunks WHERE source IN ({placeholders})", sources)
    conn.execute(f"DELETE FROM pdf_documents WHERE source IN ({placeholders})", sources)


def cleanup_removed_sources(
    conn: sqlite3.Connection,
    current_sources: set[str],
    source_prefix: str = "",
) -> list[str]:
    if source_prefix:
        rows = conn.execute(
            "SELECT source FROM pdf_documents WHERE source LIKE ?",
            (f"{source_prefix}%",),
        ).fetchall()
    else:
        rows = conn.execute("SELECT source FROM pdf_documents").fetchall()
    existing_sources = {row[0] for row in rows}
    removed = sorted(existing_sources - current_sources)
    delete_sources(conn, removed)
    return removed


def load_pdf_documents(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT source, sha256, file_size, mtime_ns, chunk_count, status, duplicate_of
        FROM pdf_documents
        """
    ).fetchall()
    return {
        row[0]: {
            "source": row[0],
            "sha256": row[1],
            "file_size": row[2],
            "mtime_ns": row[3],
            "chunk_count": row[4],
            "status": row[5],
            "duplicate_of": row[6],
        }
        for row in rows
    }


def load_primary_source_by_hash(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT sha256, MIN(source)
        FROM pdf_documents
        WHERE status = 'indexed' AND chunk_count > 0
        GROUP BY sha256
        """
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def upsert_pdf_document(
    conn: sqlite3.Connection,
    *,
    source: str,
    sha256: str,
    file_size: int,
    mtime_ns: int,
    chunk_count: int,
    status: str,
    duplicate_of: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO pdf_documents (
            source, sha256, file_size, mtime_ns, chunk_count, status,
            duplicate_of, error, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            sha256 = excluded.sha256,
            file_size = excluded.file_size,
            mtime_ns = excluded.mtime_ns,
            chunk_count = excluded.chunk_count,
            status = excluded.status,
            duplicate_of = excluded.duplicate_of,
            error = excluded.error,
            indexed_at = excluded.indexed_at
        """,
        (
            source,
            sha256,
            file_size,
            mtime_ns,
            chunk_count,
            status,
            duplicate_of,
            error,
            utc_now(),
        ),
    )


def insert_chunk(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO chunks (
            id, source, sha256, page_start, page_end, text, embedding_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["id"],
            record["source"],
            record["sha256"],
            record["page_start"],
            record["page_end"],
            record["text"],
            json.dumps(record["embedding"]),
        ),
    )


def count_current_index(conn: sqlite3.Connection) -> tuple[int, int]:
    indexed_pdfs = conn.execute(
        "SELECT COUNT(*) FROM pdf_documents WHERE status = 'indexed' AND chunk_count > 0"
    ).fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return indexed_pdfs, chunks


def write_ingest_report(report: dict) -> None:
    INGEST_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally index PDFs into the local SQLite RAG database."
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Clear PDF chunks and PDF tracking tables before indexing. Zotero papers are preserved.",
    )
    parser.add_argument(
        "--source-prefix",
        default="",
        help="Only index PDFs whose source path starts with this prefix, e.g. zotero/.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = iter_pdfs(args.source_prefix)
    if not pdfs:
        if args.source_prefix:
            print(f"No PDFs found for prefix {args.source_prefix!r} in: {PAPERS_DIR}")
        else:
            print(f"No PDFs found. Put PDFs in: {PAPERS_DIR}")
        return

    chunks_added = 0
    newly_indexed_sources = []
    unchanged_sources = []
    zero_text_sources = []
    duplicate_sources = []
    removed_sources = []
    seen_hashes: dict[str, str] = {}
    current_sources = {source_name(pdf) for pdf in pdfs}
    conn = init_index_db(INDEX_DB_PATH, rebuild=args.rebuild)
    try:
        backfill_pdf_documents(conn)
        removed_sources = cleanup_removed_sources(conn, current_sources, args.source_prefix)
        existing_docs = load_pdf_documents(conn)
        primary_source_by_hash = load_primary_source_by_hash(conn)

        for pdf in pdfs:
            source = source_name(pdf)
            print(f"Reading {source}")
            pdf_hash = file_sha256(pdf)
            stat = pdf.stat()
            file_size = stat.st_size
            mtime_ns = stat.st_mtime_ns

            existing = existing_docs.get(source)
            if existing and existing["sha256"] == pdf_hash:
                status = existing["status"]
                if status == "indexed" and existing["chunk_count"] > 0:
                    print(f"  unchanged chunks={existing['chunk_count']}")
                    unchanged_sources.append(source)
                    seen_hashes.setdefault(pdf_hash, source)
                    primary_source_by_hash.setdefault(pdf_hash, source)
                    continue
                if status == "zero_text":
                    print("  unchanged zero-text")
                    zero_text_sources.append(source)
                    continue
                if status == "duplicate":
                    duplicate_of = existing["duplicate_of"]
                    if duplicate_of in current_sources:
                        print(f"  unchanged duplicate of {duplicate_of}")
                        duplicate_sources.append(
                            {
                                "source": source,
                                "duplicate_of": duplicate_of,
                                "sha256": pdf_hash,
                            }
                        )
                        continue
                    print("  duplicate primary is gone; reindexing as primary")

            duplicate_of = seen_hashes.get(pdf_hash) or primary_source_by_hash.get(pdf_hash)
            if duplicate_of and duplicate_of != source:
                print(f"  skipping duplicate of {duplicate_of}")
                conn.execute("DELETE FROM chunks WHERE source = ?", (source,))
                upsert_pdf_document(
                    conn,
                    source=source,
                    sha256=pdf_hash,
                    file_size=file_size,
                    mtime_ns=mtime_ns,
                    chunk_count=0,
                    status="duplicate",
                    duplicate_of=duplicate_of,
                )
                duplicate_sources.append(
                    {
                        "source": source,
                        "duplicate_of": duplicate_of,
                        "sha256": pdf_hash,
                    }
                )
                continue

            conn.execute("DELETE FROM chunks WHERE source = ?", (source,))

            pages = remove_references(extract_pages(pdf))
            chunks = chunk_pages(pages)
            print(f"  pages={len(pages)} chunks={len(chunks)}")
            if not chunks:
                upsert_pdf_document(
                    conn,
                    source=source,
                    sha256=pdf_hash,
                    file_size=file_size,
                    mtime_ns=mtime_ns,
                    chunk_count=0,
                    status="zero_text",
                )
                zero_text_sources.append(source)
                continue

            newly_indexed_sources.append(source)
            seen_hashes[pdf_hash] = source
            primary_source_by_hash[pdf_hash] = source

            for i, chunk in enumerate(chunks, start=1):
                try:
                    vector = embed(chunk["text"], EMBED_MODEL)
                except OllamaError as exc:
                    raise SystemExit(
                        f"Embedding failed for {pdf.name}: {exc}\n"
                        f"Check that the RTX PC has the embedding model: "
                        f"ollama pull {EMBED_MODEL}"
                    ) from exc

                record = {
                    "id": f"{pdf_hash[:12]}-{i:04d}",
                    "source": source,
                    "sha256": pdf_hash,
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "text": chunk["text"],
                    "embedding": vector,
                }
                insert_chunk(conn, record)
                chunks_added += 1

            upsert_pdf_document(
                conn,
                source=source,
                sha256=pdf_hash,
                file_size=file_size,
                mtime_ns=mtime_ns,
                chunk_count=len(chunks),
                status="indexed",
            )
        conn.commit()
        total_indexed_pdfs, total_chunks = count_current_index(conn)
    finally:
        conn.close()

    report = {
        "found_pdfs": len(pdfs),
        "total_indexed_pdfs": total_indexed_pdfs,
        "newly_indexed_pdfs": len(newly_indexed_sources),
        "unchanged_pdfs": len(unchanged_sources),
        "zero_text_pdfs": len(zero_text_sources),
        "duplicate_pdfs": len(duplicate_sources),
        "removed_pdfs": len(removed_sources),
        "chunks_added": chunks_added,
        "total_chunks": total_chunks,
        "newly_indexed_sources": newly_indexed_sources,
        "unchanged_sources": unchanged_sources,
        "zero_text_sources": zero_text_sources,
        "duplicate_sources": duplicate_sources,
        "removed_sources": removed_sources,
    }
    write_ingest_report(report)

    print(
        "Done. "
        f"Found {len(pdfs)} PDFs / active indexed {total_indexed_pdfs} PDFs / "
        f"newly indexed {len(newly_indexed_sources)} PDFs / "
        f"unchanged {len(unchanged_sources)} PDFs / "
        f"zero-text {len(zero_text_sources)} PDFs / "
        f"duplicates {len(duplicate_sources)} PDFs / "
        f"removed {len(removed_sources)} PDFs / "
        f"chunks added {chunks_added} / total chunks {total_chunks}."
    )
    print(f"Index: {INDEX_DB_PATH}")
    print(f"Report: {INGEST_REPORT_PATH}")
    if zero_text_sources:
        print("Zero-text PDFs:")
        for source in zero_text_sources:
            print(f"  - {source}")
    if duplicate_sources:
        print("Duplicate PDFs:")
        for item in duplicate_sources:
            print(f"  - {item['source']} -> {item['duplicate_of']}")


if __name__ == "__main__":
    main()
