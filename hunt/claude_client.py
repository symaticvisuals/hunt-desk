"""Claude client setup. A key that is not tied to one workspace must send a workspace id."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hunt.inbox import upsert_env
from hunt.sources import load_env

WORKSPACE_HEADER = "anthropic-workspace-id"
WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"


class ClaudeAuthError(Exception):
    pass


@dataclass(frozen=True)
class WorkspaceLookup:
    selected: str = ""
    choices: tuple[tuple[str, str], ...] = ()


def workspace_id() -> str:
    return os.environ.get(WORKSPACE_ENV, "").strip()


def store_workspace_id(root: Path, value: str) -> None:
    text = (value or "").strip()
    if not text:
        return
    upsert_env(root / ".env", {WORKSPACE_ENV: text})
    os.environ[WORKSPACE_ENV] = text


def client_options() -> dict:
    chosen = workspace_id()
    if not chosen:
        return {}
    return {"default_headers": {WORKSPACE_HEADER: chosen}}


def make_client():
    try:
        import anthropic
    except ImportError as exc:
        raise ClaudeAuthError("The Claude API client is missing. From this directory, run: uv sync") from exc
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ClaudeAuthError("Set ANTHROPIC_API_KEY in .env. The value is never printed.")
    return anthropic.Anthropic(api_key=key, **client_options())


def list_workspaces(client) -> list[tuple[str, str]]:
    page = client.beta.organization.workspaces.list(include_default=True, limit=100)
    rows = getattr(page, "data", None)
    if rows is None:
        rows = list(page)
    found: list[tuple[str, str]] = []
    for item in rows:
        if _field(item, "archived_at"):
            continue
        identifier = str(_field(item, "id") or "").strip()
        if not identifier:
            continue
        name = str(_field(item, "name") or identifier).strip()
        found.append((identifier, name))
    return found


def lookup_workspace(root: Path, *, client=None) -> WorkspaceLookup:
    load_env(root / ".env")
    chosen = workspace_id()
    if chosen:
        return WorkspaceLookup(selected=chosen)
    api = client if client is not None else make_client()
    try:
        rows = list_workspaces(api)
    except Exception:
        return WorkspaceLookup()
    if len(rows) == 1:
        store_workspace_id(root, rows[0][0])
        return WorkspaceLookup(selected=rows[0][0], choices=tuple(rows))
    return WorkspaceLookup(choices=tuple(rows))


def workspace_required(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "anthropic-workspace-id" in text or "not scoped to a workspace" in text


def _field(value, name: str):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
