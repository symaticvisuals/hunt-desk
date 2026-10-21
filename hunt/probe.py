"""Step 2's live counts. One fetch of every board without the title filter, then each title is counted locally as the person edits."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from hunt.store import read_json, write_json

PROBE = "data/probe.json"
WINDOW_DAYS = 14
# JobSpy searches only the first four titles. The rest are counted from ATS boards and feeds.
JOBSPY_TITLES = 4


@dataclass
class Probe:
    rows: list[dict] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    done: bool = False
    at: str = ""


def load(root: Path) -> Probe:
    raw = read_json(root / PROBE, {})
    if not isinstance(raw, dict):
        return Probe()
    return Probe(list(raw.get("rows") or []), list(raw.get("failed") or []), list(raw.get("roles") or []), bool(raw.get("done")), str(raw.get("at") or ""))


def _save(root: Path, probe: Probe) -> None:
    write_json(root / PROBE, {"at": probe.at, "roles": probe.roles, "failed": probe.failed, "done": probe.done, "rows": probe.rows})


def run_probe(root: Path, env: dict[str, str], prefs: dict, *, on_unit=None, now: datetime | None = None) -> Probe:
    """Fetch every unit once without the title filter. The board schedule is left alone, so the next real check still
    fetches everything new since its last run. Raises store.Busy when a check is already running."""
    from hunt.preferences import compiled_search, normalize
    from hunt.search import _fetch_unit, load_search, load_watchlist, source_units
    from hunt.store import run_lock

    now = now or datetime.now().astimezone()
    prefs = normalize(prefs)
    search = compiled_search(prefs, load_search(root))
    search.setdefault("source_options", {}).setdefault("jobspy", {})["hours_old"] = WINDOW_DAYS * 24
    units = source_units(search, env)
    watchlist = load_watchlist(root)
    probe = Probe(roles=list(prefs["roles"]), at=now.isoformat())

    def fetch(unit: str) -> tuple[str, list, list[str]]:
        try:
            jobs, errors = _fetch_unit(unit, search, env, watchlist, lambda line: None, None)
            return unit, jobs, list(errors)
        except Exception as exc:
            return unit, [], [f"{unit}: {type(exc).__name__}: {exc}"]

    with run_lock(root):
        _save(root, probe)
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(units)))) as pool:
            for future in as_completed([pool.submit(fetch, unit) for unit in units]):
                unit, jobs, errors = future.result()
                probe.rows.extend(job.as_dict() for job in jobs)
                if errors and not jobs:
                    probe.failed.append(unit)
                _save(root, probe)
                if on_unit:
                    on_unit(unit, not (errors and not jobs))
        probe.done = True
        _save(root, probe)
    return probe


def counts(probe: Probe, roles: list[str], rule: dict, today: date) -> dict[str, int]:
    """rule is a compiled search. Each posting counts once per title, within the window and the places."""
    from hunt.preferences import places_ok
    from hunt.store import role_key
    from hunt.titles import role_matches

    place = rule.get("location") or {}
    cutoff = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    unique: dict[str, dict] = {}
    for row in probe.rows:
        posted = str(row.get("posted_at") or "")
        if posted and posted < cutoff:
            continue
        if not places_ok(row.get("location") or "", row.get("work_mode") or "", place):
            continue
        unique.setdefault(role_key(row) or row.get("url") or "", row)
    return {role: sum(1 for row in unique.values() if role_matches(row.get("title") or "", role)) for role in roles}


def count_lines(probe: Probe, roles: list[str], rule: dict, adjacent: list[str], today: date, *, running: bool) -> list[str]:
    if not probe.rows and not running:
        return []
    place = rule.get("location") or {}
    where = ", ".join(place.get("cities") or []) or next(iter(place.get("countries") or []), "") or "anywhere"
    found = counts(probe, roles, rule, today)
    searched = {role.lower() for role in probe.roles[:JOBSPY_TITLES]}
    marked = {role.lower() for role in adjacent}
    lines = []
    for role in roles:
        count = found.get(role, 0)
        if count == 0:
            text = f"{role}: 0 open · check the title"
        elif role.lower() in searched:
            text = f"{role}: {count} open · {where} · {WINDOW_DAYS} days"
        else:
            text = f"{role}: {count} open · boards only"
        if role.lower() in marked:
            text += " · adjacent"
        lines.append(text)
    if probe.failed:
        lines.append(f"No answer from: {', '.join(probe.failed)}")
    if running:
        lines.append("Counting…")
    return lines


def merge_saved(root: Path, now: datetime) -> int:
    """After save, the probe's rows that match the saved titles go straight into the inbox."""
    from hunt.evaluated import extract_job_id
    from hunt.search import load_search, title_matches
    from hunt.store import merge_into_inbox

    probe = load(root)
    if not probe.rows:
        return 0
    search = load_search(root)
    rows = []
    for row in probe.rows:
        if title_matches(row.get("title") or "", search):
            row = dict(row)
            row["job_id"] = extract_job_id(row.get("url") or "", row.get("platform") or "", str(row.get("job_id") or ""))
            rows.append(row)
    return len(merge_into_inbox(root, rows, now)) if rows else 0
