"""Fetch boards and feeds, then keep titles that match profile/search.json."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from hunt.evaluated import classify, extract_job_id, read_csv
from hunt.sources import (
    ATS_FETCH,
    FEED_FETCH,
    SPECS,
    Job,
    SourceError,
    fetch_board,
    hydrate_greenhouse,
    jobspy_sites,
    source_status,
)

ROOT_FILES = {
    "search": "profile/search.json",
    "watchlist": "data/watchlist.json",
    "evaluated": "data/evaluated-jobs.csv",
    "cache": "data/last-search.json",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_search(root: Path) -> dict:
    path = root / ROOT_FILES["search"]
    if not path.exists():
        raise SourceError(f"missing {ROOT_FILES['search']}")
    search = json.loads(path.read_text(encoding="utf-8"))
    mode = ((search.get("location") or {}).get("mode")) or "any"
    if mode not in {"any", "remote_only", "countries", "places"}:
        raise SourceError("location.mode must be any, remote_only, countries, or places")
    if mode == "countries" and not (search.get("location") or {}).get("countries"):
        raise SourceError("location.mode countries needs a countries array")
    search.setdefault("keywords", [])
    search.setdefault("exclude_title", [])
    search.setdefault("sources", [])
    return search


def load_watchlist(root: Path) -> list[dict]:
    path = root / ROOT_FILES["watchlist"]
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SourceError("data/watchlist.json must be a list")
    return payload


def title_matches(title: str, search: dict) -> bool:
    from hunt.titles import excluded, role_matches

    if excluded(title, search.get("exclude_title") or []):
        return False
    keywords = search.get("keywords") or []
    if not keywords:
        return True
    return any(role_matches(title, role) for role in keywords)


def location_ok(location: str, search: dict, work_mode: str = "") -> bool:
    rule = search.get("location") or {}
    mode = rule.get("mode") or "any"
    if mode == "any":
        return True
    if mode == "places":
        from hunt.preferences import places_ok

        return places_ok(location, work_mode, rule)
    text = (location or "").lower()
    if mode == "remote_only":
        return "remote" in text
    countries = [str(country).lower() for country in rule.get("countries") or []]
    return any(country and country in text for country in countries)


FETCH_WORKERS = 8
BOARD_WORKERS = 4
_BLOCKED = re.compile(r"\b(429|403|406)\b|rate limit|network error|recaptcha|blocked", re.IGNORECASE)


@dataclass
class UnitOutcome:
    found: int = 0
    matched: int = 0
    errors: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """A board pushed back, or nothing came back and something failed."""
        if any(_BLOCKED.search(error) for error in self.errors):
            return True
        return bool(self.errors) and self.found == 0


@dataclass
class SearchResult:
    jobs: list[Job] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    fetched: int = 0
    errors: list[str] = field(default_factory=list)
    on_progress: object = None
    units: dict[str, UnitOutcome] = field(default_factory=dict)
    # Every title match, whatever its place. The inbox keeps these; `jobs` is the screened list for last-search.json.
    matches: list[Job] = field(default_factory=list)


def _remember(result: SearchResult, line: str) -> None:
    result.lines.append(line)
    report = result.on_progress
    if report:
        report(line)


def source_units(search: dict, env: dict[str, str]) -> list[str]:
    """What the schedule runs: one unit per ATS platform, per feed, and per JobSpy site."""
    units: list[str] = []
    for name in search.get("sources") or []:
        spec = SPECS.get(name)
        if spec is None or (spec.env and any(not (env.get(key) or "").strip() for key in spec.env)):
            continue
        if name == "jobspy":
            units.extend(f"jobspy:{site}" for site in jobspy_sites(search))
        else:
            units.append(name)
    return units


def collect(
    root: Path,
    env: dict[str, str],
    on_progress=None,
    units: list[str] | None = None,
    windows: dict[str, int] | None = None,
) -> SearchResult:
    """Fetch units (all of them by default), keep title and place matches, and drop scored or screened roles."""
    search = load_search(root)
    result = SearchResult(on_progress=on_progress)
    if units is None:
        enabled = list(search.get("sources") or [])
        for row in source_status(enabled, env):
            if row["state"] == "skip":
                _remember(result, f"skip {row['name']}: {row['detail']}")
        for name in enabled:
            if name not in SPECS:
                _remember(result, f"error {name}: unknown source")
                result.errors.append(name)
        units = source_units(search, env)

    watchlist = load_watchlist(root)
    for unit in units:
        result.units.setdefault(unit, UnitOutcome())

    def fetch(unit: str) -> tuple[list[Job], list[str]]:
        outcome = result.units[unit]

        def report(line: str) -> None:
            if unit.startswith("jobspy:"):
                line = line.replace("jobspy", unit, 1)
            if line.startswith("error "):
                outcome.errors.append(line[len("error ") :])
            _remember(result, line)

        try:
            return _fetch_unit(unit, search, env, watchlist, report, (windows or {}).get(unit))
        except Exception as exc:
            return [], [f"{unit}: {type(exc).__name__}: {exc}"]

    # Units are different hosts, so they run together. A JobSpy site's own searches stay one at a time inside its unit.
    with ThreadPoolExecutor(max_workers=max(1, min(FETCH_WORKERS, len(units)))) as pool:
        fetched = list(pool.map(fetch, units))
    for unit, (found, failures) in zip(units, fetched):
        outcome = result.units[unit]
        for failure in failures:
            outcome.errors.append(failure)
            _remember(result, f"error {failure}")
            result.errors.append(unit)
        titled = [job for job in found if title_matches(job.title, search)]
        placed = [job for job in titled if location_ok(job.location, search, job.work_mode)]
        outcome.found = len(found)
        outcome.matched = len(placed)
        outcome.urls = [job.url for job in placed]
        result.fetched += len(found)
        _remember(result, f"{unit}: {len(found)} listings, {len(titled)} title matches, {len(placed)} in your places")
        result.matches.extend(titled)
        result.jobs.extend(placed)

    _hydrate(result)
    existing = read_csv(root / ROOT_FILES["evaluated"])
    statuses = classify([job.as_dict() for job in result.jobs], existing)
    new_jobs = [job for job, status in zip(result.jobs, statuses) if status == "NEW"]
    dupes = len(result.jobs) - len(new_jobs)
    from hunt.screen import max_years, prepare

    result.jobs, notes = prepare(new_jobs, max_years(root))
    if dupes:
        _remember(result, f"duplicates skipped: {dupes}")
    for note in notes:
        _remember(result, note)
    for job in result.matches:
        job.job_id = extract_job_id(job.url, job.platform, job.job_id)
    return result


def _fetch_unit(
    unit: str,
    search: dict,
    env: dict[str, str],
    watchlist: list[dict],
    report,
    window: int | None,
) -> tuple[list[Job], list[str]]:
    if not unit.startswith("jobspy:"):
        return _fetch_source(unit, search, env, watchlist, report)
    local = json.loads(json.dumps(search))
    options = local.setdefault("source_options", {}).setdefault("jobspy", {})
    options["sites"] = [unit.split(":", 1)[1]]
    if window:
        options["hours_old"] = window
    return _fetch_source("jobspy", local, env, watchlist, report)


def _fetch_source(
    name: str,
    search: dict,
    env: dict[str, str],
    watchlist: list[dict],
    report=None,
) -> tuple[list[Job], list[str]]:
    if name in ATS_FETCH:
        boards = [board for board in watchlist if board.get("platform") == name]

        def one(board: dict) -> tuple[list[Job], str]:
            label = board.get("company") or board.get("slug") or name
            try:
                return fetch_board(name, board, search), ""
            except (SourceError, KeyError) as exc:
                return [], f"{name} {label}: {exc}"

        jobs: list[Job] = []
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=max(1, min(BOARD_WORKERS, len(boards)))) as pool:
            for found, failure in pool.map(one, boards):
                jobs.extend(found)
                if failure:
                    failures.append(failure)
        return jobs, failures
    fetch = FEED_FETCH.get(name)
    if fetch is None:
        return [], [f"{name}: no adapter"]
    try:
        if name == "jobspy":
            return fetch(search, env, on_progress=report), []
        return fetch(search, env), []
    except SourceError as exc:
        return [], [f"{name}: {exc}"]


def _hydrate(result: SearchResult) -> None:
    remaining = 20
    for job in result.jobs:
        if remaining <= 0:
            break
        if job.platform != "greenhouse" or job.description:
            continue
        try:
            hydrate_greenhouse(job)
        except SourceError as exc:
            _remember(result, f"error greenhouse detail: {exc}")
        remaining -= 1


def write_cache(root: Path, result: SearchResult) -> Path:
    path = root / ROOT_FILES["cache"]
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched": result.fetched,
        "new": len(result.jobs),
        "lines": result.lines,
        "jobs": [job.as_dict() for job in result.jobs],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def format_jobs(result: SearchResult, limit: int = 40) -> str:
    lines = list(result.lines)
    if not result.jobs:
        lines.append(f"0 new jobs. Fetched {result.fetched} listings.")
        return "\n".join(lines)
    shown = result.jobs[:limit]
    for job in shown:
        preview = (job.description or "").strip()
        if len(preview) > 280:
            preview = preview[:277] + "..."
        lines.append("")
        lines.append(f"NEW  {job.company} — {job.title}")
        lines.append(f"     {job.url}")
        lines.append(f"     {job.location or 'location unknown'} · {job.source} · {job.job_id}")
        if preview:
            lines.append(f"     {preview}")
    extra = len(result.jobs) - len(shown)
    if extra > 0:
        lines.append("")
        lines.append(f"{extra} more in data/last-search.json")
    lines.append("")
    lines.append(f"{len(result.jobs)} new jobs. Fetched {result.fetched} listings. Nothing was written to the CSV.")
    return "\n".join(lines)
