import os
import unittest
from unittest import mock

from rag_poc import paper_watch


class PaperWatchScoringTests(unittest.TestCase):
    def test_keyword_synonyms_use_only_strongest_group_match(self):
        entry = {
            "title": "Persistent Spin Helix and PSH in GaAs",
            "summary": "The spin helix is controlled through spin orbit coupling.",
        }
        terms = {
            "persistent spin helix": 9.0,
            "spin helix": 7.0,
            "psh": 7.0,
            "gaas": 2.0,
        }

        with mock.patch.dict(os.environ, {"PAPER_WATCH_KEYWORD_DECAY": "1"}):
            score, reasons = paper_watch.keyword_score(entry, terms)

        self.assertEqual(score, 16.5)
        self.assertIn("persistent spin helix", reasons)
        self.assertIn("gaas", reasons)
        self.assertEqual(len(reasons), 2)

    def test_title_match_is_weighted_more_than_abstract_match(self):
        terms = {"rashba": 4.0}
        title_score, _ = paper_watch.keyword_score(
            {"title": "Rashba control", "summary": ""}, terms
        )
        abstract_score, _ = paper_watch.keyword_score(
            {"title": "Gate control", "summary": "Rashba interaction"}, terms
        )

        self.assertEqual(title_score, 6.0)
        self.assertEqual(abstract_score, 4.0)

    def test_multi_rag_uses_one_chunk_per_source_and_three_sources(self):
        chunks = [
            {"source": "a.pdf", "embedding": [1.0, 0.0], "text": "a1"},
            {"source": "a.pdf", "embedding": [0.9, 0.1], "text": "a2"},
            {"source": "b.pdf", "embedding": [0.8, 0.6], "text": "b"},
            {"source": "c.pdf", "embedding": [0.6, 0.8], "text": "c"},
            {"source": "d.pdf", "embedding": [0.0, 1.0], "text": "d"},
        ]

        score, matches = paper_watch.best_rag_matches([1.0, 0.0], chunks)

        self.assertAlmostEqual(score, 0.6 * 1.0 + 0.3 * 0.8 + 0.1 * 0.6)
        self.assertEqual([item["source"] for item in matches], ["a.pdf", "b.pdf", "c.pdf"])

    def test_title_and_abstract_are_scored_separately(self):
        chunks = [
            {"source": "title.pdf", "embedding": [1.0, 0.0], "text": "title"},
            {"source": "abstract.pdf", "embedding": [0.0, 1.0], "text": "abstract"},
            {"source": "balanced.pdf", "embedding": [0.7, 0.7], "text": "balanced"},
        ]

        score, matches = paper_watch.combine_title_abstract_matches(
            [1.0, 0.0],
            [0.0, 1.0],
            chunks,
        )

        self.assertEqual(matches[0]["source"], "balanced.pdf")
        self.assertGreater(score, 0.6)

    def test_diverse_chunk_selection_does_not_take_only_leading_chunks(self):
        chunks = [
            {"text": "intro 1", "embedding": [1.0, 0.0]},
            {"text": "intro 2", "embedding": [0.99, 0.01]},
            {"text": "intro 3", "embedding": [0.98, 0.02]},
            {"text": "results", "embedding": [0.0, 1.0]},
        ]

        selected = paper_watch.select_diverse_chunks(chunks, 2)

        self.assertEqual([chunk["text"] for chunk in selected], ["intro 1", "results"])

    def test_calibration_and_final_score_are_bounded(self):
        self.assertEqual(paper_watch.calibrate_rag_score(0.75), 0.0)
        self.assertAlmostEqual(paper_watch.calibrate_rag_score(0.825), 0.5)
        self.assertEqual(paper_watch.calibrate_rag_score(0.90), 1.0)
        self.assertEqual(
            paper_watch.normalized_final_score(
                {"keyword": 30.0, "combo": 6.0, "author": 5.0, "journal": 1.0},
                1.0,
            ),
            100.0,
        )

    def test_priority_author_bonus_is_capped_by_final_normalization(self):
        entry = {"authors": ["Yuzo Ohno", "A. Researcher"]}
        with mock.patch.dict(os.environ, {"PAPER_WATCH_PRIORITY_AUTHORS": "yuzo ohno:8"}):
            self.assertEqual(paper_watch.author_bonus(entry), 8.0)
            score = paper_watch.normalized_final_score(
                {"keyword": 0.0, "combo": 0.0, "author": 8.0, "journal": 0.0},
                0.0,
            )
        self.assertEqual(score, 5.0)

    def test_profile_metadata_contributes_author_and_journal_priority(self):
        profile = {
            "categories": {
                "authors": [{"label": "Makoto Kohda", "document_count": 8}],
                "journals": [{"label": "Physical Review B", "document_count": 6}],
            }
        }
        with mock.patch.object(paper_watch, "load_lab_profile", return_value=profile):
            self.assertEqual(paper_watch.author_bonus({"authors": ["Makoto Kohda"]}), 5.0)
            self.assertEqual(
                paper_watch.journal_priority({"journal": "Physical Review B"}), 1.0
            )

    def test_adaptive_rag_bounds_use_candidate_distribution(self):
        scores = [0.80 + index * 0.01 for index in range(10)]

        floor, ceiling = paper_watch.adaptive_rag_bounds(
            scores,
            fallback_floor=0.75,
            fallback_ceiling=0.90,
        )

        self.assertAlmostEqual(floor, 0.809)
        self.assertAlmostEqual(ceiling, 0.8855)


if __name__ == "__main__":
    unittest.main()
