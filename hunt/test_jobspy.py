import logging
import unittest
from unittest.mock import patch

from hunt.sources import SourceError, _jobspy_log, fetch_jobspy


class Frame:
    def __init__(self, rows) -> None:
        self._rows = rows

    def to_dict(self, orient: str):
        if orient != "records":
            raise AssertionError(orient)
        return self._rows


class JobSpyTests(unittest.TestCase):
    def test_rows_keep_the_apply_link_date_and_size(self):
        frame = Frame(
            [
                {
                    "id": "abc",
                    "site": "linkedin",
                    "title": "Software Engineer",
                    "company": "Acme",
                    "location": "Delhi",
                    "job_url": "https://www.linkedin.com/jobs/view/1",
                    "job_url_direct": "https://acme.example/apply",
                    "date_posted": "2026-10-01",
                    "description": "Build production systems. 8+ years required.",
                    "is_remote": False,
                    "company_num_employees": "1001-5000",
                },
                {
                    "id": "dup",
                    "site": "indeed",
                    "title": "Software Engineer",
                    "company": "Acme",
                    "location": "Delhi",
                    "job_url": "https://www.linkedin.com/jobs/view/1",
                    "job_url_direct": "",
                    "date_posted": "2026-10-02",
                    "description": "duplicate",
                    "is_remote": False,
                    "company_num_employees": None,
                },
            ]
        )
        with patch("hunt.sources._scrape_jobspy", return_value=(frame, [])) as scrape:
            jobs = fetch_jobspy(
                {"keywords": ["software engineer"], "location": {"mode": "any"}, "source_options": {}},
                {},
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].apply_url, "https://acme.example/apply")
        self.assertEqual(jobs[0].posted_at, "2026-10-01")
        self.assertTrue(jobs[0].big)
        self.assertEqual(jobs[0].source, "jobspy")
        self.assertNotIn("country_indeed", scrape.call_args.kwargs)
        self.assertTrue(scrape.call_args.kwargs["fetch_description"])

    def test_descriptions_can_be_turned_off(self):
        with patch("hunt.sources._scrape_jobspy", return_value=(Frame([]), [])) as scrape:
            fetch_jobspy(
                {
                    "keywords": ["engineer"],
                    "location": {"mode": "any"},
                    "source_options": {"jobspy": {"fetch_description": False}},
                },
                {},
            )
        self.assertFalse(scrape.call_args.kwargs["fetch_description"])

    def test_boards_verify_tls_against_certifi(self):
        import certifi

        with patch("hunt.sources._scrape_jobspy", return_value=(Frame([]), [])) as scrape:
            fetch_jobspy({"keywords": ["engineer"], "location": {"mode": "any"}, "source_options": {}}, {})
        self.assertEqual(scrape.call_args.kwargs["ca_cert"], certifi.where())

    def test_one_site_can_override_the_shared_options(self):
        options = {
            "results_wanted": 40,
            "site_options": {"linkedin": {"results_wanted": 10, "fetch_description": False}},
        }
        for site, wanted, describe in (("linkedin", 10, False), ("indeed", 40, True)):
            search = {
                "keywords": ["engineer"],
                "location": {"mode": "any"},
                "source_options": {"jobspy": {**options, "sites": [site]}},
            }
            with patch("hunt.sources._scrape_jobspy", return_value=(Frame([]), [])) as scrape:
                fetch_jobspy(search, {})
            self.assertEqual(scrape.call_args.kwargs["results_wanted"], wanted, site)
            self.assertEqual(scrape.call_args.kwargs["fetch_description"], describe, site)

    def test_the_board_experience_range_reaches_the_years_screen(self):
        from hunt.trim import years_stated

        frame = Frame(
            [
                {
                    "id": "nk-1",
                    "site": "naukri",
                    "title": "Forward Deployed Engineer",
                    "company": "Big Co",
                    "location": "Noida, India",
                    "job_url": "https://www.naukri.com/job-listings-1",
                    "description": "Work with customers on agents.",
                    "experience_range": "10-13 Yrs",
                }
            ]
        )
        with patch("hunt.sources._scrape_jobspy", return_value=(frame, [])):
            jobs = fetch_jobspy({"keywords": ["forward deployed"], "location": {"mode": "any"}}, {})
        self.assertIn("Work with customers", jobs[0].description)
        self.assertEqual(years_stated(jobs[0].description), 10)

    def test_pay_job_type_work_mode_and_size_are_kept(self):
        frame = Frame(
            [
                {
                    "id": "nk-2",
                    "site": "naukri",
                    "title": "AI Engineer",
                    "company": "Small Co",
                    "location": "Pune, India",
                    "job_url": "https://www.naukri.com/job-listings-2",
                    "description": "Ship agents.",
                    "min_amount": 1800000.0,
                    "max_amount": 2400000.0,
                    "currency": "INR",
                    "interval": "yearly",
                    "job_type": "fulltime",
                    "work_from_home_type": "Hybrid",
                    "company_num_employees": float("nan"),
                },
                {
                    "id": "li-3",
                    "site": "linkedin",
                    "title": "AI Engineer",
                    "company": "Other Co",
                    "location": "Delhi",
                    "job_url": "https://www.linkedin.com/jobs/view/3",
                    "min_amount": float("nan"),
                    "max_amount": None,
                    "company_num_employees": "51-200",
                },
            ]
        )
        with patch("hunt.sources._scrape_jobspy", return_value=(frame, [])):
            jobs = fetch_jobspy({"keywords": ["ai engineer"], "location": {"mode": "any"}}, {})
        naukri, linkedin = jobs
        self.assertEqual((naukri.pay_min, naukri.pay_max, naukri.pay_currency, naukri.pay_interval), (1800000.0, 2400000.0, "INR", "yearly"))
        self.assertEqual((naukri.job_type, naukri.work_mode, naukri.size_known), ("fulltime", "Hybrid", False))
        self.assertIsNone(linkedin.pay_min)
        self.assertTrue(linkedin.size_known)
        self.assertFalse(linkedin.big)

    def test_a_blocked_board_does_not_hide_the_other_rows(self):
        frame = Frame(
            [
                {
                    "id": "1",
                    "site": "indeed",
                    "title": "Engineer",
                    "company": "Small",
                    "location": "Remote",
                    "job_url": "https://www.indeed.com/viewjob?jk=1",
                    "job_url_direct": None,
                    "date_posted": None,
                    "description": "Ship it",
                    "is_remote": True,
                    "company_num_employees": "11-50",
                }
            ]
        )
        lines = []
        with patch("hunt.sources._scrape_jobspy", return_value=(frame, ["LinkedIn response status code 429"])):
            jobs = fetch_jobspy(
                {
                    "keywords": ["engineer"],
                    "location": {"mode": "remote_only"},
                    "source_options": {"jobspy": {"country_indeed": "India", "sites": ["indeed"]}},
                },
                {"JOBSPY_PROXIES": "localhost:8080"},
                on_progress=lines.append,
            )
        self.assertEqual(jobs[0].url, "https://www.indeed.com/viewjob?jk=1")
        self.assertFalse(jobs[0].big)
        self.assertIn("Remote", jobs[0].location)
        self.assertIn("error jobspy engineer: LinkedIn response status code 429", lines)

    def test_each_city_is_searched_and_reported(self):
        calls = []

        def scrape(**kwargs):
            calls.append(kwargs.get("location"))
            return Frame([]), []

        lines = []
        with patch("hunt.sources._scrape_jobspy", side_effect=scrape):
            fetch_jobspy(
                {
                    "keywords": ["engineer"],
                    "location": {"mode": "any"},
                    "source_options": {"jobspy": {"sites": ["naukri", "linkedin"], "locations": ["Delhi", "Pune"]}},
                },
                {},
                on_progress=lines.append,
            )
        self.assertEqual(calls, ["Delhi", "Pune"])
        self.assertIn("jobspy engineer @ Pune: 0 listings", lines)

    def test_every_board_failing_is_an_error(self):
        with patch("hunt.sources._scrape_jobspy", side_effect=RuntimeError("429")):
            with self.assertRaises(SourceError):
                fetch_jobspy({"keywords": ["engineer"], "location": {"mode": "any"}}, {})

    def test_jobspy_errors_reach_the_site_that_logged_them_and_never_the_terminal(self):
        import threading

        linkedin = logging.getLogger("JobSpy:LinkedIn")
        naukri = logging.getLogger("JobSpy:Naukri")
        for logger in (linkedin, naukri):
            logger.setLevel(logging.ERROR)
            logger.propagate = False
        console = logging.StreamHandler()
        naukri.handlers = [console]
        with _jobspy_log(["linkedin"]) as from_linkedin, _jobspy_log(["naukri"]) as from_naukri:
            worker = threading.Thread(target=lambda: linkedin.error("LinkedIn response status code 429"))
            worker.start()
            worker.join()
            naukri.error("Naukri response status code 406")
        self.assertEqual(from_linkedin, ["LinkedIn response status code 429"])
        self.assertEqual(from_naukri, ["Naukri response status code 406"])
        self.assertNotIn(console, naukri.handlers)

    def test_each_remote_country_gets_its_own_remote_search(self):
        calls = []

        def scrape(**kwargs):
            calls.append((kwargs["site_name"], kwargs.get("location"), kwargs.get("country_indeed"), kwargs.get("is_remote", False)))
            return Frame([]), []

        options = {"locations": ["Delhi"], "country_indeed": "India", "remote_countries": ["United States"]}
        for site in ("linkedin", "naukri"):
            with patch("hunt.sources._scrape_jobspy", side_effect=scrape):
                fetch_jobspy({"keywords": ["ai engineer"], "location": {"mode": "any"}, "source_options": {"jobspy": dict(options, sites=[site])}}, {})
        self.assertEqual(
            calls,
            [
                (["linkedin"], "Delhi", "India", False),
                (["linkedin"], "United States", "United States", True),
                (["naukri"], "Delhi", "India", False),
            ],
        )
