"""Files the desk and the scheduled run share: locks, atomic JSON, the inbox, and dismissals. Old roles leave the inbox through hunt.prune."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

INBOX = "data/inbox.json"
DISMISSED = "data/dismissed.json"
LAST_SEARCH = "data/last-search.json"
PRUNED = "data/pruned.json"
RUN_LOCK = "data/.hunt.run"
DATA_LOCK = "data/.hunt.lock"
ATS = {"greenhouse", "lever", "ashby", "workday"}

_COMPANY_SUFFIX = re.compile(
    r"\b(private limited|pvt\.? ltd\.?|pvt|ltd\.?|limited|inc\.?|llc|corp\.?|corporation|"
    r"technologies|technology|solutions|systems|software|services|india|global)\b"
)
_PLACE_WORDS = {
    "remote", "hybrid", "onsite", "on site", "india", "usa", "us", "uk", "europe", "emea", "apac",
    "bangalore", "bengaluru", "delhi", "new delhi", "delhi ncr", "ncr", "gurgaon", "gurugram", "noida",
    "mumbai", "navi mumbai", "pune", "hyderabad", "chennai", "kolkata", "ahmedabad",
}
_TRAILING_BRACKET = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]\s*$")
_TRAILING_DASH = re.compile(r"\s+[-–—|,]\s+([^-–—|,]+)$")
_local = threading.local()


class Busy(Exception):
    pass


def read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    """Write beside the target, then rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _lock_handle(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "a+", encoding="utf-8")


@contextmanager
def run_lock(root: Path):
    """Held for a whole fetch run. A second caller gets Busy at once."""
    handle = _lock_handle(root / RUN_LOCK)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise Busy("A check is already running.") from exc
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


@contextmanager
def data_lock(root: Path, timeout: float = 5.0):
    """Held around a read-modify-write of a shared file. Reentrant within one thread."""
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    key = str((root / DATA_LOCK).resolve())
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    handle = _lock_handle(root / DATA_LOCK)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise Busy("The job files are busy. Try again in a moment.") from None
            time.sleep(0.05)
    held[key] = 1
    try:
        yield
    finally:
        held.pop(key, None)
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", (text or "").lower()).split())


def _strip_place(title: str) -> str:
    text = (title or "").strip()
    for pattern in (_TRAILING_BRACKET, _TRAILING_DASH):
        found = pattern.search(text)
        if found and all(_words(part) in _PLACE_WORDS for part in re.split(r"[/,;&]| or | and ", found.group(1)) if part.strip()):
            text = text[: found.start()].strip()
    return text


def role_key(job: dict) -> str:
    """Same company and title on any board. Empty when the company is unknown."""
    company = _words(_COMPANY_SUFFIX.sub(" ", (job.get("company") or "").lower()))
    title = _words(_strip_place(job.get("title") or job.get("role") or ""))
    if not company or not title:
        return ""
    return f"{company}|{title}"


def _rank(job: dict) -> int:
    if (job.get("platform") or "") in ATS:
        return 0
    apply = job.get("apply_url") or ""
    if apply and apply != job.get("url"):
        return 1
    return 2


def _stamp(now: datetime) -> str:
    return now.isoformat()


