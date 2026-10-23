"""Background checks: launchd on macOS, Task Scheduler on Windows."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from hunt import system

UNSUPPORTED = "Background checks run on macOS and Windows only."


def _backend() -> ModuleType | None:
    if system.is_mac():
        from hunt import launchd

        return launchd
    if system.is_windows():
        from hunt import winsched

        return winsched
    return None


def install(root: Path) -> str:
    backend = _backend()
    if backend is None:
        raise RuntimeError(UNSUPPORTED)
    return backend.install(root)


def uninstall(root: Path) -> str:
    backend = _backend()
    if backend is None:
        raise RuntimeError(UNSUPPORTED)
    return backend.uninstall(root)


def installed(root: Path) -> bool:
    backend = _backend()
    return False if backend is None else backend.installed(root)
