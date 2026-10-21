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

    def test_a_product_resume_gives_product_titles_and_years(self):
        found = infer(
            "Jane Doe\nSenior Product Manager | Acme\n2019 – Present\nProduct Manager, Beta Corp\n2016 - 2019\n",
            today=date(2026, 10, 21),
        )
        self.assertIn("product manager", found.keywords)
        self.assertNotIn("software engineer", found.keywords)
        self.assertEqual(found.years_cap, 12)

    def test_date_ranges_with_month_names_count(self):
        text = (
            "Senior Product Manager | Finbox | Bengaluru\nJan 2022 – Present\n"
            "Product Manager | Razorpay | Bengaluru\nJul 2019 – Dec 2021\n"
            "Associate Product Manager | Swiggy | Bengaluru\nJun 2017 – Jun 2019\n"
        )
        self.assertEqual(infer(text, today=date(2026, 10, 21)).years_cap, 11)

    def test_only_an_engineering_word_brings_the_software_engineer_fallback(self):
        self.assertEqual(infer("Notes\n" + "Manager " * 20).keywords, [])
        self.assertEqual(infer("Notes\n" + "Engineer " * 20).keywords, ["software engineer"])

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
