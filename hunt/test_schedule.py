import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hunt.schedule import (
    Outcome,
    due_units,
    in_quiet,
    jobspy_window,
    load_state,
    next_state,
    tick,
)
from hunt.search import SearchResult, UnitOutcome
from hunt.sources import Job
from hunt.store import Busy, load_inbox, run_lock, save_inbox

IST = timezone(timedelta(hours=5, minutes=30))
NOON = datetime(2026, 10, 15, 12, 0, tzinfo=IST)


class Fixed(random.Random):
    def uniform(self, a, b):
        return 1.0


class PolicyTests(unittest.TestCase):
    def test_new_roles_shorten_the_interval_down_to_the_floor(self):
        state = {}
        for _ in range(6):
            state = next_state("greenhouse", state, Outcome(new=2, blocked=False), NOON, Fixed())
        self.assertEqual(state["interval_minutes"], 30)
        self.assertEqual(state["next_at"], (NOON + timedelta(minutes=30)).isoformat())

    def test_three_empty_runs_lengthen_it_up_to_the_ceiling(self):
        state = {}
        for _ in range(2):
            state = next_state("jobspy:naukri", state, Outcome(new=0, blocked=False), NOON, Fixed())
        self.assertEqual(state["interval_minutes"], 240)
        state = next_state("jobspy:naukri", state, Outcome(new=0, blocked=False), NOON, Fixed())
        self.assertEqual(state["interval_minutes"], 360)
        for _ in range(30):
            state = next_state("jobspy:naukri", state, Outcome(new=0, blocked=False), NOON, Fixed())
        self.assertEqual(state["interval_minutes"], 960)

    def test_a_block_doubles_the_wait_and_a_clean_run_resets_it(self):
        state = next_state("jobspy:linkedin", {}, Outcome(new=0, blocked=True, error="429"), NOON, Fixed())
        self.assertEqual(state["next_at"], (NOON + timedelta(minutes=480)).isoformat())
        state = next_state("jobspy:linkedin", state, Outcome(new=0, blocked=True), NOON, Fixed())
        self.assertEqual(state["next_at"], (NOON + timedelta(minutes=960)).isoformat())
        for _ in range(5):
            state = next_state("jobspy:linkedin", state, Outcome(new=0, blocked=True), NOON, Fixed())
        self.assertEqual(state["next_at"], (NOON + timedelta(hours=24)).isoformat())
        self.assertNotIn("last_clean_run", state)
        state = next_state("jobspy:linkedin", state, Outcome(new=1, blocked=False), NOON, Fixed())
        self.assertEqual(state["blocked_runs"], 0)
        self.assertEqual(state["last_clean_run"], NOON.isoformat())

    def test_jitter_stays_within_ten_percent(self):
        rng = random.Random(7)
        for _ in range(50):
            state = next_state("greenhouse", {}, Outcome(new=0, blocked=False), NOON, rng)
            gap = datetime.fromisoformat(state["next_at"]) - NOON
            self.assertTrue(timedelta(minutes=54) <= gap <= timedelta(minutes=66))

    def test_jobspy_asks_only_for_what_is_new(self):
        self.assertEqual(jobspy_window({}, NOON), 48)
        self.assertEqual(jobspy_window({"last_clean_run": (NOON - timedelta(hours=4, minutes=10)).isoformat()}, NOON), 7)
        self.assertEqual(jobspy_window({"last_clean_run": (NOON - timedelta(minutes=30)).isoformat()}, NOON), 6)
        self.assertEqual(jobspy_window({"last_clean_run": (NOON - timedelta(days=10)).isoformat()}, NOON), 168)

    def test_quiet_hours_cross_midnight(self):
        quiet = {"start": "23:00", "end": "07:00"}
        self.assertTrue(in_quiet(NOON.replace(hour=23, minute=30), quiet))
        self.assertTrue(in_quiet(NOON.replace(hour=6, minute=59), quiet))
        self.assertFalse(in_quiet(NOON.replace(hour=7), quiet))
        self.assertFalse(in_quiet(NOON, quiet))
        self.assertTrue(in_quiet(NOON.replace(hour=13), {"start": "12:00", "end": "14:00"}))

    def test_units_without_state_or_past_due_are_due(self):
        state = {"units": {"greenhouse": {"next_at": (NOON + timedelta(minutes=5)).isoformat()}, "lever": {"next_at": NOON.isoformat()}}}
        self.assertEqual(due_units(state, ["greenhouse", "lever", "ashby"], NOON), ["lever", "ashby"])


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    (root / "profile/search.json").write_text(
        json.dumps(
            {
                "keywords": ["engineer"],
                "exclude_title": [],
                "location": {"mode": "any"},
                "sources": ["greenhouse", "jobspy"],
                "source_options": {"jobspy": {"sites": ["naukri", "linkedin"]}},
            }
        ),
        encoding="utf-8",
    )
    (root / "profile/rubric.json").write_text(json.dumps({"max_years_without_cap": 5, "screenout_cap": 75}), encoding="utf-8")
    return root


