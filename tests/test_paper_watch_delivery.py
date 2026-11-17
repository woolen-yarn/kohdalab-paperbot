import contextlib
import io
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from rag_poc import ingest, paper_watch
from rag_poc.rag_embeddings import EmbeddingMismatchError
from tests.test_paper_watch_rescore import watch_entry


class PaperWatchDeliveryTests(unittest.TestCase):
    def test_report_failure_preserves_successful_posts_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.sqlite3"
            with contextlib.closing(paper_watch.init_db(path)) as conn:
                paper_watch.upsert_entry(conn, dict(watch_entry("first", "First paper"), score=90))
                paper_watch.upsert_entry(conn, dict(watch_entry("second", "Second paper"), score=80))
                conn.commit()

            with patch("sys.argv", ["paper_watch", "--mode", "report"]), patch.object(
                paper_watch, "paper_watch_db_path", return_value=path,
            ), patch.object(
                paper_watch, "build_item_payloads", return_value=[{"text": "first"}, {"text": "second"}],
            ), patch.object(
                paper_watch, "post_to_slack", side_effect=[True, False],
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "failed after 1/2 posts"):
                    paper_watch.main()
            with contextlib.closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT external_id, posted_at IS NOT NULL FROM paper_watch_items ORDER BY external_id"
                ).fetchall(), [("first", 1), ("second", 0)])

    def test_rag_rejects_model_and_dimension_mismatches_before_scoring(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.closing(
            ingest.init_index_db(Path(directory) / "chunks.sqlite3"),
        ) as conn:
            ingest.validate_embedding_model(conn, "index-model")
            conn.execute(
                "INSERT INTO chunks VALUES ('id', 'paper.pdf', 'hash', 1, 1, 'text', '[1, 0]')"
            )
            entry = watch_entry("new", "New paper")
            with patch.object(paper_watch, "paper_watch_embed_model", return_value="other-model"),                  patch.object(paper_watch, "embed_many") as embed:
                with self.assertRaisesRegex(EmbeddingMismatchError, "does not match"):
                    paper_watch.apply_rag_scores([entry], conn, enabled=True, strict=True)
                embed.assert_not_called()
            with patch.object(paper_watch, "paper_watch_embed_model", return_value="index-model"),                  patch.object(paper_watch, "embed_many", return_value=[[1, 0, 0], [1, 0, 0]]):
                with self.assertRaisesRegex(EmbeddingMismatchError, "dimensions differ"):
                    paper_watch.apply_rag_scores([entry], conn, enabled=True, strict=True)
            self.assertFalse(entry.get("rag_source"))

    def test_arxiv_fetches_next_page_when_first_page_is_full(self):
        def response(identifier):
            xml = f"""<feed xmlns="http://www.w3.org/2005/Atom"
                         xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
              <opensearch:totalResults>2</opensearch:totalResults>
              <entry><id>https://arxiv.org/abs/{identifier}</id><title>Paper</title>
              <published>2026-11-01T00:00:00Z</published><updated>2026-11-01T00:00:00Z</updated>
              </entry>
            </feed>"""
            return io.BytesIO(xml.encode())

        with patch.object(
            paper_watch.urllib.request, "urlopen",
            side_effect=[response("2611.00001v1"), response("2611.00002v1")],
        ) as urlopen, patch.object(paper_watch.time, "sleep") as wait, contextlib.redirect_stdout(io.StringIO()):
            entries = paper_watch.fetch_arxiv_entries('all:"sns"', 1)
        self.assertEqual([entry["external_id"] for entry in entries], ["2611.00001", "2611.00002"])
        starts = [
            urllib.parse.parse_qs(urllib.parse.urlsplit(call.args[0].full_url).query)["start"][0]
            for call in urlopen.call_args_list
        ]
        self.assertEqual(starts, ["0", "1"])
        wait.assert_called_once_with(3)
