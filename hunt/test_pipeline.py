import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from hunt.batch import BudgetSpent
from hunt.pipeline import score_queue, score_stage, shown_reason, tailor_queue
from hunt.screen import context
from hunt.tailor import is_tailored, tailored_folder

NOW = datetime(2026, 10, 21, 12, 0).astimezone()
KEY = {"ANTHROPIC_API_KEY": "sk-ant-x"}


def _root(budget: bool = True) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    (root / "profile/search.json").write_text(
        json.dumps({"keywords": ["engineer"], "exclude_title": [], "location": {"mode": "any"}, "sources": []}), encoding="utf-8"
    )
    (root / "profile/rubric.json").write_text(json.dumps({"screenout_cap": 75}), encoding="utf-8")
    prefs = {"confirmed": True, "roles": ["Engineer"]}
    if budget:
        prefs["budget"] = {"classify_usd_per_day": 0.5, "score_usd_per_day": 3.0}
    (root / "profile/preferences.json").write_text(json.dumps(prefs), encoding="utf-8")
    return root


def _job(url: str, company: str, fit: float, posted: str, score: str = "") -> dict:
    return {
        "url": f"https://jobs.example/{url}",
        "company": company,
        "title": "Engineer",
        "location": "Bengaluru, India",
        "description": "Build agents with customers.",
        "posted_at": posted,
        "first_seen": f"{posted}T09:00:00+05:30",
        "jev_keep": True,
        "jev_fit": fit,
        "final_score": score,
    }


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.root = _root()
        self.ctx = context(self.root)

    def test_clean_roles_come_before_maybe_roles(self):
        maybe = _job("m", "Infosys Consulting", 4.0, "2026-10-20")
        older = _job("a", "Acme", 3.0, "2026-10-18")
        newer = _job("b", "Beta", 3.0, "2026-10-20")
        scored = _job("s", "Gamma", 3.5, "2026-10-20", score="80")
        hidden = _job("h", "Delta", 4.0, "2026-10-20")
        rows = [(maybe, ""), (older, ""), (newer, ""), (scored, ""), (hidden, "stale")]
        self.assertEqual([job["url"] for job in score_queue(rows, self.ctx, set())], [newer["url"], older["url"], maybe["url"]])

    def test_waiting_roles_are_not_queued_twice(self):
        newer = _job("b", "Beta", 3.0, "2026-10-20")
        self.assertEqual(score_queue([(newer, "")], self.ctx, {newer["url"]}), [])

    def test_old_markdown_resumes_count_as_tailored(self):
        high = _job("t", "Acme", 3.0, "2026-10-20", score="85")
        higher = _job("u", "Beta", 3.0, "2026-10-20", score="90")
        folder = tailored_folder(self.root, high)
        folder.mkdir(parents=True)
        (folder / "resume.md").write_text("# Ada\n", encoding="utf-8")
        self.assertTrue(is_tailored(self.root, high))
        self.assertFalse(is_tailored(self.root, higher))
        self.assertEqual(tailor_queue(self.root, [(high, ""), (higher, "")], 75, set()), [higher])


class StageTests(unittest.TestCase):
    def test_nothing_is_sent_without_a_key_or_a_saved_budget(self):
        lines: list[str] = []
        row = [(_job("b", "Beta", 3.0, "2026-10-20"), "")]
        with patch("hunt.batch.submit") as sent, patch("hunt.pipeline._rows", return_value=row):
            score_stage(_root(budget=False), NOW, lines.append, KEY)
            score_stage(_root(), NOW, lines.append, {})
            score_stage(_root(), NOW, lines.append, {"ANTHROPIC_API_KEY": "present"})
        sent.assert_not_called()

    def test_a_spent_cap_is_reported_not_raised(self):
        lines: list[str] = []
        row = [(_job("b", "Beta", 3.0, "2026-10-20"), "")]
        spent = BudgetSpent("Today's score and resume budget is spent. The rest waits for tomorrow.")
        with patch("hunt.batch.submit", side_effect=spent), patch("hunt.pipeline._rows", return_value=row):
            self.assertEqual(score_stage(_root(), NOW, lines.append, KEY), "")
        self.assertIn("budget is spent", lines[-1])

    def test_a_role_that_failed_twice_is_not_sent_again(self):
        lines: list[str] = []
        job = _job("b", "Beta", 3.0, "2026-10-20")
        with patch("hunt.batch.submit") as sent, patch("hunt.pipeline._rows", return_value=[(job, "")]), patch(
            "hunt.batch.failures", return_value={job["url"]: 2}
        ):
            score_stage(_root(), NOW, lines.append, KEY)
        sent.assert_not_called()

    def test_waiting_reasons(self):
        job = _job("w", "Acme", 3.0, "2026-10-20")
        self.assertEqual(shown_reason(job, "not classified yet", {job["url"]}, 0.5), "waiting: batch")
        self.assertEqual(shown_reason(job, "not classified yet", set(), 0.0), "waiting: budget")
        self.assertEqual(shown_reason(job, "not classified yet", set(), 0.5), "not classified yet")
        self.assertEqual(shown_reason(job, "stale", set(), 0.0), "stale")


if __name__ == "__main__":
    unittest.main()
