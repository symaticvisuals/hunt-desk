"""Load and save the desk. The Textual screens call this. They do not score."""

from __future__ import annotations

import json
import os
from html import unescape
from pathlib import Path

from hunt.evaluated import FIELDS, read_csv, write_csv
from hunt.search import location_ok, load_search, title_matches
from hunt.sources import ATS_FETCH, SourceError, fetch_board, load_env

CACHE = "data/last-search.json"
TRACKER = "data/job-tracker.csv"
TRACKER_FIELDS = ["date", "company", "role", "status", "reached_interview", "url", "notes"]


def plain(value: str) -> str:
    return unescape(value or "").replace("\xa0", " ").strip()


def load_json(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload) -> None:
    from hunt.store import write_json

    write_json(path, payload)


def load_jobs(root: Path) -> list[dict]:
    """Every role in the inbox, with scores and classifier decisions attached. Newest posting first."""
    from datetime import datetime, timezone

    from hunt.store import load_inbox, seed_inbox

    seed_inbox(root, datetime.now(timezone.utc))
    jobs = []
    for item in load_inbox(root):
        job = dict(item)
        job["title"] = job.get("title") or job.get("role") or ""
        job["role"] = job["title"]
        job["description"] = plain(job.get("description") or "")
        job["final_score"] = ""
        job["applied"] = ""
        jobs.append(job)
    _attach_scores(jobs, read_csv(root / "data/evaluated-jobs.csv"))
    from hunt.gate import attach, load_cache
    from hunt.screen import newest_first

    attach(jobs, load_cache(root))
    jobs.sort(key=newest_first, reverse=True)
    return jobs


def _attach_scores(jobs: list[dict], rows: list[dict[str, str]]) -> None:
    by_url = {row["url"]: row for row in rows if row.get("url")}
    by_id = {
        (row.get("platform", ""), row.get("job_id", "")): row
        for row in rows
        if row.get("job_id")
    }
    for job in jobs:
        row = next((by_url[url] for url in _urls(job) if url in by_url), None)
        row = row or by_id.get((job.get("platform", ""), str(job.get("job_id", ""))))
        if not row:
            continue
        job["final_score"] = row.get("final_score", "")
        job["applied"] = row.get("applied", "")
        job["base_score"] = row.get("base_score", "")
        job["penalties"] = row.get("penalties", "")


def _urls(job: dict) -> list[str]:
    return [url for url in [job.get("url")] + list(job.get("also_at") or []) if url]


def matched_jobs(jobs: list[dict], search: dict, keywords: list[str]) -> list[dict]:
    draft = dict(search)
    draft["keywords"] = keywords
    return [
        job
        for job in jobs
        if title_matches(job.get("title") or "", draft)
        and location_ok(job.get("location") or "", draft, job.get("work_mode") or "")
    ]


def visible_jobs(jobs: list[dict], ctx) -> list[dict]:
    from hunt.screen import matters

    return [job for job in jobs if matters(job, ctx)]