def _job(n: int, unit: str = "greenhouse") -> Job:
    platform = unit.split(":")[-1]
    return Job("Acme", f"Engineer {n}", f"https://{platform}.example/{n}", platform, str(n), "Remote", "Build agents.", unit.split(":")[0], posted_at="2026-10-15")


def _collected(*jobs: Job, blocked: dict | None = None) -> SearchResult:
    result = SearchResult()
    result.jobs = list(jobs)
    for job in jobs:
        unit = "greenhouse" if job.source == "greenhouse" else f"jobspy:{job.platform}"
        outcome = result.units.setdefault(unit, UnitOutcome())
        outcome.found += 1
        outcome.urls.append(job.url)
    for unit, error in (blocked or {}).items():
        result.units.setdefault(unit, UnitOutcome()).errors.append(error)
    result.matches = list(jobs)
    return result


class TickTests(unittest.TestCase):
    def setUp(self):
        self.root = _root()
        self.notified = []
        self.classified = []

    def classify(self, jobs, report):
        from hunt.gate import load_cache, save_cache

        cache = load_cache(self.root)
        for job in jobs:
            self.classified.append(job["url"])
            cache[job["url"]] = {"url": job["url"], "keep": True, "fit": 3.5, "described": True}
        save_cache(self.root, cache)

    def tick(self, now=NOON, collected=None, **kwargs):
        collected = collected or _collected()
        with patch("hunt.schedule.collect", return_value=collected) as fetch:
            result = tick(
                self.root,
                {},
                now=now,
                classifier=self.classify,
                checker=lambda url: "open",
                notifier=lambda root, jobs: self.notified.append([job["url"] for job in jobs]),
                rng=Fixed(),
                **kwargs,
            )
        return result, fetch

    def test_quiet_hours_skip_the_run(self):
        result, fetch = self.tick(now=NOON.replace(hour=2))
        self.assertEqual(result.skipped, "quiet hours")
        fetch.assert_not_called()

    def test_nothing_due_does_no_work(self):
        self.tick(collected=_collected(_job(1)))
        result, fetch = self.tick(now=NOON + timedelta(minutes=10))
        self.assertEqual(result.skipped, "nothing due")
        fetch.assert_not_called()

    def test_an_idle_tick_with_a_key_and_a_budget_still_scores(self):
        self.tick(collected=_collected(_job(1)))
        budget = {"classify_usd_per_day": 0.5, "score_usd_per_day": 3.0}
        (self.root / "profile/preferences.json").write_text(json.dumps({"confirmed": True, "budget": budget}), encoding="utf-8")
        with patch("hunt.pipeline.score_stage", return_value="") as scored, patch("hunt.pipeline.tailor_stage", return_value=""):
            result = tick(self.root, {"ANTHROPIC_API_KEY": "sk-ant-x"}, now=NOON + timedelta(minutes=10))
        self.assertEqual(result.skipped, "nothing due")
        scored.assert_called_once()

    def test_a_held_run_lock_skips_quietly(self):
        with run_lock(self.root):
            result, fetch = self.tick()
        self.assertEqual(result.skipped, "a check is already running")

    def test_a_run_merges_classifies_checks_and_records_each_unit(self):
        collected = _collected(_job(1), _job(2, "jobspy:naukri"), blocked={"jobspy:linkedin": "LinkedIn response status code 429"})
        result, fetch = self.tick(collected=collected)
        self.assertEqual(sorted(fetch.call_args.kwargs["units"]), ["greenhouse", "jobspy:linkedin", "jobspy:naukri"])
        self.assertEqual(fetch.call_args.kwargs["windows"], {"jobspy:naukri": 48, "jobspy:linkedin": 48})
        self.assertEqual(len(load_inbox(self.root)), 2)
        self.assertEqual(sorted(self.classified), ["https://greenhouse.example/1", "https://naukri.example/2"])
        units = load_state(self.root)["units"]
        self.assertEqual(units["greenhouse"]["last_new"], 1)
        self.assertEqual(units["jobspy:linkedin"]["blocked_runs"], 1)
        self.assertTrue(all(row["live"] == "open" for row in load_inbox(self.root)))
        self.assertFalse((self.root / "data/last-search.json").exists())
        self.assertIn("greenhouse", (self.root / "data/schedule.log").read_text())

    def test_the_first_run_marks_roles_without_announcing_them(self):
        self.tick(collected=_collected(_job(1)))
        self.assertEqual(self.notified, [])
        self.assertTrue(load_inbox(self.root)[0]["first_visible_at"])

    def test_only_roles_newly_on_the_main_list_are_announced(self):
        self.tick(collected=_collected(_job(1)))
        later = NOON + timedelta(hours=5)
        self.tick(now=later, collected=_collected(_job(1), _job(3)))
        self.assertEqual(self.notified, [["https://greenhouse.example/3"]])
        self.tick(now=later + timedelta(hours=5), collected=_collected(_job(1), _job(3)))
        self.assertEqual(len(self.notified), 1)

    def test_a_full_run_writes_last_search_and_ignores_quiet_hours(self):
        from hunt.schedule import run

        with patch("hunt.schedule.collect", return_value=_collected(_job(1))) as fetch:
            result = run(self.root, {}, now=NOON.replace(hour=2), full=True, classifier=self.classify, checker=lambda url: "open", rng=Fixed())
        self.assertIsNone(fetch.call_args.kwargs["units"])
        self.assertTrue((self.root / "data/last-search.json").exists())
        self.assertEqual(len(result.added), 1)

    def test_run_refuses_while_another_holds_the_lock(self):
        from hunt.schedule import run

        with run_lock(self.root):
            with self.assertRaises(Busy):
                run(self.root, {}, now=NOON, full=True)


