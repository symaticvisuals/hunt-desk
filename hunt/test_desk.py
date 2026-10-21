import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.evaluated import read_csv
from hunt.inbox import hidden_jobs, load_jobs, mark_applied, upsert_env, visible_jobs
from hunt.screen import context


def _root() -> Path:
    directory = Path(tempfile.mkdtemp())
    (directory / "profile").mkdir()
    (directory / "data").mkdir()
    (directory / "profile/search.json").write_text(
        json.dumps(
            {
                "keywords": ["engineer"],
                "exclude_title": ["intern"],
                "location": {"mode": "any"},
                "sources": [],
            }
        ),
        encoding="utf-8",
    )
    (directory / "profile/rubric.json").write_text(json.dumps({"natural_hire_at": 80, "comp": {}}), encoding="utf-8")
    (directory / "profile/og-resume.md").write_text(
        "Shipped production systems with customer teams across healthcare and construction for several years.\n",
        encoding="utf-8",
    )
    (directory / "profile/preferences.json").write_text(
        json.dumps(
            {
                "confirmed": True,
                "roles": ["engineer"],
                "seniority": [],
                "exclude_titles": ["intern"],
                "budget": {"classify_usd_per_day": 0.5, "score_usd_per_day": 3.0},
            }
        ),
        encoding="utf-8",
    )
    (directory / "data/watchlist.json").write_text("[]\n", encoding="utf-8")
    (directory / "data/evaluated-jobs.csv").write_text(
        "date,company,role,base_score,penalties,final_score,applied,reached_interview,status,platform,job_id,url,source,location\n",
        encoding="utf-8",
    )
    (directory / "data/clef-cache.json").write_text(
        json.dumps(
            {
                "https://example.com/1": {"keep": True, "fit": 4, "worth": 0.9, "fit_label": "A natural hire if the resume is true"},
                "https://example.com/2": {"keep": True, "fit": 3, "worth": 0.8, "fit_label": "A plausible match"},
                "https://example.com/3": {"keep": True, "fit": 1, "worth": 0.2, "fit_label": "Same field, wrong role"},
            }
        ),
        encoding="utf-8",
    )
    jobs = [
        ("Software Engineer", "1", "Build the product."),
        ("Accountant", "2", "Close the books."),
        ("Support Engineer", "3", "Answer tickets."),
    ]
    (directory / "data/last-search.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "company": "Acme",
                        "title": title,
                        "url": f"https://example.com/{number}",
                        "platform": "greenhouse",
                        "job_id": number,
                        "location": "Remote",
                        "description": text,
                        "source": "greenhouse",
                    }
                    for title, number, text in jobs
                ]
            }
        ),
        encoding="utf-8",
    )
    return directory


