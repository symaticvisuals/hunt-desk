"""macOS banner for roles that just reached the main list."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

OPEN_DESK = "data/open-desk.command"


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


def send(root: Path, jobs: list[dict], *, runner=subprocess.run, which=shutil.which) -> None:
    if not jobs:
        return
    title, body = message(jobs)
    post(root, body, title=title, runner=runner, which=which)


def post(root: Path, body: str, *, title: str = "Job hunt", runner=subprocess.run, which=shutil.which) -> None:
    notifier = which("terminal-notifier")
    if notifier:
        command = [notifier, "-title", title, "-message", body, "-group", "hunt"]
        script = root / OPEN_DESK
        if script.exists():
            command += ["-execute", f"open -a Terminal {shlex.quote(str(script))}"]
    else:
        command = ["osascript", "-e", f"display notification {_apple(body)} with title {_apple(title)}"]
    runner(command, check=False, capture_output=True, text=True, encoding="utf-8", timeout=15)
