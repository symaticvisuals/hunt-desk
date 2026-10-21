"""Turn a resume into keywords and a years cap. No model required."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hunt.inbox import load_json, save_json, write_resume

_ROLE = re.compile(
    r"\b(engineer|developer|designer|scientist|architect|researcher|manager|analyst|consultant|lead|director|"
    r"specialist|strategist|marketer|writer|recruiter|owner)\b",
    re.IGNORECASE,
)
_ENGINEERING = re.compile(r"\b(engineer|developer|programmer)\b", re.IGNORECASE)
_RANGE = re.compile(
    r"\b(19\d{2}|20\d{2})\s*[–—\-]+\s*(19\d{2}|20\d{2}|now|present|current)\b",
    re.IGNORECASE,
)
_SENIORITY = re.compile(r"\b(senior|staff|principal|lead|junior|jr|sr)\b", re.IGNORECASE)

# Title phrases the board filter can match. Longer phrases are checked first.
_PHRASES = (
    "product manager",
    "program manager",
    "project manager",
    "product owner",
    "product designer",
    "product marketing",
    "data analyst",
    "business analyst",
    "forward deployed",
    "applied ai",
    "machine learning engineer",
    "machine learning",
    "ai engineer",
    "founding engineer",
    "product engineer",
    "site reliability",
    "data engineer",
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "fullstack",
    "mobile engineer",
    "devops",
    "freelance",
    "contract",
)


@dataclass
class Inferred:
    keywords: list[str]
    years_cap: int | None
    roles: list[str]


def read_upload(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"No file at {path}")
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".markdown"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        return _pdf_text(path)
    if suffix in {".doc", ".docx", ".rtf", ".html", ".htm"}:
        return _textutil(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _pdf_text(path: Path) -> str:
    if shutil.which("pdftotext"):
        completed = subprocess.run(
            ["pdftotext", "-q", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout
    text = _pypdf_text(path)
    if text.strip():
        return text
    raise RuntimeError("Could not read text from that PDF.")


def _pypdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n".join(part for part in parts if part)


def _textutil(path: Path) -> str:
    completed = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError("Could not read that document. Save it as text or PDF and try again.")
    return completed.stdout


def infer(text: str, *, today: date | None = None) -> Inferred:
    """Pull title keywords and a years cap from resume text."""
    raw = text or ""
    lowered = raw.lower()
    keywords: list[str] = []
    for phrase in _PHRASES:
        if phrase in lowered:
            keywords.append(phrase)
    roles: list[str] = []
    for line in raw.splitlines():
        cleaned = line.strip(" #-*\t")
        if not cleaned or len(cleaned) > 90 or not _ROLE.search(cleaned):
            continue
        title = re.split(r"[,|•]|(?:\s[–—-]\s)", cleaned, maxsplit=1)[0].strip()
        if not title or title.lower() in {"skills", "experience"}:
            continue
        roles.append(title)
        phrase = _SENIORITY.sub("", title).lower()
        phrase = " ".join(phrase.split())
        if phrase and phrase not in keywords and 1 < len(phrase.split()) <= 4:
            keywords.append(phrase)
    if not keywords and _ENGINEERING.search(raw):
        keywords.append("software engineer")
    return Inferred(keywords=keywords[:12], years_cap=_years_cap(raw, today or date.today()), roles=roles[:6])


def _years_cap(text: str, today: date) -> int | None:
    lines = text.splitlines()
    starts: list[int] = []
    ends: list[int] = []
    for index, line in enumerate(lines):
        window = " ".join(lines[max(0, index - 2) : index + 1])
        if not _ROLE.search(window):
            continue
        for match in _RANGE.finditer(line):
            starts.append(int(match.group(1)))
            end = match.group(2).lower()
            ends.append(today.year if end in {"now", "present", "current"} else int(end))
    if not starts:
        return None
    span = max(1, max(ends) - min(starts))
    return min(20, span + 2)


def apply_setup(root: Path, resume_text: str, inferred: Inferred) -> None:
    """Write the resume, keywords, and years cap. Sources and location stay as they are."""
    write_resume(root, resume_text.strip() + "\n")
    search_path = root / "profile/search.json"
    search = load_json(search_path, {})
    if not isinstance(search, dict):
        search = {}
    search["keywords"] = inferred.keywords
    search.setdefault("exclude_title", ["intern", "internship"])
    search.setdefault("location", {"mode": "any"})
    search.setdefault("sources", ["greenhouse", "lever", "ashby", "arbeitnow", "jobicy"])
    save_json(search_path, search)

    rubric_path = root / "profile/rubric.json"
    rubric = load_json(rubric_path, {})
    if not isinstance(rubric, dict):
        rubric = {}
    rubric.setdefault("natural_hire_at", 80)
    rubric.setdefault("screenout_cap", 75)
    rubric.setdefault("missing_platform_penalty", 8)
    rubric.setdefault("overlevel_cap", 70)
    rubric.setdefault("comp_penalty", 10)
    rubric.setdefault("comp", {"min": None, "max": None, "currency": None})
    rubric.setdefault("bonuses", [])
    if inferred.years_cap is not None:
        rubric["max_years_without_cap"] = inferred.years_cap
    save_json(rubric_path, rubric)

    save_json(
        root / "data/setup.json",
        {
            "keywords": inferred.keywords,
            "years_cap": inferred.years_cap,
            "roles": inferred.roles,
        },
    )


def needs_onboarding(root: Path) -> bool:
    path = root / "profile/og-resume.md"
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8")
    if "PLACEHOLDER_RESUME" in text:
        return True
    return len(text.strip()) < 80


def pick_resume_file() -> str | None:
    """macOS file dialog. Returns a path, or None if the person cancels."""
    script = 'POSIX path of (choose file with prompt "Choose your resume")'
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    path = completed.stdout.strip()
    return path or None
