#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.nas.yml}"
ZOTERO_ARGS="${ZOTERO_ARGS:---all --download-pdfs}"
INGEST_ARGS="${INGEST_ARGS:---source-prefix zotero/}"

if [ "${REBUILD:-0}" = "1" ]; then
  INGEST_ARGS="--rebuild --source-prefix zotero/"
fi

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

step() {
  printf '\n==> %s\n' "$1"
}

step "Ensure runtime directories"
mkdir -p rag_poc/papers/zotero rag_poc/index logs

step "Check Ollama embedding endpoint"
compose run --rm zotero python -c 'import os; from rag_poc.ollama_client import embed, base_url; model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"); embed("paperbot connectivity test", model, timeout=60); print(f"ok {base_url()} {model}")'

step "Sync Zotero metadata and unique PDFs"
compose run --rm zotero python rag_poc/zotero_sync.py $ZOTERO_ARGS

step "Ingest Zotero PDFs"
compose run --rm ingest python rag_poc/ingest.py $INGEST_ARGS

if [ "${SKIP_RESTART:-0}" != "1" ]; then
  step "Restart PaperBot"
  compose restart paperbot
fi

step "Pipeline report"
compose run --rm -T zotero python - <<'PY'
import sqlite3
from pathlib import Path

db = Path("rag_poc/index/chunks.sqlite3")
print(f"db={db} exists={db.exists()}")
conn = sqlite3.connect(db)
tables = {
    row[0]
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )
}


def count(sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


papers = count("SELECT COUNT(*) FROM papers") if "papers" in tables else 0
unique = count("SELECT COUNT(*) FROM unique_papers") if "unique_papers" in tables else 0
duplicates = (
    count("SELECT COUNT(*) FROM papers WHERE COALESCE(is_duplicate, 0) = 1")
    if "papers" in tables
    else 0
)
downloaded = (
    count("SELECT COUNT(*) FROM papers WHERE pdf_status = 'downloaded'")
    if "papers" in tables
    else 0
)
chunks = count("SELECT COUNT(*) FROM chunks") if "chunks" in tables else 0
indexed_pdfs = (
    count("SELECT COUNT(*) FROM pdf_documents WHERE status = 'indexed' AND chunk_count > 0")
    if "pdf_documents" in tables
    else 0
)

print(f"papers={papers}")
print(f"unique_papers={unique}")
print(f"duplicates={duplicates}")
print(f"zotero_downloaded={downloaded}")
print(f"chunks={chunks}")
print(f"indexed_pdfs={indexed_pdfs}")
conn.close()
PY

step "Done"
