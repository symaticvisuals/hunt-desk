"""Hunt Desk.app posts every macOS banner, so a click relaunches it and it opens the desk."""

from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from hunt import system

APP = "data/Hunt Desk.app"
NOTE = "data/notification.txt"
OPEN_DESK = "data/open-desk.command"
APP_VERSION = 1
BUILD = "data/.hunt-desk-build"

# The folder comes from `path to me` at run time, so nothing is baked in and the source stays ASCII.
# The note is read with cat because `system attribute` decodes the environment as Mac Roman.
SOURCE = """on run
	set dataDir to do shell script "dirname " & quoted form of POSIX path of (path to me)
	if (system attribute "HUNT_POST") is "1" then
		set notePath to dataDir & "/notification.txt"
		set noteText to do shell script "cat " & quoted form of notePath & " && rm -f " & quoted form of notePath
		display notification (paragraph 2 of noteText) with title (paragraph 1 of noteText)
	else
		do shell script "open -a Terminal " & quoted form of (dataDir & "/open-desk.command")
	end if
end run
"""


def bundle_id(root: Path) -> str:
    return f"com.hunt.desk.{system.digest(root)}"


def source() -> str:
    return SOURCE


def desk_command(root: Path, uv: str | None) -> str:
    run = f"{shlex.quote(uv)} run python -m hunt" if uv else f"{shlex.quote(sys.executable)} -m hunt"
    return f"cd {shlex.quote(str(root))} && {run}"


def write_desk_script(root: Path, uv: str | None) -> Path:
    script = root / OPEN_DESK
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!/bin/sh\n{desk_command(root, uv)}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def current(root: Path) -> bool:
    info = root / APP / "Contents/Info.plist"
    try:
        plist = plistlib.loads(info.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return plist.get("CFBundleIdentifier") == bundle_id(root) and plist.get("HuntDeskVersion") == APP_VERSION


def _script_current(root: Path) -> bool:
    script = root / OPEN_DESK
    try:
        return f"cd {shlex.quote(str(root))} &&" in script.read_text(encoding="utf-8")
    except OSError:
        return False


def _run(runner, tool: str, args: list[str]) -> None:
    done = runner([tool, *args], check=False, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise RuntimeError(f"{tool} could not build Hunt Desk.app: {(done.stderr or '').strip() or done.returncode}")


def _build(root: Path, runner) -> None:
    work = root / BUILD
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        script = work / "hunt-desk.applescript"
        script.write_text(source(), encoding="utf-8")
        built = work / "Hunt Desk.app"
        _run(runner, "osacompile", ["-o", str(built), str(script)])
        info = built / "Contents/Info.plist"
        plist = plistlib.loads(info.read_bytes())
        plist.update(
            {
                "CFBundleIdentifier": bundle_id(root),
                "CFBundleName": "Hunt Desk",
                "LSUIElement": True,
                "HuntDeskVersion": APP_VERSION,
            }
        )
        info.write_bytes(plistlib.dumps(plist))
        # Editing Info.plist breaks osacompile's ad-hoc signature, and Notification Center wants a valid one.
        _run(runner, "codesign", ["--force", "--sign", "-", str(built)])
        target = root / APP
        shutil.rmtree(target, ignore_errors=True)
        built.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ensure(root: Path, *, runner=subprocess.run, which=shutil.which) -> Path:
    if not _script_current(root):
        write_desk_script(root, which("uv"))
    if not current(root):
        _build(root, runner)
    return root / APP