def hidden_jobs(jobs: list[dict], ctx) -> list[tuple[dict, str]]:
    from hunt.screen import hidden_reason

    found = [(job, hidden_reason(job, ctx)) for job in jobs]
    return [(job, reason) for job, reason in found if reason and reason != "applied"]


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    """Write KEY=value lines. Blank updates are ignored, so a blank field does not wipe a key."""
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates and updates[key].strip():
            lines.append(f"{key}={updates[key].strip()}")
            written.add(key)
        else:
            lines.append(line)
    for key, value in updates.items():
        if key not in written and value.strip():
            lines.append(f"{key}={value.strip()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def anthropic_key_present(root: Path) -> bool:
    load_env(root / ".env")
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def store_anthropic_key(root: Path, value: str) -> None:
    text = (value or "").strip()
    if not text:
        return
    upsert_env(root / ".env", {"ANTHROPIC_API_KEY": text})
    os.environ["ANTHROPIC_API_KEY"] = text


def add_company(root: Path, board: dict) -> tuple[list[dict], str]:
    """Probe one board, then append the watchlist and the cache. Raises SourceError on a dead slug."""
    platform = board["platform"]
    if platform not in ATS_FETCH:
        raise SourceError(f"unknown platform {platform}")
    search = load_search(root)
    try:
        found = fetch_board(platform, board, search)
    except (SourceError, KeyError) as exc:
        raise SourceError(str(exc)) from exc
    watch = load_json(root / "data/watchlist.json", [])
    if not isinstance(watch, list):
        watch = []
    signature = (board.get("company"), platform, board.get("slug") or board.get("tenant"))
    for row in watch:
        other = (row.get("company"), row.get("platform"), row.get("slug") or row.get("tenant"))
        if other == signature:
            raise SourceError(f"{board.get('company')} is already on the watchlist")
    watch.append({key: value for key, value in board.items() if value})
    save_json(root / "data/watchlist.json", watch)

    fresh = []
    for job in found:
        item = job.as_dict()
        item["description"] = plain(item.get("description") or "")
        if title_matches(item["title"], search) and location_ok(item.get("location") or "", search, item.get("work_mode") or ""):
            fresh.append(item)
    from datetime import datetime, timezone

    from hunt.store import merge_into_inbox

    added = merge_into_inbox(root, fresh, datetime.now(timezone.utc))
    return added, f"Added {board.get('company')}. {len(added)} titles match your keywords."


def _same_role(row: dict[str, str], job: dict) -> bool:
    url = job.get("url") or ""
    job_id = str(job.get("job_id") or "")
    same_url = bool(url) and row.get("url") == url
    same_id = bool(job_id) and row.get("job_id") == job_id and row.get("platform") == job.get("platform")
    return same_url or same_id


def mark_applied(root: Path, *jobs: dict) -> None:
    """One write for any number of roles, under the data lock so a score landing at the same moment keeps the apply."""
    from hunt.store import data_lock

    with data_lock(root):
        rows = read_csv(root / "data/evaluated-jobs.csv")
        for job in jobs:
            matched = False
            for row in rows:
                if _same_role(row, job):
                    row["applied"] = "Yes"
                    row["status"] = "Applied"
                    matched = True
            if not matched:
                rows.append(
                    {
                        "date": "",
                        "company": job.get("company", ""),
                        "role": job.get("title") or job.get("role") or "",
                        "base_score": job.get("base_score", ""),
                        "penalties": job.get("penalties", ""),
                        "final_score": job.get("final_score", ""),
                        "applied": "Yes",
                        "reached_interview": "",
                        "status": "Applied",
                        "platform": job.get("platform", ""),
                        "job_id": str(job.get("job_id") or ""),
                        "url": job.get("url") or "",
                        "source": job.get("source", ""),
                        "location": job.get("location", ""),
                    }
                )
        write_csv(root / "data/evaluated-jobs.csv", [{field: row.get(field, "") for field in FIELDS} for row in rows])
        _append_tracker(root, jobs)


def unmark_applied(root: Path, *jobs: dict) -> None:
    """Undo mark_applied. A scored row keeps its score. A row that only recorded the apply is dropped,
    so dedupe does not treat the role as scored."""
    from hunt.store import data_lock

    with data_lock(root):
        kept: list[dict[str, str]] = []
        for row in read_csv(root / "data/evaluated-jobs.csv"):
            if any(_same_role(row, job) for job in jobs):
                if not (row.get("final_score") or row.get("base_score") or "").strip():
                    continue
                row["applied"] = ""
                if row.get("status") == "Applied":
                    row["status"] = ""
            kept.append(row)
        write_csv(root / "data/evaluated-jobs.csv", kept)

        path = root / TRACKER
        rows = _read_tracker(path)
        left = [row for row in rows if not any(_tracked(row, job) for job in jobs)]
        if len(left) != len(rows):
            _write_tracker(path, left)


def _tracked(row: dict[str, str], job: dict) -> bool:
    url = job.get("url") or ""
    if url:
        return row.get("url") == url
    return (row.get("company") or "", row.get("role") or "") == (job.get("company") or "", job.get("role") or job.get("title") or "")


def _read_tracker(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.stat().st_size:
        return []
    import csv

    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _append_tracker(root: Path, jobs: tuple[dict, ...]) -> None:
    path = root / TRACKER
    rows = _read_tracker(path)
    for job in jobs:
        url = job.get("url") or ""
        if any(row.get("url") == url and url for row in rows):
            for row in rows:
                if row.get("url") == url:
                    row["status"] = "Applied"
            continue
        rows.append(
            {
                "date": "",
                "company": job.get("company", ""),
                "role": job.get("title") or job.get("role") or "",
                "status": "Applied",
                "reached_interview": "",
                "url": url,
                "notes": "",
            }
        )
    _write_tracker(path, rows)


def _write_tracker(path: Path, rows: list[dict[str, str]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACKER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in TRACKER_FIELDS})


def resume_is_placeholder(root: Path) -> bool:
    path = root / "profile/og-resume.md"
    if not path.exists():
        return True
    return "PLACEHOLDER_RESUME" in path.read_text(encoding="utf-8")


def read_resume(root: Path) -> str:
    path = root / "profile/og-resume.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_resume(root: Path, text: str) -> None:
    path = root / "profile/og-resume.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
