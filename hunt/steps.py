"""The desk's guided screens: Step 1 resume, Step 2 preferences, and Settings."""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Select, SelectionList, Static, TextArea

from hunt.inbox import read_resume, resume_is_placeholder, write_resume
from hunt.preferences import JOB_TYPES, SENIORITY, SIZES, WORK_MODES, load, migrate, save
from hunt.sources import load_env


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]


class AnswerScreen(ModalScreen[list[str] | None]):
    """The numbers the rewrite needs. A blank answer is saved as unknown."""

    def __init__(self, questions: list[str]) -> None:
        super().__init__()
        self.questions = questions

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("Before the rewrite")
            yield Static("Answer each number. A blank answer is saved as unknown.", classes="hint")
            with VerticalScroll(id="answer-scroll"):
                for index, question in enumerate(self.questions):
                    yield Label(question)
                    yield Input(placeholder="unknown", id=f"answer-{index}")
            yield Button("Continue rewrite", variant="primary", id="save-answers")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-answers":
            self.dismiss([self.query_one(f"#answer-{index}", Input).value.strip() for index in range(len(self.questions))])

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class ResumeStep(ModalScreen[bool]):
    """Step 1. Choose or paste the resume, then rework it with Claude or keep it as it is."""

    def __init__(self, root: Path, *, can_leave: bool) -> None:
        super().__init__()
        self._root = root
        self._can_leave = can_leave
        self._running = False

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("Step 1 of 2 · Your resume")
            yield Static(
                "Choose a file or paste the text. Rework with Claude tightens each line without adding facts. "
                "Use as is keeps it unchanged. Either way it becomes the resume every role is judged against.",
                classes="hint",
            )
            with Horizontal(id="pick-row"):
                yield Button("Choose file", id="pick")
                yield Input(placeholder="File path or Overleaf project URL, then Enter", id="path")
            yield TextArea("", id="resume-input", show_line_numbers=False)
            yield RichLog(id="step-progress", max_lines=60, markup=False, wrap=True)
            with Horizontal(id="step-buttons"):
                yield Button("Rework with Claude", variant="primary", id="rework")
                yield Button("Use as is", id="keep")

    def on_mount(self) -> None:
        if not resume_is_placeholder(self._root):
            self.query_one("#resume-input", TextArea).load_text(read_resume(self._root).strip())

    def on_key(self, event) -> None:
        if event.key == "escape" and self._can_leave and not self._running:
            event.stop()
            self.dismiss(False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "path":
            self._load(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pick":
            self.run_worker(self._pick, thread=True, exclusive=True)
        elif event.button.id == "keep":
            if self._save_text():
                self.dismiss(True)
        elif event.button.id == "rework":
            if self._save_text():
                self.app.ask_anthropic_key(lambda: self.app.ensure_workspace(lambda: self._start({})))

    def _pick(self) -> None:
        from hunt.onboard import pick_resume_file

        self.app.call_from_thread(self._load, pick_resume_file() or "")

    def _load(self, raw: str) -> None:
        if not raw:
            return
        from hunt.overleaf import load_source

        try:
            loaded = load_source(self._root, raw)
        except (OSError, RuntimeError, FileNotFoundError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.query_one("#resume-input", TextArea).load_text(loaded.text)
        self.query_one("#path", Input).value = raw

    def _save_text(self) -> bool:
        text = self.query_one("#resume-input", TextArea).text.strip()
        if len(text) < 80:
            self.notify("Paste a resume, or choose a file, first.", severity="error")
            return False
        if text != read_resume(self._root).strip():
            write_resume(self._root, text)
        return True

    def _progress(self, message: str) -> None:
        try:
            self.query_one("#step-progress", RichLog).write(message)
        except Exception:
            pass

    def _busy(self, running: bool) -> None:
        self._running = running
        for name in ("#rework", "#keep", "#pick"):
            self.query_one(name, Button).disabled = running

    def _start(self, extra: dict) -> None:
        self._busy(True)
        self._progress("Reworking with Claude. This takes a minute or two.")
        payload = {"resume": "profile/og-resume.md", **extra}
        self.run_worker(lambda: self._rework(payload), thread=True, exclusive=True)

    def _rework(self, payload: dict) -> None:
        from hunt.rewrite_api import RewriteError, run_resume_rewrite

        report = lambda message: self.app.call_from_thread(self._progress, message)
        try:
            result = run_resume_rewrite(self._root, payload, on_progress=report)
        except RewriteError as exc:
            self.app.call_from_thread(self._failed, str(exc))
            return
        self.app.call_from_thread(self._finished, result, payload)

    def _failed(self, message: str) -> None:
        self._busy(False)
        self._progress(message)
        self.notify(message, severity="error")

    def _finished(self, result, payload: dict) -> None:
        if result.paused:
            self._busy(False)

            def answered(replies: list[str] | None) -> None:
                if replies is None:
                    self._progress("The questions are saved in data/resume-answers.md. Rework again when ready.")
                    return
                from hunt.rewrite_api import write_answers

                text = write_answers(self._root, result.questions, replies)
                self._start({"answers": text})

            self.app.push_screen(AnswerScreen(result.questions), answered)
            return
        self._busy(False)
        self._progress(result.summary)
        if not result.clean:
            self.notify("The fact check failed, so your resume is unchanged. Rework again or use it as is.", severity="error")
            return
        if not result.saved:
            self._progress(result.next_steps)
            self.notify(result.next_steps, severity="warning")
            return
        self.notify("Resume reworked and saved. Next, your preferences.")
        self.dismiss(True)


class PreferencesStep(ModalScreen[bool]):
    """Step 2. Claude proposes from the resume; the person edits and saves."""

    def __init__(self, root: Path, *, can_leave: bool, auto_probe: bool = False) -> None:
        super().__init__()
        self._root = root
        self._can_leave = can_leave
        self._auto_probe = auto_probe
        self._adjacent: list[str] = []
        self._recount = None

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("Step 2 of 2 · What you want")
            yield Static("Reading your resume…", id="prefs-note", classes="hint")
            with VerticalScroll(id="prefs-scroll"):
                yield Label("Roles and seniority", classes="group")
                yield Static("Target roles, one per line. A title matches when it has all of a role's key words in any order, so Full Stack Developer also finds Fullstack Engineer. The first four are searched on the job boards.", classes="hint")
                yield TextArea("", id="pref-roles", show_line_numbers=False)
                yield Static("", id="prefs-counts", classes="hint")
                yield SelectionList[str](*[(level, level) for level in SENIORITY], id="pref-seniority")
                yield Label("Most years a posting may ask for")
                yield Input(id="pref-years", placeholder="5")
                yield Label("Where", classes="group")
                yield Label("Cities, comma separated")
                yield Input(id="pref-cities", placeholder="Delhi, Gurgaon, Bangalore")
                yield Label("Countries for any role, comma separated. With no cities, anywhere in these countries counts.")
                yield Input(id="pref-countries", placeholder="India")
                yield Label("Countries for remote roles only, comma separated")
                yield Input(id="pref-remote-countries", placeholder="United States")
                yield SelectionList[str](*[(mode, mode) for mode in WORK_MODES], id="pref-modes")
                yield Label("Pay", classes="group")
                with Horizontal(id="pay-row"):
                    yield Input(id="pref-pay", placeholder="Yearly floor, blank for none")
                    yield Input(id="pref-currency", placeholder="INR")
                yield Label("Company and job type", classes="group")
                yield Select([(size, size) for size in SIZES], value="any", allow_blank=False, id="pref-size")
                yield SelectionList[str](*[(kind, kind) for kind in JOB_TYPES], id="pref-types")
                yield Label("Titles to exclude, comma separated")
                yield Input(id="pref-exclude", placeholder="intern, recruiter")
                yield Label("Freshness", classes="group")
                yield Label("Hide roles posted more than N days ago (0 keeps all)")
                yield Input(id="pref-max-age", placeholder="0")
                yield Label("Daily Claude budget", classes="group")
                yield Static("Background checks sort, score, and write resumes on their own and stop for the day at each cap. 0 turns a step off.", classes="hint")
                with Horizontal(id="cap-row"):
                    yield Input(id="pref-classify-cap", placeholder="Sorting, USD per day")
                    yield Input(id="pref-score-cap", placeholder="Scoring and resumes, USD per day")
            yield Button("Save preferences", variant="primary", id="save-prefs")

    def on_mount(self) -> None:
        self._fill(load(self._root) or migrate(self._root))
        self.query_one("#save-prefs", Button).disabled = True
        self.app.ask_anthropic_key(self._propose, required=False)

    def _propose(self) -> None:
        self.run_worker(self._propose_worker, thread=True, exclusive=True)

    def _propose_worker(self) -> None:
        from hunt.preferences import propose

        prefs, note = propose(self._root)
        self.app.call_from_thread(self._proposed, prefs, note)

    def _proposed(self, prefs: dict, note: str) -> None:
        self._fill(prefs)
        self._adjacent = list(prefs.get("adjacent") or [])
        self.query_one("#prefs-note", Static).update(note)
        self.query_one("#save-prefs", Button).disabled = False
        # Onboarding probes the boards once; opening preferences later reuses that count.
        if self._auto_probe:
            self.app.start_probe(self._form_prefs())
        else:
            self._refresh_counts()

    def _refresh_counts(self) -> None:
        from datetime import date

        from hunt.preferences import compiled_search, normalize
        from hunt.probe import count_lines
        from hunt.probe import load as load_probe
        from hunt.search import load_search

        prefs = normalize(self._form_prefs())
        try:
            rule = compiled_search(prefs, load_search(self._root))
        except Exception:
            return
        running = bool(getattr(self.app, "_probe_running", False))
        lines = count_lines(load_probe(self._root), prefs["roles"], rule, self._adjacent, date.today(), running=running)
        self.query_one("#prefs-counts", Static).update("\n".join(lines))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._schedule_recount()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"pref-cities", "pref-countries", "pref-remote-countries"}:
            self._schedule_recount()

    def _schedule_recount(self) -> None:
        if self._recount is not None:
            self._recount.stop()
        self._recount = self.set_timer(0.5, self._refresh_counts)

    def _form_prefs(self) -> dict:
        """The form as it stands, unvalidated, for counting."""
        return {
            "roles": [line.strip() for line in self.query_one("#pref-roles", TextArea).text.splitlines() if line.strip()],
            "cities": _split(self.query_one("#pref-cities", Input).value),
            "countries": _split(self.query_one("#pref-countries", Input).value),
            "remote_only_countries": _split(self.query_one("#pref-remote-countries", Input).value),
            "work_modes": list(self.query_one("#pref-modes", SelectionList).selected),
            "exclude_titles": _split(self.query_one("#pref-exclude", Input).value),
        }

    def _fill(self, prefs: dict) -> None:
        self.query_one("#pref-roles", TextArea).load_text("\n".join(prefs["roles"]))
        self._choose("#pref-seniority", prefs["seniority"])
        self.query_one("#pref-years", Input).value = "" if prefs["max_years"] is None else str(prefs["max_years"])
        self.query_one("#pref-cities", Input).value = ", ".join(prefs["cities"])
        self.query_one("#pref-countries", Input).value = ", ".join(prefs["countries"])
        self.query_one("#pref-remote-countries", Input).value = ", ".join(prefs["remote_only_countries"])
        self._choose("#pref-modes", prefs["work_modes"])
        floor = prefs["pay_floor"]
        self.query_one("#pref-pay", Input).value = "" if floor["amount"] is None else str(floor["amount"])
        self.query_one("#pref-currency", Input).value = floor["currency"]
        self.query_one("#pref-size", Select).value = prefs["company_size"]
        self._choose("#pref-types", prefs["job_types"])
        self.query_one("#pref-exclude", Input).value = ", ".join(prefs["exclude_titles"])
        budget = prefs.get("budget") or {}
        self.query_one("#pref-classify-cap", Input).value = f"{budget.get('classify_usd_per_day', 0.5):g}"
        self.query_one("#pref-score-cap", Input).value = f"{budget.get('score_usd_per_day', 3.0):g}"
        from hunt.worth import load_rules

        self.query_one("#pref-max-age", Input).value = str(load_rules(self._root)[0].get("max_posted_days") or 0)

    def _choose(self, selector: str, values: list[str]) -> None:
        widget = self.query_one(selector, SelectionList)
        widget.deselect_all()
        for value in values:
            widget.select(value)

    def on_key(self, event) -> None:
        if event.key == "escape" and self._can_leave:
            event.stop()
            self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save-prefs":
            return
        roles = [line.strip() for line in self.query_one("#pref-roles", TextArea).text.splitlines() if line.strip()]
        if not roles:
            self.notify("Add at least one target role.", severity="error")
            return
        years = self.query_one("#pref-years", Input).value.strip()
        pay = self.query_one("#pref-pay", Input).value.strip().replace(",", "")
        try:
            years_value = int(years) if years else None
            pay_value = float(pay) if pay else None
            max_age = int(self.query_one("#pref-max-age", Input).value.strip() or 0)
            classify_cap = float(self.query_one("#pref-classify-cap", Input).value.strip() or 0)
            score_cap = float(self.query_one("#pref-score-cap", Input).value.strip() or 0)
        except ValueError:
            self.notify("Years, pay, age, and caps must be numbers.", severity="error")
            return
        if min(max_age, classify_cap, score_cap) < 0:
            self.notify("Age and caps cannot be negative.", severity="error")
            return
        modes = list(self.query_one("#pref-modes", SelectionList).selected)
        if not modes:
            self.notify("Pick at least one work mode.", severity="error")
            return
        save(
            self._root,
            {
                "roles": roles,
                "seniority": list(self.query_one("#pref-seniority", SelectionList).selected),
                "max_years": years_value,
                "cities": _split(self.query_one("#pref-cities", Input).value),
                "countries": _split(self.query_one("#pref-countries", Input).value),
                "remote_only_countries": _split(self.query_one("#pref-remote-countries", Input).value),
                "work_modes": modes,
                "pay_floor": {"amount": pay_value, "currency": self.query_one("#pref-currency", Input).value, "period": "year"},
                "company_size": str(self.query_one("#pref-size", Select).value),
                "job_types": list(self.query_one("#pref-types", SelectionList).selected),
                "exclude_titles": _split(self.query_one("#pref-exclude", Input).value),
                "adjacent": [role for role in self._adjacent if role in roles],
                "budget": {"classify_usd_per_day": classify_cap, "score_usd_per_day": score_cap},
            },
        )
        from hunt.worth import set_max_posted_days

        try:
            set_max_posted_days(self._root, max_age)
        except ValueError as exc:
            self.app.notify(str(exc), severity="warning")
        self.dismiss(True)


class KeyStep(ModalScreen[bool]):
    """Before Step 1 on a fresh clone: the Anthropic API key, or skip."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        from hunt.preferences import DEFAULT_BUDGET

        with Vertical(id="dialog"):
            yield Label("Before you start · Anthropic API key")
            yield Static(
                "Claude reads your resume to suggest roles, sorts new postings, scores the ones worth a look, and writes "
                "a one-page resume for the best of them. Background spend stops for the day at "
                f"${DEFAULT_BUDGET['classify_usd_per_day']:.2f} for sorting and ${DEFAULT_BUDGET['score_usd_per_day']:.2f} "
                "for scoring and resumes. You can change both in Step 2. The key is saved in .env and never printed.",
                classes="hint",
            )
            yield Input(placeholder="sk-ant-...", password=True, id="key-input")
            with Horizontal(id="step-buttons"):
                yield Button("Save key", variant="primary", id="key-save")
                yield Button("Skip for now", id="key-skip")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from hunt.inbox import store_anthropic_key

        if event.button.id == "key-skip":
            self.dismiss(False)
            return
        value = self.query_one("#key-input", Input).value.strip()
        if not value:
            self.notify("Paste a key, or skip for now.")
            return
        store_anthropic_key(self._root, value)
        self.dismiss(True)


class BudgetScreen(ModalScreen[bool]):
    """The two daily caps, shown once to a checkout that saved preferences before caps existed."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        prefs = load(self._root)
        budget = prefs["budget"] if prefs else {}
        with Vertical(id="dialog"):
            yield Label("Daily Claude budget")
            yield Static(
                "Background checks now sort, score, and write resumes on their own. Each stops for the day at its cap. "
                "A cap of 0 turns that step off.",
                classes="hint",
            )
            yield Label("Sorting new roles, USD per day")
            yield Input(value=f"{budget.get('classify_usd_per_day', 0.5):g}", id="budget-classify")
            yield Label("Scoring and resumes, USD per day")
            yield Input(value=f"{budget.get('score_usd_per_day', 3.0):g}", id="budget-score")
            yield Button("Save caps", variant="primary", id="save-budget")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from hunt.preferences import save_budget

        if event.button.id != "save-budget":
            return
        try:
            classify = float(self.query_one("#budget-classify", Input).value.strip() or 0)
            score = float(self.query_one("#budget-score", Input).value.strip() or 0)
        except ValueError:
            self.notify("Caps must be numbers.", severity="error")
            return
        if classify < 0 or score < 0:
            self.notify("Caps cannot be negative.", severity="error")
            return
        save_budget(self._root, {"classify_usd_per_day": classify, "score_usd_per_day": score})
        self.dismiss(True)


class ChoiceScreen(ModalScreen[str | None]):
    """A question with several answers. Returns the chosen id, or None on escape."""

    def __init__(self, question: str, detail: str, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self._question = question
        self._detail = detail
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._question, id="choice-question")
            yield Static(self._detail, classes="hint")
            with Horizontal(id="step-buttons"):
                for index, (choice, label) in enumerate(self._choices):
                    yield Button(label, variant="primary" if index == 0 else "default", id=f"choice-{choice}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss((event.button.id or "").removeprefix("choice-") or None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, question: str, detail: str, yes: str, no: str) -> None:
        super().__init__()
        self._question = question
        self._detail = detail
        self._yes = yes
        self._no = no

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._question)
            yield Static(self._detail, classes="hint")
            with Horizontal(id="step-buttons"):
                yield Button(self._yes, variant="primary", id="yes")
                yield Button(self._no, id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(False)


class SettingsScreen(ModalScreen[str | None]):
    """Schedule, check now, classifier, keys, companies, and workflows. Returns the action the desk should run."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("Settings")
            yield Static("", id="schedule-state", classes="hint")
            yield Static("", id="schedule-lines")
            with Horizontal(id="step-buttons"):
                yield Button("Turn schedule on", variant="primary", id="install")
                yield Button("Turn schedule off", id="uninstall")
                yield Button("Check now", id="check")
            yield Label("Quiet hours, 24-hour clock")
            with Horizontal(id="pay-row"):
                yield Input(id="quiet-start", placeholder="23:00")
                yield Input(id="quiet-end", placeholder="07:00")
                yield Button("Save quiet hours", id="save-quiet")
            yield Static("", id="classifier-state", classes="hint")
            with Horizontal(id="classifier-row"):
                yield Button("Use Haiku", id="classifier")
            yield Static("", id="source-lines", classes="hint")
            with Horizontal(id="step-buttons-2"):
                yield Button("API keys", id="keys")
                yield Button("Add company", id="add")
                yield Button("Workflows", id="workflows")

    def on_mount(self) -> None:
        self._refresh()

    def _env(self) -> dict[str, str]:
        load_env(self._root / ".env")
        return dict(os.environ)

    def _refresh(self) -> None:
        from hunt.gate import CLASSIFIERS, label
        from hunt.launchd import installed
        from hunt.schedule import load_state, status_lines
        from hunt.search import load_search
        from hunt.sources import format_status, source_status

        on = installed(self._root)
        self.query_one("#schedule-state", Static).update(
            "The schedule is on. launchd checks every 15 minutes and runs only the boards that are due."
            if on
            else "The schedule is off. Boards are fetched only when you press Check now."
        )
        self.query_one("#schedule-lines", Static).update("\n".join(status_lines(self._root, self._env())))
        quiet = load_state(self._root)["quiet"]
        self.query_one("#quiet-start", Input).value = quiet.get("start") or ""
        self.query_one("#quiet-end", Input).value = quiet.get("end") or ""
        haiku = label(self._root) == CLASSIFIERS["haiku"]
        self.query_one("#classifier-state", Static).update(
            "Haiku classifies new roles through the Claude API, billed to ANTHROPIC_API_KEY, about $0.0003 a role. "
            "Scheduled checks use it too."
            if haiku
            else "Clef-flash classifies new roles on this Mac through Ollama. Free."
        )
        self.query_one("#classifier", Button).label = "Use Clef-flash" if haiku else "Use Haiku"
        search = load_search(self._root)
        self.query_one("#source-lines", Static).update(format_status(source_status(search.get("sources") or [], self._env())))
        self.query_one("#install", Button).disabled = on
        self.query_one("#uninstall", Button).disabled = not on

    def on_button_pressed(self, event: Button.Pressed) -> None:
        name = event.button.id
        if name in {"check", "keys", "add", "workflows"}:
            self.dismiss(name)
            return
        if name == "save-quiet":
            from hunt.schedule import save_quiet

            try:
                save_quiet(self._root, self.query_one("#quiet-start", Input).value.strip(), self.query_one("#quiet-end", Input).value.strip())
            except ValueError:
                self.notify("Use HH:MM, for example 23:00.", severity="error")
                return
            self.notify("Quiet hours saved")
        elif name == "classifier":
            from hunt.gate import CLASSIFIERS, GateError, label, set_classifier

            chosen = "clef" if label(self._root) == CLASSIFIERS["haiku"] else "haiku"
            if chosen == "haiku" and not self._env().get("ANTHROPIC_API_KEY", "").strip():
                self.notify("Haiku needs ANTHROPIC_API_KEY. Add it with API keys first.", severity="error")
                return
            try:
                set_classifier(self._root, chosen)
            except GateError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(f"{CLASSIFIERS[chosen]} now classifies new roles. Roles already classified keep their decision.")
        elif name in {"install", "uninstall"}:
            from hunt.launchd import install, uninstall

            try:
                message = install(self._root) if name == "install" else uninstall(self._root)
            except RuntimeError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(message)
        self._refresh()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
