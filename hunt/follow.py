"""After a clean resume rewrite: search, drop weak roles, score the rest."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from hunt.sources import load_env


def select_for_claude(jobs: list[dict], max_years, checker, ctx=None) -> tuple[list[dict], list[str]]:
    """Jobs worth a tailored resume, newest first. checker(url) returns open, closed, or unknown."""
    from hunt.screen import newest_first, worth_custom_resume

    chosen = []
    notes = []
    for job in jobs:
        label = f"{job.get('company') or 'company'} — {job.get('title') or 'role'}"
        ok, reason = worth_custom_resume(job, max_years, ctx)
        if not ok:
            notes.append(f"skip {label}: {reason}")
            continue
        state = checker(job.get("apply_url") or job.get("url") or "")
        if state != "open":
            notes.append(f"skip {label}: posting is {state}")
            continue
        chosen.append(job)
    chosen.sort(key=newest_first, reverse=True)
    return chosen, notes


def run_after_rewrite(root: Path, on_progress=None) -> str:
    def report(message: str) -> None:
        if on_progress:
            on_progress(message)

    from hunt.preferences import is_confirmed

    if not is_confirmed(root):
        return "The resume is saved. Open the desk and confirm your preferences (step 2). The search starts from there."
    report("Searching the boards for your target roles.")
    try:
        from hunt.schedule import run

        load_env(root / ".env")
        result = run(root, dict(os.environ), full=True)
    except Exception as exc:
        return f"Search did not finish: {exc}. Run Check now from Settings to try again."
    jobs = [job.as_dict() for job in result.search.jobs]
    report(f"Search found {len(jobs)} new roles.")
    if not jobs:
        return "Search found nothing new. The schedule keeps checking. The updated resume is already saved."

    from hunt.gate import CLASSIFIERS, evaluate_many, label

    name = label(root)
    report(f"{name} is reading the new roles.")
    try:
        checked = evaluate_many(root, jobs, limit=15, on_progress=report)
    except Exception as exc:
        fix = "Start Ollama and pull clef-flash" if name == CLASSIFIERS["clef"] else "Check ANTHROPIC_API_KEY in .env"
        return f"Search saved {len(jobs)} roles. {name} did not run: {exc}. {fix}, then run Check now from Settings."
    from hunt.gate import current, load_cache
    from hunt.live import check_live
    from hunt.screen import context, max_years

    cached = load_cache(root)
    by_url = {url: row for url, row in cached.items() if isinstance(row, dict)}
    by_url.update({row.get("url"): row for row in checked})
    merged_jobs = []
    for job in jobs:
        record = by_url.get(job.get("url"))
        if not current(record, job):
            continue
        merged = dict(job)
        merged["keep"] = record.get("keep")
        merged["fit"] = record.get("fit")
        merged_jobs.append(merged)
    chosen, skipped = select_for_claude(merged_jobs, max_years(root), check_live, context(root))
    report(f"{name} read {len(checked)}. {len(chosen)} are worth a custom resume.")
    for line in skipped:
        report(line)
    if not chosen:
        return "Nothing here is worth a tailored resume. The list stays in the desk. Press S on one row if you want Claude anyway."
    if shutil.which("claude") is None:
        return (
            f"{len(chosen)} roles are worth a tailored resume. Claude Code is not on PATH, so scoring did not start. "
            "Press s on a row once claude is installed."
        )
    from hunt.cli import SCORE_WORKERS, score_many

    report(f"Scoring {len(chosen)} roles, {SCORE_WORKERS} at a time.")
    results = score_many(root, chosen, on_done=lambda job, outcome: report(outcome.message))
    if any(outcome.code == 127 for outcome in results):
        return "Claude Code is not on PATH. Press s on a kept row once it is installed."
    scored = sum(1 for outcome in results if outcome.code == 0)
    failed = len(results) - scored
    if failed:
        return f"Scored {scored} roles. {failed} could not be scored; the reasons are above. Press a yourself when you apply."
    return f"Scored {scored} roles. They are in the desk. Press a yourself when you apply."
