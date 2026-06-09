import hashlib
import json
import os
import re
import sqlite3
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


def iter_pdfs() -> list[Path]:
    return sorted(
        path
        for path in PAPERS_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def source_name(path: Path) -> str:
    return path.relative_to(PAPERS_DIR).as_posix()


def init_index_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE chunks (
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
    conn.execute("CREATE INDEX idx_chunks_source ON chunks(source)")
    conn.execute("CREATE INDEX idx_chunks_sha256 ON chunks(sha256)")
    return conn


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


def main() -> None:
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = iter_pdfs()
    if not pdfs:
        print(f"No PDFs found. Put PDFs in: {PAPERS_DIR}")
        return

    total_chunks = 0
    seen_hashes: dict[str, str] = {}
    conn = init_index_db(INDEX_DB_PATH)
    try:
        for pdf in pdfs:
            source = source_name(pdf)
            print(f"Reading {source}")
            pdf_hash = file_sha256(pdf)
            if pdf_hash in seen_hashes:
                print(f"  skipping duplicate of {seen_hashes[pdf_hash]}")
                continue
            seen_hashes[pdf_hash] = source

            pages = remove_references(extract_pages(pdf))
            chunks = chunk_pages(pages)
            print(f"  pages={len(pages)} chunks={len(chunks)}")

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
                total_chunks += 1
        conn.commit()
    finally:
        conn.close()

    print(f"Done. Indexed {len(pdfs)} PDFs / {total_chunks} chunks.")
    print(f"Index: {INDEX_DB_PATH}")


if __name__ == "__main__":
    main()
