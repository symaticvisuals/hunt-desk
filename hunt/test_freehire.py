import unittest
import urllib.parse
from unittest.mock import patch

from hunt.sources import (
    FEED_FETCH,
    FREEHIRE_PAGE,
    SourceError,
    fetch_freehire,
    freehire_places,
    freehire_queries,
    source_status,
)

SEARCH = {
    "keywords": ["Forward Deployed Engineer"],
    "location": {
        "mode": "places",
        "countries": ["India"],
        "remote_only_countries": ["United States"],
        "work_modes": ["onsite", "hybrid", "remote"],
    },
}


def posting(n: int = 1, **over) -> dict:
    row = {
        "title": "Forward Deployed Engineer",
        "company": "66degrees",
        "source": "greenhouse",
        "external_id": f"66degrees:62084960{n:02d}",
        "url": f"https://job-boards.greenhouse.io/66degrees/jobs/62084960{n:02d}?utm_source=freehire.me&utm_medium=api",
        "location": "Bengaluru",
        "countries": ["in"],
        "work_mode": "hybrid",
        "description": "<p>Build <b>agents</b> with customers.</p>",
        "posted_at": "2026-10-20T00:00:00Z",
        "created_at": "2026-10-19T07:00:00Z",
        "enrichment": {
            "experience_years_min": 4,
            "company_size": "51-200",
            "salary_min": 1200000,
            "salary_max": 2400000,
            "salary_currency": "INR",
            "salary_period": "year",
            "employment_type": "full_time",
            "requirements": [{"text": "4+ years shipping software", "priority": "required"}],
        },
    }
    row.update(over)
    return row


class FakeFreehire:
    def __init__(self, pages: list[list[dict]] | None = None) -> None:
        self.pages = pages if pages is not None else [[posting()]]
        self.params: list[dict] = []

    def __call__(self, url, *, label, **_kwargs):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.params.append(query)
        index = int(query.get("offset") or 0) // FREEHIRE_PAGE
        return {"data": self.pages[index] if index < len(self.pages) else []}


class FreehireQueryTests(unittest.TestCase):
    def test_queries_match_the_role_words_in_titles_with_board_spellings(self):
        self.assertEqual(
            freehire_queries(
                ["Full Stack Software Developer", "Forward Deployed Engineer", "AI Engineer", "Software Engineer", "Solutions Engineer", "ML Engineer"]
            ),
            [
                'title:"full stack" OR title:"fullstack"',
                'title:"forward deployed" OR title:"fde"',
                'title:"ai"',
                'title:"software engineer"',
                'title:"solutions" OR title:"solution"',
                'title:"ml" OR title:"machine learning"',
            ],
        )

    def test_home_countries_and_remote_only_countries_are_separate_searches(self):
        self.assertEqual(
            freehire_places(SEARCH),
            [{"countries": "IN"}, {"countries": "US", "work_mode": "remote"}],
        )

    def test_a_narrower_work_mode_is_sent_and_remote_only_needs_remote(self):
        search = {"location": {**SEARCH["location"], "work_modes": ["onsite", "hybrid"]}}
        self.assertEqual(freehire_places(search), [{"countries": "IN", "work_mode": "onsite,hybrid"}])

    def test_an_unknown_country_says_where_to_set_its_code(self):
        search = {"location": {"mode": "countries", "countries": ["Atlantis"]}}
        with self.assertRaisesRegex(SourceError, "source_options.freehire.country_codes"):
            freehire_places(search)
        search["source_options"] = {"freehire": {"country_codes": {"Atlantis": "AT"}}}
        self.assertEqual(freehire_places(search), [{"countries": "AT"}])


class FreehireFetchTests(unittest.TestCase):
    def test_a_posting_keeps_its_hiring_board_identity(self):
        with patch("hunt.sources.get_json", side_effect=FakeFreehire()):
            [job] = fetch_freehire(SEARCH, {})
        self.assertEqual(job.source, "freehire")
        self.assertEqual(job.platform, "greenhouse")
        self.assertEqual((job.board, job.job_id), ("66degrees", "6208496001"))
        self.assertEqual(job.url, "https://job-boards.greenhouse.io/66degrees/jobs/6208496001")
        self.assertIn("India", job.location)
        self.assertEqual(job.work_mode, "hybrid")
        self.assertTrue(job.description.startswith("Experience: 4+ years."))
        self.assertIn("Build agents with customers.", job.description)
        self.assertIn("4+ years shipping software", job.description)
        self.assertEqual((job.pay_min, job.pay_max, job.pay_currency, job.pay_interval), (1200000, 2400000, "INR", "year"))
        self.assertEqual(job.job_type, "fulltime")
        self.assertEqual((job.big, job.size_known), (False, True))

    def test_the_earlier_of_the_board_date_and_first_seen_is_the_posting_date(self):
        with patch("hunt.sources.get_json", side_effect=FakeFreehire()):
            [job] = fetch_freehire(SEARCH, {})
        self.assertTrue(job.posted_at.startswith("2026-10-19"), job.posted_at)

    def test_workday_ids_match_the_direct_board_fetch(self):
        row = posting(
            source="workday",
            external_id="cisco.wd5.myworkdayjobs.com/cisco_careers:/job/Bangalore-India/Senior-FDE_2027881",
            url="https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/Bangalore-India/Senior-FDE_2027881",
        )
        with patch("hunt.sources.get_json", side_effect=FakeFreehire([[row]])):
            [job] = fetch_freehire(SEARCH, {})
        self.assertEqual((job.platform, job.board, job.job_id), ("workday", "cisco", "2027881"))

    def test_a_remote_posting_says_remote(self):
        row = posting(location="", countries=["us"], work_mode="remote")
        with patch("hunt.sources.get_json", side_effect=FakeFreehire([[row]])):
            [job] = fetch_freehire(SEARCH, {})
        self.assertIn("United States", job.location)
        self.assertIn("Remote", job.location)

    def test_it_pages_until_a_short_page_and_dedupes_by_url(self):
        fake = FakeFreehire([[posting(n) for n in range(FREEHIRE_PAGE)], [posting(1)]])
        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_freehire({**SEARCH, "location": {"mode": "countries", "countries": ["India"]}}, {})
        self.assertEqual([params["offset"] for params in fake.params], ["0", "100"])
        self.assertEqual(len(jobs), FREEHIRE_PAGE)

    def test_requests_ask_for_new_postings_newest_first_without_evergreen_ones(self):
        fake = FakeFreehire()
        with patch("hunt.sources.get_json", side_effect=fake):
            fetch_freehire(SEARCH, {})
        first = fake.params[0]
        self.assertEqual(first["q"], 'title:"forward deployed" OR title:"fde"')
        self.assertEqual(first["open_within_days"], "3")
        self.assertEqual(first["reality_exclude"], "likely-evergreen")
        self.assertEqual(first["sort"], "created_at")
        fake = FakeFreehire()
        with patch("hunt.sources.get_json", side_effect=fake):
            fetch_freehire({**SEARCH, "source_options": {"freehire": {"open_within_days": 7}}}, {})
        self.assertEqual(fake.params[0]["open_within_days"], "7")

    def test_it_needs_no_key(self):
        self.assertIs(FEED_FETCH["freehire"], fetch_freehire)
        [row] = [row for row in source_status(["freehire"], {}) if row["name"] == "freehire"]
        self.assertEqual(row["state"], "on")


if __name__ == "__main__":
    unittest.main()
