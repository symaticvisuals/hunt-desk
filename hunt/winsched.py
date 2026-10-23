"""A per-user Task Scheduler task that runs `hunt tick` every 15 minutes on Windows."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from hunt import system, winclick
from hunt.winclick import Registry

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def task_name(root: Path) -> str:
    return f"hunt-tick-{system.digest(root)}"


def pythonw(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "pythonw.exe"


def _add(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, f"{{{NS}}}{tag}", attrs)
    if text is not None:
        element.text = text
    return element


def build_xml(root: Path, pythonw: str, start: datetime) -> str:
    ET.register_namespace("", NS)
    task = ET.Element(f"{{{NS}}}Task", {"version": "1.2"})
    _add(_add(task, "RegistrationInfo"), "Description", "hunt: check the job boards every 15 minutes")
    trigger = _add(_add(task, "Triggers"), "TimeTrigger")
    _add(trigger, "StartBoundary", start.strftime("%Y-%m-%dT%H:%M:%S"))
    _add(trigger, "Enabled", "true")
    repetition = _add(trigger, "Repetition")
    _add(repetition, "Interval", "PT15M")
    _add(repetition, "StopAtDurationEnd", "false")
    principal = _add(_add(task, "Principals"), "Principal", id="Author")
    _add(principal, "LogonType", "InteractiveToken")
    _add(principal, "RunLevel", "LeastPrivilege")
    settings = _add(task, "Settings")
    _add(settings, "MultipleInstancesPolicy", "IgnoreNew")
    # Windows skips tasks on battery by default, so a laptop would never check.
    _add(settings, "DisallowStartIfOnBatteries", "false")
    _add(settings, "StopIfGoingOnBatteries", "false")
    _add(settings, "StartWhenAvailable", "true")
    _add(settings, "ExecutionTimeLimit", "PT2H")
    _add(settings, "Enabled", "true")
    action = _add(_add(task, "Actions", Context="Author"), "Exec")
    _add(action, "Command", pythonw)
    _add(action, "Arguments", "-X utf8 -m hunt tick")
    _add(action, "WorkingDirectory", str(root))
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(task, encoding="unicode")


def _schtasks(runner, args: list[str]):
    return runner(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **system.no_window(),
    )


def install(
    root: Path,
    *,
    runner=subprocess.run,
    which=shutil.which,
    registry: Registry | None = None,
    now: datetime | None = None,
) -> str:
    command = pythonw(root)
    if not command.exists():
        raise RuntimeError(r"Run start.cmd first. .venv\Scripts\pythonw.exe is missing.")
    xml = build_xml(root, str(command), now or datetime.now())
    handle, path = tempfile.mkstemp(prefix="hunt-task-", suffix=".xml")
    try:
        with open(handle, "wb") as out:
            out.write(xml.encode("utf-16"))
        done = _schtasks(runner, ["/Create", "/TN", task_name(root), "/XML", path, "/F"])
    finally:
        Path(path).unlink(missing_ok=True)
    if done.returncode != 0:
        raise RuntimeError(f"Task Scheduler could not add the schedule: {(done.stderr or '').strip() or done.returncode}")
    winclick.ensure(root, which=which, registry=registry)
    return "The schedule is on. Task Scheduler checks every 15 minutes and runs only the boards that are due."


def uninstall(root: Path, *, runner=subprocess.run, registry: Registry | None = None) -> str:
    done = _schtasks(runner, ["/Delete", "/TN", task_name(root), "/F"])
    winclick.remove(root, registry=registry)
    return "The schedule is off." if done.returncode == 0 else "The schedule was not installed."


def installed(root: Path, *, runner=subprocess.run) -> bool:
    return _schtasks(runner, ["/Query", "/TN", task_name(root)]).returncode == 0
