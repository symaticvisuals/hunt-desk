"""Scheduled fetching. launchd runs `hunt tick` every 15 minutes; this decides which boards are due."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from hunt.search import SearchResult, UnitOutcome, collect, load_search, source_units, write_cache
from hunt.sources import ATS_FETCH
from hunt.store import Busy, data_lock, merge_into_inbox, read_json, run_lock, update_inbox, write_json

SCHEDULE = "data/schedule.json"
LOG = "data/schedule.log"
LOG_LINES = 500
# Minutes: base, floor, ceiling.
CADENCE = {"ats": (60, 30, 240), "feed": (120, 60, 480), "jobspy": (240, 120, 960)}
BACKOFF_CAP = 24 * 60
EMPTY_RUNS_BEFORE_SLOWING = 3
DEFAULT_QUIET = {"start": "23:00", "end": "07:00"}
LIVE_PER_TICK = 30
LIVE_WORKERS = 8
LIVE_RECHECK = timedelta(hours=24)


@dataclass
class Outcome:
    new: int
    blocked: bool
    error: str = ""


@dataclass
class RunResult:
    units: list[str] = field(default_factory=list)
    added: list[dict] = field(default_factory=list)
    announced: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    search: SearchResult | None = None
    skipped: str = ""


def kind_of(unit: str) -> str:
    if unit.startswith("jobspy:"):
        return "jobspy"
    return "ats" if unit in ATS_FETCH else "feed"


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def next_state(unit: str, state: dict, outcome: Outcome, now: datetime, rng: random.Random) -> dict:
    """Pure interval policy: backoff on a block, faster on new roles, slower after empty runs, with jitter."""
    base, floor, ceiling = CADENCE[kind_of(unit)]
    updated = dict(state)
    interval = float(updated.get("interval_minutes") or base)
    updated["last_run"] = now.isoformat()
    updated["last_error"] = outcome.error
    if outcome.blocked:
        updated["blocked_runs"] = int(updated.get("blocked_runs") or 0) + 1
        wait = min(BACKOFF_CAP, base * 2 ** updated["blocked_runs"])
    else:
        updated["blocked_runs"] = 0
        updated["last_clean_run"] = now.isoformat()
        updated["last_new"] = outcome.new
        if outcome.new > 0:
            updated["empty_runs"] = 0
            interval = max(floor, interval * 0.75)
        else:
            updated["empty_runs"] = int(updated.get("empty_runs") or 0) + 1
            if updated["empty_runs"] >= EMPTY_RUNS_BEFORE_SLOWING:
                interval = min(ceiling, interval * 1.5)
                updated["empty_runs"] = 0
        wait = interval
    updated["interval_minutes"] = round(interval, 2)
    updated["next_at"] = (now + timedelta(minutes=wait * rng.uniform(0.9, 1.1))).isoformat()
    return updated


def jobspy_window(state: dict, now: datetime, cap: int = 168) -> int:
    """Hours of postings to ask JobSpy for: since the last clean run, plus two hours of overlap."""
    last = _parse(state.get("last_clean_run"))
    if last is None:
        return min(48, cap)
    hours = math.ceil(max(0.0, (now - last).total_seconds()) / 3600) + 2
    return max(6, min(cap, hours))


def window_cap(root: Path) -> int:
    """A board blocked for days would otherwise ask for postings older than max_posted_days, which the desk hides."""
    days = int((read_json(root / "profile/worth.json", {}) or {}).get("max_posted_days") or 0)
    return (days + 1) * 24 if days > 0 else 168


def _clock(text: str) -> time:
    hour, minute = (int(part) for part in str(text).split(":", 1))
    return time(hour, minute)


def in_quiet(now: datetime, quiet: dict) -> bool:
    try:
        start, end = _clock(quiet.get("start") or ""), _clock(quiet.get("end") or "")
    except ValueError:
        return False
    moment = now.time().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def due_units(state: dict, units: list[str], now: datetime) -> list[str]:
    known = state.get("units") or {}
    due = []
    for unit in units:
        next_at = _parse((known.get(unit) or {}).get("next_at"))
        if next_at is None or next_at <= now:
            due.append(unit)
    return due


def load_state(root: Path) -> dict:
    state = read_json(root / SCHEDULE, {})
    state = state if isinstance(state, dict) else {}
    state.setdefault("quiet", dict(DEFAULT_QUIET))
    state.setdefault("units", {})
    return state


def save_quiet(root: Path, start: str, end: str) -> None:
    _clock(start)
    _clock(end)
    with data_lock(root):
        state = load_state(root)
        state["quiet"] = {"start": start, "end": end}
        write_json(root / SCHEDULE, state)


def _log(root: Path, lines: list[str], now: datetime) -> None:
    path = root / LOG
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    stamp = now.strftime("%Y-%m-%d %H:%M")
    kept = (old + [f"{stamp} {line}" for line in lines])[-LOG_LINES:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def tick(root: Path, env: dict[str, str], *, now: datetime | None = None, force: bool = False, **hooks) -> RunResult:
    """One heartbeat: skip in quiet hours or when nothing is due, otherwise run the due units."""
    now = now or datetime.now().astimezone()
    state = load_state(root)
    if not force and in_quiet(now, state["quiet"]):
        return RunResult(skipped="quiet hours")
    _poll_batches(root, now)
    units = source_units(load_search(root), env)
    due = units if force else due_units(state, units, now)
    if not due:
        _advance_idle(root, now, env)
        return RunResult(skipped="nothing due")
    try:
        return run(root, env, units=due, now=now, notify=True, **hooks)
    except Busy:
        return RunResult(skipped="a check is already running")


def run(
    root: Path,
    env: dict[str, str],
    *,
    units: list[str] | None = None,
    now: datetime | None = None,
    full: bool = False,
    notify: bool = False,
    on_progress=None,
    classifier=None,
    checker=None,
    notifier=None,
    describer=None,
    rng: random.Random | None = None,
) -> RunResult:
    """Fetch, merge into the inbox, classify, live-check, record each unit, then announce new roles.

    full=True fetches every unit and writes data/last-search.json the way `hunt search` always has.
    Raises Busy when another run holds the run lock.
    """
    now = now or datetime.now().astimezone()
    rng = rng or random.Random()
    lines: list[str] = []

    def report(line: str) -> None:
        lines.append(line)
        if on_progress:
            on_progress(line)

    with run_lock(root):
        state = load_state(root)
        first_run = not state["units"]
        chosen = source_units(load_search(root), env) if full or units is None else list(units)
        cap = window_cap(root)
        windows = {unit: jobspy_window(state["units"].get(unit) or {}, now, cap) for unit in chosen if unit.startswith("jobspy:")}
        found = collect(root, env, on_progress=report, units=None if full else chosen, windows=windows)
        added = merge_into_inbox(root, [job.as_dict() for job in found.matches], now)
        if full:
            write_cache(root, found)
        added_urls = {record.get("url") for record in added}
        result = RunResult(units=chosen, added=added, search=found)
        for unit in chosen:
            outcome = found.units.get(unit) or UnitOutcome()
            new = sum(1 for url in outcome.urls if url in added_urls)
            error = "; ".join(outcome.errors)[:300]
            with data_lock(root):
                latest = load_state(root)
                latest["units"][unit] = next_state(unit, latest["units"].get(unit) or {}, Outcome(new, outcome.blocked, error), now, rng)
                latest["last_tick"] = now.isoformat()
                write_json(root / SCHEDULE, latest)
            report(f"{unit}: {new} new{' · blocked' if outcome.blocked else ''}")
            if error:
                result.errors.append(f"{unit}: {error}")

        _describe(root, report, fetcher=describer)
        _classify(root, now, report, classifier, result, env)
        _check_live(root, now, report, checker)
        _advance(root, now, report, env, result)
        result.announced = _mark_visible(root, now)
        if result.announced and notify and not first_run:
            send = notifier
            if send is None:
                from hunt.notify import send
            try:
                send(root, result.announced)
            except Exception as exc:
                report(f"notification failed: {exc}")
        report(f"run done: {len(added)} new in inbox, {len(result.announced)} newly worth a look")
    _log(root, lines, now)
    return result


def _advance(root: Path, now: datetime, report, env: dict[str, str], result: RunResult) -> None:
    from hunt import pipeline

    for error in (pipeline.tailor_stage(root, now, report, env), pipeline.score_stage(root, now, report, env)):
        if error:
            result.errors.append(error)


def _advance_idle(root: Path, now: datetime, env: dict[str, str]) -> None:
    """Nothing to fetch: still send waiting roles to Claude within the caps."""
    from hunt import pipeline

    if not pipeline.allowed(root, env):
        return
    lines: list[str] = []
    try:
        with run_lock(root):
            pipeline.tailor_stage(root, now, lines.append, env)
            pipeline.score_stage(root, now, lines.append, env)
    except Busy:
        return
    except Exception as exc:
        lines.append(f"error pipeline: {type(exc).__name__}: {exc}")
    if lines:
        _log(root, lines, now)


def _poll_batches(root: Path, now: datetime) -> None:
    """Apply finished Batch API results. Cheap when nothing is open, so every tick checks."""
    from hunt import batch, notify

    if not batch.open_batches(root):
        return
    try:
        notes = batch.poll(root)
    except Exception as exc:
        _log(root, [f"error batch poll: {type(exc).__name__}: {exc}"], now)
        return
    if notes:
        _log(root, notes, now)
    for note in notes:
        try:
            notify.post(root, note)
        except Exception:
            pass


def _jobs_with_reasons(root: Path, now: datetime) -> list[tuple[dict, str]]:
    from hunt.inbox import load_jobs
    from hunt.screen import context, hidden_reason

    ctx = context(root, now.date())
    return [(job, hidden_reason(job, ctx)) for job in load_jobs(root)]


def _describe(root: Path, report, limit: int = 30, fetcher=None) -> int:
    """Fetch the text of listings that came without it, before the classifier and the worth rules read them."""
    from hunt.inbox import load_jobs
    from hunt.screen import context, needs_text
    from hunt.sources import DETAIL_PLATFORMS
    from hunt.trim import has_text

    if fetcher is None:
        from hunt.sources import fetch_description as fetcher
    ctx = context(root)
    wanted = [job for job in load_jobs(root) if job.get("platform") in DETAIL_PLATFORMS and needs_text(job, ctx)][:limit]
    if not wanted:
        return 0
    changes: dict[str, dict] = {}
    for job in wanted:
        try:
            text = fetcher(job) or ""
        except Exception as exc:
            report(f"error description {job.get('company') or ''}: {type(exc).__name__}: {exc}")
            text = ""
        url = job.get("url") or ""
        tries = {"description_tries": int(job.get("description_tries") or 0) + 1}
        if has_text(text):
            changes[url] = {"description": text}
        else:
            changes[url] = {"description": text, **tries} if len(text.strip()) > len((job.get("description") or "").strip()) else tries
    update_inbox(root, changes)
    filled_jobs = [job for job in wanted if has_text(changes[job.get("url") or ""].get("description") or "")]
    # Whatever the classifier decided before was decided without this text.
    if filled_jobs:
        from hunt.gate import forget

        forget(root, filled_jobs)
    filled = len(filled_jobs)
    report(f"descriptions: {filled} fetched, {len(changes) - filled} still empty")
    return filled


def _classify(root: Path, now: datetime, report, classifier, result: RunResult, env: dict[str, str]) -> None:
    pending = [job for job, reason in _jobs_with_reasons(root, now) if reason == "not classified yet"]
    if not pending:
        return
    from hunt.gate import CLASSIFIERS, GateError, active_classifier, classifier as chosen_classifier, classify_pending

    active = active_classifier(root, env) if classifier is None else chosen_classifier(root)
    name = CLASSIFIERS.get(active, active)
    if active != chosen_classifier(root):
        name += " (Clef-flash is not running)"
    report(f"{name}: classifying {len(pending)} roles")
    try:
        if classifier is None:
            classify_pending(root, pending, on_progress=None, env=env)
        else:
            classifier(pending, report)
    except GateError as exc:
        result.errors.append(f"{name}: {exc}")
        report(f"error {name}: {exc}")


def _check_live(root: Path, now: datetime, report, checker) -> None:
    if checker is None:
        from hunt.live import check_live as checker
    candidates = []
    for job, reason in _jobs_with_reasons(root, now):
        checked = _parse(job.get("live_checked_at"))
        if not reason and (checked is None or now - checked >= LIVE_RECHECK):
            candidates.append(job)
    from concurrent.futures import ThreadPoolExecutor

    batch = candidates[:LIVE_PER_TICK]
    changes = {}
    if batch:
        with ThreadPoolExecutor(max_workers=min(LIVE_WORKERS, len(batch))) as pool:
            states = list(pool.map(lambda job: checker(job.get("apply_url") or job.get("url") or ""), batch))
        stamp = now.isoformat()
        changes = {job["url"]: {"live": state, "live_checked_at": stamp} for job, state in zip(batch, states)}
    update_inbox(root, changes)
    closed = sum(1 for change in changes.values() if change["live"] == "closed")
    if changes:
        report(f"live check: {len(changes)} checked, {closed} closed")


def _mark_visible(root: Path, now: datetime) -> list[dict]:
    fresh = [job for job, reason in _jobs_with_reasons(root, now) if not reason and not job.get("first_visible_at")]
    update_inbox(root, {job["url"]: {"first_visible_at": now.isoformat()} for job in fresh})
    return fresh


def status_lines(root: Path, env: dict[str, str], now: datetime | None = None) -> list[str]:
    now = now or datetime.now().astimezone()
    state = load_state(root)
    quiet = state["quiet"]
    lines = [f"quiet hours {quiet.get('start')}–{quiet.get('end')}"]
    for unit in source_units(load_search(root), env):
        unit_state = state["units"].get(unit) or {}
        next_at = _parse(unit_state.get("next_at"))
        when = "due now" if next_at is None or next_at <= now else f"next {next_at.astimezone(now.tzinfo).strftime('%H:%M')}"
        interval = unit_state.get("interval_minutes") or CADENCE[kind_of(unit)][0]
        detail = f"every {int(interval)} min"
        if unit_state.get("blocked_runs"):
            detail += f", backing off ({unit_state['blocked_runs']} blocked)"
        if "last_new" in unit_state:
            detail += f", last run +{unit_state['last_new']}"
        lines.append(f"{unit:18} {when:12} {detail}")
    return lines


def next_check(root: Path, env: dict[str, str]) -> datetime | None:
    state = load_state(root)
    times = [_parse((state["units"].get(unit) or {}).get("next_at")) for unit in source_units(load_search(root), env)]
    times = [stamp for stamp in times if stamp is not None]
    return min(times) if times else None
