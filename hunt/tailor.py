"""Tailor the master resume to one job. Claude only reads; this module writes resumes/<company>/<role>/."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.ats import Check

RESUME_RULES = """\
Build a one-page resume for this job from the master resume. Every fact must already be in the master resume: do not add an employer, title, date, number, name, school, or tool that it does not state. Reorder and reframe true lines so the ones this job cares about come first, and use the job's words where they honestly fit.

candidate_level: new_grad, entry, mid, senior, manager, or director, judged from the master resume. It sets the section order. new_grad: Education, Experience, Projects, Skills. entry: Experience, Skills, Education, Projects. mid and senior: Summary, Experience, Projects, Skills, Education. manager and director: Summary, Experience, Education, Skills.
Summary: only from mid up, two sentences at most, leading with a specific system or product the person shipped. Leave it empty otherwise.
Experience: 3 to 5 bullets for recent or relevant roles, 2 to 3 for older ones, at most 16 bullets in total. Each bullet opens with an action verb, says what was done and how, and ends with the result or the scale the master resume gives. Give every bullet a priority: 1 for what this job most needs to see, higher numbers for bullets that can go first if the page runs long.
Projects: at most 2, only when they show something the experience does not. Priority works the same way.
Skills: a few labelled lines, with the job's terms first where the master resume backs them.
jd_keywords: 5 to 8 keywords or short phrases copied from the job description, each used somewhere in the resume where it is true.
Never use: responsible for, helped with, assisted in, supported, results-driven, dynamic, passionate, visionary, innovative, proven track record. No em dashes. No three bullets in a row that start with the same word. No "not X but Y" contrasts.
header: name, email, phone, location, and links exactly as the master resume gives them. Leave a field empty when the master resume has none.
changes: one line per thing you moved or reframed, and why. gaps: what the job asks for that the master resume cannot honestly claim.
"""
TAILOR_PROMPT = (
    "Tailor the master resume for one job. Read CLAUDE.md, profile/og-resume.md, and {packet} . "
    "Read prompts/humanizer/SKILL.md and apply it to the summary and every bullet. "
    "Read profile/linkedin-profile.md for extra story, and profile/projects.md only when the job wants someone who "
    "builds product, agents, or internal tools. Do not edit files. Follow these rules.\n\n" + RESUME_RULES
)
_STRING = {"type": "string"}
_BULLETS = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "priority"],
        "properties": {"text": _STRING, "priority": {"type": "integer"}},
    },
}


def _object(fields: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


TAILOR_SCHEMA = _object(
    {
        "candidate_level": {"type": "string", "enum": ["new_grad", "entry", "mid", "senior", "manager", "director"]},
        "header": _object(
            {"name": _STRING, "email": _STRING, "phone": _STRING, "location": _STRING, "links": {"type": "array", "items": _object({"label": _STRING, "url": _STRING})}}
        ),
        "summary": _STRING,
        "experience": {"type": "array", "items": _object({"company": _STRING, "title": _STRING, "location": _STRING, "dates": _STRING, "bullets": _BULLETS})},
        "projects": {"type": "array", "items": _object({"name": _STRING, "stack": _STRING, "dates": _STRING, "priority": {"type": "integer"}, "bullets": _BULLETS})},
        "education": {"type": "array", "items": _object({"school": _STRING, "location": _STRING, "degree": _STRING, "dates": _STRING})},
        "skills": {"type": "array", "items": _object({"label": _STRING, "items": _STRING})},
        "jd_keywords": {"type": "array", "items": _STRING},
        "changes": {"type": "array", "items": _STRING},
        "gaps": {"type": "array", "items": _STRING},
    }
)
RESUME_KEYS = ("candidate_level", "header", "summary", "experience", "projects", "education", "skills", "jd_keywords")
HUMANIZER = "prompts/humanizer/SKILL.md"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\+?%?")


@dataclass
class Tailored:
    code: int
    message: str
    folder: Path | None = None
    warn: bool = False


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


def master_text(root: Path) -> str:
    """The one resume tailoring starts from. Per-track resumes can replace it here later."""
    from hunt.inbox import read_resume

    return read_resume(root)


def humanizer_text(root: Path) -> str:
    path = root / HUMANIZER
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) == 3 else text
    return text.strip()


def resume_text(resume: dict) -> str:
    """Plain text of a tailored resume, for the number check and sameness."""
    header = resume.get("header") or {}
    lines = [header.get("name") or "", resume.get("summary") or ""]
    for entry in resume.get("experience") or []:
        lines.append(" ".join(part for part in (entry.get("title"), entry.get("company"), entry.get("dates")) if part))
        lines += [bullet.get("text") or "" for bullet in entry.get("bullets") or []]
    for project in resume.get("projects") or []:
        lines.append(" ".join(part for part in (project.get("name"), project.get("stack"), project.get("dates")) if part))
        lines += [bullet.get("text") or "" for bullet in project.get("bullets") or []]
    lines += [f"{row.get('label') or ''}: {row.get('items') or ''}" for row in resume.get("skills") or []]
    lines += [" ".join(part for part in (row.get("school"), row.get("degree"), row.get("dates")) if part) for row in resume.get("education") or []]
    return "\n".join(line for line in lines if line.strip())


def save_tailored(root: Path, job: dict, output: dict, *, compile=None) -> Tailored:
    """Write resume.json, resume.tex, resume.pdf when it builds, ats.json, jd.md, and notes.md for one role."""
    from hunt import ats, latex, pdf

    experience = [entry for entry in output.get("experience") or [] if any((bullet.get("text") or "").strip() for bullet in entry.get("bullets") or [])]
    if not experience:
        return Tailored(1, "Tailoring failed: Claude returned an empty resume.")
    resume = {key: output.get(key) for key in RESUME_KEYS}
    resume["experience"] = experience
    folder = tailored_folder(root, job)
    folder.mkdir(parents=True, exist_ok=True)
    problem = ""
    fitted = None
    try:
        fitted = pdf.fit_one_page(resume, compile=compile or pdf.compile_tex)
    except pdf.NoTeX:
        problem = "PDF not built: no TeX engine found. Run ./start to install it."
    final = fitted.resume if fitted else resume
    tex = fitted.tex if fitted else latex.render(resume)
    (folder / "resume.json").write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "resume.tex").write_text(tex, encoding="utf-8")
    (folder / "jd.md").write_text(_job_text(job), encoding="utf-8")
    master = master_text(root)
    checks: list[Check] = []
    built = bool(fitted and fitted.compiled.ok)
    if fitted and not fitted.compiled.ok:
        problem = "PDF not built: the TeX engine stopped.\n\n```\n" + fitted.compiled.log_tail + "\n```"
    if built:
        (folder / "resume.pdf").write_bytes(fitted.compiled.pdf)
        text = ats.pdf_text(fitted.compiled.pdf)
        checks = ats.check(text, fitted.compiled.pages, ats.from_resume(final), job.get("description") or "", master)
    over = bool(fitted and fitted.over)
    failed = ats.failed(checks)
    state = {"pdf": built, "pages": fitted.compiled.pages if built else 0, "failed": failed, "over": over}
    (folder / "ats.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
    added = new_numbers(master, resume_text(final))
    (folder / "notes.md").write_text(_notes(output, added, fitted.cuts if fitted else [], checks, problem), encoding="utf-8")
    where = (folder / ("resume.pdf" if built else "resume.tex")).relative_to(root)
    message = f"Tailored resume: {where}"
    if added:
        message += f". Check these numbers, they are not in your master resume: {', '.join(added)}"
    return Tailored(0, message, folder, warn=not built or over or bool(failed))


def resume_status(root: Path, job: dict) -> str:
    folder = tailored_folder(root, job)
    for name in ("resume.pdf", "resume.tex"):
        if (folder / name).exists():
            return str((folder / name).relative_to(root))
    if (folder / "resume.md").exists():
        return f"{(folder / 'resume.md').relative_to(root)} (older format)"
    return ""


def resume_flag(root: Path, job: dict) -> bool:
    """A tailored role whose PDF did not build, ran over a page, or failed an ATS check."""
    path = tailored_folder(root, job) / "ats.json"
    if not path.is_file():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    return not state.get("pdf") or bool(state.get("over")) or bool(state.get("failed"))


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


def _notes(output: dict, added: list[str], cuts: list[str], checks: list[Check], problem: str) -> str:
    from hunt.ats import report

    lines = ["# Notes", "", "## What changed", ""]
    lines += [f"- {item}" for item in output.get("changes") or []] or ["- Nothing was moved."]
    lines += ["", "## What this job wants that your resume does not show", ""]
    lines += [f"- {item}" for item in output.get("gaps") or []] or ["- No gaps found."]
    if added:
        lines += ["", "## Check these numbers", "", "They are not in profile/og-resume.md. Remove any that are not true.", ""]
        lines += [f"- {token}" for token in added]
    if cuts:
        lines += ["", "## Cuts for one page", ""] + [f"- {cut}" for cut in cuts]
    if problem:
        lines += ["", problem]
    if checks:
        lines += ["", report(checks)]
    return "\n".join(lines) + "\n"
