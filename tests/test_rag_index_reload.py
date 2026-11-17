import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag_poc import ask
from rag_poc.rag_embeddings import EmbeddingMismatchError


def create_index(path: Path, text: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE chunks (
                id TEXT, source TEXT, sha256 TEXT, page_start INTEGER,
                page_end INTEGER, text TEXT, embedding_json TEXT
            )
            """
        )
        conn.execute("CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO index_metadata VALUES ('embedding_model', ?)", (ask.EMBED_MODEL,))
        conn.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("1", "paper.pdf", "hash", 1, 1, text, json.dumps([1.0, 0.0])),
        )


class RagIndexReloadTests(unittest.TestCase):
    def test_query_rejects_incompatible_index_and_vector(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunks.sqlite3"
            create_index(path, "text")
            with mock.patch.object(ask, "INDEX_DB_PATH", path), \
                 mock.patch.object(ask, "EMBED_MODEL", "other-model"):
                ask.load_chunks_for_mtime.cache_clear()
                with self.assertRaisesRegex(EmbeddingMismatchError, "does not match"):
                    ask.load_chunks()
            with mock.patch.object(ask, "embed", return_value=[1, 0, 0]):
                with self.assertRaisesRegex(EmbeddingMismatchError, "dimensions differ"):
                    ask.search("query", [{"embedding": [1, 0]}])

    def test_load_chunks_reloads_when_sqlite_mtime_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunks.sqlite3"
            create_index(path, "first")
            with mock.patch.object(ask, "INDEX_DB_PATH", path):
                ask.load_chunks_for_mtime.cache_clear()
                self.assertEqual(ask.load_chunks()[0]["text"], "first")

                path.unlink()
                create_index(path, "second version")
                stat = path.stat()
                path.touch()
                self.assertGreaterEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)
                self.assertEqual(ask.load_chunks()[0]["text"], "second version")


if __name__ == "__main__":
    unittest.main()
