"""Full-screen desk: resume, then preferences, then the roles worth acting on."""

from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, RichLog, Select, Static

from hunt.inbox import (
    add_company,
    anthropic_key_present,
    load_jobs,
    load_json,
    mark_applied,
    store_anthropic_key,
    unmark_applied,
    upsert_env,
    visible_jobs,
)
from hunt.onboard import needs_onboarding
from hunt.sources import SourceError, load_env
from hunt.steps import AnswerScreen, ChoiceScreen, ConfirmScreen, PreferencesStep, ResumeStep, SettingsScreen
from hunt.tailor import resume_flag, resume_status, tailored_folder
from hunt.trim import removed_boilerplate, shorten
from hunt.workflows import Workflow, command, discover

KEY_VARS = (
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "JOOBLE_API_KEY",
    "FINDWORK_API_KEY",
    "REED_API_KEY",
    "USAJOBS_API_KEY",
    "USAJOBS_EMAIL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_WORKSPACE_ID",
)

WATCHED = (
    "data/inbox.json",
    "data/evaluated-jobs.csv",
    "data/job-tracker.csv",
    "data/dismissed.json",
    "data/clef-cache.json",
    "data/batches.json",
    "data/schedule.json",
    "profile/preferences.json",
    "profile/search.json",
    "profile/rubric.json",
)
REFRESH_SECONDS = 15
# Acting on this many marked roles asks first.
BULK_CONFIRM = 3


def _slug(company: str) -> str:
    return "".join(ch for ch in company.lower() if ch.isalnum())


def _env(root: Path) -> dict[str, str]:
    load_env(root / ".env")
    return dict(os.environ)