class InboxTests(unittest.TestCase):
    def test_the_main_list_keeps_only_roles_that_matter(self):
        root = _root()
        jobs = load_jobs(root)
        ctx = context(root)
        self.assertEqual([job["title"] for job in visible_jobs(jobs, ctx)], ["Software Engineer"])
        reasons = {job["title"]: reason for job, reason in hidden_jobs(jobs, ctx)}
        self.assertEqual(reasons, {"Accountant": "not a target role", "Support Engineer": "stretch"})

    def test_the_last_search_seeds_the_inbox(self):
        root = _root()
        load_jobs(root)
        self.assertTrue((root / "data/inbox.json").exists())

    def test_mark_applied_keeps_the_row(self):
        root = _root()
        mark_applied(root, load_jobs(root)[0])
        jobs = load_jobs(root)
        self.assertEqual(jobs[0]["applied"], "Yes")
        tracker = (root / "data/job-tracker.csv").read_text(encoding="utf-8")
        self.assertIn("Applied", tracker)

    def test_undo_applied_drops_a_row_that_only_recorded_the_apply(self):
        from hunt.inbox import unmark_applied

        root = _root()
        job = load_jobs(root)[0]
        mark_applied(root, job)
        unmark_applied(root, job)
        self.assertEqual(read_csv(root / "data/evaluated-jobs.csv"), [])
        self.assertNotIn(job["url"], (root / "data/job-tracker.csv").read_text(encoding="utf-8"))
        self.assertEqual([found["title"] for found in visible_jobs(load_jobs(root), context(root))], ["Software Engineer"])

    def test_undo_applied_keeps_the_score(self):
        from hunt.inbox import unmark_applied

        root = _root()
        with (root / "data/evaluated-jobs.csv").open("a", encoding="utf-8") as handle:
            handle.write("2026-10-15,Acme,Software Engineer,84,,82,,,,greenhouse,1,https://example.com/1,greenhouse,Remote\n")
        job = load_jobs(root)[0]
        mark_applied(root, job)
        unmark_applied(root, job)
        rows = read_csv(root / "data/evaluated-jobs.csv")
        self.assertEqual([(row["final_score"], row["applied"], row["status"]) for row in rows], [("82", "", "")])

    def test_env_blank_does_not_wipe_a_key(self):
        root = _root()
        path = root / ".env"
        path.write_text("JOOBLE_API_KEY=keep-me\n", encoding="utf-8")
        upsert_env(path, {"JOOBLE_API_KEY": "  ", "REED_API_KEY": "new-key"})
        text = path.read_text(encoding="utf-8")
        self.assertIn("JOOBLE_API_KEY=keep-me", text)
        self.assertIn("REED_API_KEY=new-key", text)

    def test_missing_claude_key_is_stored_when_supplied(self):
        from hunt.inbox import anthropic_key_present, store_anthropic_key

        root = _root()
        previous = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertFalse(anthropic_key_present(root))
            store_anthropic_key(root, "  ")
            self.assertFalse((root / ".env").exists())
            store_anthropic_key(root, "sk-ant-test")
            self.assertTrue(anthropic_key_present(root))
            self.assertIn("ANTHROPIC_API_KEY=sk-ant-test", (root / ".env").read_text(encoding="utf-8"))
        finally:
            if previous is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = previous


def _local_proposal(root):
    from hunt.preferences import migrate

    return migrate(root), "Filled locally for the test."


def _pilot(root: Path, script, *, key: bool = True) -> None:
    from hunt.desk import HuntApp
    from hunt.probe import Probe

    previous = os.environ.pop("ANTHROPIC_API_KEY", None)
    if key:
        (root / ".env").write_text("ANTHROPIC_API_KEY=present\n", encoding="utf-8")
    app = HuntApp(root)

    async def run() -> None:
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await script(app, pilot)

    try:
        with (
            patch("hunt.launchd.installed", return_value=True),
            patch("hunt.schedule.run", side_effect=RuntimeError("no network in tests")),
            patch("hunt.preferences.propose", side_effect=_local_proposal),
            patch("hunt.probe.run_probe", return_value=Probe(done=True)),
        ):
            asyncio.run(run())
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if previous is not None:
            os.environ["ANTHROPIC_API_KEY"] = previous


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


