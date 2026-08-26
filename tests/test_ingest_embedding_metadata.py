import sqlite3
import tempfile
import unittest
from pathlib import Path

from rag_poc.ingest import init_index_db, validate_embedding_model


class EmbeddingMetadataTests(unittest.TestCase):
    def test_records_model_for_empty_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = init_index_db(Path(temp_dir) / "index.sqlite3")
            try:
                validate_embedding_model(conn, "qwen3-embedding-4b")
                row = conn.execute(
                    "SELECT value FROM index_metadata WHERE key = 'embedding_model'"
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(row, ("qwen3-embedding-4b",))

    def test_rejects_model_change_when_chunks_exist(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chunks (id TEXT)")
        conn.execute("CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO chunks VALUES ('chunk-1')")
        conn.execute(
            "INSERT INTO index_metadata VALUES ('embedding_model', 'nomic-embed-text')"
        )

        with self.assertRaisesRegex(SystemExit, "Run ingest with --rebuild"):
            validate_embedding_model(conn, "qwen3-embedding-4b")

    def test_rebuild_allows_model_change(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chunks (id TEXT)")
        conn.execute("CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO chunks VALUES ('chunk-1')")

        validate_embedding_model(conn, "qwen3-embedding-4b", rebuild=True)
        row = conn.execute(
            "SELECT value FROM index_metadata WHERE key = 'embedding_model'"
        ).fetchone()
        self.assertEqual(row, ("qwen3-embedding-4b",))


if __name__ == "__main__":
    unittest.main()
