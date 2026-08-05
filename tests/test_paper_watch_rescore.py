import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rag_poc import paper_watch


def watch_entry(external_id: str, title: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "source": "test",
        "external_id": external_id,
        "title": title,
        "authors": [],
        "summary": "Rashba spin orbit interaction",
        "url": "https://example.test/paper",
        "published_at": now,
        "updated_at": now,
        "score": 1.0,
        "term_score": 1.0,
        "rag_score": 0.0,
        "reasons": ["old"],
    }


class PaperWatchRescoreTests(unittest.TestCase):
    def test_strict_rescore_requires_reference_chunks(self):
        with sqlite3.connect(":memory:") as conn:
            with self.assertRaisesRegex(RuntimeError, "requires indexed PDF chunks"):
                paper_watch.apply_rag_scores(
                    [watch_entry("one", "One paper")],
                    conn,
                    enabled=True,
                    candidate_limit_override=1,
                    strict=True,
                )

    def test_rescore_defaults_to_unposted_rows_and_preserves_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "paper_watch.sqlite3"
            conn = paper_watch.init_db(db_path)
            try:
                unposted = watch_entry("new", "Rashba control")
                posted = watch_entry("posted", "Previously posted")
                paper_watch.upsert_entry(conn, unposted)
                paper_watch.upsert_entry(conn, posted)
                paper_watch.mark_posted(conn, [posted])
                conn.commit()
                schema_before = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()

                active = paper_watch.select_rescore_candidates(
                    conn,
                    lookback_days=30,
                    include_posted=False,
                )
                all_rows = paper_watch.select_rescore_candidates(
                    conn,
                    lookback_days=30,
                    include_posted=True,
                )
                paper_watch.prepare_items_for_rescore(active, {"rashba": 4.0})
                updated = paper_watch.update_rescored_items(conn, active)
                conn.commit()
                schema_after = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()

                self.assertEqual([item["external_id"] for item in active], ["new"])
                self.assertEqual({item["external_id"] for item in all_rows}, {"new", "posted"})
                self.assertEqual(updated, 1)
                self.assertEqual(schema_before, schema_after)
                self.assertGreater(
                    conn.execute(
                        "SELECT score FROM paper_watch_items WHERE external_id = 'new'"
                    ).fetchone()[0],
                    1.0,
                )
            finally:
                conn.close()

    def test_backup_database_creates_readable_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "paper_watch.sqlite3"
            conn = paper_watch.init_db(db_path)
            try:
                paper_watch.upsert_entry(conn, watch_entry("one", "One paper"))
                conn.commit()
                backup_path = paper_watch.backup_database(conn, db_path)
            finally:
                conn.close()

            with sqlite3.connect(backup_path) as backup_conn:
                count = backup_conn.execute("SELECT COUNT(*) FROM paper_watch_items").fetchone()[0]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
