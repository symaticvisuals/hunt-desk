import unittest
from datetime import date

from hunt.filters import parse, row_for

TODAY = date(2026, 10, 22)


def _row(reason: str = "", maybe: bool = False, **extra):
    job = {
        "company": "Hadrian",
        "title": "Forward Deployed Engineer",
        "location": "Remote, US",
        "source": "jobspy",
        "platform": "linkedin",
        "final_score": "84",
        "posted_at": "2026-10-20",
    }
    job.update(extra)
    return row_for(job, reason, lambda: maybe)


def _keeps(text: str, row) -> bool:
    return parse(text, TODAY).matches(row)


class FilterTests(unittest.TestCase):
    def test_every_word_must_match_any_field_and_a_dash_inverts_one(self):
        self.assertTrue(_keeps("hadrian deployed", _row()))
        self.assertTrue(_keeps("REMOTE jobspy", _row()))
        self.assertFalse(_keeps("hadrian senior", _row()))
        self.assertTrue(_keeps("-senior", _row()))
        self.assertFalse(_keeps("-forward", _row()))

    def test_score_compares_and_none_finds_unscored_roles(self):
        self.assertTrue(_keeps("score>80", _row()))
        self.assertFalse(_keeps("score>84", _row()))
        self.assertTrue(_keeps("score>=84 score<=84", _row()))
        self.assertTrue(_keeps("score:80", _row()))
        self.assertFalse(_keeps("score<90", _row(final_score="")))
        self.assertTrue(_keeps("score:none", _row(final_score="")))
        self.assertFalse(_keeps("score:none", _row()))

    def test_source_reason_and_days_read_their_own_fields(self):
        self.assertTrue(_keeps("src:linked", _row()))
        self.assertFalse(_keeps("src:workday", _row()))
        self.assertTrue(_keeps("why:stretch", _row(reason="stretch")))
        self.assertFalse(_keeps("why:stretch", _row(reason="scored low")))
        self.assertTrue(_keeps("-why:dismissed", _row(reason="stretch")))
        self.assertTrue(_keeps("days:2", _row()))
        self.assertFalse(_keeps("days:1", _row()))
        self.assertTrue(_keeps("days:3", _row(posted_at="", first_seen="2026-10-20T08:00:00+00:00")))
        self.assertFalse(_keeps("days:30", _row(posted_at="", first_seen="")))

    def test_maybe_is_asked_only_when_the_filter_wants_it(self):
        asked = []
        row = row_for({"company": "Acme", "title": "AI Engineer"}, "", lambda: asked.append(1) or True)
        self.assertTrue(parse("acme", TODAY).matches(row))
        self.assertEqual(asked, [])
        self.assertTrue(parse("maybe", TODAY).matches(row))
        self.assertEqual(asked, [1])

    def test_a_bad_number_is_reported_and_left_out(self):
        query = parse("score>high days:week hadrian", TODAY)
        self.assertEqual(query.problems, ["score needs a number, like score>80", "days needs a number, like days:7"])
        self.assertTrue(query.matches(_row()))
        self.assertFalse(parse("", TODAY))


if __name__ == "__main__":
    unittest.main()
