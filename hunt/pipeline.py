"""After a fetch, and on idle ticks: tailor the high scorers, then score the visible roles, each within the daily cap.

Tailoring goes first so a spent cap holds back new scores, not resumes for roles already scored high.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def key_in(env: dict[str, str]) -> bool:
    return (env.get("ANTHROPIC_API_KEY") or "").strip().startswith("sk-ant-")


def allowed(root: Path, env: dict[str, str]) -> bool:
    """A key, and caps the person has seen and saved."""
    from hunt.preferences import budget_set

    return key_in(env) and budget_set(root)


def number(value) -> float | None:
    try:
        return float(str(value if value is not None else "").strip())
    except ValueError:
        return None


def _rows(root: Path, now: datetime) -> list[tuple[dict, str]]:
    from hunt.inbox import load_jobs
    from hunt.screen import context, hidden_reason

    ctx = context(root, now.date())
    return [(job, hidden_reason(job, ctx)) for job in load_jobs(root)]


def score_queue(rows: list[tuple[dict, str]], ctx, waiting: set[str]) -> list[dict]:
    """Visible, unscored roles: clean ones before maybe ones, then by fit, then newest."""
    from hunt.screen import newest_first, worth_of

    todo = [job for job, reason in rows if not reason and number(job.get("final_score")) is None and (job.get("url") or "") not in waiting]
    todo.sort(key=newest_first, reverse=True)
    todo.sort(key=lambda job: (worth_of(job, ctx).verdict != "apply", -(number(job.get("jev_fit")) or 0.0)))
    return todo


def tailor_queue(root: Path, rows: list[tuple[dict, str]], cutoff: float, waiting: set[str]) -> list[dict]:
    from hunt.tailor import is_tailored

    ready = []
    for job, reason in rows:
        score = number(job.get("final_score"))
        if reason or score is None or score <= cutoff or (job.get("url") or "") in waiting or is_tailored(root, job):
            continue
        ready.append(job)
    ready.sort(key=lambda job: number(job.get("final_score")) or 0.0, reverse=True)
    return ready


def _send(root: Path, kind: str, jobs: list[dict], report) -> str:
    from hunt.batch import BatchError, BudgetSpent, submit

    if not jobs:
        return ""
    try:
        batch_id, count = submit(root, kind, jobs)
    except BudgetSpent as exc:
        report(str(exc))
        return ""
    except BatchError as exc:
        report(f"error {kind} batch: {exc}")
        return f"{kind} batch: {exc}"
    report(f"{kind}: sent {count} of {len(jobs)} roles in {batch_id}")
    return ""


def tailor_stage(root: Path, now: datetime, report, env: dict[str, str]) -> str:
    if not allowed(root, env):
        return ""
    from hunt.batch import pending_urls
    from hunt.cli import screenout_cap

    return _send(root, "tailor", tailor_queue(root, _rows(root, now), screenout_cap(root), pending_urls(root)), report)


def score_stage(root: Path, now: datetime, report, env: dict[str, str]) -> str:
    if not allowed(root, env):
        return ""
    from hunt.batch import pending_urls
    from hunt.screen import context

    return _send(root, "score", score_queue(_rows(root, now), context(root, now.date()), pending_urls(root)), report)


def shown_reason(job: dict, reason: str, in_batch: set[str], classify_room: float) -> str:
    """The hidden view's reason, with why an unclassified role is still waiting."""
    if reason != "not classified yet":
        return reason
    if (job.get("url") or "") in in_batch:
        return "waiting: batch"
    if classify_room <= 0:
        return "waiting: budget"
    return reason
