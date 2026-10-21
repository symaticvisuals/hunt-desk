import unittest
from unittest.mock import patch

from hunt.sources import SourceError, fetch_board, fetch_description, fetch_workday

BOARD = {"company": "Acme", "platform": "workday", "tenant": "acme", "host": "wd5", "site": "External"}
SEARCH = {
    "keywords": ["AI Engineer", "Full Stack Developer"],
    "location": {"mode": "places", "countries": ["India"], "remote_only_countries": ["United States"]},
}
NESTED = [
    {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineering", "id": "eng"}]},
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locationHierarchy1",
                "descriptor": "Locations",
                "values": [
                    {"descriptor": "India", "id": "in-1"},
                    {"descriptor": "United States of America", "id": "us-1"},
                ],
            }
        ],
    },
]


def posting(n: int, place: str = "India, Pune") -> dict:
    return {"title": f"AI Engineer {n}", "externalPath": f"/job/x/AI-Engineer_R{n}", "locationsText": place, "postedOn": "Posted Today"}


class FakeWorkday:
    def __init__(self, facets, pages=None) -> None:
        self.facets = facets
        self.pages = pages or {}
        self.bodies = []

    def __call__(self, url, *, label, method="GET", body=None, **_kwargs):
        self.bodies.append(body)
        key = (body["searchText"], tuple(sorted((k, tuple(v)) for k, v in body["appliedFacets"].items())), body["offset"])
        postings, total = self.pages.get(key, ([], 0))
        reply = {"total": total if body["offset"] == 0 else 0, "jobPostings": postings}
        if body["offset"] == 0 and not body["searchText"] and not body["appliedFacets"]:
            reply["facets"] = self.facets
        return reply


class WorkdayTests(unittest.TestCase):
    def test_each_keyword_is_searched_inside_each_target_country(self):
        india = (("locationHierarchy1", ("in-1",)),)
        usa = (("locationHierarchy1", ("us-1",)),)
        fake = FakeWorkday(
            NESTED,
            {
                ("AI Engineer", india, 0): ([posting(n) for n in range(20)], 25),
                ("AI Engineer", india, 20): ([posting(n) for n in range(20, 25)], 0),
                ("Full Stack Developer", india, 0): ([posting(3), posting(99, "2 Locations")], 2),
                ("AI Engineer", usa, 0): ([posting(500, "Remote - USA")], 1),
            },
        )
        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_workday(BOARD, SEARCH)
        by_url = {job.url: job for job in jobs}
        self.assertEqual(len(jobs), 27)
        self.assertEqual(by_url["https://acme.wd5.myworkdayjobs.com/External/job/x/AI-Engineer_R99"].location, "India (2 locations)")
        self.assertEqual(by_url["https://acme.wd5.myworkdayjobs.com/External/job/x/AI-Engineer_R500"].location, "Remote - USA")
        self.assertTrue(all(job.big for job in jobs))
        searched = {(body["searchText"], str(body["appliedFacets"])) for body in fake.bodies if body["searchText"]}
        self.assertEqual(len(searched), 4)

    def test_a_board_without_a_country_filter_is_searched_globally(self):
        flat = [{"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineering", "id": "eng"}]}]
        fake = FakeWorkday(flat, {("AI Engineer", (), 0): ([posting(1, "Bengaluru Millenia")], 1)})
        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_workday(BOARD, SEARCH)
        self.assertEqual([job.location for job in jobs], ["Bengaluru Millenia"])
        self.assertTrue(all(body["appliedFacets"] == {} for body in fake.bodies))

    def test_one_missing_country_keeps_the_search_global(self):
        india_only = [{"facetParameter": "Location_Country", "values": [{"descriptor": "India", "id": "in-1"}]}]
        fake = FakeWorkday(india_only)
        with patch("hunt.sources.get_json", side_effect=fake):
            fetch_workday(BOARD, SEARCH)
        self.assertTrue(all(body["appliedFacets"] == {} for body in fake.bodies))

    def test_no_keywords_reads_the_board_from_the_top(self):
        fake = FakeWorkday(
            [],
            {
                ("", (), 0): ([posting(n) for n in range(20)], 70),
                ("", (), 20): ([posting(n) for n in range(20, 40)], 0),
                ("", (), 40): ([posting(n) for n in range(40, 60)], 0),
            },
        )
        with patch("hunt.sources.get_json", side_effect=fake):
            jobs = fetch_workday(BOARD)
        self.assertEqual(len(jobs), 60)
        self.assertEqual([body["offset"] for body in fake.bodies], [0, 20, 40])

    def test_only_workday_receives_the_search(self):
        seen = []
        with (
            patch("hunt.sources.fetch_workday", side_effect=lambda board, search=None: seen.append(("workday", search)) or []),
            patch.dict("hunt.sources.ATS_FETCH", {"greenhouse": lambda board: seen.append(("greenhouse", board["slug"])) or []}),
        ):
            fetch_board("workday", BOARD, SEARCH)
            fetch_board("greenhouse", {"slug": "acme"}, SEARCH)
        self.assertEqual(seen, [("workday", SEARCH), ("greenhouse", "acme")])


class DescriptionTests(unittest.TestCase):
    def test_detail_text_comes_from_the_cxs_job_endpoint(self):
        seen = []

        def fake(url, *, label, headers=None, **_kwargs):
            seen.append(url)
            return {"jobPostingInfo": {"jobDescription": "<p>Build <b>agents</b> with customers.</p>"}}

        row = {"platform": "workday", "url": "https://acme.wd5.myworkdayjobs.com/External/job/x/AI-Engineer_R1"}
        with patch("hunt.sources.get_json", side_effect=fake):
            text = fetch_description(row)
        self.assertEqual(seen, ["https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/x/AI-Engineer_R1"])
        self.assertEqual(text, "Build agents with customers.")

    def test_greenhouse_detail_and_other_platforms(self):
        with patch("hunt.sources.get_json", return_value={"content": "&lt;p&gt;Ship it.&lt;/p&gt;"}) as fake:
            self.assertEqual(fetch_description({"platform": "greenhouse", "board": "acme", "job_id": "7"}), "Ship it.")
        self.assertIn("boards/acme/jobs/7", fake.call_args[0][0])
        self.assertEqual(fetch_description({"platform": "linkedin", "url": "https://x.example/1"}), "")
        with patch("hunt.sources.get_json", side_effect=SourceError("HTTP 404 from workday")):
            with self.assertRaises(SourceError):
                fetch_description({"platform": "workday", "url": "https://acme.wd5.myworkdayjobs.com/External/job/x/R2"})


if __name__ == "__main__":
    unittest.main()
