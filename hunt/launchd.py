"""A per-user launchd agent that runs `hunt tick` every 15 minutes."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shlex
import shutil
import subprocess
from pathlib import Path

from hunt.notify import OPEN_DESK

INTERVAL_SECONDS = 900
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def label(root: Path) -> str:
    digest = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]
    return f"com.hunt.tick.{digest}"


def plist_path(root: Path, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{label(root)}.plist"


def build_plist(root: Path, uv: str) -> dict:
    out = str(root / "data" / "schedule.out")
    return {
        "Label": label(root),
        "ProgramArguments": [uv, "run", "--directory", str(root), "python", "-m", "hunt", "tick"],
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "WorkingDirectory": str(root),
        "StandardOutPath": out,
        "StandardErrorPath": out,
        "EnvironmentVariables": {"PATH": PATH},
    }


def _domain(uid: int | None) -> str:
    return f"gui/{os.getuid() if uid is None else uid}"


def install(root: Path, *, runner=subprocess.run, which=shutil.which, home: Path | None = None, uid: int | None = None) -> str:
    uv = which("uv")
    if not uv:
        raise RuntimeError("uv is not on PATH, so the schedule cannot start. Install uv, then try again.")
    path = plist_path(root, home)
    domain = _domain(uid)
    runner(["launchctl", "bootout", f"{domain}/{label(root)}"], check=False, capture_output=True, text=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(build_plist(root, uv)))
    done = runner(["launchctl", "bootstrap", domain, str(path)], check=False, capture_output=True, text=True)
    if done.returncode != 0:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"launchctl could not load the schedule: {(done.stderr or '').strip() or done.returncode}")
    script = root / OPEN_DESK
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!/bin/sh\ncd {shlex.quote(str(root))} && {shlex.quote(uv)} run python -m hunt\n", encoding="utf-8")
    script.chmod(0o755)
    return "The schedule is on. launchd checks every 15 minutes and runs only the boards that are due."


def uninstall(root: Path, *, runner=subprocess.run, home: Path | None = None, uid: int | None = None) -> str:
    path = plist_path(root, home)
    runner(["launchctl", "bootout", f"{_domain(uid)}/{label(root)}"], check=False, capture_output=True, text=True)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return "The schedule is off." if existed else "The schedule was not installed."


def installed(root: Path, *, runner=subprocess.run, uid: int | None = None) -> bool:
    done = runner(["launchctl", "print", f"{_domain(uid)}/{label(root)}"], check=False, capture_output=True, text=True)
    return done.returncode == 0
