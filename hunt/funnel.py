"""Where every inbox role ends up. Recorded before and after each change so the effect is measured, not guessed."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from hunt.store import data_lock, read_json, write_json

FUNNEL = "data/funnel.json"
_DETAIL = ("stretch labelled plausible", "stretch over years", "visible maybe")


def funnel_counts(root: Path, today: date | None = None) -> dict[str, int]:
    from hunt.inbox import load_jobs
    from hunt.screen import context, hidden_reason, worth_of

    ctx = context(root, today)
    jobs = load_jobs(root)
    reasons = Counter()
    detail = Counter({name: 0 for name in _DETAIL})
    for job in jobs:
        reason = hidden_reason(job, ctx)
        reasons[reason or "visible"] += 1
        if not reason:
            detail["visible maybe"] += worth_of(job, ctx).verdict == "maybe"
        if reason == "stretch":
            detail["stretch labelled plausible"] += job.get("jev_fit_label") == "A plausible match"
            detail["stretch over years"] += bool(job.get("jev_over_years"))
    visible = reasons.pop("visible", 0)
    counts = {"inbox": len(jobs)}
    counts.update(dict(reasons.most_common()))
    counts.update(detail)
    counts["visible"] = visible
    return counts


def format_funnel(counts: dict[str, int]) -> str:
    lines = ["| Stage | Roles |", "|---|---|"]
    lines += [f"| {name} | {value} |" for name, value in counts.items()]
    return "\n".join(lines)


def record_funnel(root: Path, label: str, counts: dict[str, int]) -> None:
    with data_lock(root):
        history = read_json(root / FUNNEL, [])
        history = history if isinstance(history, list) else []
        history.append({"at": datetime.now(timezone.utc).isoformat(), "label": label, "counts": counts})
        write_json(root / FUNNEL, history)
