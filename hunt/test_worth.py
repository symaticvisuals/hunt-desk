import json
import tempfile
import unittest
from pathlib import Path

from hunt.worth import (
    DEFAULT_RULES,
    Worth,
    assess,
    company_class,
    keep_anyway,
    load_companies,
    load_overrides,
    load_rules,
    remote_check,
    set_company_class,
)

SEARCH = {
    "location": {
        "mode": "places",
        "countries": ["India"],
        "remote_only_countries": ["United States"],
        "work_modes": ["onsite", "hybrid", "remote"],
    }
}


def _job(**extra) -> dict:
    base = {
        "company": "Acme Robotics",
        "title": "AI Engineer",
        "url": "https://example.com/1",
        "location": "Bengaluru, India",
        "description": "Build agents with customers.",
        "posted_at": "2026-10-19",
        "first_seen": "2026-10-20T09:00:00+00:00",
    }
    base.update(extra)
    return base


def _assess(job: dict, companies: dict | None = None, overrides: set[str] | None = None) -> Worth:
    return assess(job, DEFAULT_RULES, companies or {}, overrides or set(), SEARCH)


class WorthTests(unittest.TestCase):
    def test_words_match_at_a_word_start(self):
        self.assertEqual(company_class("Talentgigs", DEFAULT_RULES, {}), "staffing")
        self.assertEqual(company_class("Brightalent Labs", DEFAULT_RULES, {}), "unknown")
        self.assertEqual(company_class("Softcrayons IT Education Pvt", DEFAULT_RULES, {}), "training")
        self.assertEqual(company_class("Deloitte Consulting", DEFAULT_RULES, {}), "services")
        self.assertEqual(company_class("Talentgigs", DEFAULT_RULES, {"talentgigs": {"name": "Talentgigs", "class": "product"}}), "product")

    def test_remote_us_reads_the_sentence(self):
        closed = remote_check("Remote role. Must be authorized to work in the United States. Apply today.", DEFAULT_RULES)
        self.assertEqual(closed, Worth("skip", "remote US: US only", "Must be authorized to work in the United States."))
        self.assertEqual(remote_check("This role is open to candidates worldwide.", DEFAULT_RULES).verdict, "apply")
        self.assertEqual(remote_check("Remote role on a fast team.", DEFAULT_RULES), Worth("maybe", "remote US: unclear"))
        both = remote_check("We hire across India and the US. W-2 only.", DEFAULT_RULES, ["India"])
        self.assertEqual(both, Worth("apply", "", "We hire across India and the US."))
        self.assertEqual(remote_check("We hire across India and the US. W-2 only.", DEFAULT_RULES).verdict, "skip")
        self.assertEqual(remote_check("Based in Indiana.", DEFAULT_RULES, ["India"]).verdict, "maybe")
        self.assertEqual(remote_check("Candidates must be US citizens.", DEFAULT_RULES).verdict, "skip")
        legal = remote_check("Must be legally authorized to work in the U.S. Apply now.", DEFAULT_RULES)
        self.assertEqual(legal, Worth("skip", "remote US: US only", "Must be legally authorized to work in the U.S."))
        self.assertEqual(remote_check("We are a globally recognised leader.", DEFAULT_RULES).verdict, "maybe")
        self.assertEqual(remote_check("Any location in the US.", DEFAULT_RULES).verdict, "maybe")
        self.assertNotIn("india", DEFAULT_RULES["remote_only"]["open_when"])

    def test_assess_order(self):
        self.assertEqual(_assess(_job(company="Talentgigs")), Worth("skip", "staffing", ""))
        self.assertEqual(_assess(_job(company="Deloitte Consulting")), Worth("maybe", "services", ""))
        self.assertEqual(_assess(_job(description="", description_tries=2)), Worth("skip", "no description", ""))
        self.assertEqual(_assess(_job(description="", description_tries=1)).verdict, "apply")
        self.assertEqual(_assess(_job(posted_at="2026-09-01", first_seen="2026-10-01T09:00:00+00:00")), Worth("maybe", "old when found", ""))
        self.assertEqual(_assess(_job(location="Remote - USA", description="Remote role on a fast team.")), Worth("maybe", "remote US: unclear", ""))
        self.assertEqual(_assess(_job(location="Bengaluru, India", description="Remote role on a fast team.")), Worth("apply", "", ""))
        self.assertEqual(_assess(_job(location="Remote - USA", description="Open to engineers in India. W-2 only.")).verdict, "apply")
        self.assertEqual(_assess(_job()), Worth("apply", "", ""))

    def test_a_keep_covers_every_url_of_the_row(self):
        root = Path(tempfile.mkdtemp())
        keep_anyway(root, {"url": "a", "also_at": ["b"]})
        self.assertEqual(load_overrides(root), {"a", "b"})
        self.assertEqual(_assess(_job(company="Talentgigs", url="b"), overrides=load_overrides(root)), Worth("apply", "kept by you", ""))

    def test_a_person_set_class_sticks(self):
        root = Path(tempfile.mkdtemp())
        set_company_class(root, "Talentgigs", "product")
        companies = load_companies(root)
        self.assertEqual(companies["talentgigs"], {"name": "Talentgigs", "class": "product"})
        self.assertEqual(_assess(_job(company="Talentgigs"), companies=companies).verdict, "apply")
        with self.assertRaises(ValueError):
            set_company_class(root, "Talentgigs", "cult")

    def test_a_broken_rules_file_falls_back_to_defaults(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        self.assertEqual(load_rules(root), (DEFAULT_RULES, ""))
        (root / "profile/worth.json").write_text("{", encoding="utf-8")
        self.assertEqual(load_rules(root), (DEFAULT_RULES, "profile/worth.json is not valid JSON; using defaults"))
        (root / "profile/worth.json").write_text(json.dumps({"company_words": {"staffing": ["agency"]}}), encoding="utf-8")
        rules, warning = load_rules(root)
        self.assertEqual(warning, "")
        self.assertEqual(rules["company_words"]["staffing"], ["agency"])
        self.assertEqual(rules["company_words"]["training"], DEFAULT_RULES["company_words"]["training"])

    def test_set_max_posted_days_writes_only_that_rule(self):
        from hunt.worth import set_max_posted_days

        root = Path(tempfile.mkdtemp())
        set_max_posted_days(root, 2)
        self.assertEqual(json.loads((root / "profile/worth.json").read_text(encoding="utf-8")), {"max_posted_days": 2})
        self.assertEqual(load_rules(root)[0]["old_when_found_days"], 14)
        set_max_posted_days(root, 0)
        self.assertEqual(load_rules(root)[0]["max_posted_days"], 0)

    def test_the_shipped_rules_file_matches_the_defaults(self):
        shipped = json.loads((Path(__file__).resolve().parent.parent / "profile/defaults/worth.json").read_text(encoding="utf-8"))
        self.assertEqual(shipped, DEFAULT_RULES)


if __name__ == "__main__":
    unittest.main()