def merge(rows: list[dict], fresh: list[dict], now: datetime, pruned: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Add fresh roles to the inbox. Returns the inbox and the records that are new to it. A pruned URL never comes back."""
    pruned = pruned or {}
    by_url: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for record in rows:
        for url in [record.get("url")] + list(record.get("also_at") or []):
            if url:
                by_url[url] = record
        key = role_key(record)
        if key:
            by_key.setdefault(key, record)
    added: list[dict] = []
    stamp = _stamp(now)
    for job in fresh:
        url = job.get("url") or ""
        if not url or url in pruned:
            continue
        record = by_url.get(url)
        if record is None:
            key = role_key(job)
            record = by_key.get(key) if key else None
            if record is not None:
                _absorb(record, job)
                by_url[url] = record
        if record is not None:
            from hunt.sources import DETAIL_PLATFORMS

            record["last_seen"] = stamp
            if len(job.get("description") or "") > len(record.get("description") or ""):
                record["description"] = job.get("description") or ""
            if not (record.get("description") or "").strip() and record.get("platform") not in DETAIL_PLATFORMS:
                record["description_tries"] = int(record.get("description_tries") or 0) + 1
            if not record.get("posted_at") and job.get("posted_at"):
                record["posted_at"] = job["posted_at"]
            continue
        record = dict(job)
        record.update(
            {
                "first_seen": stamp,
                "last_seen": stamp,
                "first_visible_at": "",
                "live": "",
                "live_checked_at": "",
                "also_at": [],
            }
        )
        rows.append(record)
        added.append(record)
        by_url[url] = record
        key = role_key(record)
        if key:
            by_key.setdefault(key, record)
    return rows, added


_KEEP_ON_SWAP = ("first_seen", "last_seen", "first_visible_at", "live", "live_checked_at", "also_at")


def _absorb(record: dict, job: dict) -> None:
    """One role from another board. The better URL becomes the record's URL."""
    also = list(record.get("also_at") or [])
    if _rank(job) < _rank(record):
        kept = {name: record.get(name) for name in _KEEP_ON_SWAP}
        old_url = record.get("url")
        description = record.get("description") or ""
        record.clear()
        record.update(job)
        record.update(kept)
        if len(description) > len(record.get("description") or ""):
            record["description"] = description
        if old_url and old_url not in also:
            also.append(old_url)
    elif job.get("url") and job["url"] not in also:
        also.append(job["url"])
    record["also_at"] = also


def load_inbox(root: Path) -> list[dict]:
    payload = read_json(root / INBOX, [])
    return payload if isinstance(payload, list) else []


def save_inbox(root: Path, rows: list[dict]) -> None:
    write_json(root / INBOX, rows)


def merge_into_inbox(root: Path, jobs: list[dict], now: datetime) -> list[dict]:
    with data_lock(root):
        seed_inbox(root, now)
        rows, added = merge(load_inbox(root), jobs, now, load_pruned(root))
        save_inbox(root, rows)
    return added


def load_pruned(root: Path) -> dict[str, str]:
    """URLs hunt.prune removed from the inbox, with the day it removed each."""
    payload = read_json(root / PRUNED, {})
    return payload if isinstance(payload, dict) else {}


def update_inbox(root: Path, changes: dict[str, dict]) -> None:
    """Apply field updates keyed by URL under the data lock."""
    if not changes:
        return
    with data_lock(root):
        rows = load_inbox(root)
        for record in rows:
            patch = changes.get(record.get("url") or "")
            if patch:
                record.update(patch)
        save_inbox(root, rows)


def seed_inbox(root: Path, now: datetime) -> int:
    """First run only: the last search becomes the inbox."""
    if (root / INBOX).exists():
        return 0
    payload = read_json(root / LAST_SEARCH, {})
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    with data_lock(root):
        if (root / INBOX).exists():
            return 0
        rows, added = merge([], list(jobs or []), now)
        save_inbox(root, rows)
    return len(added)


def load_dismissed(root: Path) -> dict:
    entries = read_json(root / DISMISSED, [])
    entries = entries if isinstance(entries, list) else []
    return {
        "urls": {entry.get("url") for entry in entries if entry.get("url")},
        "keys": {entry.get("key") for entry in entries if entry.get("key")},
    }


def dismiss(root: Path, *jobs: dict) -> None:
    at = datetime.now(timezone.utc).isoformat()
    added = [
        {
            "url": job.get("url") or "",
            "key": role_key(job),
            "company": job.get("company") or "",
            "title": job.get("title") or job.get("role") or "",
            "at": at,
        }
        for job in jobs
    ]
    with data_lock(root):
        entries = read_json(root / DISMISSED, [])
        entries = entries if isinstance(entries, list) else []
        entries.extend(added)
        write_json(root / DISMISSED, entries)


def is_dismissed(job: dict, dismissed: dict) -> bool:
    urls = dismissed.get("urls") or set()
    if job.get("url") in urls or any(url in urls for url in job.get("also_at") or []):
        return True
    key = role_key(job)
    return bool(key) and key in (dismissed.get("keys") or set())
