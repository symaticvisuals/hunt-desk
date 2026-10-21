import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from hunt.follow import run_after_rewrite


class Job:
    def __init__(self, company: str, title: str, url: str, posted: str) -> None:
        self.company = company
        self.title = title
        self.url = url
        self.apply_url = url
        self.posted_at = posted
        self.description = "Ships production systems."
        self.big = False

    def as_dict(self) -> dict:
        return {
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "apply_url": self.apply_url,
            "posted_at": self.posted_at,
            "description": self.description,
            "big": self.big,
        }


class Result:
    def __init__(self, jobs) -> None:
        self.jobs = jobs


class Run:
    def __init__(self, jobs) -> None:
        self.search = Result(jobs)


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


class FollowTests(unittest.TestCase):
    def test_preferences_come_before_the_search(self):
        root = Path(tempfile.mkdtemp())
        with patch("hunt.schedule.run") as run:
            note = run_after_rewrite(root)
        run.assert_not_called()
        self.assertIn("confirm your preferences", note)

    def test_scores_only_the_roles_clef_keeps(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "profile/preferences.json").write_text('{"confirmed": true, "seniority": [], "exclude_titles": []}', encoding="utf-8")
        jobs = [
            Job("Older", "Engineer", "https://example.com/old", _ago(10)),
            Job("Acme", "Engineer", "https://example.com/new", _ago(1)),
            Job("Stretch", "Engineer", "https://example.com/stretch", _ago(0)),
            Job("Closed", "Engineer", "https://example.com/closed", _ago(2)),
        ]
        notes = []
        scored = []

        def evaluate(_root, batch, *, limit, on_progress=None):
            self.assertEqual(limit, 15)
            return [
                {"url": "https://example.com/old", "keep": True, "fit": 4},
                {"url": "https://example.com/new", "keep": True, "fit": 4},
                {"url": "https://example.com/stretch", "keep": True, "fit": 2},
                {"url": "https://example.com/closed", "keep": True, "fit": 4},
            ]

        def score(_root, job, **_kwargs):
            from hunt.cli import Scored

            scored.append(job["url"])
            return Scored(0, f"{job['url']}: 85")

        with (
            patch("hunt.schedule.run", return_value=Run(jobs)),
            patch("hunt.gate.evaluate_many", side_effect=evaluate),
            patch("shutil.which", return_value="/bin/claude"),
            patch("hunt.live.check_live", side_effect=lambda url: "closed" if url.endswith("closed") else "open"),
            patch("hunt.cli.score_one", side_effect=score),
        ):
            note = run_after_rewrite(root, on_progress=notes.append)
        self.assertCountEqual(scored, ["https://example.com/new", "https://example.com/old"])
        self.assertIn("Scored 2 roles", note)
        self.assertTrue(any(line.startswith("Searching") for line in notes))
        self.assertIn("Scoring 2 roles, 3 at a time.", notes)
