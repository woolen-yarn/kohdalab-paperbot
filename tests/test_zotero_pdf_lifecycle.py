import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rag_poc import ingest, zotero_sync


class ZoteroPdfLifecycleTests(unittest.TestCase):
    def test_incremental_sync_rechecks_attachment_only_changes_and_deletions(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "chunks.sqlite3"
            conn = zotero_sync.init_db(db_path)
            for key in ("PAPER001", "PAPER002", "PAPER003"):
                paper = zotero_sync.normalize_item({
                    "key": key, "version": 4,
                    "data": {"itemType": "journalArticle", "title": key},
                })
                zotero_sync.upsert_paper(conn, paper)
            conn.execute("UPDATE papers SET pdf_status = 'no_pdf'")
            conn.execute("UPDATE papers SET pdf_status = 'failed' WHERE zotero_key = 'PAPER003'")
            conn.execute("""
                INSERT INTO zotero_attachments (attachment_key, parent_key, status, zotero_json)
                VALUES ('ATTACH02', 'PAPER002', 'downloaded', '{}')
            """)
            conn.commit()
            conn.close()

            args = SimpleNamespace(
                dry_run=False, all=False, limit=None, download_pdfs=True,
                refresh_pdf_metadata=False, force_pdf_download=False,
                verbose_pdfs=False, pdf_workers=1,
            )
            attachment = {"data": {"parentItem": "PAPER001"}}
            with patch.multiple(
                zotero_sync, INDEX_DB_PATH=db_path, load_env_file=lambda *_: None,
                parse_args=lambda: args, get_sync_version=lambda _: 4,
                fetch_deleted_items=lambda _: ({"ATTACH02"}, 7),
                set_sync_version=lambda *_: None, write_sync_report=lambda _: None,
            ), patch.object(
                zotero_sync, "fetch_top_items", side_effect=[([], 6), ([attachment], 7)],
            ) as fetch, patch.object(
                zotero_sync, "download_unique_pdfs", return_value={},
            ) as download, contextlib.redirect_stdout(io.StringIO()):
                zotero_sync.main()
            fetch.assert_called_with(None, since=4, attachments=True)
            self.assertEqual(
                {paper["zotero_key"]: paper["pdf_status"] for paper in download.call_args.args[1]},
                {"PAPER001": "pending", "PAPER002": "pending", "PAPER003": "failed"},
            )

    def test_ingest_removes_stale_chunks_with_no_active_pdfs_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "chunks.sqlite3"
            pdf = root / "papers" / "zotero" / "PAPER001_ATTACH01.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.touch()
            conn = zotero_sync.init_db(db_path)
            conn.execute("INSERT INTO zotero_sync_state VALUES ('/groups/test', 1, '')")
            conn.commit()
            conn.close()
            conn = ingest.init_index_db(db_path)
            conn.execute(
                "INSERT INTO chunks VALUES ('old', ?, 'hash', 1, 1, 'old text', '[]')",
                ("zotero/" + pdf.name,),
            )
            conn.commit()
            conn.close()

            with patch.multiple(
                ingest, PAPERS_DIR=root / "papers", INDEX_DIR=root, INDEX_DB_PATH=db_path,
                parse_args=lambda: SimpleNamespace(source_prefix="zotero/", rebuild=False),
                write_ingest_report=lambda report: self.assertEqual(report["removed_pdfs"], 1),
                refresh_lab_profile=lambda: None,
            ), contextlib.redirect_stdout(io.StringIO()):
                ingest.main()
            self.assertTrue(pdf.exists())
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM chunks").fetchone(), (0,))

    def test_full_sync_reconciles_deleted_papers_and_reindexes_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "chunks.sqlite3"
            papers_dir = root / "papers"
            zotero_dir = papers_dir / "zotero"
            zotero_dir.mkdir(parents=True)
            old = zotero_dir / "PAPER001_ATTACH01.pdf"
            deleted = zotero_dir / "PAPER002_ATTACH01.pdf"
            old.touch()
            deleted.touch()

            def item(key, **data):
                return {"key": key, "version": 7, "data": {
                    "itemType": "journalArticle", "title": key, **data,
                }}

            with contextlib.closing(zotero_sync.init_db(db_path)) as conn:
                for key, pdf in (("PAPER001", old), ("PAPER002", deleted), ("PAPER003", deleted)):
                    zotero_sync.upsert_paper(conn, zotero_sync.normalize_item(item(key)))
                    zotero_sync.update_paper_pdf_status(
                        conn, paper_key=key, path="zotero/" + pdf.name, status="downloaded",
                    )
                conn.commit()
            with contextlib.closing(ingest.init_index_db(db_path)) as conn:
                ingest.validate_embedding_model(conn, ingest.EMBED_MODEL)
                for pdf in (old, deleted):
                    conn.execute(
                        "INSERT INTO chunks VALUES (?, ?, 'old', 1, 1, 'old text', '[1, 0]')",
                        (pdf.name, "zotero/" + pdf.name),
                    )
                conn.commit()
            with ingest.fitz.open() as document:
                document.new_page().insert_text((72, 72), "Replacement PDF text")
                replacement = document.tobytes()
            args = SimpleNamespace(
                dry_run=False, all=True, limit=None, download_pdfs=True,
                refresh_pdf_metadata=False, force_pdf_download=False, verbose_pdfs=False, pdf_workers=1,
            )
            attachment = {"key": "ATTACH02", "data": {
                "itemType": "attachment", "contentType": "application/pdf",
            }}
            with patch.multiple(
                zotero_sync, INDEX_DB_PATH=db_path, PAPERS_DIR=papers_dir, ZOTERO_PDF_DIR=zotero_dir,
                load_env_file=lambda *_: None, parse_args=lambda: args,
                library_base_path=lambda: "/groups/test",
                fetch_top_items=lambda *_args, **_kwargs: ([item("PAPER001"), item("PAPER003", deleted=1)], 7),
                fetch_children=lambda key: [attachment] if key == "PAPER001" else [],
                zotero_get_file=lambda _: (replacement, {}), write_sync_report=lambda _: None,
            ), contextlib.redirect_stdout(io.StringIO()):
                zotero_sync.main()
            # A corrupt current PDF must not prevent indexing the valid replacement.
            bad = papers_dir / "invalid.pdf"
            bad.write_text("not a PDF")
            with patch.multiple(
                ingest, PAPERS_DIR=papers_dir, INDEX_DIR=root, INDEX_DB_PATH=db_path,
                parse_args=lambda: SimpleNamespace(source_prefix="", rebuild=False, verify_hash=False),
                embed=lambda *_: [1.0, 0.0], write_ingest_report=lambda _: None,
                refresh_lab_profile=lambda: None,
            ), contextlib.redirect_stdout(io.StringIO()):
                ingest.main()
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(conn.execute("SELECT zotero_key FROM papers").fetchall(), [("PAPER001",)])
                self.assertEqual(conn.execute("SELECT version FROM zotero_sync_state").fetchone(), (7,))
                self.assertEqual(
                    conn.execute("SELECT source, text FROM chunks").fetchall(),
                    [("zotero/PAPER001_ATTACH02.pdf", "Replacement PDF text")],
                )
                self.assertEqual(conn.execute(
                    "SELECT status FROM pdf_documents WHERE source='invalid.pdf'"
                ).fetchone(), ("error",))
            self.assertTrue(old.exists())
            self.assertTrue(deleted.exists())

    def test_full_snapshot_rejects_version_change_mid_pagination(self):
        headers = {"Total-Results": "2"}
        with patch.multiple(zotero_sync, REQUEST_BATCH_SIZE=1, library_base_path=lambda: "/groups/test"),              patch.object(zotero_sync.time, "sleep"), patch.object(
                 zotero_sync, "zotero_get", side_effect=[
                     ([{"key": "PAPER001"}], dict(headers, **{"Last-Modified-Version": "7"})),
                     ([{"key": "PAPER002"}], dict(headers, **{"Last-Modified-Version": "8"})),
                 ],
             ):
            with self.assertRaisesRegex(zotero_sync.ZoteroError, "snapshot changed"):
                zotero_sync.fetch_top_items(None)
