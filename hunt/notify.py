"""The banner for roles that just reached the main list. A click opens the desk."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape

from hunt import macapp, system, winclick

UNSUPPORTED = "Notifications run on macOS and Windows only."
# PowerShell's own app id. Windows 11 shows nothing for an app id that was never registered.
POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


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
    if system.is_windows():
        return _post_windows(root, title, body, runner, which, registry)
    return Posted(False, UNSUPPORTED)


def _xml(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def toast_script(title: str, body: str, link: str) -> str:
    toast = (
        f'<toast activationType="protocol" launch="{_xml(link)}:open"><visual><binding template="ToastGeneric">'
        f"<text>{_xml(title)}</text><text>{_xml(body)}</text></binding></visual></toast>"
    )
    quoted = "'" + toast.replace("'", "''") + "'"
    # The catch prints a plain line, because an uncaught error under -EncodedCommand reaches stderr as CLIXML.
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "try {",
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null",
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
            f"$xml.LoadXml({quoted})",
            "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml",
            "$toast.Tag = 'hunt'",
            "$toast.Group = 'hunt'",
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{POWERSHELL_APP_ID}').Show($toast)",
            "} catch {",
            "[Console]::Error.WriteLine($_.Exception.Message)",
            "exit 1",
            "}",
        ]
    )


def _post_windows(root: Path, title: str, body: str, runner, which, registry) -> Posted:
    try:
        link = winclick.ensure(root, which=which, registry=registry)
    except OSError:
        link = winclick.scheme(root)
    try:
        done = runner(
            system.powershell(toast_script(title, body, link)),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            **system.no_window(),
        )
    except subprocess.TimeoutExpired:
        return Posted(False, "PowerShell did not show the toast within 15 seconds.")
    except OSError as exc:
        return Posted(False, str(exc))
    if done.returncode == 0:
        return Posted(True)
    lines = [line.strip() for line in (done.stderr or "").splitlines()]
    plain = [line for line in lines if line and not line.startswith(("#< CLIXML", "<"))]
    return Posted(False, plain[-1] if plain else f"PowerShell exited with {done.returncode}")


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
