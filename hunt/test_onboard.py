import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from hunt.onboard import apply_setup, infer


RESUME = """
# Ada Lovelace

## Experience

### Forward Deployed Engineer, Analytical Engines
London. 2024 — now
- Shipped production systems with customer teams.

### Software Engineer, Difference Engine
London. 2021 — 2024
- Built full stack tools for operators.

## Education
B.Sc. Mathematics. 2016 — 2020

## Skills
Python, applied AI, PostgreSQL
"""


class OnboardTests(unittest.TestCase):
    def test_infer_uses_roles_and_ignores_school_dates(self):
        found = infer(RESUME, today=date(2026, 10, 15))
        self.assertIn("forward deployed", found.keywords)
        self.assertIn("software engineer", found.keywords)
        self.assertIn("applied ai", found.keywords)
        self.assertIn("full stack", found.keywords)
        self.assertEqual(found.years_cap, 7)
        self.assertTrue(any("Forward Deployed" in role for role in found.roles))

    def test_apply_setup_rewrites_keywords_and_keeps_sources(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "data").mkdir()
        (root / "profile/search.json").write_text(
            json.dumps({"keywords": ["accountant"], "exclude_title": ["intern"], "location": {"mode": "any"}, "sources": ["greenhouse"]}),
            encoding="utf-8",
        )
        (root / "profile/rubric.json").write_text(json.dumps({"natural_hire_at": 80, "comp": {"min": None}}), encoding="utf-8")
        found = infer(RESUME, today=date(2026, 10, 15))
        apply_setup(root, RESUME, found)
        search = json.loads((root / "profile/search.json").read_text(encoding="utf-8"))
        rubric = json.loads((root / "profile/rubric.json").read_text(encoding="utf-8"))
        self.assertEqual(search["sources"], ["greenhouse"])
        self.assertIn("software engineer", search["keywords"])
        self.assertNotIn("accountant", search["keywords"])
        self.assertEqual(rubric["max_years_without_cap"], 7)
        self.assertIn("Forward Deployed Engineer", (root / "profile/og-resume.md").read_text(encoding="utf-8"))