class DeskPilotTests(unittest.TestCase):
    def test_a_missing_resume_opens_step_one(self):
        from hunt.steps import ResumeStep

        root = _root()
        (root / "profile/og-resume.md").write_text("PLACEHOLDER_RESUME\n", encoding="utf-8")

        async def script(app, pilot):
            self.assertIsInstance(app.screen, ResumeStep)

        _pilot(root, script)

    def test_no_key_on_a_fresh_clone_opens_the_key_step_first(self):
        from hunt.steps import KeyStep

        root = _root()
        (root / "profile/og-resume.md").write_text("PLACEHOLDER_RESUME\n", encoding="utf-8")

        async def script(app, pilot):
            self.assertIsInstance(app.screen, KeyStep)

        _pilot(root, script, key=False)

    def test_a_confirmed_checkout_without_a_budget_sees_the_caps_once(self):
        from hunt.preferences import budget_set
        from hunt.steps import BudgetScreen

        root = _root()
        prefs = json.loads((root / "profile/preferences.json").read_text())
        prefs.pop("budget")
        (root / "profile/preferences.json").write_text(json.dumps(prefs), encoding="utf-8")

        async def first(app, pilot):
            self.assertIsInstance(app.screen, BudgetScreen)
            await pilot.click("#save-budget")
            await _settle(app, pilot)
            self.assertNotIsInstance(app.screen, BudgetScreen)

        async def second(app, pilot):
            self.assertNotIsInstance(app.screen, BudgetScreen)

        _pilot(root, first)
        self.assertTrue(budget_set(root))
        _pilot(root, second)

    def test_use_as_is_saves_the_resume_and_moves_to_step_two(self):
        from textual.widgets import TextArea

        from hunt.steps import PreferencesStep, ResumeStep

        root = _root()
        (root / "profile/og-resume.md").write_text("PLACEHOLDER_RESUME\n", encoding="utf-8")
        resume = "Ada Lovelace. Forward deployed engineer who shipped agents for healthcare customers, 2019 to 2025.\n"

        async def script(app, pilot):
            self.assertIsInstance(app.screen, ResumeStep)
            app.screen.query_one("#resume-input", TextArea).load_text(resume)
            await pilot.click("#keep")
            await _settle(app, pilot)
            self.assertIsInstance(app.screen, PreferencesStep)
            self.assertIn("Forward deployed engineer", (root / "profile/og-resume.md").read_text())

        _pilot(root, script)

    def test_a_rework_that_leaves_the_master_alone_stays_on_step_one(self):
        from textual.widgets import TextArea

        from hunt.claude_client import WorkspaceLookup
        from hunt.rewrite_api import RewriteResult
        from hunt.steps import ResumeStep

        root = _root()
        (root / "profile/og-resume.md").write_text("PLACEHOLDER_RESUME\n", encoding="utf-8")
        resume = "Ada Lovelace. Forward deployed engineer who shipped agents for healthcare customers, 2019 to 2025.\n"
        result = RewriteResult(
            summary="Rewrite passed the fact check.",
            path=root / "data/resume-rewrite.md",
            clean=True,
            paused=False,
            next_steps="The draft still has [metric], so profile/og-resume.md was left unchanged.",
            saved=False,
        )

        async def script(app, pilot):
            app.screen.query_one("#resume-input", TextArea).load_text(resume)
            with (
                patch("hunt.claude_client.lookup_workspace", return_value=WorkspaceLookup(selected="ws")),
                patch("hunt.rewrite_api.run_resume_rewrite", return_value=result),
            ):
                await pilot.click("#rework")
                await _settle(app, pilot)
                await _settle(app, pilot)
            self.assertIsInstance(app.screen, ResumeStep)
            self.assertFalse(app.screen.query_one("#rework").disabled)

        _pilot(root, script)

    def test_a_workflow_rewrite_says_when_the_master_was_left_alone(self):
        from textual.widgets import RichLog

        from hunt.desk import WorkflowScreen
        from hunt.rewrite_api import RewriteResult

        root = _root()
        result = RewriteResult(
            summary="Rewrite passed the fact check.",
            path=root / "data/resume-rewrite.md",
            clean=True,
            paused=False,
            next_steps="The draft still has [metric], so profile/og-resume.md was left unchanged.",
            saved=False,
        )

        async def script(app, pilot):
            app.push_screen(WorkflowScreen(root))
            await _settle(app, pilot)
            with (
                patch("hunt.rewrite_api.run_resume_rewrite", return_value=result),
                patch("hunt.follow.run_after_rewrite", return_value="Search found 3 roles."),
            ):
                app.screen._start_claude("api", {"resume": "profile/og-resume.md"})
                await _settle(app, pilot)
            shown = " ".join(" ".join(line.text for line in app.screen.query_one("#wf-progress", RichLog).lines).split())
            self.assertIn("left unchanged", shown)
            self.assertIn("Search found 3 roles.", shown)

        _pilot(root, script)

    def test_unconfirmed_preferences_ask_for_the_key_then_save(self):
        from textual.widgets import TextArea

        from hunt.desk import AnthropicKeyScreen
        from hunt.steps import PreferencesStep

        root = _root()
        (root / "profile/preferences.json").unlink()

        async def script(app, pilot):
            self.assertIsInstance(app.screen, AnthropicKeyScreen)
            await pilot.press("escape")
            await _settle(app, pilot)
            self.assertIsInstance(app.screen, PreferencesStep)
            self.assertIn("engineer", app.screen.query_one("#pref-roles", TextArea).text)
            await pilot.click("#save-prefs")
            await _settle(app, pilot)
            saved = json.loads((root / "profile/preferences.json").read_text())
            self.assertTrue(saved["confirmed"])
            self.assertEqual(saved["budget"], {"classify_usd_per_day": 0.5, "score_usd_per_day": 3.0})
            search = json.loads((root / "profile/search.json").read_text())
            self.assertEqual(search["location"]["mode"], "places")
            self.assertNotIsInstance(app.screen, PreferencesStep)

        _pilot(root, script, key=False)

    def test_the_main_list_dismisses_and_explains_hidden_roles(self):
        from textual.widgets import Static

        root = _root()

        async def script(app, pilot):
            self.assertIn("Software Engineer", str(app.query_one("#detail-title", Static).render()))
            self.assertEqual(len(app.shown), 1)
            await pilot.press("x")
            await pilot.pause()
            self.assertEqual(app.shown, [])
            await pilot.press("h")
            await pilot.pause()
            reasons = {job["title"]: reason for job, reason in app.hidden}
            self.assertEqual(reasons["Software Engineer"], "dismissed")
            self.assertEqual(reasons["Accountant"], "not a target role")
            self.assertIn("Hidden roles", str(app.query_one("#summary", Static).render()))

        _pilot(root, script)

    def test_tab_shows_applied_roles_and_u_undoes_one(self):
        root = _root()

        async def script(app, pilot):
            await pilot.press("u")
            await pilot.pause()
            self.assertEqual([job["title"] for job in app.shown], ["Software Engineer"])
            await pilot.press("a")
            await pilot.pause()
            self.assertEqual(app.shown, [])
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual([job["title"] for job in app.shown], ["Software Engineer"])
            await pilot.press("u")
            await pilot.pause()
            self.assertEqual(app.shown, [])
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual([job["title"] for job in app.shown], ["Software Engineer"])

        _pilot(root, script)

    def test_roles_the_schedule_adds_appear_without_moving_the_cursor(self):
        from datetime import datetime, timezone

        from textual.widgets import RichLog

        from hunt.store import merge_into_inbox

        root = _root()
        now = datetime.now(timezone.utc)
        fresh = {
            "company": "Beta",
            "title": "Platform Engineer",
            "url": "https://example.com/9",
            "platform": "greenhouse",
            "job_id": "9",
            "location": "Remote",
            "description": "Run the platform.",
            "source": "greenhouse",
            "posted_at": now.isoformat(),
        }

        async def script(app, pilot):
            self.assertEqual([job["title"] for job in app.shown], ["Software Engineer"])
            cache = json.loads((root / "data/clef-cache.json").read_text())
            cache[fresh["url"]] = {"keep": True, "fit": 4}
            (root / "data/clef-cache.json").write_text(json.dumps(cache))
            merge_into_inbox(root, [fresh], now)
            app._refresh_if_changed()
            await pilot.pause()
            self.assertEqual([job["title"] for job in app.shown], ["Platform Engineer", "Software Engineer"])
            self.assertEqual(app._selected()["title"], "Software Engineer")
            log = app.query_one("#activity", RichLog)
            self.assertTrue(any("1 new role" in line.text for line in log.lines))

        _pilot(root, script)