class AnthropicKeyScreen(ModalScreen[str | None]):
    """Ask for the Claude key. Escape leaves it unset."""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Anthropic API key")
            yield Static("Saved in .env. Used when a rewrite calls Claude.", classes="hint")
            yield Input(placeholder="sk-ant-...", password=True, id="anthropic-key")
            yield Button("Save", variant="primary", id="save-anthropic")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save-anthropic":
            return
        value = self.query_one("#anthropic-key", Input).value.strip()
        if not value:
            self.notify("Enter a key")
            return
        self.dismiss(value)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class WorkspaceScreen(ModalScreen[str | None]):
    """Pick a workspace, or paste its id when the key can see more than one."""

    def __init__(self, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self.choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Anthropic workspace")
            if self.choices:
                yield Static("This key can use more than one workspace. Pick the one to bill.", classes="hint")
                yield Select(
                    [(f"{name}  {identifier}", identifier) for identifier, name in self.choices],
                    id="workspace-choice",
                    allow_blank=True,
                )
            else:
                yield Static(
                    "Paste the workspace id from platform.claude.com/settings/workspaces. It starts with wrkspc_.",
                    classes="hint",
                )
                yield Input(placeholder="wrkspc_...", id="workspace-id")
            yield Button("Save", variant="primary", id="save-workspace")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save-workspace":
            return
        if self.choices:
            choice = self.query_one("#workspace-choice", Select).value
            if choice is Select.BLANK or not choice:
                self.notify("Pick a workspace")
                return
            self.dismiss(str(choice))
            return
        value = self.query_one("#workspace-id", Input).value.strip()
        if not value:
            self.notify("Enter a workspace id")
            return
        self.dismiss(value)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class KeysScreen(ModalScreen[dict[str, str] | None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("API keys")
            yield Static("Stored in .env. Leave a field blank to keep the current value.", classes="hint")
            for name in KEY_VARS:
                yield Label(name)
                yield Input(placeholder=name, password=True, id=f"key-{name}")
            yield Button("Save", variant="primary", id="save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            return
        updates = {}
        for name in KEY_VARS:
            updates[name] = self.query_one(f"#key-{name}", Input).value
        self.dismiss(updates)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class AddScreen(ModalScreen[dict | None]):
    def __init__(self) -> None:
        super().__init__()
        self._slug_edited = False
        self._filling_slug = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Add a company")
            yield Static("The slug is checked against the public board before it is saved.", classes="hint")
            yield Label("Company")
            yield Input(id="company", placeholder="Anthropic")
            yield Label("Board")
            yield Select(
                [("Greenhouse", "greenhouse"), ("Lever", "lever"), ("Ashby", "ashby"), ("Workday", "workday")],
                value="greenhouse",
                id="platform",
                allow_blank=False,
            )
            yield Label("Slug")
            yield Input(id="slug", placeholder="anthropic")
            yield Label("Workday tenant, host, site")
            yield Input(id="tenant", placeholder="tenant")
            yield Input(id="host", placeholder="wd5", value="wd1")
            yield Input(id="site", placeholder="External")
            yield Button("Add", variant="primary", id="save")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "slug":
            if self._filling_slug:
                return
            self._slug_edited = True
        if event.input.id == "company" and not self._slug_edited:
            self._filling_slug = True
            self.query_one("#slug", Input).value = _slug(event.value)
            self._filling_slug = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            return
        company = self.query_one("#company", Input).value.strip()
        platform = self.query_one("#platform", Select).value
        slug = self.query_one("#slug", Input).value.strip() or _slug(company)
        if not company or not slug:
            self.notify("Company and slug are required", severity="error")
            return
        board = {"company": company, "platform": str(platform), "slug": slug}
        if platform == "workday":
            board["tenant"] = self.query_one("#tenant", Input).value.strip() or slug
            board["host"] = self.query_one("#host", Input).value.strip() or "wd1"
            board["site"] = self.query_one("#site", Input).value.strip()
            if not board["site"]:
                self.notify("Workday needs a site name", severity="error")
                return
        self.dismiss(board)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class CompanyClassScreen(ModalScreen[str | None]):
    """One company's class, for every posting it ever makes."""

    def __init__(self, company: str) -> None:
        super().__init__()
        self._company = company

    def compose(self) -> ComposeResult:
        from hunt.worth import CLASSES

        with Vertical(id="dialog"):
            yield Label(f"What kind of company is {self._company}?")
            yield Static("Staffing and training firms are hidden. Services firms stay on the list as maybe.", classes="hint")
            with Horizontal(id="step-buttons"):
                for klass in CLASSES:
                    yield Button(klass.capitalize(), id=f"class-{klass}")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        name = event.button.id or ""
        self.dismiss(name.removeprefix("class-") if name.startswith("class-") else None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Keys")
            yield Static(
                "\n".join(
                    [
                        "The main list shows only roles worth acting on.",
                        "",
                        "o   open the apply link",
                        "s   score with Claude",
                        "A   score every unscored role on the list in one Batch API batch, within today's budget",
                        "t   tailor a scored role now: a one-page PDF resume",
                        "v   open the role's resume PDF",
                        "y   copy a prompt to paste into Claude.ai for this role's LaTeX resume",
                        "⚠   on a score: the resume PDF did not build, ran over a page, or failed an ATS check",
                        "a   I applied (moves it to the Applied view)",
                        "u   undo applied (in the Applied view)",
                        "x   not for me (stays hidden, even if it is posted again)",
                        "space   mark or unmark a role; * marks every role in the view; esc clears the marks",
                        "With roles marked, a, x, u, s, t, and o act on all of them. Three or more ask first.",
                        "s skips marked roles already scored, and t skips roles already tailored.",
                        "A role scored above the cutoff stays on the list however old it gets, until you apply, hide it, or it closes.",
                        "c   set the company's class (product, services, staffing, training) for every posting",
                        "k   keep this role even though a worth rule hid or flagged it",
                        "?   on a row marks a maybe; the detail says why",
                        "p   preferences",
                        "r   resume",
                        ",   settings: schedule, check now, keys, companies, workflows",
                        "enter   full role; S there scores with Claude anyway",
                        "h   hidden roles and why",
                        "tab   applied roles",
                        "The list refreshes on its own when the schedule finds new roles.",
                        "q   quit",
                    ]
                )
            )
            yield Button("Close", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class WorkflowScreen(ModalScreen[None]):
    """Pick a workflow. The api engine calls Claude here. Grok and Claude Code take over the terminal."""

    BINDINGS = [Binding("escape", "close", "Back")]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._rows: list[Workflow] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield RichLog(id="wf-progress", max_lines=40, markup=False, wrap=True)
            yield Label("Workflows")
            yield Static(
                "api and managed stay in this desk. api calls Claude directly. managed starts one Managed Agents session. grok and claude step aside.",
                classes="hint",
            )
            yield DataTable(id="workflow-table", cursor_type="row", zebra_stripes=True)
            yield Label("Resume file")
            yield Input(value="profile/og-resume.md", id="wf-resume")
            yield Label("Job posting, optional")
            yield Input(id="wf-job")
            yield Label("Numbers you already know, optional")
            yield Input(id="wf-answers")
            yield Button("Run", variant="primary", id="run-workflow")

    def on_mount(self) -> None:
        table = self.query_one("#workflow-table", DataTable)
        table.add_columns("Engine", "Name", "What it does")
        self._rows = discover(self._root)
        for index, workflow in enumerate(self._rows):
            table.add_row(workflow.engine, workflow.name, _clip(workflow.description, 42), key=str(index))
        if not self._rows:
            self.notify("No workflows in .grok/workflows or .claude/workflows.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-workflow":
            self.action_run()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_run(self) -> None:
        table = self.query_one("#workflow-table", DataTable)
        if not self._rows or table.cursor_row is None or table.cursor_row >= len(self._rows):
            self.notify("Select a workflow first.")
            return
        workflow = self._rows[table.cursor_row]
        payload = {
            "resume": self.query_one("#wf-resume", Input).value.strip() or "profile/og-resume.md",
            "job": self.query_one("#wf-job", Input).value.strip(),
            "answers": self.query_one("#wf-answers", Input).value.strip(),
        }
        if workflow.engine in {"api", "managed"}:
            engine = workflow.engine
            self.app.ask_anthropic_key(
                lambda engine=engine, payload=payload: self.app.ensure_workspace(
                    lambda: self._start_claude(engine, payload)
                )
            )
            return
        try:
            cmd = command(workflow, self._root, payload)
        except RuntimeError as exc:
            self.notify(str(exc), severity="error")
            return
        self._show_progress(f"Opening {workflow.engine}. This desk steps aside until that session ends.")
        with self.app.suspend():
            subprocess.run(cmd, cwd=self._root)
        self._show_progress(f"{workflow.engine} finished.")

    def _show_progress(self, message: str) -> None:
        try:
            self.query_one("#wf-progress", RichLog).write(message)
        except Exception:
            pass
        self.app.activity(message)

    def _start_claude(self, engine: str, payload: dict) -> None:
        self.query_one("#run-workflow", Button).disabled = True
        if engine == "api":
            self._show_progress("Starting the Claude API rewrite.")
            self.run_worker(lambda: self._api_worker(payload), thread=True, exclusive=True)
            return
        self._show_progress("Starting a Managed Agents session.")
        self.run_worker(lambda: self._managed_worker(payload), thread=True, exclusive=True)

    def _api_worker(self, payload: dict) -> None:
        from hunt.rewrite_api import RewriteError, run_resume_rewrite

        try:
            result = run_resume_rewrite(
                self._root,
                payload,
                on_progress=lambda message: self.app.call_from_thread(self._show_progress, message),
            )
        except RewriteError as exc:
            self.app.call_from_thread(self._show_progress, str(exc))
            self.app.call_from_thread(self.notify, str(exc), severity="error")
            self.app.call_from_thread(self._enable_run)
            return
        if result.paused:
            self.app.call_from_thread(self._open_followup, result, payload)
            return
        note = result.next_steps or result.summary
        if result.clean:
            from hunt.follow import run_after_rewrite

            note = run_after_rewrite(
                self._root,
                on_progress=lambda message: self.app.call_from_thread(self._show_progress, message),
            )
            if not result.saved:
                note = f"{result.next_steps} {note}"
            self.app.call_from_thread(self.app.reload)
        self.app.call_from_thread(self._show_progress, note)
        self.app.call_from_thread(self.notify, note)
        self.app.call_from_thread(self._enable_run)

    def _enable_run(self) -> None:
        try:
            self.query_one("#run-workflow", Button).disabled = False
        except Exception:
            pass

    def _open_followup(self, result, payload: dict) -> None:
        def done(replies: list[str] | None) -> None:
            if replies is None:
                self._enable_run()
                self.notify("The questions are in data/resume-answers.md.")
                return
            from hunt.rewrite_api import write_answers

            nxt = dict(payload)
            nxt["answers"] = write_answers(self._root, result.questions, replies)
            self._start_claude("api", nxt)

        self.app.push_screen(AnswerScreen(result.questions), done)

    def _managed_worker(self, payload: dict) -> None:
        from hunt.managed_rewrite import ManagedError, run_managed_rewrite

        try:
            result = run_managed_rewrite(
                self._root,
                payload,
                on_progress=lambda message: self.app.call_from_thread(self._show_progress, message),
            )
        except ManagedError as exc:
            self.app.call_from_thread(self._show_progress, str(exc))
            self.app.call_from_thread(self.notify, str(exc), severity="error")
            self.app.call_from_thread(self._enable_run)
            return
        self.app.call_from_thread(self._show_progress, result.summary)
        self.app.call_from_thread(self.notify, f"{result.summary} {result.trace}")
        self.app.call_from_thread(self._enable_run)


class RoleScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("o", "open", "Open"),
        Binding("s", "score", "Score"),
        Binding("S", "score_force", "Score anyway"),
        Binding("a", "applied", "I applied"),
        Binding("x", "dismiss_role", "Not for me"),
        Binding("escape", "close", "Back"),
    ]

    def __init__(self, job: dict, hire_at: int) -> None:
        super().__init__()
        self.job = job
        self.hire_at = hire_at

    def compose(self) -> ComposeResult:
        job = self.job
        with Vertical(id="role-dialog"):
            yield Static(job.get("company") or "", id="role-company")
            yield Static(job.get("title") or "", id="role-title")
            yield Static(_meta(job), classes="hint")
            yield Static(_score_line(job, self.hire_at), id="role-score")
            yield VerticalScroll(Static(_posting(job), id="role-body"))
            yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_applied(self) -> None:
        self.dismiss("applied")

    def action_score(self) -> None:
        self.dismiss("score")

    def action_score_force(self) -> None:
        self.dismiss("score_force")

    def action_dismiss_role(self) -> None:
        self.dismiss("dismiss")

    def action_open(self) -> None:
        _open_url(self.job.get("apply_url") or self.job.get("url") or "")


class HuntApp(App):
    TITLE = "hunt"
    CSS = """
    Screen { background: #1c1b19; color: #e7e5e4; }
    #summary { height: 1; background: #262420; color: #c8c2b8; padding: 0 1; }
    #activity { height: 6; border-bottom: solid #3f3b36; background: #161513; }
    #body { height: 1fr; }
    #jobs { width: 3fr; height: 1fr; }
    #detail { width: 2fr; height: 1fr; padding: 1 2; background: #221f1c; }
    #detail-title { text-style: bold; color: #f4f1ea; height: auto; }
    #detail-score { height: auto; color: #e6b84d; }
    .hint { color: #8a847c; height: auto; }
    #detail-scroll { height: 1fr; }
    Footer { background: #262420; }
    DataTable { height: 1fr; background: #1c1b19; }
    #dialog { width: 72; height: auto; max-height: 90%; background: #262420; border: solid #3f3b36; padding: 1 2; }
    #resume-dialog, #role-dialog { width: 92%; height: 92%; background: #1c1b19; border: solid #3f3b36; padding: 1 2; }
    #answer-scroll { height: 1fr; }
    #prefs-scroll { height: 1fr; }
    #pref-roles { height: 7; }
    SelectionList { height: auto; max-height: 9; }
    .group { color: #e6b84d; text-style: bold; margin-top: 1; }
    #pick-row, #pay-row, #cap-row, #step-buttons, #step-buttons-2, #classifier-row { height: auto; }
    #pay-row Input, #cap-row Input { width: 1fr; }
    #step-progress { height: 8; border: solid #3f3b36; }
    #schedule-lines { height: auto; color: #c8c2b8; }
    #wf-progress { height: 8; border: solid #3f3b36; }
    TextArea { height: 1fr; }
    #role-dialog VerticalScroll { height: 1fr; }
    Button { margin-top: 1; }
    """
    BINDINGS = [
        Binding("o", "open", "Open"),
        Binding("s", "score", "Score"),
        Binding("A", "process_all", "Score all"),
        Binding("t", "tailor_now", "Tailor"),
        Binding("v", "view_pdf", "PDF"),
        Binding("y", "copy_prompt", "Prompt", show=False),
        Binding("a", "applied", "Applied"),
        Binding("u", "undo_applied", "Undo applied"),
        Binding("x", "dismiss_role", "Not for me"),
        Binding("space", "toggle_mark", "Mark"),
        Binding("asterisk", "mark_all", "Mark all", show=False),
        Binding("escape", "clear_marks", "Clear marks"),
        Binding("c", "company_class", "Company", show=False),
        Binding("k", "keep_role", "Keep", show=False),
        Binding("p", "preferences", "Preferences"),
        Binding("r", "resume", "Resume"),
        Binding("comma", "settings", "Settings"),
        Binding("h", "toggle_hidden", "Hidden", show=False),
        Binding("tab", "toggle_applied", "Applied view", priority=True),
        Binding("enter", "open_role", "Role", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.jobs: list[dict] = []
        self.shown: list[dict] = []
        self.main: list[dict] = []
        self.hidden: list[tuple[dict, str]] = []
        self.applied: list[dict] = []
        self.view = "main"
        self.hire_at = 80
        self._busy = False
        self._key_asked = False
        self._probe_running = False
        self._tailoring: set[str] = set()
        self._tailor_queue: list[dict] = []
        self._marked: set[str] = set()
        self._after_probe = None
        self._timers_started = False
        self._ctx = None
        self._warned_rules = False
        self._scoring: dict[str, dict] = {}
        self._queue: list[tuple[dict, bool]] = []
        self._in_batch: set[str] = set()
        self._polling = False
        self._signature: tuple[tuple[int, int] | None, ...] = ()

    def compose(self) -> ComposeResult:
        yield Static("", id="summary")
        yield RichLog(id="activity", max_lines=200, markup=False, wrap=True)
        with Horizontal(id="body"):
            yield DataTable(id="jobs", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail"):
                yield Static("", id="detail-kicker", classes="hint")
                yield Static("No roles yet", id="detail-title")
                yield Static("", id="detail-score")
                yield VerticalScroll(Static("", id="detail-body"), id="detail-scroll")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#jobs", DataTable)
        table.add_columns("Company", "Role", "Score", "Source")
        table.focus()
        self.next_step()

    def next_step(self) -> None:
        """Open the first unfinished step: the key on a fresh clone, the resume, preferences, then the caps once.
        With all done, show the jobs."""
        from hunt.inbox import anthropic_key_present
        from hunt.preferences import budget_set, is_confirmed, migrate
        from hunt.steps import BudgetScreen, KeyStep

        if not self._key_asked and needs_onboarding(self.root) and not anthropic_key_present(self.root):
            self._key_asked = True
            self.push_screen(KeyStep(self.root), self._key_step_done)
            return
        if needs_onboarding(self.root):
            self.push_screen(ResumeStep(self.root, can_leave=False), self._resume_done)
            return
        if not is_confirmed(self.root):
            migrate(self.root)
            self.push_screen(PreferencesStep(self.root, can_leave=False, auto_probe=True), self._preferences_done)
            return
        if not budget_set(self.root):
            self.push_screen(BudgetScreen(self.root), lambda _saved: self.next_step())
            return
        self.reload()
        self._classify_open()
        self._start_timers()

    def _start_timers(self) -> None:
        if self._timers_started:
            return
        self._timers_started = True
        self.set_interval(60, self._poll_batches)
        self.set_interval(REFRESH_SECONDS, self._refresh_if_changed)

    def start_probe(self, prefs: dict) -> None:
        """Step 2's count. Runs on the app, so saving Step 2 does not cut it short."""
        self._probe_running = True

        def work() -> None:
            from hunt.probe import run_probe
            from hunt.store import Busy

            try:
                run_probe(self.root, _env(self.root), prefs, on_unit=lambda unit, ok: self.call_from_thread(self._probe_progress))
            except Busy:
                self.call_from_thread(self.notify, "A check is running. Counts appear after the next one.")
            except Exception as exc:
                self.call_from_thread(self.notify, f"Counting stopped: {type(exc).__name__}: {exc}", severity="warning")
            self.call_from_thread(self._probe_finished)

        self.run_worker(work, thread=True, group="probe", exclusive=False)

    def _probe_progress(self) -> None:
        if isinstance(self.screen, PreferencesStep):
            self.screen._refresh_counts()

    def _probe_finished(self) -> None:
        self._probe_running = False
        self._probe_progress()
        if self._after_probe is not None:
            then, self._after_probe = self._after_probe, None
            then()

    def _key_step_done(self, saved: bool | None) -> None:
        """A key that is refused without a workspace gets one now, or every background call would fail quietly."""
        if not saved:
            self.next_step()
            return

        def work() -> None:
            from hunt.claude_client import needs_workspace

            try:
                needed = needs_workspace(self.root)
            except Exception:
                needed = False
            self.call_from_thread(self.ensure_workspace if needed else (lambda then: then()), self.next_step)

        self.run_worker(work, thread=True, group="key", exclusive=False)

    def _resume_done(self, saved: bool | None) -> None:
        if saved:
            self.push_screen(PreferencesStep(self.root, can_leave=False, auto_probe=True), self._preferences_done)
            return
        self.next_step()

    def _preferences_done(self, saved: bool | None) -> None:
        if not saved:
            self.next_step()
            return
        if self._probe_running:
            self._after_probe = self._after_preferences
            self.notify("Preferences saved. The boards are checked as soon as the count finishes.")
            return
        self._after_preferences()

    def _after_preferences(self) -> None:
        from datetime import datetime

        from hunt.launchd import install, installed
        from hunt.probe import merge_saved

        added = merge_saved(self.root, datetime.now().astimezone())
        self.reload()
        self._start_timers()
        if not installed(self.root):
            try:
                install(self.root)
                self.notify("Background checks are on. Turn them off in Settings (,).")
            except RuntimeError as exc:
                self.notify(str(exc), severity="error")
        self.notify(f"Preferences saved. {added} roles from the count went straight to your list. Checking the boards now.")
        self.action_check()

    def activity(self, message: str) -> None:
        try:
            self.query_one("#activity", RichLog).write(message)
        except Exception:
            pass
        try:
            self.query_one("#summary", Static).update(message)
        except Exception:
            pass

    def reload(self) -> None:
        from hunt.inbox import hidden_jobs
        from hunt.screen import context

        self._signature = _data_signature(self.root)
        rubric = load_json(self.root / "profile/rubric.json", {})
        self.hire_at = int(rubric.get("natural_hire_at") or 80)
        ctx = context(self.root)
        self._ctx = ctx
        if ctx.worth_warning and not self._warned_rules:
            self._warned_rules = True
            self.notify(ctx.worth_warning, severity="warning")
        self.jobs = load_jobs(self.root)
        self.main = visible_jobs(self.jobs, ctx)
        self.hidden = sorted(hidden_jobs(self.jobs, ctx), key=lambda pair: (pair[1], pair[0].get("company") or ""))
        self.applied = _applied_rows(self.root)
        from hunt.batch import pending_urls

        self._in_batch = pending_urls(self.root)
        self._fill()

    def _refresh_if_changed(self) -> None:
        """Pick up roles the background schedule wrote. A modal or a running task reloads on its own."""
        if self._busy or len(self.screen_stack) > 1 or _data_signature(self.root) == self._signature:
            return
        before = {job.get("url") for job in self.main}
        self.reload()
        fresh = [job for job in self.main if job.get("url") not in before]
        if fresh:
            self.activity(f"{len(fresh)} new role{'s' if len(fresh) != 1 else ''} on the main list")

    def _fill(self) -> None:
        table = self.query_one("#jobs", DataTable)
        keep = table.cursor_row if table.row_count else 0
        current = self.shown[keep] if 0 <= keep < len(self.shown) else None
        keep_url = (current or {}).get("url")
        table.clear(columns=True)
        if self.view == "hidden":
            table.add_columns("Company", "Role", "Why hidden", "Source")
            from hunt import spend
            from hunt.pipeline import shown_reason

            classify_room = spend.room(self.root, "classify")
            rows = [(job, shown_reason(job, reason, self._in_batch, classify_room)) for job, reason in self.hidden]
        elif self.view == "applied":
            table.add_columns("Company", "Role", "Status", "Date")
            rows = [(job, job.get("status") or "Applied") for job in self.applied]
        else:
            table.add_columns("Company", "Role", "Score", "Posted", "Source")
            rows = [(job, None) for job in self.main]
        self.shown = [job for job, _ in rows]
        self._marked &= {_mark_key(job) for job in self.shown}
        for index, (job, extra) in enumerate(rows):
            if extra is not None:
                third = Text(extra, style="#8a847c")
            elif (job.get("url") or "") in self._scoring:
                third = Text("scoring…", style="#e6b84d")
            elif self._queued(job):
                third = Text("queued", style="#8a847c")
            elif (job.get("url") or "") in self._tailoring:
                third = Text("tailoring…", style="#e6b84d")
            elif self._tailor_waiting(job):
                third = Text("queued", style="#8a847c")
            elif (job.get("url") or "") in self._in_batch:
                third = Text("batch", style="#8a847c")
            else:
                third = _score_cell(job, self.hire_at)
                if resume_flag(self.root, job):
                    third = Text.assemble(third, (" ⚠", "#e6b84d"))
            fourth = job.get("date") if self.view == "applied" else job.get("source")
            role = _clip(job.get("title") or job.get("role") or "", 42)
            if self.view == "main" and self._worth(job).verdict == "maybe":
                role = Text.assemble(("? ", "#e6b84d"), role)
            cells = [self._company_cell(job), role, third]
            if self.view == "main":
                cells.append(_posted_cell(job))
            table.add_row(*cells, fourth or "", key=str(index))
        self._paint_summary()
        if self.shown:
            urls = [job.get("url") for job in self.shown]
            row = urls.index(keep_url) if keep_url and keep_url in urls else min(max(keep or 0, 0), len(self.shown) - 1)
            table.move_cursor(row=row)
            self._show(self.shown[row])
        else:
            self._show(None)

    def _paint_summary(self) -> None:
        from hunt.schedule import load_state, next_check

        views = {"main": "", "hidden": "Hidden roles · h returns to the main list   ", "applied": "Applied · tab returns to the main list   "}
        parts = [f"{views[self.view]}{len(self.main)} matter", f"{len(self.hidden)} hidden"]
        if self._marked:
            parts.append(f"{len(self._marked)} marked")
        if self._scoring:
            parts.append(f"scoring {len(self._scoring)}")
        if self._tailoring:
            parts.append(f"tailoring {len(self._tailoring)}")
        if self._queue or self._tailor_queue:
            parts.append(f"queued {len(self._queue) + len(self._tailor_queue)}")
        if self._in_batch:
            parts.append(f"batch {len(self._in_batch)}")
        env = _env(self.root)
        if (env.get("ANTHROPIC_API_KEY") or "").strip():
            from hunt.spend import summary

            parts.append(summary(self.root))
        else:
            parts.append("no API key · Settings (,) → API keys")
        upcoming = next_check(self.root, env)
        if upcoming is not None:
            parts.append(f"next check {upcoming.astimezone().strftime('%H:%M')}")
        last = load_state(self.root).get("last_tick")
        if last:
            parts.append(f"last run {last[11:16]}")
        self.query_one("#summary", Static).update(" · ".join(parts))

    def _show(self, job: dict | None) -> None:
        if job is None:
            empty = {
                "main": ("Nothing worth acting on yet", "h shows what was hidden and why"),
                "hidden": ("Nothing hidden", ""),
                "applied": ("No applications yet", "Press a on a role after you apply"),
            }[self.view]
            self.query_one("#detail-kicker", Static).update("")
            self.query_one("#detail-title", Static).update(empty[0])
            self.query_one("#detail-score", Static).update(empty[1])
            self.query_one("#detail-body", Static).update("")
            return
        self.query_one("#detail-kicker", Static).update(_meta(job))
        self.query_one("#detail-title", Static).update(job.get("title") or job.get("role") or "")
        line = _score_line(job, self.hire_at) + "   " + _jev_line(job)
        if job.get("jev_reason"):
            line += f"\nWhy: {job['jev_reason']}"
        years = _years_note(job)
        if years:
            line += "\n" + years
        worth = self._worth(job)
        if worth.verdict in {"maybe", "skip"}:
            line += "\n" + _worth_line(worth)
        status = resume_status(self.root, job)
        if status:
            line += f"   tailored: {status}"
        self.query_one("#detail-score", Static).update(line)
        body = _posting(job)
        if len(body) > 1800:
            body = body[:1800].rsplit(" ", 1)[0] + " …"
        self.query_one("#detail-body", Static).update(body)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row < 0 or event.cursor_row >= len(self.shown):
            return
        self._show(self.shown[event.cursor_row])

    def _selected(self) -> dict | None:
        table = self.query_one("#jobs", DataTable)
        if not self.shown or table.cursor_row is None:
            return None
        row = table.cursor_row
        if row < 0 or row >= len(self.shown):
            return None
        return self.shown[row]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "undo_applied":
            return self.view == "applied"
        if action == "clear_marks":
            return bool(self._marked)
        return True

    def action_toggle_hidden(self) -> None:
        self.view = "main" if self.view == "hidden" else "hidden"
        self._marked.clear()
        self._fill()
        self.refresh_bindings()

    def action_toggle_applied(self) -> None:
        self.view = "main" if self.view == "applied" else "applied"
        self._marked.clear()
        self._fill()
        self.refresh_bindings()

    def _company_cell(self, job: dict):
        name = _clip(job.get("company") or "", 18)
        return Text.assemble(("● ", "#e6b84d"), name) if _mark_key(job) in self._marked else name

    def _targets(self) -> list[dict]:
        """The marked roles in list order, or the highlighted one when nothing is marked."""
        if self._marked:
            return [job for job in self.shown if _mark_key(job) in self._marked]
        job = self._selected()
        return [job] if job is not None else []

    def action_toggle_mark(self) -> None:
        job = self._selected()
        if job is None:
            return
        key = _mark_key(job)
        if key in self._marked:
            self._marked.discard(key)
        else:
            self._marked.add(key)
        table = self.query_one("#jobs", DataTable)
        row = table.cursor_row
        table.update_cell_at(Coordinate(row, 0), self._company_cell(job), update_width=True)
        if row + 1 < len(self.shown):
            table.move_cursor(row=row + 1)
        self._paint_summary()
        self.refresh_bindings()

    def action_mark_all(self) -> None:
        keys = {_mark_key(job) for job in self.shown}
        self._marked = set() if keys and keys <= self._marked else keys
        self._fill()
        self.refresh_bindings()

    def action_clear_marks(self) -> None:
        self._marked.clear()
        self._fill()
        self.refresh_bindings()

    def _bulk(self, jobs: list[dict], run, question: str, detail: str, yes: str, *, ask: bool | None = None) -> None:
        """Clear the marks and run. Three or more roles, or ask=True, confirm first."""

        def go() -> None:
            self._marked.clear()
            self.refresh_bindings()
            run(jobs)

        if not (len(jobs) >= BULK_CONFIRM if ask is None else ask):
            go()
            return
        self.push_screen(ConfirmScreen(question, detail, yes, "Cancel"), lambda ok: go() if ok else None)

    def action_preferences(self) -> None:
        self.push_screen(PreferencesStep(self.root, can_leave=True), self._preferences_done)

    def action_resume(self) -> None:
        self.push_screen(ResumeStep(self.root, can_leave=True), self._resume_done)

    def action_settings(self) -> None:
        def chosen(action: str | None) -> None:
            self._paint_summary()
            if action == "check":
                self.action_check()
            elif action == "keys":
                self.push_screen(KeysScreen(), self._keys_saved)
            elif action == "add":
                self.action_add()
            elif action == "workflows":
                self.push_screen(WorkflowScreen(self.root))

        self.push_screen(SettingsScreen(self.root), chosen)

    def _keys_saved(self, updates: dict[str, str] | None) -> None:
        if not updates:
            return
        upsert_env(self.root / ".env", updates)
        self.notify("Keys saved")

    def ask_anthropic_key(self, then=None, required: bool = True) -> None:
        if anthropic_key_present(self.root):
            if then:
                then()
            return

        def done(key: str | None) -> None:
            if key:
                store_anthropic_key(self.root, key)
                self.notify("API key saved")
            elif required:
                if then:
                    self.notify("Claude needs an API key.")
                return
            if then:
                then()

        self.push_screen(AnthropicKeyScreen(), done)

    def ensure_workspace(self, then) -> None:
        from hunt.claude_client import WorkspaceLookup, lookup_workspace, store_workspace_id

        def finish(found: WorkspaceLookup) -> None:
            if found.selected:
                if found.choices:
                    self.notify(f"Using workspace {found.choices[0][1]}")
                then()
                return
            self.push_screen(WorkspaceScreen(list(found.choices)), lambda value: saved(value))

        def saved(value: str | None) -> None:
            if not value:
                self.notify("Claude needs a workspace id for this key.")
                return
            store_workspace_id(self.root, value)
            self.notify("Workspace saved")
            then()

        def work() -> None:
            try:
                found = lookup_workspace(self.root)
            except Exception as exc:
                self.call_from_thread(self.notify, str(exc), severity="error")
                return
            self.call_from_thread(finish, found)

        self.run_worker(work, thread=True, exclusive=True)

    def action_add(self) -> None:
        if self._busy:
            self.notify("Wait for the current task to finish")
            return
        self.push_screen(AddScreen(), self._add_saved)

    def _add_saved(self, board: dict | None) -> None:
        if not board:
            return
        self._busy = True
        self.activity(f"Checking {board['company']}…")
        self.run_worker(lambda: self._add_worker(board), thread=True, exclusive=True)

    def _add_worker(self, board: dict) -> None:
        try:
            _added, message = add_company(self.root, board)
        except SourceError as exc:
            self.call_from_thread(self._busy_done, str(exc), True)
            return
        self.call_from_thread(self._busy_done, message, False)

    def action_check(self) -> None:
        if self._busy:
            self.notify("Wait for the current task to finish")
            return
        self._busy = True
        self.activity("Checking every board now…")
        self.run_worker(self._check_worker, thread=True, exclusive=True)

    def _check_worker(self) -> None:
        from hunt.schedule import run
        from hunt.store import Busy

        def report(message: str) -> None:
            self.call_from_thread(self.activity, message)

        try:
            result = run(self.root, _env(self.root), full=True, on_progress=report)
        except Busy:
            self.call_from_thread(self._busy_done, "A scheduled check is running. The list refreshes when it ends.", False)
            return
        except Exception as exc:
            self.call_from_thread(self._busy_done, str(exc), True)
            return
        from hunt.gate import CLASSIFIER_LABELS

        problem = next((error for error in result.errors if error.startswith(CLASSIFIER_LABELS)), "")
        message = problem or f"{len(result.added)} new roles. {len(result.announced)} are worth a look."
        self.call_from_thread(self._busy_done, message, bool(problem))

    def _classify_open(self) -> None:
        if self._busy or not any(reason == "not classified yet" for _, reason in self.hidden):
            return
        self._busy = True
        self.activity("Classifying new roles…")
        self.run_worker(self._classify_worker, thread=True, exclusive=True)

    def _classify_worker(self) -> None:
        self.call_from_thread(self.activity, _classify_message(self.root, _env(self.root)))
        from hunt.gate import GateError, classify_pending
        from hunt.store import Busy, run_lock

        def report(message: str) -> None:
            self.call_from_thread(self.activity, message)

        pending = [job for job, reason in self.hidden if reason == "not classified yet"]
        try:
            with run_lock(self.root):
                classified = classify_pending(self.root, pending, on_progress=report, env=_env(self.root))
        except Busy:
            self.call_from_thread(self._busy_done, "A scheduled check is classifying these roles.", False)
            return
        except GateError as exc:
            self.call_from_thread(self._busy_done, str(exc), True)
            return
        self.call_from_thread(self._busy_done, f"Classified {len(classified)} roles.", False)

    def _busy_done(self, message: str, failed: bool) -> None:
        self._busy = False
        self.reload()
        self.notify(message, severity="error" if failed else "information")

    def action_open_role(self) -> None:
        job = self._selected()
        if job is None:
            return
        self.push_screen(RoleScreen(job, self.hire_at), lambda result: self._role_result(job, result))

    def _role_result(self, job: dict, result: str | None) -> None:
        if result == "applied":
            self._mark([job])
        elif result == "dismiss":
            self._dismiss([job])
        elif result in {"score", "score_force"}:
            self._score_job(job, force=result == "score_force")

    def action_score(self) -> None:
        if self._marked:
            self._score_marked(self._targets())
            return
        job = self._selected()
        if job is None:
            self.notify("Highlight a role first")
            return
        self._score_job(job, force=False)

    def _score_marked(self, jobs: list[dict]) -> None:
        from hunt.cli import SCORE_WORKERS
        from hunt.screen import context, max_years, worth_custom_resume

        ctx, cap = context(self.root), max_years(self.root)
        todo: list[dict] = []
        skipped: dict[str, int] = {}
        for job in jobs:
            url = job.get("url") or ""
            if str(job.get("final_score") or "").strip():
                reason = "already scored"
            elif url in self._scoring or self._queued(job) or url in self._in_batch:
                reason = "already being scored"
            else:
                reason = worth_custom_resume(job, cap, ctx)[1]
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                todo.append(job)
        note = _skipped_note(skipped)
        if not todo:
            self.notify(f"None of the {len(jobs)} marked roles can be scored. {note} S on one role scores it anyway.")
            return

        def run(chosen: list[dict]) -> None:
            for job in chosen:
                self._enqueue_or_start(job, False, True)
            self.notify(f"Scoring {len(chosen)} roles, {SCORE_WORKERS} at a time. {note}".strip())

        over = self._api_cost_over_cap(todo)
        if over:
            question = "This goes past today's score and resume budget."
            detail = over + " Without the claude CLI, scoring and any resume it triggers go through the Claude API at full price."
            self._bulk(todo, run, question, detail, "Score anyway", ask=True)
            return
        detail = f"{SCORE_WORKERS} at a time. A score above the cutoff also tailors a resume. {note}".strip()
        self._bulk(todo, run, f"Score {len(todo)} roles with Claude?", detail, "Score them")

    def _queued(self, job: dict) -> bool:
        return any(waiting.get("url") == job.get("url") for waiting, _force in self._queue)

    def action_process_all(self) -> None:
        if self.view != "main":
            self.notify("Score all works on the main list.")
            return
        todo = [
            job
            for job in self.main
            if not str(job.get("final_score") or "").strip()
            and (job.get("url") or "") not in self._scoring
            and (job.get("url") or "") not in self._in_batch
            and not self._queued(job)
        ]
        if not todo:
            self.notify("Every role on the main list is already scored or on its way.")
            return
        from hunt import spend
        from hunt.batch import estimates

        room = spend.room(self.root, "score")
        fit, total = 0, 0.0
        for cost in estimates(self.root, "score", todo):
            if total + cost > room + 1e-12:
                break
            total += cost
            fit += 1

        def send(enforce: bool) -> None:
            self.ask_anthropic_key(lambda: self.ensure_workspace(lambda: self._send_batch(todo, enforce)))

        if fit == len(todo):
            self.push_screen(
                ConfirmScreen(
                    f"Score all {len(todo)} roles through the Batch API?",
                    "One batch at half the API price, billed to the ANTHROPIC_API_KEY in .env, within today's score budget "
                    f"(${room:.2f} left). Results usually arrive within an hour; the desk and the schedule pick them up. "
                    "Roles above the cutoff then go into a second batch that writes one-page PDF resumes.",
                    "Send batch",
                    "Cancel",
                ),
                lambda yes: send(True) if yes else None,
            )
            return
        choices = ([("send-fit", f"Send {fit}")] if fit else []) + [("send-all", f"Send all {len(todo)} anyway"), ("cancel", "Cancel")]

        def chosen(choice: str | None) -> None:
            if choice == "send-fit":
                send(True)
            elif choice == "send-all":
                send(False)

        self.push_screen(
            ChoiceScreen(
                f"{fit} of {len(todo)} roles fit today's score budget (${room:.2f} left).",
                "Estimates are upper bounds, so the real bill is usually far lower. Sending all of them goes past today's cap, "
                "and background scoring then waits for tomorrow.",
                choices,
            ),
            chosen,
        )

    def _send_batch(self, jobs: list[dict], enforce: bool = True) -> None:
        self.activity(f"Sending {len(jobs)} roles to the Batch API…")

        def work() -> None:
            from hunt.batch import BatchError, submit

            try:
                batch_id, count = submit(self.root, "score", jobs, enforce=enforce)
            except BatchError as exc:
                self.call_from_thread(self.notify, str(exc), severity="error")
                return
            self.call_from_thread(self._batch_sent, batch_id, count)

        self.run_worker(work, thread=True, group="batch", exclusive=False)

    def _batch_sent(self, batch_id: str, count: int) -> None:
        self.reload()
        self.notify(f"Sent {count} roles in {batch_id}. Results usually arrive within an hour.")

    def _poll_batches(self) -> None:
        from hunt.batch import open_batches

        if self._polling or not open_batches(self.root):
            return
        self._polling = True

        def work() -> None:
            from hunt.batch import poll

            try:
                notes = poll(self.root)
            except Exception as exc:
                notes = [f"Batch check failed: {exc}"]
            self.call_from_thread(self._batch_polled, notes)

        self.run_worker(work, thread=True, group="batch", exclusive=False)

    def _batch_polled(self, notes: list[str]) -> None:
        self._polling = False
        if notes:
            self.reload()
        for note in notes:
            self.notify(note, timeout=10)

    def _score_job(self, job: dict, *, force: bool, quiet: bool = False) -> None:
        url = job.get("url") or ""
        if url in self._scoring or self._queued(job) or url in self._in_batch:
            if not quiet:
                self.notify("This role is already being scored.")
            return
        if not force:
            from hunt.screen import context, max_years, worth_custom_resume

            ok, reason = worth_custom_resume(job, max_years(self.root), context(self.root))
            if not ok:
                if not quiet:
                    self.notify(f"{reason}. Open the role and press S to score with Claude anyway.")
                return
        over = None if quiet else self._api_cost_over_cap([job])
        if over:
            self.push_screen(
                ConfirmScreen(
                    "This goes past today's score and resume budget.",
                    over + " Without the claude CLI, scoring and any resume it triggers go through the Claude API at full price.",
                    "Score anyway",
                    "Cancel",
                ),
                lambda yes: self._enqueue_or_start(job, force, quiet) if yes else None,
            )
            return
        self._enqueue_or_start(job, force, quiet)

    def _api_cost_over_cap(self, jobs: list[dict]) -> str:
        """Empty unless s would bill the API (no claude CLI) beyond today's room. Then the line that says by how much."""
        import shutil

        from hunt import spend
        from hunt.batch import estimate_now
        from hunt.tailor import api_key_ready

        if shutil.which("claude") is not None or not api_key_ready(self.root):
            return ""
        cost = sum(estimate_now(self.root, "score", job) + estimate_now(self.root, "tailor", job) for job in jobs)
        left = spend.room(self.root, "score")
        return f"Up to ${cost:.2f} with ${left:.2f} left today." if cost > left else ""

    def _enqueue_or_start(self, job: dict, force: bool, quiet: bool) -> None:
        from hunt.cli import SCORE_WORKERS

        if len(self._scoring) >= SCORE_WORKERS:
            self._queue.append((job, force))
            if not quiet:
                self.notify(f"Queued. {len(self._queue)} waiting for a free slot.")
            self._fill()
            return
        self._start_score(job, force)

    def _start_score(self, job: dict, force: bool) -> None:
        self._scoring[job.get("url") or ""] = job
        self.activity(f"Scoring {job.get('company') or 'company'} — {job.get('title') or 'role'}. A score above 75 also tailors your resume.")
        self._fill()
        self.run_worker(lambda: self._score_worker(job, force), thread=True, group="score", exclusive=False)

    def _score_worker(self, job: dict, force: bool) -> None:
        from hunt.cli import score_one
        from hunt.live import check_live

        if not force:
            state = check_live(job.get("apply_url") or job.get("url") or "")
            if state == "closed":
                self.call_from_thread(self._score_done, job, "The apply link is closed. Not sent to Claude.", True)
                return
        scored = score_one(self.root, job)
        self.call_from_thread(self._score_done, job, scored.message, scored.code != 0)

    def _score_done(self, job: dict, message: str, failed: bool) -> None:
        from hunt.cli import SCORE_WORKERS

        self._scoring.pop(job.get("url") or "", None)
        while self._queue and len(self._scoring) < SCORE_WORKERS:
            waiting, force = self._queue.pop(0)
            self._start_score(waiting, force)
        self.reload()
        self.notify(message, severity="error" if failed else "information")

    def action_open(self) -> None:
        jobs = self._targets()
        if not jobs:
            return
        self._bulk(jobs, _open_all, f"Open {len(jobs)} apply links?", "Each opens in a browser tab.", "Open them")

    def action_applied(self) -> None:
        jobs = self._targets()
        if not jobs or self.view == "applied":
            return
        self._bulk(jobs, self._mark, f"Mark {len(jobs)} roles applied?", "They move to the Applied view (tab), where u undoes it.", "Mark applied")

    def action_tailor_now(self) -> None:
        if self._marked:
            self._tailor_marked(self._targets())
            return
        job = self._selected()
        if job is None:
            self.notify("Highlight a role first")
            return
        if not str(job.get("final_score") or "").strip():
            self.notify("Score this role first (s). Tailoring starts from its score.")
            return
        if (job.get("url") or "") in self._tailoring or self._tailor_waiting(job) or (job.get("url") or "") in self._in_batch:
            self.notify("This role is already being tailored.")
            return
        import shutil

        from hunt import spend
        from hunt.batch import estimate_now

        if shutil.which("claude") is None:
            cost = estimate_now(self.root, "tailor", job)
            left = spend.room(self.root, "tailor")
            if cost > left:
                self.push_screen(
                    ConfirmScreen(
                        "This goes past today's score and resume budget.",
                        f"Up to ${cost:.2f} at full price with ${left:.2f} left today.",
                        "Tailor anyway",
                        "Cancel",
                    ),
                    lambda yes: self._start_tailor(job) if yes else None,
                )
                return
        self._start_tailor(job)

    def _tailor_marked(self, jobs: list[dict]) -> None:
        import shutil

        from hunt import spend
        from hunt.batch import estimate_now
        from hunt.cli import SCORE_WORKERS
        from hunt.tailor import is_tailored

        todo: list[dict] = []
        skipped: dict[str, int] = {}
        for job in jobs:
            url = job.get("url") or ""
            if not str(job.get("final_score") or "").strip():
                reason = "not scored"
            elif url in self._tailoring or self._tailor_waiting(job) or url in self._in_batch:
                reason = "already being tailored"
            elif is_tailored(self.root, job):
                reason = "already tailored"
            else:
                reason = ""
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                todo.append(job)
        note = _skipped_note(skipped)
        if not todo:
            self.notify(f"None of the {len(jobs)} marked roles need a resume. {note} t on one role tailors it again.")
            return

        def run(chosen: list[dict]) -> None:
            for job in chosen:
                self._start_tailor(job)
            self.notify(f"Tailoring {len(chosen)} resumes, {SCORE_WORKERS} at a time. {note}".strip())

        if shutil.which("claude") is None:
            cost = sum(estimate_now(self.root, "tailor", job) for job in todo)
            left = spend.room(self.root, "tailor")
            if cost > left:
                detail = f"Up to ${cost:.2f} at full price with ${left:.2f} left today. {note}".strip()
                self._bulk(todo, run, "This goes past today's score and resume budget.", detail, "Tailor anyway", ask=True)
                return
        detail = f"{SCORE_WORKERS} at a time, each a one-page PDF under resumes/. {note}".strip()
        self._bulk(todo, run, f"Tailor {len(todo)} resumes?", detail, "Tailor them")

    def _tailor_waiting(self, job: dict) -> bool:
        return any(waiting.get("url") == job.get("url") for waiting in self._tailor_queue)

    def _start_tailor(self, job: dict) -> None:
        from hunt.cli import SCORE_WORKERS

        if len(self._tailoring) >= SCORE_WORKERS:
            self._tailor_queue.append(job)
            self._fill()
            return
        url = job.get("url") or ""
        self._tailoring.add(url)
        self.activity(f"Tailoring {job.get('company') or 'company'} — {job.get('title') or 'role'}…")
        self._fill()

        def work() -> None:
            from hunt.tailor import tailor_now

            made = tailor_now(self.root, job)
            self.call_from_thread(self._tailor_done, url, made.message, made.code != 0)

        self.run_worker(work, thread=True, group="score", exclusive=False)

    def _tailor_done(self, url: str, message: str, failed: bool) -> None:
        from hunt.cli import SCORE_WORKERS

        self._tailoring.discard(url)
        while self._tailor_queue and len(self._tailoring) < SCORE_WORKERS:
            self._start_tailor(self._tailor_queue.pop(0))
        self.reload()
        self.notify(message, severity="error" if failed else "information")

    def action_copy_prompt(self) -> None:
        job = self._selected()
        if job is None:
            self.notify("Highlight a role first")
            return
        import shutil
        import subprocess

        from hunt.tailor import raw_prompt

        text = raw_prompt(self.root, job)
        if shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=text, text=True, check=False)
            self.notify("Prompt copied. Paste it into Claude.ai, then check the .tex with: hunt resume check <file>")
            return
        path = self.root / "data/prompt.md"
        path.write_text(text, encoding="utf-8")
        self.notify(f"No clipboard tool here. The prompt is in {path.relative_to(self.root)}.")

    def action_view_pdf(self) -> None:
        job = self._selected()
        if job is None:
            self.notify("Highlight a role first")
            return
        import subprocess

        pdf = tailored_folder(self.root, job) / "resume.pdf"
        if not pdf.exists():
            self.notify("No PDF for this role yet.")
            return
        subprocess.run(["open", str(pdf)], check=False)

    def action_undo_applied(self) -> None:
        jobs = self._targets()
        if not jobs or self.view != "applied":
            return
        detail = "Their tracker rows go. A scored role keeps its score and goes back to the list."
        self._bulk(jobs, self._unmark, f"Undo applied on {len(jobs)} roles?", detail, "Undo applied")

    def action_dismiss_role(self) -> None:
        jobs = self._targets()
        if not jobs or self.view == "applied":
            return
        self._bulk(jobs, self._dismiss, f"Hide {len(jobs)} roles for good?", "They stay hidden even if they are posted again.", "Hide them")

    def _mark(self, jobs: list[dict]) -> None:
        from hunt.store import Busy

        try:
            mark_applied(self.root, *jobs)
        except Busy as exc:
            self.notify(str(exc), severity="error")
            return
        self.reload()
        if len(jobs) == 1:
            self.notify("Marked applied. It is in the Applied view (tab).")
        else:
            self.notify(f"Marked {len(jobs)} roles applied. They are in the Applied view (tab).")

    def _unmark(self, jobs: list[dict]) -> None:
        from hunt.store import Busy

        try:
            unmark_applied(self.root, *jobs)
        except Busy as exc:
            self.notify(str(exc), severity="error")
            return
        self.reload()
        if len(jobs) == 1:
            self.notify("No longer marked applied. It is back with the other roles.")
        else:
            self.notify(f"{len(jobs)} roles are no longer marked applied. They are back with the other roles.")

    def _dismiss(self, jobs: list[dict]) -> None:
        from hunt.store import Busy, dismiss

        try:
            dismiss(self.root, *jobs)
        except Busy as exc:
            self.notify(str(exc), severity="error")
            return
        self.reload()
        if len(jobs) == 1:
            self.notify("Hidden for good, even if it is posted again.")
        else:
            self.notify(f"Hid {len(jobs)} roles for good, even if they are posted again.")

    def _worth(self, job: dict):
        from hunt.screen import worth_of
        from hunt.worth import Worth

        if self._ctx is None or self.view == "applied":
            return Worth("apply", "")
        return worth_of(job, self._ctx)

    def action_company_class(self) -> None:
        job = self._selected()
        if job is None or self.view == "applied" or not job.get("company"):
            return
        company = job["company"]

        def chosen(klass: str | None) -> None:
            if not klass:
                return
            from hunt.worth import set_company_class

            set_company_class(self.root, company, klass)
            self.reload()
            self.notify(f"{company} is now {klass} for every posting.")

        self.push_screen(CompanyClassScreen(company), chosen)

    def action_keep_role(self) -> None:
        job = self._selected()
        if job is None or self.view == "applied":
            return
        from hunt.worth import keep_anyway

        keep_anyway(self.root, job)
        self.reload()
        self.notify("Kept. Worth rules will not hide this role.")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def _classify_message(root: Path, env: dict[str, str]) -> str:
    """Names the classifier that will really run. Asks Ollama, so it is called off the UI thread."""
    from hunt.gate import active_classifier, classifier

    active = active_classifier(root, env)
    if active == "clef":
        return "Clef-flash is classifying new roles locally. This does not call Claude."
    if classifier(root) == "clef":
        return "Haiku is classifying new roles through the Claude API, because Clef-flash is not running."
    return "Haiku is classifying new roles through the Claude API."


def _data_signature(root: Path) -> tuple[tuple[int, int] | None, ...]:
    stamps: list[tuple[int, int] | None] = []
    for name in WATCHED:
        try:
            stat = (root / name).stat()
        except OSError:
            stamps.append(None)
            continue
        stamps.append((stat.st_mtime_ns, stat.st_size))
    return tuple(stamps)


def _applied_rows(root: Path) -> list[dict]:
    import csv

    path = root / "data/job-tracker.csv"
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    for row in rows:
        row["title"] = row.get("role") or ""
        row["apply_url"] = row.get("url") or ""
        row["applied"] = "Yes"
    return list(reversed(rows))


def _mark_key(job: dict) -> str:
    return job.get("url") or f"{job.get('company') or ''}|{job.get('title') or job.get('role') or ''}"


def _skipped_note(skipped: dict[str, int]) -> str:
    if not skipped:
        return ""
    return "Skipped " + ", ".join(f"{count} {reason}" for reason, count in skipped.items()) + "."


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _meta(job: dict) -> str:
    posted = job.get("posted_at") or "date unknown"
    kind = "big org" if str(job.get("big")).lower() in {"true", "1"} else ""
    parts = [job.get("company") or "", job.get("location") or "location unknown", posted, kind, job.get("source") or ""]
    return " · ".join(part for part in parts if part)


def _posting(job: dict) -> str:
    apply = job.get("apply_url") or job.get("url") or "no apply link"
    raw = job.get("description") or ""
    short = shorten(raw)
    head = f"Apply: {apply}"
    if not short:
        return head + "\n\nNo description on this listing."
    body = short + "\n\nBoilerplate removed. The full text is still in the cache." if removed_boilerplate(raw, short) else short
    return head + "\n\n" + body


def _worth_line(worth) -> str:
    head = f"{'Maybe' if worth.verdict == 'maybe' else 'Hidden'}: {worth.reason}"
    if worth.evidence:
        evidence = worth.evidence if len(worth.evidence) <= 140 else worth.evidence[:140].rsplit(" ", 1)[0] + " …"
        head += f' "{evidence}"'
    return head + "   c sets the company class, k keeps this role"


def _years_note(job: dict) -> str:
    from hunt.trim import years_required

    years, sentence = years_required(job.get("description") or "")
    if years is None:
        return ""
    if len(sentence) > 140:
        sentence = sentence[:140].rsplit(" ", 1)[0] + " …"
    return f'Asks {years} years: "{sentence}"'


def _jev_line(job: dict) -> str:
    from hunt.gate import CLASSIFIERS

    if job.get("jev_worth") is None and job.get("jev_keep") is None:
        return "Not classified yet"
    by = job.get("jev_by") or "clef"
    decision = "keep" if job.get("jev_keep") else "skip"
    label = job.get("jev_fit_label") or ""
    extra = f" · {label}" if label else ""
    if job.get("jev_over_years"):
        extra += " · years over the cap"
    if by != "clef":
        return f"{CLASSIFIERS.get(by, by)} {decision}{extra}"
    worth = job.get("jev_worth")
    worth_text = f"{float(worth):.2f}" if isinstance(worth, (int, float)) else "—"
    return f"Clef-flash {worth_text} {decision}{extra}"


def _score_line(job: dict, hire_at: int) -> str:
    score = str(job.get("final_score") or "").strip()
    applied = (job.get("applied") or "").lower() == "yes"
    if not score and not applied:
        return "Not scored"
    bits = []
    if score:
        try:
            number = float(score)
        except ValueError:
            number = 0
        label = f"{score}"
        if number >= hire_at:
            label += "  natural hire"
        bits.append(label)
    if job.get("penalties"):
        bits.append(str(job["penalties"]))
    if applied:
        bits.append("applied")
    return "  ".join(bits)


def _score_cell(job: dict, hire_at: int):
    raw = str(job.get("final_score") or "").strip()
    if (job.get("applied") or "").lower() == "yes" and not raw:
        return Text("yes", style="#8a847c")
    if not raw:
        return Text("—", style="#8a847c")
    try:
        number = float(raw)
    except ValueError:
        return Text(raw)
    if number >= hire_at:
        return Text(raw, style="bold #e6b84d")
    return Text(raw, style="#c8c2b8")


def _posted_cell(job: dict):
    return job.get("posted_at") or Text("unknown", style="#8a847c")


def _open_url(url: str) -> None:
    if url:
        webbrowser.open(url)


def _open_all(jobs: list[dict]) -> None:
    for job in jobs:
        _open_url(job.get("apply_url") or job.get("url") or "")
