"""Find Grok and Claude workflows and build the command that runs one."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"""name:\s*["']([^"']+)["']""")
_DESCRIPTION = re.compile(r"""description:\s*["']([^"']+)["']""")


@dataclass(frozen=True)
class Workflow:
    name: str
    engine: str
    description: str
    path: Path

    def label(self) -> str:
        return f"{self.engine}:{self.name}"


def discover(root: Path) -> list[Workflow]:
    found: list[Workflow] = [
        Workflow(
            name="resume-rewrite",
            engine="api",
            description="Claude API. Each step uses the model in profile/models.json.",
            path=root / "profile" / "models.json",
        ),
        Workflow(
            name="resume-rewrite",
            engine="managed",
            description="Claude Managed Agents. One session, a worker per role.",
            path=root / "agents" / "resume-lead" / "agent.md",
        ),
    ]
    grok_dir = root / ".grok" / "workflows"
    claude_dir = root / ".claude" / "workflows"
    if grok_dir.is_dir():
        for path in sorted(grok_dir.glob("*.rhai")):
            found.append(_from_file(path, "grok"))
    if claude_dir.is_dir():
        for path in sorted(claude_dir.glob("*.js")):
            found.append(_from_file(path, "claude"))
    return found


def _from_file(path: Path, engine: str) -> Workflow:
    text = path.read_text(encoding="utf-8", errors="replace")
    name = _first(_NAME, text) or path.stem
    description = _first(_DESCRIPTION, text) or ""
    return Workflow(name=name, engine=engine, description=description, path=path)


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def command(workflow: Workflow, root: Path, payload: dict) -> list[str]:
    """Command line that opens the engine on this workflow. The desk suspends to run it."""
    args = json.dumps(payload, separators=(",", ":"))
    if workflow.engine == "grok":
        binary = shutil.which("grok")
        if binary is None:
            raise RuntimeError("grok is not on PATH.")
        prompt = f"/workflow {workflow.name} {args}"
        return [binary, "--cwd", str(root), prompt]
    if workflow.engine == "api":
        raise RuntimeError(
            "The api engine calls the Claude API inside hunt. "
            "Run: uv run python -m hunt run api resume-rewrite"
        )
    if workflow.engine == "managed":
        raise RuntimeError(
            "The managed engine starts a Claude Managed Agents session inside hunt. "
            "Run: uv run python -m hunt run managed resume-rewrite"
        )
    if workflow.engine == "claude":
        binary = shutil.which("claude")
        if binary is None:
            raise RuntimeError("claude is not on PATH.")
        prompt = (
            "Run the Claude dynamic workflow in "
            + str(workflow.path)
            + " with these args: "
            + args
            + ". Use the Workflow tool on that script. Do not rewrite the resume yourself."
        )
        return [binary, prompt]
    raise RuntimeError(f"Unknown engine {workflow.engine}")
