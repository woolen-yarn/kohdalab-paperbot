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
    def test_zero_rag_score_survives_collection_until_content_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = paper_watch.init_db(Path(directory) / "paper_watch.sqlite3")
            try:
                entry = watch_entry("one", "Rashba control")
                paper_watch.upsert_entry(conn, entry)
                enriched = dict(entry, rag_source="reference.pdf", score=12.0)
                self.assertEqual(paper_watch.update_rag_scores(conn, [enriched]), 1)
                paper_watch.upsert_entry(conn, entry)
                saved = conn.execute(
                    "SELECT score, rag_score, rag_source FROM paper_watch_items"
                ).fetchone()
                self.assertEqual(saved, (12.0, 0.0, "reference.pdf"))
                self.assertEqual(paper_watch.select_rag_enrichment_candidates(conn, lookback_days=7), [])

                paper_watch.upsert_entry(conn, dict(entry, summary="Revised abstract"))
                pending = paper_watch.select_rag_enrichment_candidates(conn, lookback_days=7)
                self.assertEqual([item["external_id"] for item in pending], ["one"])
            finally:
                conn.close()

    def test_empty_title_keys_do_not_match_previously_posted_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = paper_watch.init_db(Path(directory) / "paper_watch.sqlite3")
            try:
                posted = watch_entry("old", "半導体")
                candidate = watch_entry("new", "光物性")
                paper_watch.upsert_entry(conn, posted)
                paper_watch.mark_posted(conn, [posted])
                paper_watch.upsert_entry(conn, candidate)
                items = paper_watch.select_report_candidates(
                    conn, min_score=0, limit=5, lookback_days=7,
                    scope="all", selected_groups=set(),
                )
            finally:
                conn.close()
        self.assertEqual([item["external_id"] for item in items], ["new"])

    def test_arxiv_report_searches_beyond_higher_scored_journal_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = paper_watch.init_db(Path(directory) / "paper_watch.sqlite3")
            try:
                for number in range(26):
                    entry = watch_entry(str(number), f"Journal paper {number}")
                    paper_watch.upsert_entry(conn, dict(entry, score=80, source="rss"))
                arxiv = watch_entry("preprint", "Relevant preprint")
                paper_watch.upsert_entry(conn, dict(arxiv, score=50, source="arxiv"))

                items = paper_watch.select_report_candidates(
                    conn, min_score=45, limit=1, lookback_days=7,
                    scope="arxiv", selected_groups=set(),
                )
            finally:
                conn.close()

        self.assertEqual([item["external_id"] for item in items], ["preprint"])

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
