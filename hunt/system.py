"""What differs between macOS and Windows, in one place so tests can patch it."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def is_windows() -> bool:
    return sys.platform == "win32"


def is_mac() -> bool:
    return sys.platform == "darwin"


def no_window() -> dict[str, int]:
    """A console program started from pythonw opens its own window unless told not to."""
    return {"creationflags": CREATE_NO_WINDOW} if is_windows() else {}


def digest(root: Path) -> str:
    return hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]


def _startfile(path: str) -> None:
    os.startfile(path)  # type: ignore[attr-defined]


def open_path(path: Path, *, runner=subprocess.run) -> None:
    if is_windows():
        _startfile(str(path))
    elif is_mac():
        runner(["open", str(path)], check=False)
    else:
        runner(["xdg-open", str(path)], check=False)


def powershell(script: str, *, sta: bool = False) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", *(["-STA"] if sta else []), "-EncodedCommand", encoded]
