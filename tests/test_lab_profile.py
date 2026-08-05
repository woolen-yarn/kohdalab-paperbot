import sqlite3
import unittest

from rag_poc import lab_profile


class LabProfileTests(unittest.TestCase):
    def test_load_documents_samples_chunks_across_the_pdf(self):
        with sqlite3.connect(":memory:") as conn:
            conn.execute(
                """
                CREATE TABLE chunks (
                    source TEXT, page_start INTEGER, page_end INTEGER, text TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                [("paper.pdf", index + 1, index + 1, f"chunk-{index}") for index in range(10)],
            )

            documents = lab_profile.load_documents(
                conn,
                max_docs=10,
                max_chunks_per_source=3,
            )

        self.assertEqual(len(documents), 1)
        text = documents[0]["text"]
        self.assertIn("chunk-0", text)
        self.assertIn("chunk-4", text)
        self.assertIn("chunk-9", text)
        self.assertNotIn("chunk-1\n", text)


if __name__ == "__main__":
    unittest.main()