class ParallelScoreDeskTests(unittest.TestCase):
    def test_two_roles_score_at_once_and_the_desk_stays_usable(self):
        import threading

        from textual.widgets import Static

        from hunt.cli import Scored

        root = _root()
        cache = json.loads((root / "data/clef-cache.json").read_text())
        cache["https://example.com/3"]["fit"] = 4
        (root / "data/clef-cache.json").write_text(json.dumps(cache))
        release = threading.Event()
        started = []

        def slow_score(_root, job, **_kwargs):
            started.append(job["url"])
            release.wait(5)
            return Scored(0, f"{job['title']}: 81", 81)

        async def script(app, pilot):
            self.assertEqual(len(app.shown), 2)
            with patch("hunt.screen.worth_custom_resume", return_value=(True, "")), patch("hunt.cli.score_one", side_effect=slow_score), patch("hunt.live.check_live", return_value="open"):
                await pilot.press("s")
                await pilot.press("s")
                await pilot.press("down")
                await pilot.press("s")
                for _ in range(20):
                    await pilot.pause(0.05)
                    if len(started) == 2:
                        break
                self.assertEqual(len(app._scoring), 2)
                self.assertIn("scoring 2", str(app.query_one("#summary", Static).render()))
                await pilot.press("x")
                await pilot.pause()
                self.assertEqual(len(app.shown), 1)
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app._scoring, {})

        _pilot(root, script)


