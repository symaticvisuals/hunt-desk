"""Tailor the master resume to one job. Claude only reads; this module writes resumes/<company>/<role>/."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

TAILOR_PROMPT = (
    "Tailor the master resume for one job. Read CLAUDE.md, profile/og-resume.md, and {packet} . "
    "Read profile/linkedin-profile.md for extra story, and profile/projects.md only when the job wants someone who "
    "builds product, agents, or internal tools. Follow the Apply and Voice sections of CLAUDE.md, and the humanizer "
    "skill when it is available. Reorder and reframe lines that are already true so the ones this job cares about come "
    "first and use the job's words where they honestly fit. Keep every fact. Do not add an employer, title, date, "
    "number, name, or tool that is not already in profile/og-resume.md. Do not edit files. "
    "Return resume_markdown (the whole tailored resume in Markdown), changes (one line per thing you moved or "
    "reframed, and why), and gaps (requirements in the job that the resume cannot honestly claim)."
)
TAILOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["resume_markdown", "changes", "gaps"],
    "properties": {
        "resume_markdown": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
}
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\+?%?")


@dataclass
class Tailored:
    code: int
    message: str
    folder: Path | None = None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "role"


def tailored_folder(root: Path, job: dict) -> Path:
    return root / "resumes" / _slug(job.get("company") or "company") / _slug(job.get("title") or job.get("role") or "role")


def is_tailored(root: Path, job: dict) -> bool:
    """A folder from before the LaTeX output, with only resume.md, counts too. It is never queued again."""
    folder = tailored_folder(root, job)
    return (folder / "resume.json").exists() or (folder / "resume.md").exists()


def new_numbers(master: str, tailored: str) -> list[str]:
    """Numbers in the tailored resume that the master resume never states."""
    known = {token.rstrip(",.") for token in _NUMBER.findall(master or "")}
    found: list[str] = []
    for token in _NUMBER.findall(tailored or ""):
        token = token.rstrip(",.")
        if token and token not in known and token not in found:
            found.append(token)
    return found


def tailor_one(root: Path, job: dict, *, runner=subprocess.run) -> Tailored:
    from hunt.claude_cli import ClaudeMissing, ask, job_dir

    work = job_dir(root, job)
    packet = dict(job)
    packet["description"] = (job.get("description") or "")[:6000]
    (work / "job-full.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
    try:
        output, problem = ask(
            root,
            TAILOR_PROMPT.format(packet=(work / "job-full.json").relative_to(root)),
            TAILOR_SCHEMA,
            log=str((work / "tailor.out").relative_to(root)),
            timeout=900,
            runner=runner,
        )
    except ClaudeMissing as exc:
        return Tailored(127, str(exc))
    if output is None:
        return Tailored(1, f"Tailoring failed: {problem}")
    return save_tailored(root, job, output)


def save_tailored(root: Path, job: dict, output: dict) -> Tailored:
    """Write resume.md, jd.md, and notes.md for one role from Claude's structured answer."""
    from hunt.inbox import read_resume

    resume = str(output.get("resume_markdown") or "").strip()
    if len(resume) < 80:
        return Tailored(1, "Tailoring failed: Claude returned an empty resume.")
    folder = tailored_folder(root, job)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "resume.md").write_text(resume + "\n", encoding="utf-8")
    (folder / "jd.md").write_text(_job_text(job), encoding="utf-8")
    added = new_numbers(read_resume(root), resume)
    (folder / "notes.md").write_text(_notes(output, added), encoding="utf-8")
    where = (folder / "resume.md").relative_to(root)
    message = f"Tailored resume: {where}"
    if added:
        message += f". Check these numbers, they are not in your master resume: {', '.join(added)}"
    return Tailored(0, message, folder)


def _job_text(job: dict) -> str:
    lines = [
        f"# {job.get('title') or job.get('role') or 'Role'}",
        "",
        " · ".join(part for part in (job.get("company"), job.get("location"), job.get("posted_at")) if part),
        "",
        f"Apply: {job.get('apply_url') or job.get('url') or ''}",
        "",
        job.get("description") or "No description on this listing.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _notes(output: dict, added: list[str]) -> str:
    lines = ["# Notes", "", "## What changed", ""]
    lines += [f"- {item}" for item in output.get("changes") or []] or ["- Nothing was moved."]
    lines += ["", "## What this job wants that your resume does not show", ""]
    lines += [f"- {item}" for item in output.get("gaps") or []] or ["- No gaps found."]
    if added:
        lines += ["", "## Check these numbers", "", "They are not in profile/og-resume.md. Remove any that are not true.", ""]
        lines += [f"- {token}" for token in added]
    return "\n".join(lines) + "\n"
