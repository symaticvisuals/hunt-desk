"""Run `claude -p` for one structured answer. Output is captured, and Claude Code signs in on its own."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# These .env keys are for the API rewrite. Passed on, they would override the person's claude.ai login.
CLAUDE_AUTH_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID")


class ClaudeMissing(Exception):
    pass


def job_dir(root: Path, job: dict) -> Path:
    """One folder per role for its Claude packets and logs, so runs in parallel never share a file."""
    words = re.sub(r"[^a-z0-9]+", "-", f"{job.get('company') or ''} {job.get('title') or job.get('role') or ''}".lower()).strip("-")[:60]
    digest = hashlib.sha1((job.get("url") or words).encode()).hexdigest()[:6]
    folder = root / "data" / "work" / f"{words or 'role'}-{digest}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def claude_env(root: Path) -> dict[str, str]:
    from hunt.sources import read_env_file

    env = dict(os.environ)
    from_file = read_env_file(root / ".env")
    for key in CLAUDE_AUTH_KEYS:
        if key in from_file and env.get(key) == from_file[key]:
            env.pop(key, None)
    return env


def result_of(stdout: str) -> dict | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    items = data if isinstance(data, list) else [data]
    return next((item for item in reversed(items) if isinstance(item, dict) and item.get("type") == "result"), None)


def ask(root: Path, prompt: str, schema: dict, *, log: str, timeout: int, runner=subprocess.run) -> tuple[dict | None, str]:
    """The structured output, or None and the reason. Raises ClaudeMissing when claude is not on PATH."""
    binary = shutil.which("claude")
    if binary is None:
        raise ClaudeMissing("claude is not on PATH.")
    command = [binary, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema), "--allowedTools", "Read"]
    try:
        done = runner(
            command,
            cwd=root,
            env=claude_env(root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"Claude did not finish in {timeout // 60} minutes."
    (root / log).write_text((done.stdout or "") + (done.stderr or ""), encoding="utf-8")
    result = result_of(done.stdout or "")
    if result is None:
        detail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
        return None, f"Claude Code failed: {detail[0] if detail else f'exit {done.returncode}'}"
    if result.get("is_error") or result.get("subtype") != "success":
        reason = str(result.get("result") or "").strip()[:200]
        return None, f"{result.get('subtype') or 'error'}. {reason}".strip()
    output = result.get("structured_output")
    if not isinstance(output, dict):
        return None, "Claude returned no structured answer."
    return output, ""