class ProcessAllTests(unittest.TestCase):
    def _root_with_six(self) -> Path:
        root = _root()
        payload = json.loads((root / "data/last-search.json").read_text())
        cache = json.loads((root / "data/clef-cache.json").read_text())
        for n in range(4, 9):
            url = f"https://example.com/{n}"
            payload["jobs"].append(dict(payload["jobs"][0], title=f"Platform Engineer {n}", url=url, job_id=str(n)))
            cache[url] = {"keep": True, "fit": 4}
        (root / "data/last-search.json").write_text(json.dumps(payload))
        (root / "data/clef-cache.json").write_text(json.dumps(cache))
        return root

    def test_score_all_sends_one_batch_and_marks_the_rows(self):
        from textual.widgets import Static

        from hunt.claude_client import WorkspaceLookup
        from hunt.steps import ConfirmScreen

        root = self._root_with_six()
        sent = []

        def submit(_root, jobs, **_kwargs):
            sent.append([job["url"] for job in jobs])
            from hunt.batch import BATCHES
            from hunt.store import write_json

            write_json(_root / BATCHES, [{"id": "msgbatch_1", "kind": "score", "status": "open", "jobs": {str(n): job for n, job in enumerate(jobs)}}])
            return "msgbatch_1"

        async def script(app, pilot):
            self.assertEqual(len(app.shown), 6)
            with patch("hunt.batch.submit_scores", side_effect=submit), patch("hunt.claude_client.lookup_workspace", return_value=WorkspaceLookup(selected="wrkspc_1")):
                await pilot.press("A")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmScreen)
                await pilot.click("#yes")
                await _settle(app, pilot)
            self.assertEqual(len(sent), 1)
            self.assertEqual(len(sent[0]), 6)
            self.assertIn("batch 6", str(app.query_one("#summary", Static).render()))
            await pilot.press("s")
            await pilot.pause()
            self.assertEqual(app._scoring, {})

        _pilot(root, script)

    def test_s_queues_once_three_roles_are_scoring(self):
        import threading

        from hunt.cli import Scored

        root = self._root_with_six()
        release = threading.Event()

        def slow_score(_root, job, **_kwargs):
            release.wait(5)
            return Scored(0, f"{job['title']}: 70", 70)

        async def script(app, pilot):
            with patch("hunt.cli.score_one", side_effect=slow_score), patch("hunt.live.check_live", return_value="open"):
                for _ in range(4):
                    await pilot.press("s")
                    await pilot.press("down")
                await pilot.pause()
                self.assertEqual(len(app._scoring), 3)
                self.assertEqual(len(app._queue), 1)
                release.set()
                for _ in range(100):
                    await pilot.pause(0.05)
                    if not app._scoring and not app._queue:
                        break
                self.assertEqual(app._queue, [])

        _pilot(root, script)