class ParallelFetchTests(unittest.TestCase):
    def test_units_are_fetched_at_the_same_time_and_reported_in_order(self):
        import time

        from hunt.search import collect

        root = _root()
        (root / "data/watchlist.json").write_text("[]", encoding="utf-8")

        def slow(unit, search, env, watchlist, report, window):
            time.sleep(0.3)
            return [_job(int(time.time() * 1000) % 1000, "jobspy:" + unit.split(":")[-1] if unit.startswith("jobspy") else unit)], []

        start = time.time()
        with patch("hunt.search._fetch_unit", side_effect=slow):
            result = collect(root, {}, units=["greenhouse", "jobspy:naukri", "jobspy:linkedin"])
        self.assertLess(time.time() - start, 0.75)
        self.assertEqual(list(result.units), ["greenhouse", "jobspy:naukri", "jobspy:linkedin"])
        summary = [line for line in result.lines if line.endswith("in your places")]
        self.assertEqual([line.split(":")[0] if not line.startswith("jobspy") else ":".join(line.split(":")[:2]) for line in summary], ["greenhouse", "jobspy:naukri", "jobspy:linkedin"])

    def test_live_checks_run_together(self):
        import time

        from hunt.schedule import _check_live
        from hunt.store import merge_into_inbox

        root = _root()
        merge_into_inbox(root, [_job(n).as_dict() for n in range(8)], NOON)
        from hunt.gate import save_cache

        save_cache(root, {f"https://greenhouse.example/{n}": {"keep": True, "fit": 3.5} for n in range(8)})

        def slow(url):
            time.sleep(0.25)
            return "open"

        start = time.time()
        _check_live(root, NOON, lambda line: None, slow)
        self.assertLess(time.time() - start, 0.9)
        self.assertTrue(all(row["live"] == "open" for row in load_inbox(root)))


