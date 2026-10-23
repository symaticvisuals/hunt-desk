"""The banner for roles that just reached the main list. A click opens the desk."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from hunt import macapp, system

UNSUPPORTED = "Notifications run on macOS and Windows only."


class Posted(NamedTuple):
    ok: bool
    reason: str = ""


def message(jobs: list[dict]) -> tuple[str, str]:
    count = len(jobs)
    first = jobs[0]
    lead = f"{first.get('company') or 'A company'} — {first.get('title') or first.get('role') or 'a role'}"
    noun = "role" if count == 1 else "roles"
    body = f"{count} new {noun} worth a look · {lead}"
    if count > 1:
        body += f", +{count - 1}"
    return "Job hunt", body


def _apple(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _one_line(text: str) -> str:
    return " ".join(text.splitlines())


def send(root: Path, jobs: list[dict], *, runner=subprocess.run, which=shutil.which, registry=None) -> Posted:
    if not jobs:
        return Posted(True)
    title, body = message(jobs)
    return post(root, body, title=title, runner=runner, which=which, registry=registry)


def post(root: Path, body: str, *, title: str = "Job hunt", runner=subprocess.run, which=shutil.which, registry=None) -> Posted:
    """Never raises. ok is False when the banner fell back or failed, with the reason."""
    title, body = _one_line(title), _one_line(body)
    if system.is_mac():
        return _post_mac(root, title, body, runner, which)
    return Posted(False, UNSUPPORTED)


def _post_mac(root: Path, title: str, body: str, runner, which) -> Posted:
    try:
        app = macapp.ensure(root, runner=runner, which=which)
        (root / macapp.NOTE).write_text(f"{title}\n{body}\n", encoding="utf-8")
        done = runner(
            ["open", "-W", "-g", "-n", "--env", "HUNT_POST=1", "-a", str(app)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        if done.returncode == 0:
            return Posted(True)
        reason = f"open could not start Hunt Desk.app: {(done.stderr or '').strip() or done.returncode}"
    except subprocess.TimeoutExpired:
        reason = "Hunt Desk.app did not finish within 15 seconds."
    except (RuntimeError, OSError) as exc:
        reason = str(exc)
    try:
        runner(
            ["osascript", "-e", f"display notification {_apple(body)} with title {_apple(title)}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
    return Posted(False, reason)