class SettingsClassifierTests(unittest.TestCase):
    def _toggle(self, *, key: bool) -> tuple[list[str], list[str]]:
        from textual.widgets import Button

        from hunt.gate import classifier
        from hunt.steps import SettingsScreen

        root = _root()
        seen: list[str] = []
        labels: list[str] = []

        async def script(app, pilot):
            app.action_settings()
            await pilot.pause()
            self.assertIsInstance(app.screen, SettingsScreen)
            button = app.screen.query_one("#classifier", Button)
            for _ in range(2):
                labels.append(str(button.label))
                button.press()
                await pilot.pause()
                seen.append(classifier(root))

        _pilot(root, script, key=key)
        return seen, labels

    def test_settings_switches_between_clef_and_haiku(self):
        seen, labels = self._toggle(key=True)
        self.assertEqual(seen, ["haiku", "clef"])
        self.assertEqual(labels, ["Use Haiku", "Use Clef-flash"])

    def test_haiku_is_refused_without_an_api_key(self):
        seen, labels = self._toggle(key=False)
        self.assertEqual(seen, ["clef", "clef"])
        self.assertEqual(labels, ["Use Haiku", "Use Haiku"])


class WorthDeskTests(unittest.TestCase):
    def _root_with(self, company: str, location: str) -> Path:
        root = _root()
        search = json.loads((root / "profile/search.json").read_text())
        search["location"] = {"mode": "places", "countries": ["India"], "remote_only_countries": ["United States"], "work_modes": ["onsite", "hybrid", "remote"]}
        (root / "profile/search.json").write_text(json.dumps(search), encoding="utf-8")
        last = json.loads((root / "data/last-search.json").read_text())
        last["jobs"][0].update({"company": company, "location": location})
        (root / "data/last-search.json").write_text(json.dumps(last), encoding="utf-8")
        return root

    def test_c_sets_the_company_class_for_good(self):
        root = self._root_with("Talentgigs", "Bengaluru, India")

        async def script(app, pilot):
            from hunt.desk import CompanyClassScreen

            self.assertEqual({job["company"]: reason for job, reason in app.hidden}.get("Talentgigs"), "staffing")
            await pilot.press("h")
            await pilot.pause()
            row = next(index for index, job in enumerate(app.shown) if job["company"] == "Talentgigs")
            app.query_one("#jobs").move_cursor(row=row)
            await pilot.press("c")
            await pilot.pause()
            self.assertIsInstance(app.screen, CompanyClassScreen)
            await pilot.click("#class-product")
            await pilot.pause()
            self.assertNotIn("Talentgigs", {job["company"] for job, _ in app.hidden})
            self.assertIn("Talentgigs", {job["company"] for job in app.main})
            saved = json.loads((root / "data/companies.json").read_text())
            self.assertEqual(saved["talentgigs"]["class"], "product")

        _pilot(root, script)

    def test_k_keeps_a_maybe_row_and_drops_its_marker(self):
        root = self._root_with("Acme", "Remote - USA")

        async def script(app, pilot):
            table = app.query_one("#jobs")
            row = next(index for index, job in enumerate(app.shown) if job["location"] == "Remote - USA")
            self.assertTrue(str(table.get_cell_at((row, 1))).startswith("? "))
            table.move_cursor(row=row)
            await pilot.press("k")
            await pilot.pause()
            row = next(index for index, job in enumerate(app.shown) if job["location"] == "Remote - USA")
            self.assertFalse(str(table.get_cell_at((row, 1))).startswith("? "))
            self.assertIn("https://example.com/1", json.loads((root / "data/worth-overrides.json").read_text()))

        _pilot(root, script)

    def test_the_detail_shows_the_years_sentence_and_the_worth_reason(self):
        root = self._root_with("Acme", "Remote - USA")
        last = json.loads((root / "data/last-search.json").read_text())
        last["jobs"][0]["description"] = "Experience: 2-4 years. Build the product."
        (root / "data/last-search.json").write_text(json.dumps(last), encoding="utf-8")

        async def script(app, pilot):
            from textual.widgets import Static

            row = next(index for index, job in enumerate(app.shown) if job["location"] == "Remote - USA")
            app.query_one("#jobs").move_cursor(row=row)
            await pilot.pause()
            detail = app.query_one("#detail-score", Static)
            text = str(detail.render())
            self.assertIn('Asks 2 years: "Experience: 2-4 years."', text)
            self.assertIn("Maybe: remote US: unclear", text)
            self.assertGreaterEqual(detail.size.height, 3)

        _pilot(root, script)
