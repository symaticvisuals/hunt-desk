"""Score all through the Message Batches API: half price, results usually within the hour.

One batch scores every role. When it ends, a second batch tailors the roles above the cutoff.
Open batches live in data/batches.json; poll() applies whatever has finished.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from hunt import spend
from hunt.store import data_lock, read_json, write_json
from hunt.tailor import RESUME_RULES

BATCHES = "data/batches.json"

SCORE_INSTRUCTIONS = (
    "Score the job below for the person whose rules, rubric, and resume are in the system prompt. "
    "Apply the rubric exactly as the Rubric section describes. "
    "Return exactly one job: company, role, base_score, penalties (the penalty and bonus ids, comma separated, "
    "empty when none), final_score, and url (the job's url)."
)
TAILOR_INSTRUCTIONS = "Tailor the master resume in the system prompt for the job below.\n\n" + RESUME_RULES


class BatchError(Exception):
    pass


class BudgetSpent(BatchError):
    pass


def _load(root: Path) -> list[dict]:
    records = read_json(root / BATCHES, [])
    return records if isinstance(records, list) else []


def open_batches(root: Path) -> list[dict]:
    return [record for record in _load(root) if record.get("status") == "open"]


def pending_urls(root: Path) -> set[str]:
    return {job.get("url") or "" for record in open_batches(root) for job in record.get("jobs", {}).values()}


def _section(text: str, heading: str) -> str:
    """One Markdown section, from its heading to the next heading at the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#+)\s", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _context(root: Path, kind: str = "score") -> str:
    """The prefix every request of one kind shares: rules, rubric, resume, profile, projects. Cached across the batch.
    Tailor requests also carry the humanizer's writing rules; scoring does not pay for them."""
    from hunt.tailor import humanizer_text, master_text

    rules = ""
    claude_md = root / "CLAUDE.md"
    if claude_md.is_file():
        text = claude_md.read_text(encoding="utf-8")
        rules = "\n\n".join(part for part in (_section(text, "## Rubric"), _section(text, "### Apply"), _section(text, "## Voice")) if part)
    parts = [("Rules from CLAUDE.md", rules)]
    if kind == "tailor":
        parts.append(("Writing rules (humanizer skill, MIT, Copyright (c) 2025 Siqi Chen)", humanizer_text(root)))
    rubric = root / "profile/rubric.json"
    if rubric.is_file():
        parts.append(("profile/rubric.json", rubric.read_text(encoding="utf-8").strip()))
    parts.append(("Master resume (profile/og-resume.md)", master_text(root).strip()))
    for title, name in (
        ("LinkedIn profile (extra story, not a second resume)", "profile/linkedin-profile.md"),
        ("Projects (only when the job wants a builder of product, agents, or internal tools)", "profile/projects.md"),
    ):
        path = root / name
        if path.is_file():
            parts.append((title, path.read_text(encoding="utf-8").strip()))
    return "\n\n".join(f"# {title}\n\n{body}" for title, body in parts if body)


def _custom_id(kind: str, job: dict) -> str:
    return f"{kind}-{hashlib.sha1((job.get('url') or '').encode()).hexdigest()[:20]}"


def _request(kind: str, job: dict, context: str, step: dict, instructions: str, schema: dict, description_limit: int) -> dict:
    packet = dict(job)
    packet["description"] = (job.get("description") or "")[:description_limit]
    return {
        "custom_id": _custom_id(kind, job),
        "params": {
            "model": step["model"],
            "max_tokens": step["max_tokens"],
            "system": [{"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": f"{instructions}\n\nJob:\n{json.dumps(packet, indent=2)}"}],
            "output_config": {"effort": step["effort"], "format": {"type": "json_schema", "schema": schema}},
        },
    }


def _client(root: Path, client):
    if client is not None:
        return client
    from hunt.claude_client import ClaudeAuthError, make_client
    from hunt.sources import load_env

    load_env(root / ".env")
    try:
        return make_client()
    except ClaudeAuthError as exc:
        raise BatchError(str(exc)) from exc


