"""What an ATS reads from the PDF, checked against the rules the tailoring prompt follows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import NamedTuple

from hunt.latex import TITLES, plain, present_sections

BANNED = (
    "responsible for",
    "helped with",
    "assisted in",
    "supported",
    "results-driven",
    "dynamic",
    "passionate",
    "visionary",
    "innovative",
    "proven track record",
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d ().-]{7,}\d")
_PLACEHOLDER = re.compile(r"TODO|YOUR NAME|\[metric\]|PLACEHOLDER")
_UNESCAPE = (
    (r"\textbackslash{}", "\\"), (r"\textasciitilde{}", "~"), (r"\textasciicircum{}", "^"),
    (r"\&", "&"), (r"\%", "%"), (r"\$", "$"), (r"\#", "#"), (r"\_", "_"), (r"\{", "{"), (r"\}", "}"), ("--", "-"),
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class Facts(NamedTuple):
    names: list[str]
    headings: list[str]
    keywords: list[str]
    bullets: list[str]


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(_page_text(page) for page in PdfReader(BytesIO(data)).pages)


def _page_text(page) -> str:
    """Layout mode keeps kerned words whole ("Trichy", not "T richy"), as poppler and Preview read them.
    It fails on a page with no content stream, where plain mode still answers."""
    try:
        return page.extract_text(extraction_mode="layout") or ""
    except Exception:
        return page.extract_text() or ""


def from_resume(resume: dict) -> Facts:
    header = resume.get("header") or {}
    names = [plain(header.get("name") or "")] + [plain(entry.get("company") or "") for entry in resume.get("experience") or []]
    bullets = [
        plain(bullet.get("text") or "")
        for group in ("experience", "projects")
        for entry in resume.get(group) or []
        for bullet in entry.get("bullets") or []
    ]
    return Facts(
        names=[name for name in names if name],
        headings=[TITLES[name] for name in present_sections(resume)],
        keywords=[plain(word) for word in resume.get("jd_keywords") or [] if plain(word)],
        bullets=[bullet for bullet in bullets if bullet],
    )


def _args(text: str, start: int, count: int) -> list[str]:
    """The next count brace groups after start, honouring nested braces."""
    found: list[str] = []
    index = start
    while len(found) < count:
        index = text.find("{", index)
        if index < 0:
            break
        depth, end = 0, index
        while end < len(text):
            if text[end] == "{" and text[end - 1] != "\\":
                depth += 1
            elif text[end] == "}" and text[end - 1] != "\\":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        found.append(text[index + 1 : end])
        index = end + 1
    return found


def _unlatex(text: str) -> str:
    for old, new in _UNESCAPE:
        text = text.replace(old, new)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    return " ".join(text.replace("{", "").replace("}", "").split())


def _sections(tex: str) -> list[tuple[str, str]]:
    body = tex.split("\\begin{document}", 1)[-1]
    parts = re.split(r"\\section\{([^}]*)\}", body)
    return [(parts[index].strip(), parts[index + 1]) for index in range(1, len(parts) - 1, 2)]


def from_tex(tex: str) -> Facts:
    """Facts from a .tex file someone pasted back, using Jake's macros."""
    name = re.search(r"\\scshape\s+([^}]*)\}", tex)
    names = [_unlatex(name.group(1))] if name else []
    headings: list[str] = []
    bullets: list[str] = []
    for title, body in _sections(tex):
        headings.append(_unlatex(title))
        if title.lower() == "experience":
            for match in re.finditer(r"\\resumeSubheading", body):
                args = _args(body, match.end(), 4)
                if len(args) == 4:
                    names.append(_unlatex(args[2]))
        for match in re.finditer(r"\\resumeItem(?!List)", body):
            item = _args(body, match.end(), 1)
            if item:
                bullets.append(_unlatex(item[0]))
    keywords = re.search(r"^%\s*jd_keywords:\s*(.+)$", tex, re.MULTILINE)
    words = [word.strip() for word in keywords.group(1).split(",") if word.strip()] if keywords else []
    return Facts(names=[item for item in names if item], headings=headings, keywords=words, bullets=bullets)


def section_sizes(tex: str) -> dict[str, int]:
    return {_unlatex(title): sum(1 for line in body.splitlines() if line.strip() and not line.strip().startswith("%")) for title, body in _sections(tex)}


def _has(text: str, needle: str) -> bool:
    return " ".join(needle.lower().split()) in " ".join(text.lower().split())


def _first_word(bullet: str) -> str:
    words = bullet.split()
    return words[0].lower().strip(",.:;") if words else ""


def check(text: str, pages: int, facts: Facts, job_text: str | None, master: str) -> list[Check]:
    from hunt.tailor import new_numbers

    checks = [Check("One page", pages == 1, "" if pages == 1 else f"{pages} pages")]
    missing = [item for item in facts.names + facts.headings if not _has(text, item)]
    checks.append(Check("Text layer", not missing, f"not in the PDF text: {', '.join(missing)}" if missing else ""))
    contact = []
    if not _EMAIL.search(text):
        contact.append("no email address found")
    # A date range like 2014 - 2018 has the same shape, so a phone needs ten digits.
    if not any(sum(ch.isdigit() for ch in match) >= 10 for match in _PHONE.findall(text)):
        contact.append("no phone number found")
    checks.append(Check("Contact", not contact, "; ".join(contact)))
    if job_text is not None:
        present = [word for word in facts.keywords if _has(text, word)]
        outside = [word for word in present if not _has(job_text, word)]
        ok = len(present) >= 5 and not outside
        detail = f"{len(present)} of {len(facts.keywords)} in the resume"
        if outside:
            detail += f"; not in the posting: {', '.join(outside)}"
        checks.append(Check("Keywords", ok, detail))
    # A company called Dynamic Yield is a name, not the word "dynamic".
    prose = text
    for name in facts.names:
        if name:
            prose = re.sub(re.escape(name), " ", prose, flags=re.IGNORECASE)
    banned = [phrase for phrase in BANNED if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", prose, re.IGNORECASE)]
    if "—" in text:
        banned.append("em dash")
    checks.append(Check("Banned phrases", not banned, ", ".join(banned)))
    added = new_numbers(master, text)
    checks.append(Check("New numbers", not added, ", ".join(added)))
    placeholders = sorted(set(_PLACEHOLDER.findall(text)))
    checks.append(Check("Placeholders", not placeholders, ", ".join(placeholders)))
    seen: set[str] = set()
    repeated = []
    for bullet in facts.bullets:
        key = " ".join(bullet.lower().split())
        if key in seen:
            repeated.append(bullet)
        seen.add(key)
    checks.append(Check("Duplicates", not repeated, "; ".join(repeated)))
    openings = [_first_word(bullet) for bullet in facts.bullets]
    runs = sorted({openings[index] for index in range(len(openings) - 2) if openings[index] and openings[index] == openings[index + 1] == openings[index + 2]})
    checks.append(Check("Openings", not runs, ", ".join(runs)))
    return checks


def failed(checks: list[Check]) -> list[str]:
    return [item.name for item in checks if not item.ok]


def report(checks: list[Check]) -> str:
    lines = ["## ATS check", ""]
    for item in checks:
        line = f"- {'pass' if item.ok else 'fail'} {item.name}"
        if item.detail:
            line += f": {item.detail}"
        lines.append(line)
    return "\n".join(lines)