class DescribeTests(unittest.TestCase):
    def test_describe_fills_or_counts(self):
        from hunt.schedule import _describe
        from hunt.sources import SourceError

        root = _root()
        search = json.loads((root / "profile/search.json").read_text())
        search["location"] = {"mode": "places", "countries": ["India"], "work_modes": ["onsite", "hybrid", "remote"]}
        (root / "profile/search.json").write_text(json.dumps(search))
        rows = [
            {"company": "Acme", "title": "Engineer 1", "url": "https://wd.example/1", "platform": "workday", "location": "Pune, India", "description": ""},
            {"company": "Acme", "title": "Engineer 2", "url": "https://wd.example/2", "platform": "workday", "location": "Pune, India", "description": ""},
            {"company": "Acme", "title": "Engineer 3", "url": "https://wd.example/3", "platform": "workday", "location": "London, UK", "description": ""},
            {"company": "Acme", "title": "Engineer 4", "url": "https://li.example/4", "platform": "linkedin", "location": "Pune, India", "description": ""},
        ]
        save_inbox(root, rows)
        asked = []

        def fetcher(row):
            asked.append(row["url"])
            if row["url"].endswith("/1"):
                return "Build agents with customers."
            raise SourceError("HTTP 500 from workday")

        filled = _describe(root, lambda line: None, fetcher=fetcher)
        self.assertEqual(filled, 1)
        self.assertEqual(sorted(asked), ["https://wd.example/1", "https://wd.example/2"])
        by_url = {row["url"]: row for row in load_inbox(root)}
        self.assertEqual(by_url["https://wd.example/1"]["description"], "Build agents with customers.")
        self.assertEqual(by_url["https://wd.example/2"]["description_tries"], 1)
        self.assertNotIn("description_tries", by_url["https://wd.example/3"])

        by_url["https://wd.example/2"]["description_tries"] = 2
        save_inbox(root, list(by_url.values()))
        asked.clear()
        _describe(root, lambda line: None, fetcher=fetcher)
        self.assertEqual(asked, [])


class DescribeFailureTests(unittest.TestCase):
    def test_any_fetch_failure_counts_a_try_and_the_rest_continue(self):
        from hunt.schedule import _describe

        root = _root()
        rows = [
            {"company": "Acme", "title": f"Engineer {n}", "url": f"https://wd.example/{n}", "platform": "workday", "location": "Remote", "description": ""}
            for n in (1, 2)
        ]
        save_inbox(root, rows)

        def fetcher(row):
            if row["url"].endswith("/1"):
                raise TimeoutError("The read operation timed out")
            return "Build agents."

        lines = []
        self.assertEqual(_describe(root, lines.append, fetcher=fetcher), 1)
        by_url = {row["url"]: row for row in load_inbox(root)}
        self.assertEqual(by_url["https://wd.example/1"]["description_tries"], 1)
        self.assertEqual(by_url["https://wd.example/2"]["description"], "Build agents.")
        self.assertTrue(any("timed out" in line for line in lines))

    def test_detail_fetches_use_a_short_timeout(self):
        from hunt.sources import fetch_description

        with patch("hunt.sources.get_json", return_value={"jobPostingInfo": {"jobDescription": "Build."}}) as fake:
            fetch_description({"platform": "workday", "url": "https://acme.wd5.myworkdayjobs.com/External/job/x/R1"})
        self.assertEqual(fake.call_args.kwargs["timeout"], 10)


