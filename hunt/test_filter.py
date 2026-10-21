import json
import unittest

from hunt.search import location_ok, title_matches
from hunt.sources import html_to_text, source_status


class FilterTests(unittest.TestCase):
    def test_unrelated_keyword_drops_engineer(self):
        search = {"keywords": ["accountant"], "exclude_title": []}
        self.assertFalse(title_matches("Senior Software Engineer", search))

    def test_full_stack_hyphen_matches(self):
        search = {"keywords": ["full stack"], "exclude_title": []}
        self.assertTrue(title_matches("Senior Full-Stack Engineer", search))

    def test_exclude_survives_punctuation(self):
        search = {"keywords": ["applied ai"], "exclude_title": ["recruiter"]}
        self.assertFalse(title_matches("Recruiter, Applied AI", search))

    def test_escaped_html_is_stripped(self):
        raw = "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;Hello&lt;/p&gt;&lt;/div&gt;"
        self.assertEqual(html_to_text(raw), "Hello")

    def test_intern_does_not_match_internal(self):
        search = {"keywords": ["software engineer"], "exclude_title": ["intern"]}
        self.assertTrue(title_matches("Internal Software Engineer", search))
        self.assertFalse(title_matches("Software Engineer Intern", search))

    def test_location_modes(self):
        self.assertTrue(location_ok("New York", {"location": {"mode": "any"}}))
        self.assertFalse(location_ok("New York", {"location": {"mode": "remote_only"}}))
        self.assertTrue(location_ok("Remote", {"location": {"mode": "remote_only"}}))
        countries = {"location": {"mode": "countries", "countries": ["Germany"]}}
        self.assertTrue(location_ok("Berlin, Germany", countries))
        self.assertFalse(location_ok("Austin, TX", countries))

    def test_skip_does_not_echo_secret(self):
        rows = source_status(
            ["greenhouse", "adzuna"],
            {"ADZUNA_APP_ID": "super-secret-id", "ADZUNA_APP_KEY": ""},
        )
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["greenhouse"]["state"], "on")
        self.assertEqual(by_name["adzuna"]["state"], "skip")
        self.assertEqual(by_name["jooble"]["state"], "off")
        self.assertNotIn("super-secret-id", json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
