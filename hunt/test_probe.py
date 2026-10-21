import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date, timedelta

from hunt.preferences import compiled_search, normalize
from hunt.probe import Probe, count_lines, counts, run_probe

TODAY = date(2026, 10, 21)


def _row(title: str, location: str, days_ago: int) -> dict:
    return {
        "title": title,
        "company": f"Co {title} {location}",
        "location": location,
        "url": f"https://jobs.example/{title}/{location}",
        "posted_at": (TODAY - timedelta(days=days_ago)).isoformat(),
    }


RULE = compiled_search(normalize({"roles": ["Product Manager"], "countries": ["India"]}), {})


class ProbeCountTests(unittest.TestCase):
    def test_counts_use_the_window_titles_and_places(self):
        probe = Probe(
            rows=[
                _row("Senior PM", "Bengaluru, India", 2),
                _row("Product Manager", "Berlin, Germany", 1),
                _row("Product Manager", "Pune, India", 20),
                _row("Data Analyst", "Pune, India", 1),
            ],
            failed=[],
            roles=["Product Manager"],
            done=True,
            at="",
        )
        self.assertEqual(counts(probe, ["Product Manager", "Data Analyst"], RULE, TODAY), {"Product Manager": 1, "Data Analyst": 1})

    def test_count_lines_mark_adjacent_hand_added_zero_and_failures(self):
        probe = Probe(
            rows=[_row("Senior PM", "Bengaluru, India", 2), _row("Growth Lead", "Pune, India", 1)],
            failed=["ashby"],
            roles=["Product Manager"],
            done=True,
            at="",
        )
        lines = count_lines(probe, ["Product Manager", "Growth Lead", "Chief of Staff"], RULE, ["Growth Lead"], TODAY, running=False)
        self.assertEqual(
            lines,
            [
                "Product Manager: 1 open · India · 14 days",
                "Growth Lead: 1 open · boards only · adjacent",
                "Chief of Staff: 0 open · check the title",
                "No answer from: ashby",
            ],
        )

    def test_a_running_probe_says_so_and_an_empty_finished_one_says_nothing(self):
        running = Probe(rows=[], failed=[], roles=["Product Manager"], done=False, at="")
        self.assertEqual(count_lines(running, ["Product Manager"], RULE, [], TODAY, running=True)[-1], "Counting…")
        self.assertEqual(count_lines(Probe(rows=[], failed=[], roles=[], done=True, at=""), ["Product Manager"], RULE, [], TODAY, running=False), [])


class ProbeRunTests(unittest.TestCase):
    def test_a_probe_leaves_the_board_schedule_alone(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "data").mkdir()
        (root / "profile/search.json").write_text(json.dumps({"keywords": [], "location": {"mode": "any"}, "sources": ["greenhouse"]}), encoding="utf-8")
        with patch("hunt.search.source_units", return_value=["greenhouse"]), patch("hunt.search._fetch_unit", return_value=([], [])):
            probe = run_probe(root, {}, {"roles": ["Product Manager"]})
        self.assertTrue(probe.done)
        self.assertFalse((root / "data/schedule.json").exists())


if __name__ == "__main__":
    unittest.main()
