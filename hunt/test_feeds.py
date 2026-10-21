import unittest
import urllib.parse
from unittest.mock import patch

from hunt.sources import (
    HIMALAYAS_PAGE,
    HIMALAYAS_PAGES,
    JOBICY_PAGES,
    fetch_adzuna,
    fetch_himalayas,
    fetch_jobicy,
    fetch_jooble,
)

SEARCH = {
    "keywords": ["AI Engineer", "Solutions Engineer"],
    "location": {"mode": "places", "countries": ["India"], "remote_only_countries": ["United States"]},
}


def himalayas_job(n: int) -> dict:
    return {
        "guid": f"https://himalayas.app/companies/acme/jobs/{n}",
        "title": f"AI Engineer {n}",
        "companyName": "Acme",
        "applicationLink": f"https://himalayas.app/companies/acme/jobs/{n}",
        "locationRestrictions": ["India"],
        "description": "<p>Ship agents.</p>",
        "pubDate": 1792587611,
    }


def jobicy_job(n: int) -> dict:
    return {
        "id": n,
        "jobTitle": f"AI Engineer {n}",
        "companyName": "Acme",
        "url": f"https://jobicy.com/jobs/{n}",
        "jobGeo": "USA",
        "jobDescription": "<p>Ship agents.</p>",
        "pubDate": "2026-10-21T11:05:02+00:00",
    }


def query_of(url: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class HimalayasTests(unittest.TestCase):
    def test_each_keyword_is_searched_newest_first_in_each_country(self):
        calls = []

        def fake(url, *, label, **_kwargs):
            calls.append(query_of(url))
            return {"jobs": [himalayas_job(len(calls))]}

        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_himalayas(SEARCH, {})
        self.assertEqual(
            [(call["q"], call["country"], call["page"]) for call in calls],
            [
                ("AI Engineer", "India", "1"),
                ("AI Engineer", "United States", "1"),
                ("Solutions Engineer", "India", "1"),
                ("Solutions Engineer", "United States", "1"),
            ],
        )
        self.assertTrue(all(call["sort"] == "recent" for call in calls))
        self.assertEqual(len(jobs), 4)
        self.assertEqual(jobs[0].posted_at, "2026-10-21")
        self.assertIn("Remote", jobs[0].location)

    def test_a_full_page_reads_the_next_one_and_rows_dedupe(self):
        calls = []

        def fake(url, *, label, **_kwargs):
            calls.append(query_of(url))
            return {"jobs": [himalayas_job(n) for n in range(HIMALAYAS_PAGE)]}

        search = {**SEARCH, "keywords": ["AI Engineer"], "location": {"mode": "any"}}
        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_himalayas(search, {})
        self.assertEqual([call["page"] for call in calls], [str(page) for page in range(1, HIMALAYAS_PAGES + 1)])
        self.assertNotIn("country", calls[0])
        self.assertEqual(len(jobs), HIMALAYAS_PAGE)


class JobicyTests(unittest.TestCase):
    def test_it_follows_the_cursor_for_a_few_pages(self):
        calls = []

        def fake(url, *, label, **_kwargs):
            calls.append(query_of(url))
            page = len(calls)
            return {"jobs": [jobicy_job(page)], "hasMore": True, "nextCursor": f"c{page}"}

        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_jobicy(SEARCH, {})
        self.assertEqual([call.get("cursor") for call in calls], [None] + [f"c{page}" for page in range(1, JOBICY_PAGES)])
        self.assertTrue(all(call["count"] == "100" for call in calls))
        self.assertEqual(len(jobs), JOBICY_PAGES)
        self.assertEqual(jobs[0].posted_at, "2026-10-21")

    def test_it_stops_when_there_is_no_more(self):
        calls = []

        def fake(url, *, label, **_kwargs):
            calls.append(url)
            return {"jobs": [jobicy_job(1)], "hasMore": False, "nextCursor": "c1"}

        with patch("hunt.sources.get_json", side_effect=fake):
            fetch_jobicy(SEARCH, {})
        self.assertEqual(len(calls), 1)


class KeyedFeedTests(unittest.TestCase):
    def test_adzuna_searches_each_keyword_newest_first(self):
        calls = []

        def fake(url, *, label, **_kwargs):
            calls.append((urllib.parse.urlsplit(url).path, query_of(url)))
            return {"results": [{"id": len(calls), "title": "AI Engineer", "redirect_url": f"https://adzuna.example/{len(calls)}"}]}

        search = {**SEARCH, "source_options": {"adzuna": {"country": "in"}}}
        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_adzuna(search, {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"})
        self.assertEqual([path for path, _query in calls], ["/v1/api/jobs/in/search/1"] * 2)
        self.assertEqual([query["what"] for _path, query in calls], ["AI Engineer", "Solutions Engineer"])
        self.assertTrue(all(query["sort_by"] == "date" for _path, query in calls))
        self.assertEqual(len(jobs), 2)

    def test_jooble_searches_each_keyword_in_each_country(self):
        bodies = []

        def fake(url, *, label, body=None, **_kwargs):
            bodies.append(body)
            return {"jobs": [{"id": len(bodies), "title": "AI Engineer", "link": f"https://jooble.example/{len(bodies)}"}]}

        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_jooble(SEARCH, {"JOOBLE_API_KEY": "key"})
        self.assertEqual(
            [(body["keywords"], body["location"]) for body in bodies],
            [
                ("AI Engineer", "India"),
                ("AI Engineer", "United States"),
                ("Solutions Engineer", "India"),
                ("Solutions Engineer", "United States"),
            ],
        )
        self.assertEqual(len(jobs), 4)


if __name__ == "__main__":
    unittest.main()
