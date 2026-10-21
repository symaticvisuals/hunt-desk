"""Resume rewrite as one Claude Managed Agents session.

Agents and the environment are files under agents/ and environments/. Sync them
once with `ant apply`. Each run only creates a session.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from hunt.onboard import read_upload
from hunt.resume_audit import latex_to_text
from hunt.sources import load_env

REPORT = "data/resume-rewrite.md"
ANSWERS = "data/resume-answers.md"
LOCK = "claude-lock.json"
LEAD = "agents/resume-lead/agent.md"
ENVIRONMENT = "environments/resume-rewrite.yaml"
MOUNT = "/mnt/session/uploads/resume.md"
OUTPUT_NAME = "resume-rewrite.md"
# Minor units: 800 cents is $8.00 at public list rates, shared by every thread.
SESSION_BUDGET_CENTS = "800"

# Starter rubric. Tune the criteria in this constant if the grader is too strict or too loose.
RUBRIC = """# Resume rewrite rubric - starter, tune the criteria
- The only deliverable is /mnt/session/outputs/resume-rewrite.md
- Every employer, title, and date from the source resume is still present
- No employer, tool, or number appears unless it is in the source, the user-supplied answers, or the placeholder [metric]
- At most four roles are rewritten
- When the inventory has questions and no answers were supplied, the file lists those questions and does not rewrite
- The prose has no em dash and no "not X but Y" contrast
- A section named Invented lists what the fact check flagged, or says none
"""

APPLY_HINT = (
    "Managed Agents are not synced yet. From this directory, review the plan, then apply it:\n"
    "  ant apply --dry-run agents environments\n"
    "  ant apply --yes agents environments"
)


class ManagedError(Exception):
    pass


@dataclass
class ManagedResult:
    summary: str
    path: Path
    session_id: str
    trace: str


def run_managed_rewrite(root: Path, payload: dict, *, client=None, sleeper=time.sleep, on_progress=None) -> ManagedResult:
    load_env(root / ".env")
    agent_id, environment_id = load_ids(root)
    resume_path, resume = _resume_text(root, str(payload.get("resume") or "profile/og-resume.md"))
    job = str(payload.get("job") or "").strip()
    answers = _answers(root, payload)
    api = client if client is not None else make_client()

    def report(message: str) -> None:
        if on_progress:
            on_progress(message)

    report(f"Reading {resume_path.name}")
    report("Uploading the resume")
    uploaded = api.beta.files.upload(file=_as_file(resume))
    file_id = _get(uploaded, "id")
    if not file_id:
        raise ManagedError("The resume upload did not return a file id.")

    session = api.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title="Resume rewrite",
        resources=[{"type": "file", "file_id": file_id, "mount_path": "/resume.md"}],
        budget={"type": "limit", "max_list_cost": {"amount": SESSION_BUDGET_CENTS, "currency": "USD"}},
    )
    session_id = str(_get(session, "id") or "")
    if not session_id:
        raise ManagedError("The session did not return an id.")
    trace = f"https://platform.claude.com/workspaces/default/sessions/{session_id}"
    report(f"Session {session_id} is open")

    outcome = {
        "type": "user.define_outcome",
        "description": _task(resume_path, job, answers),
        "rubric": {"type": "text", "content": RUBRIC},
        "max_iterations": 3,
    }
    report("The agent is working")
    _drain(api, session_id, outcome, report)
    report("Downloading the draft")
    text = _download_report(api, session_id, sleeper)
    path = root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    summary = f"Managed Agents session {session_id} wrote the draft. The source resume was left unchanged."
    return ManagedResult(summary=summary, path=path, session_id=session_id, trace=trace)


def load_ids(root: Path) -> tuple[str, str]:
    path = root / LOCK
    if not path.is_file():
        raise ManagedError(APPLY_HINT)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManagedError(f"claude-lock.json is not valid JSON. {APPLY_HINT}") from exc
    resources = payload.get("resources") if isinstance(payload, dict) else None
    if not isinstance(resources, dict):
        raise ManagedError(APPLY_HINT)
    agent_id = _resource_id(resources, LEAD)
    environment_id = _resource_id(resources, ENVIRONMENT)
    if not agent_id or not environment_id:
        raise ManagedError(APPLY_HINT)
    return agent_id, environment_id


def make_client():
    from hunt.claude_client import ClaudeAuthError, make_client as build

    try:
        return build()
    except ClaudeAuthError as exc:
        raise ManagedError(str(exc)) from exc


def _drain(api, session_id: str, outcome: dict, report) -> None:
    with api.beta.sessions.events.stream(session_id=session_id) as stream:
        api.beta.sessions.events.send(session_id=session_id, events=[outcome])
        report("Outcome sent")
        for event in stream:
            kind = _get(event, "type")
            line = _event_line(event)
            if line:
                report(line)
            if kind in {"agent.tool_use", "agent.mcp_tool_use"} and _get(event, "evaluated_permission") == "ask":
                api.beta.sessions.events.send(
                    session_id=session_id,
                    events=[{
                        "type": "user.tool_confirmation",
                        "tool_use_id": _get(event, "id"),
                        "result": "deny",
                    }],
                )
                continue
            if kind == "session.error":
                message = _get(_get(event, "error"), "message") or "The session reported an error."
                raise ManagedError(str(message))
            if kind == "session.status_terminated":
                return
            if kind != "session.status_idle":
                continue
            reason = _stop_reason(event)
            if reason == "requires_action":
                continue
            if reason == "budget_reached":
                raise ManagedError(
                    "The session paused at its $8.00 list-price budget. "
                    "Raise max_list_cost in hunt/managed_rewrite.py and start a new session."
                )
            return
    raise ManagedError("The session stream ended before the agent finished.")


def _download_report(api, session_id: str, sleeper) -> str:
    last = []
    for attempt in range(3):
        page = api.beta.files.list(scope_id=session_id, betas=["managed-agents-2026-04-01"])
        last = list(_get(page, "data") or [])
        if last:
            break
        if attempt < 2:
            sleeper(1)
    chosen = _pick_output(last)
    if chosen is None:
        raise ManagedError(
            "The session finished and no file was in /mnt/session/outputs/. "
            f"Open https://platform.claude.com/workspaces/default/sessions/{session_id}"
        )
    downloaded = api.beta.files.download(_get(chosen, "id"))
    if hasattr(downloaded, "read"):
        raw = downloaded.read()
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    if hasattr(downloaded, "write_to_file"):
        target = Path(os.getenv("TMPDIR", "/tmp")) / f"hunt-{session_id}-{OUTPUT_NAME}"
        downloaded.write_to_file(str(target))
        return target.read_text(encoding="utf-8")
    if isinstance(downloaded, bytes):
        return downloaded.decode("utf-8")
    raise ManagedError("The output file could not be read.")


def _pick_output(files: list) -> object | None:
    named = [item for item in files if _get(item, "filename") == OUTPUT_NAME]
    if named:
        return named[0]
    markdown = [item for item in files if str(_get(item, "filename") or "").endswith(".md")]
    if markdown:
        return markdown[0]
    return files[0] if files else None


def _task(source: Path, job: str, answers: str) -> str:
    lines = [
        f"Rewrite the resume mounted at {MOUNT}.",
        f"The local source file is {source.name}. Do not edit it.",
        f"Write /mnt/session/outputs/{OUTPUT_NAME}.",
        "",
        "Target job, empty if none:",
        job or "(none)",
        "",
        "Numbers the user supplied:",
        answers or "(none)",
    ]
    return "\n".join(lines)


def _resume_text(root: Path, raw: str) -> tuple[Path, str]:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise ManagedError(f"Resume file not found: {raw}")
    try:
        text = read_upload(path)
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise ManagedError(str(exc)) from exc
    if path.suffix.lower() == ".tex":
        text = latex_to_text(text)
    if len(text.strip()) < 40:
        raise ManagedError("The resume file is too short to rewrite.")
    return path, text


def _answers(root: Path, payload: dict) -> str:
    typed = str(payload.get("answers") or "").strip()
    if typed:
        return typed
    path = root / ANSWERS
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def _as_file(text: str):
    import io

    handle = io.BytesIO(text.encode("utf-8"))
    handle.name = "resume.md"
    return handle


def _resource_id(resources: dict, relative: str) -> str:
    for key in (relative, f"./{relative}"):
        item = resources.get(key)
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
        if isinstance(item, str) and item:
            return item
    return ""


def _event_line(event) -> str:
    kind = str(_get(event, "type") or "")
    if kind in {"agent.tool_use", "agent.mcp_tool_use"}:
        name = _get(event, "name") or "call"
        return f"Tool · {name}"
    if kind == "session.status_running":
        return "Session running"
    if kind == "session.status_idle":
        return "Session idle · " + (_stop_reason(event) or "done")
    if kind == "agent.message":
        text = _message_text(event).strip().splitlines()
        return text[0][:90] if text else ""
    return ""


def _message_text(event) -> str:
    content = _get(event, "content") or _get(event, "text") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            else:
                value = _get(block, "text")
                if value:
                    parts.append(str(value))
        return " ".join(parts)
    return ""


def _stop_reason(event) -> str:
    reason = _get(event, "stop_reason")
    if reason is None:
        return ""
    if isinstance(reason, str):
        return reason
    return str(_get(reason, "type") or "")


def _get(value, name: str):
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
