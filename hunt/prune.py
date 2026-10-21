"""Roles hidden for age or because they closed leave the inbox. data/pruned.json keeps their URLs so a board cannot bring them back."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from hunt.store import LAST_SEARCH, PRUNED, data_lock, load_inbox, load_pruned, read_json, save_inbox, write_json

OLD = ("stale", "posted too long ago", "closed")


def prune(root: Path, today: date | None = None) -> dict[str, int]:
    """Drop old roles, the classifier decisions for roles no longer kept, and the posting copies in finished batches."""
    from hunt.inbox import load_jobs
    from hunt.screen import context, hidden_reason

    today = today or datetime.now(timezone.utc).date()
    ctx = context(root, today)
    with data_lock(root):
        old = {job["url"] for job in load_jobs(root) if hidden_reason(job, ctx) in OLD}
        rows = load_inbox(root)
        kept = [row for row in rows if row.get("url") not in old]
        if len(kept) < len(rows):
            pruned = load_pruned(root)
            for row in rows:
                if row.get("url") in old:
                    for url in [row["url"]] + list(row.get("also_at") or []):
                        pruned.setdefault(url, today.isoformat())
            write_json(root / PRUNED, pruned)
            save_inbox(root, kept)
        decisions = _drop_decisions(root, kept)
        batches = _slim_batches(root)
    return {"roles": len(rows) - len(kept), "decisions": decisions, "batches": batches}


def _drop_decisions(root: Path, kept: list[dict]) -> int:
    from hunt.gate import load_cache, save_cache

    cache = load_cache(root)
    if not cache:
        return 0
    wanted = {url for row in kept for url in [row.get("url")] + list(row.get("also_at") or []) if url}
    last = read_json(root / LAST_SEARCH, {})
    if isinstance(last, dict):
        wanted |= {job.get("url") for job in last.get("jobs") or [] if isinstance(job, dict)}
    trimmed = {url: decision for url, decision in cache.items() if url in wanted}
    if len(trimmed) < len(cache):
        save_cache(root, trimmed)
    return len(cache) - len(trimmed)


def _slim_batches(root: Path) -> int:
    """A finished batch needs only its summary and failed_urls, which stop billed failures from repeating."""
    from hunt.batch import BATCHES

    records = read_json(root / BATCHES, [])
    if not isinstance(records, list):
        return 0
    slimmed = 0
    for record in records:
        if record.get("status") == "done" and "jobs" in record:
            del record["jobs"]
            slimmed += 1
    if slimmed:
        write_json(root / BATCHES, records)
    return slimmed
