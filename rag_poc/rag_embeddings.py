import sqlite3
from collections.abc import Sequence


class EmbeddingMismatchError(RuntimeError):
    pass


def require_index_model(conn: sqlite3.Connection, model: str) -> None:
    has_metadata = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'index_metadata'"
    ).fetchone()
    row = conn.execute(
        "SELECT value FROM index_metadata WHERE key = 'embedding_model'"
    ).fetchone() if has_metadata else None
    if not row or row[0] != model:
        stored_model = row[0] if row else "unknown"
        raise EmbeddingMismatchError(
            f"RAG index model {stored_model!r} does not match query model {model!r}. "
            "Use the matching embedding model or rebuild the index."
        )


def require_matching_dimensions(query: Sequence[float], indexed: Sequence[float]) -> None:
    if not query or len(query) != len(indexed):
        raise EmbeddingMismatchError(
            f"RAG embedding dimensions differ: query={len(query)}, index={len(indexed)}."
        )
