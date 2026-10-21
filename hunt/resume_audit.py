"""Find resume problems and apply only rewrites that do not invent facts."""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMENT = re.compile(r"(?<!\\)%.*")
_VAGUE = re.compile(
    r"\b(responsible for|worked on|helped with|helped to|tasked with|duties included|various tasks)\b",
    re.IGNORECASE,
)
_OPENER = re.compile(
    r"^(responsible for|worked on|helped with|helped to|tasked with)\s+",
    re.IGNORECASE,
)
_IMPACT = re.compile(r"\b(increased|reduced|cut|improved|saved|grew|decreased)\b", re.IGNORECASE)
_JARGON = re.compile(
    r"\b(leverage[ds]?|spearheaded|robust|delve|cutting-edge|synergy|pivotal|tapestry|"
    r"testament|underscore|showcase|nestled|groundbreaking|meticulous(?:ly)?|vibrant)\b",
    re.IGNORECASE,
)
_NOT_JUST = re.compile(r"\bnot (?:just |only |merely )?.{3,60}?\bbut\b", re.IGNORECASE)
_CHAT = re.compile(
    r"\b(i hope this helps|let me know if|great question|of course!|certainly!|would you like me to)\b",
    re.IGNORECASE,
)
_PROSE_DASH = re.compile(r"\s+(?:—|–|--)\s+")
_SERVE = re.compile(r"\b(?:serves|stands|functions|operates) as\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\b(lorem ipsum|todo|fixme|your name here)\b", re.IGNORECASE)
_ITEM = re.compile(r"^(\s*\\item(?:\[[^\]]*\])?\s+)(.*)$")


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str


def latex_to_text(source: str) -> str:
    body = source or ""
    document = re.search(r"\\begin\{document\}(.*)\\end\{document\}", body, re.DOTALL)
    if document:
        body = document.group(1)
    lines = [_COMMENT.sub("", line) for line in body.splitlines()]
    body = "\n".join(lines)
    body = re.sub(r"\\(?:item|cvitem|cventry)(?:\[[^\]]*\])?", "\n", body)
    previous = None
    while previous != body:
        previous = body
        body = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", body)
    body = body.replace("~", " ").replace("\\\\", "\n")
    body = re.sub(r"[{}\\$]", " ", body)
    return re.sub(r"[ \t]+\n", "\n", body).strip()


def bullets(text: str) -> list[str]:
    found: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "•", "*")):
            found.append(stripped.lstrip("-•* ").strip())
    return [item for item in found if item]


def audit(text: str) -> list[Finding]:
    findings: list[Finding] = []
    raw = text or ""
    if not re.search(r"[\w.+-]+@[\w.-]+\.\w+", raw):
        findings.append(Finding("contact", "No email address found."))
    if not re.search(r"\b(?:19|20)\d{2}\b", raw):
        findings.append(Finding("dates", "No years found. Put a start and end date on each role."))
    if _PLACEHOLDER.search(raw):
        findings.append(Finding("placeholder", "Placeholder or TODO text is still in the resume."))
    if len(re.findall(r"(?i)(?:^|\s)I\s", raw)) >= 3:
        findings.append(Finding("voice", "Several lines use first person. Leave the subject out."))
    for bullet in bullets(raw):
        if _VAGUE.search(bullet):
            findings.append(Finding("vague", _clip(bullet)))
        if len(bullet) > 280:
            findings.append(Finding("long", _clip(bullet)))
        if _IMPACT.search(bullet) and not re.search(r"\d", bullet):
            findings.append(Finding("unquantified", _clip(bullet)))
        if _JARGON.search(bullet) or _NOT_JUST.search(bullet):
            findings.append(Finding("ai-tell", _clip(bullet)))
    if len(_PROSE_DASH.findall(raw)) >= 2:
        findings.append(Finding("ai-tell", "Prose uses dashes as the connector between clauses."))
    if _CHAT.search(raw):
        findings.append(Finding("ai-tell", "Chat leftover is still in the resume."))
    return findings[:12]


def report(findings: list[Finding]) -> str:
    if not findings:
        return "Audit: no issues found."
    lines = [f"Audit: {len(findings)} issue{'s' if len(findings) != 1 else ''}."]
    for finding in findings:
        lines.append(f"- {finding.code}: {finding.detail}")
    return "\n".join(lines)


def rewrite_bullet(text: str) -> str:
    """Apply the humanizer edits that do not need new facts."""
    cleaned = _OPENER.sub("", (text or "").strip())
    cleaned = _PROSE_DASH.sub(", ", cleaned)
    cleaned = _SERVE.sub("is", cleaned)
    cleaned = re.sub(r"^Additionally,\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"')
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def rewrite_plain(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("-", "•", "*")):
            mark = stripped[0]
            body = rewrite_bullet(stripped[1:].strip())
            indent = line[: len(line) - len(stripped)]
            lines.append(f"{indent}{mark} {body}" if body else line)
        elif _CHAT.search(stripped) and len(stripped) < 80:
            continue
        else:
            lines.append(rewrite_bullet(line) if _PROSE_DASH.search(line) else line)
    return "\n".join(lines)


def rewrite_tex(source: str) -> tuple[str, int]:
    """Rewrite single-line \\item bodies. Preamble and commands stay put."""
    changed = 0
    output: list[str] = []
    for line in source.splitlines():
        code = _COMMENT.sub("", line).rstrip()
        if re.search(r"(?i)\\lipsum\b|lorem ipsum", code):
            changed += 1
            continue
        match = _ITEM.match(line)
        if not match:
            output.append(line)
            continue
        rewritten = rewrite_bullet(match.group(2))
        if rewritten != match.group(2).strip():
            changed += 1
            output.append(f"{match.group(1)}{rewritten}")
        else:
            output.append(line)
    return "\n".join(output) + ("\n" if source.endswith("\n") else ""), changed


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= 110:
        return text
    return text[:107] + "..."