class StoreEveryMatchTests(unittest.TestCase):
    def test_title_matches_are_kept_for_the_inbox_and_screened_for_last_search(self):
        from hunt.search import collect

        root = _root()
        search = json.loads((root / "profile/search.json").read_text())
        search["location"] = {"mode": "places", "cities": [], "countries": ["India"], "remote_only_countries": ["United States"], "work_modes": ["onsite", "hybrid", "remote"]}
        (root / "profile/search.json").write_text(json.dumps(search))
        (root / "data/watchlist.json").write_text("[]", encoding="utf-8")
        onsite_us = Job("Acme", "Engineer 1", "https://gh.example/1", "greenhouse", "1", "New York, NY", "Build agents.", "greenhouse", posted_at="2026-10-15")
        big_years = Job("Bigco", "Engineer 2", "https://gh.example/2", "greenhouse", "2", "Pune, India", "10+ years required.", "greenhouse", posted_at="2026-10-15", big=True)
        fine = Job("Acme", "Engineer 3", "https://gh.example/3", "greenhouse", "3", "Bengaluru, Karnataka", "Build agents.", "greenhouse", posted_at="2026-10-15")
        wrong_title = Job("Acme", "Account Executive", "https://gh.example/4", "greenhouse", "4", "Pune", "Sell.", "greenhouse", posted_at="2026-10-15")
        with patch("hunt.search._fetch_unit", return_value=([onsite_us, big_years, fine, wrong_title], [])):
            result = collect(root, {}, units=["greenhouse"])
        self.assertEqual(sorted(job.url for job in result.matches), ["https://gh.example/1", "https://gh.example/2", "https://gh.example/3"])
        self.assertEqual([job.url for job in result.jobs], ["https://gh.example/3"])

    def test_a_run_appends_every_match(self):
        from hunt.schedule import run

        root = _root()
        onsite_us = _job(1)
        onsite_us.location = "New York, NY"
        collected = _collected(onsite_us, _job(2))
        collected.matches = list(collected.jobs)
        collected.jobs = [collected.jobs[1]]
        with patch("hunt.schedule.collect", return_value=collected):
            run(root, {}, now=NOON, full=True, classifier=lambda jobs, report: None, checker=lambda url: "open", rng=Fixed())
        self.assertEqual(sorted(row["url"] for row in load_inbox(root)), ["https://greenhouse.example/1", "https://greenhouse.example/2"])


class BatchPollTests(unittest.TestCase):
    def test_a_tick_applies_finished_batches_even_when_no_board_is_due(self):
        root = _root()
        posted = []
        with patch("hunt.schedule.collect", return_value=_collected(_job(1))):
            tick(root, {}, now=NOON, classifier=lambda jobs, report: None, checker=lambda url: "open", notifier=lambda r, j: None, rng=Fixed())
        with (
            patch("hunt.batch.open_batches", return_value=[{"id": "msgbatch_1"}]),
            patch("hunt.batch.poll", return_value=["Batch scored 4 roles: 2 above 75. Tailoring those now in a second batch."]),
            patch("hunt.notify.post", side_effect=lambda root, body: posted.append(body)),
        ):
            result = tick(root, {}, now=NOON + timedelta(minutes=10))
        self.assertEqual(result.skipped, "nothing due")
        self.assertEqual(posted, ["Batch scored 4 roles: 2 above 75. Tailoring those now in a second batch."])
        self.assertIn("Batch scored 4 roles", (root / "data/schedule.log").read_text())
