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

from hunt.store import data_lock, read_json, write_json

BATCHES = "data/batches.json"

SCORE_INSTRUCTIONS = (
    "Score the job below for the person whose rules, rubric, and resume are in the system prompt. "
    "Apply the rubric exactly as the Rubric section describes. "
    "Return exactly one job: company, role, base_score, penalties (the penalty and bonus ids, comma separated, "
    "empty when none), final_score, and url (the job's url)."
)
TAILOR_INSTRUCTIONS = (
    "Tailor the master resume in the system prompt for the job below. Follow the Apply and Voice rules. "
    "Use the LinkedIn profile for extra story, and the projects only when the job wants someone who builds product, "
    "agents, or internal tools. Reorder and reframe lines that are already true so the ones this job cares about come "
    "first and use the job's words where they honestly fit. Keep every fact. Do not add an employer, title, date, "
    "number, name, or tool that is not already in the master resume. Return resume_markdown (the whole tailored "
    "resume in Markdown), changes (one line per thing you moved or reframed, and why), and gaps (requirements in the "
    "job that the resume cannot honestly claim)."
)


class BatchError(Exception):
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


def _context(root: Path) -> str:
    """The prefix every request shares: rules, rubric, resume, profile, projects. Cached across the batch."""
    rules = ""
    claude_md = root / "CLAUDE.md"
    if claude_md.is_file():
        text = claude_md.read_text(encoding="utf-8")
        rules = "\n\n".join(part for part in (_section(text, "## Rubric"), _section(text, "### Apply"), _section(text, "## Voice")) if part)
    parts = [("Rules from CLAUDE.md", rules)]
    for title, name in (
        ("profile/rubric.json", "profile/rubric.json"),
        ("Master resume (profile/og-resume.md)", "profile/og-resume.md"),
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


def _submit(root: Path, kind: str, jobs: list[dict], client) -> str:
    from hunt.cli import SCORE_SCHEMA
    from hunt.rewrite_api import load_steps
    from hunt.tailor import TAILOR_SCHEMA

    unique = list({job.get("url") or "": job for job in jobs if job.get("url")}.values())
    if not unique:
        raise BatchError("No roles to send.")
    steps = load_steps(root)
    context = _context(root)
    if kind == "score":
        requests = [_request(kind, job, context, steps["score"], SCORE_INSTRUCTIONS, SCORE_SCHEMA, 2200) for job in unique]
    else:
        requests = [_request(kind, job, context, steps["tailor"], TAILOR_INSTRUCTIONS, TAILOR_SCHEMA, 6000) for job in unique]
    api = _client(root, client)
    try:
        batch = api.messages.batches.create(requests=requests)
    except Exception as exc:
        raise BatchError(f"The Batch API refused the {kind} batch: {type(exc).__name__}: {getattr(exc, 'message', exc)}") from exc
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
    return batch.id


def submit_scores(root: Path, jobs: list[dict], *, client=None) -> str:
    return _submit(root, "score", jobs, client)


def _payload(item) -> dict | None:
    result = getattr(item, "result", None)
    if getattr(result, "type", None) != "succeeded":
        return None
    message = result.message
    if getattr(message, "stop_reason", None) in {"refusal", "max_tokens"}:
        return None
    text = next((block.text for block in message.content if getattr(block, "type", None) == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


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
        for item in api.messages.batches.results(record["id"]):
            if item.custom_id in record["jobs"]:
                payload = _payload(item)
                if payload is not None:
                    outputs[item.custom_id] = payload
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
            _submit(root, "tailor", high, api)
            note += ". Tailoring those now in a second batch."
        except BatchError as exc:
            note += f". Tailoring did not start: {exc}"
    return note + ("" if note.endswith(".") else ".")


def _apply_tailors(root: Path, record: dict, outputs: dict, failed: int) -> str:
    from hunt.tailor import save_tailored

    made = 0
    flagged = 0
    for custom_id, payload in outputs.items():
        result = save_tailored(root, record["jobs"][custom_id], payload)
        if result.code == 0:
            made += 1
            flagged += "Check these numbers" in result.message
        else:
            failed += 1
    note = f"Batch wrote {made} tailored resumes to resumes/"
    if flagged:
        note += f", {flagged} with numbers to check in notes.md"
    if failed:
        note += f", {failed} failed"
    return note + "."