def _unique(jobs: list[dict]) -> list[dict]:
    return list({job.get("url") or "": job for job in jobs if job.get("url")}.values())


def build_requests(root: Path, kind: str, jobs: list[dict]) -> list[dict]:
    from hunt.cli import SCORE_SCHEMA
    from hunt.rewrite_api import load_steps
    from hunt.tailor import TAILOR_SCHEMA

    steps = load_steps(root)
    context = _context(root, kind)
    if kind == "score":
        return [_request(kind, job, context, steps["score"], SCORE_INSTRUCTIONS, SCORE_SCHEMA, 2200) for job in jobs]
    return [_request(kind, job, context, steps["tailor"], TAILOR_INSTRUCTIONS, TAILOR_SCHEMA, 6000) for job in jobs]


def _estimate(table: dict, request: dict, *, batch: bool) -> float:
    params = request["params"]
    chars = sum(len(block["text"]) for block in params["system"]) + sum(len(message["content"]) for message in params["messages"])
    return spend.estimate(table, params["model"], chars, params["max_tokens"], batch=batch)


def estimates(root: Path, kind: str, jobs: list[dict]) -> list[float]:
    """Upper-bound batch prices, one per unique role, in the order submit would send them."""
    table = spend.prices(root)
    return [_estimate(table, request, batch=True) for request in build_requests(root, kind, _unique(jobs))]


def estimate_now(root: Path, kind: str, job: dict) -> float:
    return _estimate(spend.prices(root), build_requests(root, kind, [job])[0], batch=False)


def submit(root: Path, kind: str, jobs: list[dict], *, client=None, enforce: bool = True) -> tuple[str, int]:
    """Send the roles that fit today's cap, in the order given. Returns the batch id and how many went."""
    unique = _unique(jobs)
    if not unique:
        raise BatchError("No roles to send.")
    requests = build_requests(root, kind, unique)
    table = spend.prices(root)
    token, count = spend.reserve(root, kind, [_estimate(table, request, batch=True) for request in requests], enforce=enforce)
    if count == 0:
        raise BudgetSpent(f"Today's {spend.CAP_LABEL[kind]} budget is spent. The rest waits for tomorrow.")
    requests, unique = requests[:count], unique[:count]
    try:
        api = _client(root, client)
        batch = api.messages.batches.create(requests=requests)
    except BatchError:
        spend.release(root, token)
        raise
    except Exception as exc:
        spend.release(root, token)
        raise BatchError(f"The Batch API refused the {kind} batch: {type(exc).__name__}: {getattr(exc, 'message', exc)}") from exc
    spend.attach(root, token, batch.id)
    record = {
        "id": batch.id,
        "kind": kind,
        "created": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "jobs": {request["custom_id"]: job for request, job in zip(requests, unique)},
        "summary": "",
    }
    with data_lock(root):
        records = _load(root)
        records.append(record)
        write_json(root / BATCHES, records)
    return batch.id, count


def submit_scores(root: Path, jobs: list[dict], *, client=None, enforce: bool = True) -> str:
    return submit(root, "score", jobs, client=client, enforce=enforce)[0]


