"""Daily Claude spend. A batch reserves its estimate first; when it ends, the reservation becomes the billed cost."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from hunt.store import data_lock, read_json, write_json

LEDGER = "data/spend.json"
KEEP_DAYS = 60
CAP_FIELD = {"classify": "classify_usd_per_day", "score": "score_usd_per_day", "tailor": "score_usd_per_day"}
CAP_LABEL = {"classify": "classify", "score": "score and resume", "tailor": "score and resume"}
# USD per million tokens, from the Anthropic rate card checked 2026-10-06.
DEFAULT_PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
}
BATCH_DISCOUNT = 0.5
CACHE_WRITE = 1.25


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def prices(root: Path) -> dict:
    table = {model: dict(rate) for model, rate in DEFAULT_PRICES.items()}
    payload = read_json(root / "profile/models.json", {})
    raw = payload.get("prices") if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        for model, rate in raw.items():
            if isinstance(rate, dict) and {"input", "output"} <= set(rate):
                table[model] = dict(rate)
    return table


def _rate(table: dict, model: str) -> dict:
    rate = table.get(model)
    if rate is None:
        # An unknown model is priced like the dearest one, so a cap still holds.
        rate = max(table.values(), key=lambda item: float(item["output"]))
    return rate


def cost(table: dict, model: str, usage: dict[str, int], *, batch: bool) -> float:
    rate = _rate(table, model)
    price_in = float(rate["input"])
    cache_read = float(rate.get("cache_read", price_in * 0.1))
    dollars = (
        usage.get("input_tokens", 0) * price_in
        + usage.get("cache_creation_input_tokens", 0) * price_in * CACHE_WRITE
        + usage.get("cache_read_input_tokens", 0) * cache_read
        + usage.get("output_tokens", 0) * float(rate["output"])
    ) / 1_000_000
    return dollars * (BATCH_DISCOUNT if batch else 1.0)


def estimate(table: dict, model: str, chars: int, max_tokens: int, *, batch: bool) -> float:
    """An upper bound: every character as fresh input and the whole max_tokens as output."""
    return cost(table, model, {"input_tokens": chars // 4 + 1, "output_tokens": max_tokens}, batch=batch)


def cap(root: Path, step: str) -> float:
    from hunt.preferences import load, normalize

    prefs = load(root) or normalize({})
    return float(prefs["budget"][CAP_FIELD[step]])


def _entries(root: Path) -> list[dict]:
    payload = read_json(root / LEDGER, {})
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return entries if isinstance(entries, list) else []


def _save(root: Path, entries: list[dict]) -> None:
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    write_json(root / LEDGER, {"entries": [entry for entry in entries if str(entry.get("day") or "") >= cutoff]})


def _amount(entry: dict) -> float:
    actual = entry.get("actual")
    return float(actual if actual is not None else entry.get("reserved") or 0.0)


def spent(root: Path, step: str, day: str | None = None) -> float:
    day = day or today()
    group = CAP_FIELD[step]
    return sum(
        _amount(entry) for entry in _entries(root) if entry.get("day") == day and CAP_FIELD.get(str(entry.get("step"))) == group
    )


def room(root: Path, step: str, day: str | None = None) -> float:
    return max(0.0, cap(root, step) - spent(root, step, day))


def reserve(root: Path, step: str, estimates: list[float], *, enforce: bool = True) -> tuple[str, int]:
    """Reserve the longest prefix of estimates that fits today's room. Returns a token and how many fit."""
    day = today()
    with data_lock(root):
        left = room(root, step, day) if enforce else float("inf")
        total = 0.0
        count = 0
        for amount in estimates:
            if total + amount > left + 1e-12:
                break
            total += amount
            count += 1
        if count == 0:
            return "", 0
        token = f"pending-{uuid.uuid4().hex[:12]}"
        entries = _entries(root)
        entries.append({"id": token, "day": day, "step": step, "reserved": round(total, 8), "actual": None})
        _save(root, entries)
    return token, count


def _update(root: Path, entry_id: str, changes: dict) -> None:
    with data_lock(root):
        entries = _entries(root)
        for entry in entries:
            if entry.get("id") == entry_id:
                entry.update(changes)
        _save(root, entries)


def attach(root: Path, token: str, batch_id: str) -> None:
    _update(root, token, {"id": batch_id})


def release(root: Path, token: str) -> None:
    with data_lock(root):
        _save(root, [entry for entry in _entries(root) if entry.get("id") != token])


def settle(root: Path, entry_id: str, actual: float) -> None:
    _update(root, entry_id, {"actual": round(actual, 8)})


def record(root: Path, step: str, actual: float) -> None:
    """A synchronous call is billed at once, so there is nothing to reserve."""
    with data_lock(root):
        entries = _entries(root)
        entries.append({"id": f"sync-{uuid.uuid4().hex[:12]}", "day": today(), "step": step, "reserved": 0.0, "actual": round(actual, 8)})
        _save(root, entries)


def summary(root: Path) -> str:
    return " · ".join(f"{name} ${spent(root, name):.2f}/${cap(root, name):.2f}" for name in ("classify", "score"))
