import csv
import json
import tempfile
import unittest
from pathlib import Path

from hunt.classify_eval import labelled, metrics, spearman


class MetricsTests(unittest.TestCase):
    def test_recall_and_precision_count_scores_above_the_cutoff(self):
        results = [
            (True, 4.0, 90.0),
            (True, 3.0, 85.0),
            (False, 2.0, 82.0),
            (True, 3.0, 60.0),
            (False, 1.0, 40.0),
        ]
        found = metrics(results, positive_above=79)
        self.assertEqual((found["roles"], found["passed"], found["positives"]), (5, 3, 3))
        self.assertAlmostEqual(found["recall"], 2 / 3, places=3)
        self.assertAlmostEqual(found["precision"], 2 / 3, places=3)

    def test_rank_correlation_handles_ties(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)
        self.assertAlmostEqual(spearman([3, 3, 3, 3], [1, 2, 3, 4]), 0.0)


class LabelledTests(unittest.TestCase):
    def test_scored_roles_with_posting_text_are_labelled_under_any_board_url(self):
        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        text = "Build agents with customers. " * 20
        inbox = [
            {"url": "https://a.example/1", "also_at": ["https://linkedin.example/1"], "title": "AI Engineer", "description": text},
            {"url": "https://a.example/2", "title": "FDE", "description": "JR2028008"},
        ]
        (root / "data/inbox.json").write_text(json.dumps(inbox), encoding="utf-8")
        with (root / "data/evaluated-jobs.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["url", "final_score"])
            writer.writeheader()
            writer.writerows(
                [
                    {"url": "https://linkedin.example/1", "final_score": "84"},
                    {"url": "https://a.example/2", "final_score": "90"},
                    {"url": "https://gone.example/3", "final_score": "70"},
                    {"url": "https://a.example/1", "final_score": "n/a"},
                ]
            )
        pairs = labelled(root)
        self.assertEqual([(job["title"], score) for job, score in pairs], [("AI Engineer", 84.0)])


if __name__ == "__main__":
    unittest.main()