def message_payload(message) -> dict | None:
    if message is None or getattr(message, "stop_reason", None) in {"refusal", "max_tokens"}:
        return None
    text = next((block.text for block in getattr(message, "content", None) or [] if getattr(block, "type", None) == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def usage_of(message) -> dict[str, int]:
    from hunt.rewrite_api import _usage

    fresh, output, cache_read, cache_write = _usage(message)
    return {"input_tokens": fresh, "output_tokens": output, "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write}


def run_now(root: Path, kind: str, job: dict, *, client=None) -> dict:
    """One request sent at once at full price, for s and t when the claude CLI is missing. Raises BatchError."""
    request = build_requests(root, kind, [job])[0]
    api = _client(root, client)
    try:
        with api.messages.stream(**request["params"]) as stream:
            message = stream.get_final_message()
    except Exception as exc:
        raise BatchError(f"Claude could not {kind} this role: {type(exc).__name__}: {getattr(exc, 'message', exc)}") from exc
    model = str(getattr(message, "model", "") or request["params"]["model"])
    spend.record(root, kind, spend.cost(spend.prices(root), model, usage_of(message), batch=False))
    payload = message_payload(message)
    if payload is None:
        raise BatchError(f"Claude returned no usable {kind} result for this role.")
    return payload


def _payload(item) -> dict | None:
    result = getattr(item, "result", None)
    if getattr(result, "type", None) != "succeeded":
        return None
    return message_payload(result.message)


def poll(root: Path, *, client=None) -> list[str]:
    """Apply every batch that has ended. Returns one line per batch applied."""
    waiting = open_batches(root)
    if not waiting:
        return []
    api = _client(root, client)
    notes: list[str] = []
    for record in waiting:
        batch = api.messages.batches.retrieve(record["id"])
        if batch.processing_status != "ended":
            continue
        outputs: dict[str, dict] = {}
        billed = 0.0
        table = spend.prices(root)
        for item in api.messages.batches.results(record["id"]):
            result = getattr(item, "result", None)
            if getattr(result, "type", None) == "succeeded":
                message = result.message
                billed += spend.cost(table, str(getattr(message, "model", "") or ""), usage_of(message), batch=True)
            if item.custom_id in record["jobs"]:
                payload = _payload(item)
                if payload is not None:
                    outputs[item.custom_id] = payload
        spend.settle(root, record["id"], billed)
        # Errored, refused, expired, or missing from the results.
        failed = len(record["jobs"]) - len(outputs)
        note = _apply_scores(root, record, outputs, failed, api) if record["kind"] == "score" else _apply_tailors(root, record, outputs, failed)
        with data_lock(root):
            records = _load(root)
            for stored in records:
                if stored.get("id") == record["id"]:
                    stored["status"] = "done"
                    stored["summary"] = note
            write_json(root / BATCHES, records)
        notes.append(note)
    return notes


def _apply_scores(root: Path, record: dict, outputs: dict, failed: int, api) -> str:
    from hunt.cli import record_score, screenout_cap

    cutoff = screenout_cap(root)
    high: list[dict] = []
    scored = 0
    for custom_id, payload in outputs.items():
        job = record["jobs"][custom_id]
        rows = payload.get("jobs") or []
        row = next((item for item in rows if item.get("url") == job.get("url")), rows[0] if rows else None)
        if row is None:
            failed += 1
            continue
        record_score(root, job, row)
        scored += 1
        if float(row["final_score"]) > cutoff:
            high.append(job)
    note = f"Batch scored {scored} roles: {len(high)} above {cutoff:g}"
    if failed:
        note += f", {failed} failed"
    if high:
        try:
            submit(root, "tailor", high, client=api)
            note += ". Tailoring those now in a second batch."
        except BudgetSpent:
            note += ". Tailoring waits for tomorrow's budget."
        except BatchError as exc:
            note += f". Tailoring did not start: {exc}"
    return note + ("" if note.endswith(".") else ".")


def _apply_tailors(root: Path, record: dict, outputs: dict, failed: int) -> str:
    from hunt.tailor import save_tailored

    made = 0
    flagged = 0
    warned = 0
    for custom_id, payload in outputs.items():
        result = save_tailored(root, record["jobs"][custom_id], payload)
        if result.code == 0:
            made += 1
            flagged += "Check these numbers" in result.message
            warned += result.warn
        else:
            failed += 1
    note = f"Batch wrote {made} tailored resumes to resumes/"
    if flagged:
        note += f", {flagged} with numbers to check in notes.md"
    if warned:
        note += f", {warned} need a look (⚠)"
    if failed:
        note += f", {failed} failed"
    return note + "."
