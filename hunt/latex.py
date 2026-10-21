"""Render a tailored resume (the TAILOR_SCHEMA dict) into Jake Gutierrez's one-page LaTeX template."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates/jake.tex"
LEVELS = ("new_grad", "entry", "mid", "senior", "manager", "director")
ORDER: dict[str, tuple[str, ...]] = {
    "new_grad": ("education", "experience", "projects", "skills"),
    "entry": ("experience", "skills", "education", "projects"),
    "mid": ("summary", "experience", "projects", "skills", "education"),
    "senior": ("summary", "experience", "projects", "skills", "education"),
    "manager": ("summary", "experience", "education", "skills"),
    "director": ("summary", "experience", "education", "skills"),
}
TITLES = {"summary": "Summary", "experience": "Experience", "projects": "Projects", "skills": "Skills", "education": "Education"}

# Characters the engine cannot set, or the Voice rules forbid, mapped before anything else.
_MAP = (
    ("“", '"'), ("”", '"'), ("„", '"'), ("‘", "'"), ("’", "'"),
    (" — ", ", "), ("—", ", "), ("–", "-"), ("→", "to"), ("×", "x"), ("•", "-"), ("…", "..."), ("\u00a0", " "),
)
_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_GUARD = "\\ifdefined\\pdfgentounicode\n  \\input{glyphtounicode}\n  \\pdfgentounicode=1\n\\fi"


# Latin-1, Latin Extended-A and -B, and Latin Extended Additional (Vietnamese). Names in these scripts are kept.
_LATIN = ((0x20, 0x7E), (0xA0, 0x24F), (0x1E00, 0x1EFF))


def _settable(ch: str) -> bool:
    if ch in " \t\n\r\u00a0":
        return True
    if unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "So"}:
        return False
    return any(low <= ord(ch) <= high for low, high in _LATIN)


def plain(text: str) -> str:
    out = unicodedata.normalize("NFC", str(text or ""))
    for old, new in _MAP:
        out = out.replace(old, new)
    out = "".join(ch for ch in out if _settable(ch))
    return re.sub(r"\s+", " ", out).strip()


def escape(text: str) -> str:
    return "".join(_SPECIAL.get(ch, ch) for ch in plain(text))


def escape_url(url: str) -> str:
    """hyperref takes a URL almost raw; only # and % would break the argument."""
    return plain(url).replace("#", r"\#").replace("%", r"\%")


def _dates(text: str) -> str:
    return re.sub(r"\s*-+\s*", " -- ", escape(text))


def preamble() -> str:
    """The template up to \\begin{document}, with the pdfTeX-only ATS lines wrapped so XeTeX engines skip them."""
    head = TEMPLATE.read_text(encoding="utf-8").split("\\begin{document}", 1)[0]
    head = head.replace("\\input{glyphtounicode}\n", "", 1)
    return head.replace("\\pdfgentounicode=1", _GUARD, 1)


def _level(resume: dict) -> str:
    level = str(resume.get("candidate_level") or "")
    return level if level in ORDER else "mid"


def _bullets(entry: dict) -> list[str]:
    return [escape(bullet.get("text") or "") for bullet in entry.get("bullets") or [] if (bullet.get("text") or "").strip()]


def present_sections(resume: dict) -> list[str]:
    def has(name: str) -> bool:
        if name == "summary":
            return bool(plain(resume.get("summary") or ""))
        return bool(resume.get(name))

    return [name for name in ORDER[_level(resume)] if has(name)]


def _heading(header: dict) -> str:
    items: list[str] = []
    for name in ("location", "phone"):
        if plain(header.get(name) or ""):
            items.append(escape(header.get(name) or ""))
    email = plain(header.get("email") or "")
    if email:
        items.append(f"\\href{{mailto:{escape_url(email)}}}{{\\underline{{{escape(email)}}}}}")
    for link in header.get("links") or []:
        url = plain(link.get("url") or "")
        if not url:
            continue
        label = escape(link.get("label") or url)
        target = url if re.match(r"^[a-z]+:", url) else f"https://{url}"
        items.append(f"\\href{{{escape_url(target)}}}{{\\underline{{{label}}}}}")
    lines = ["\\begin{center}", f"    \\textbf{{\\Huge \\scshape {escape(header.get('name') or '')}}} \\\\ \\vspace{{1pt}}"]
    if items:
        lines.append("    \\small " + " $|$ ".join(items))
    lines.append("\\end{center}")
    return "\n".join(lines)


def _items(bullets: list[str]) -> list[str]:
    if not bullets:
        return []
    return ["      \\resumeItemListStart"] + [f"        \\resumeItem{{{text}}}" for text in bullets] + ["      \\resumeItemListEnd"]


def _section(name: str, resume: dict) -> list[str]:
    lines = [f"\\section{{{TITLES[name]}}}"]
    if name == "summary":
        return lines + [f"{{\\small {escape(resume.get('summary') or '')}\\par}}"]
    if name == "skills":
        rows = [f"     \\textbf{{{escape(row.get('label') or '')}}}{{: {escape(row.get('items') or '')}}}" for row in resume["skills"]]
        return lines + [
            " \\begin{itemize}[leftmargin=0.15in, label={}]",
            "    \\small{\\item{",
            " \\\\\n".join(rows),
            "    }}",
            " \\end{itemize}",
        ]
    lines.append("  \\resumeSubHeadingListStart")
    for entry in resume[name]:
        if name == "experience":
            lines.append(
                f"    \\resumeSubheading{{{escape(entry.get('title') or '')}}}{{{_dates(entry.get('dates') or '')}}}"
                f"{{{escape(entry.get('company') or '')}}}{{{escape(entry.get('location') or '')}}}"
            )
            lines += _items(_bullets(entry))
        elif name == "projects":
            title = f"\\textbf{{{escape(entry.get('name') or '')}}}"
            if plain(entry.get("stack") or ""):
                title += f" $|$ \\emph{{{escape(entry.get('stack') or '')}}}"
            lines.append(f"    \\resumeProjectHeading{{{title}}}{{{_dates(entry.get('dates') or '')}}}")
            lines += _items(_bullets(entry))
        else:
            lines.append(
                f"    \\resumeSubheading{{{escape(entry.get('school') or '')}}}{{{escape(entry.get('location') or '')}}}"
                f"{{{escape(entry.get('degree') or '')}}}{{{_dates(entry.get('dates') or '')}}}"
            )
    lines.append("  \\resumeSubHeadingListEnd")
    return lines


def render(resume: dict) -> str:
    parts = [preamble().rstrip(), "", "\\begin{document}", "", _heading(resume.get("header") or {}), ""]
    for name in present_sections(resume):
        parts += _section(name, resume) + [""]
    parts.append("\\end{document}")
    return "\n".join(parts) + "\n"
