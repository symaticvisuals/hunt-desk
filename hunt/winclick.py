"""What a toast click opens on Windows: a per-user URL protocol that runs data\\open-desk.cmd."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol

from hunt import system

OPEN_DESK_CMD = "data/open-desk.cmd"


class Registry(Protocol):
    """Keys are relative to HKEY_CURRENT_USER. The name "" is a key's default value."""

    def get(self, key: str, name: str) -> str | None: ...

    def set(self, key: str, name: str, value: str) -> None: ...

    def delete_tree(self, key: str) -> None: ...


class WinRegistry:
    def get(self, key: str, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                value, _ = winreg.QueryValueEx(handle, name)
        except OSError:
            return None
        return str(value)

    def set(self, key: str, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_WRITE) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete_tree(self, key: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                children = []
                while True:
                    try:
                        children.append(winreg.EnumKey(handle, len(children)))
                    except OSError:
                        break
        except FileNotFoundError:
            return
        for child in children:
            self.delete_tree(f"{key}\\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def scheme(root: Path) -> str:
    return f"hunt-desk-{system.digest(root)}"


def desk_cmd(root: Path, uv: str | None) -> str:
    run = f'"{uv}" run python -m hunt' if uv else f'"{root / ".venv" / "Scripts" / "python.exe"}" -m hunt'
    lines = ["@echo off", "chcp 65001 >nul", "title Hunt desk", "set PYTHONUTF8=1", f'cd /d "{root}"', run]
    return "\r\n".join(lines) + "\r\n"


def protocol_command(root: Path) -> str:
    shell = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    return f'"{shell}" /c "{root / OPEN_DESK_CMD}"'


def _values(root: Path) -> list[tuple[str, str, str]]:
    key = f"Software\\Classes\\{scheme(root)}"
    return [
        (key, "", "URL:Hunt Desk"),
        (key, "URL Protocol", ""),
        (f"{key}\\shell\\open\\command", "", protocol_command(root)),
    ]


def ensure(root: Path, *, which=shutil.which, registry: Registry | None = None) -> str:
    script = root / OPEN_DESK_CMD
    body = desk_cmd(root, which("uv")).encode("utf-8")
    if not script.exists() or script.read_bytes() != body:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_bytes(body)
    registry = registry or WinRegistry()
    for key, name, value in _values(root):
        if registry.get(key, name) != value:
            registry.set(key, name, value)
    return scheme(root)


def remove(root: Path, *, registry: Registry | None = None) -> None:
    (registry or WinRegistry()).delete_tree(f"Software\\Classes\\{scheme(root)}")
