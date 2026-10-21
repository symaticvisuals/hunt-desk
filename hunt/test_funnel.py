import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from hunt.funnel import format_funnel, funnel_counts, record_funnel

TODAY = date(2026, 10, 21)


def _row(number: int, title: str, location: str, company: str = "Acme", description: str = "Build agents with customers.") -> dict:
    return {
        "company": company,
        "title": title,
        "url": f"https://example.com/{number}",
        "apply_url": f"https://example.com/{number}",
        "platform": "greenhouse",
        "job_id": str(number),
        "location": location,
        "description": description,
        "source": "greenhouse",
        "first_seen": "2026-10-20T10:00:00+00:00",
        "last_seen": "2026-10-20T10:00:00+00:00",
    }


def _root(rows: list[dict], cache: dict, **rubric) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    place = {"mode": "places", "countries": ["India"], "remote_only_countries": ["United States"], "work_modes": ["onsite", "hybrid", "remote"]}
    (root / "profile/search.json").write_text(
        json.dumps({"keywords": ["AI Engineer"], "exclude_title": [], "location": place, "sources": []}), encoding="utf-8"
    )
    (root / "profile/rubric.json").write_text(json.dumps({"max_years_without_cap": 5, "screenout_cap": 75, **rubric}), encoding="utf-8")
    (root / "profile/preferences.json").write_text(
        json.dumps({"confirmed": True, "roles": ["AI Engineer"], "seniority": [], "exclude_titles": []}), encoding="utf-8"
    )
    (root / "data/inbox.json").write_text(json.dumps(rows), encoding="utf-8")
    (root / "data/clef-cache.json").write_text(json.dumps(cache), encoding="utf-8")
    return root


class FunnelTests(unittest.TestCase):
    def test_counts_match_hidden_reasons(self):
        rows = [
            _row(1, "AI Engineer", "Bengaluru, India"),
            _row(2, "AI Engineer", "London, United Kingdom"),
            _row(3, "AI Engineer", "Pune, India"),
        ]
        cache = {
            "https://example.com/1": {"keep": True, "fit": 3, "worth": 0.9, "fit_label": "A plausible match"},
            "https://example.com/2": {"keep": True, "fit": 3, "worth": 0.9, "fit_label": "A plausible match"},
            "https://example.com/3": {"keep": True, "fit": 2.52, "worth": 0.9, "fit_label": "A plausible match", "over_years": True},
        }
        root = _root(rows, cache, plausible_at=3.0)
        counts = funnel_counts(root, TODAY)
        self.assertEqual(counts["inbox"], 3)
        self.assertEqual(counts["visible"], 1)
        self.assertEqual(counts["location"], 1)
        self.assertEqual(counts["stretch"], 1)
        self.assertEqual(counts["stretch labelled plausible"], 1)
        self.assertEqual(counts["stretch over years"], 1)
        self.assertEqual(counts["visible maybe"], 0)

        table = format_funnel(counts).splitlines()
        self.assertIn("| inbox | 3 |", table[2])
        self.assertIn("| visible | 1 |", table[-1])

        record_funnel(root, "t", counts)
        saved = json.loads((root / "data/funnel.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["label"], "t")
        self.assertEqual(saved[0]["counts"]["inbox"], 3)

    def test_maybe_rows_are_counted_and_skips_are_hidden(self):
        rows = [
            _row(1, "AI Engineer", "Bengaluru, India", company="Deloitte Consulting"),
            _row(2, "AI Engineer", "Pune, India", company="Talentgigs"),
            _row(3, "AI Engineer", "Pune, India"),
        ]
        cache = {f"https://example.com/{n}": {"keep": True, "fit": 3, "worth": 0.9, "fit_label": "A plausible match"} for n in (1, 2, 3)}
        counts = funnel_counts(_root(rows, cache), TODAY)
        self.assertEqual(counts["visible"], 2)
        self.assertEqual(counts["visible maybe"], 1)
        self.assertEqual(counts["staffing"], 1)


if __name__ == "__main__":
    unittest.main()
