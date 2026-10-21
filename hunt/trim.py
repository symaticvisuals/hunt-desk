"""Cut job-ad boilerplate before a model sees it. This is local and costs nothing."""

from __future__ import annotations

import re

_ROLE_START = re.compile(
    r"(?i)\b(about the role|the role|what you(?:'|’)ll do|responsibilities|requirements|you(?:'|’)ll be|you may be a good fit)\b"
)
_DROP = re.compile(
    r"(?i)(equal opportunity|we encourage you to apply even if|imposter syndrome|"
    r"visa sponsorship|public benefit corporation|competitive compensation and benefits|"
    r"parental leave|generous vacation|only contact you from|your safety matters|"
    r"be cautious of emails|don't click any links|do not click any links|"
    r"we.?re an extremely collaborative|come work with us|how we.?re different|"
    r"guidance on candidates|using ai in our application|"
    r"not all strong candidates will meet every single qualification)"
)
_YEARS = re.compile(r"(?i)(?<![\d.])(\d{1,2})\s*\+?\s*(?:(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*|or\s+more\s+)?(?:years?|yrs?)\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|[\n•·]+")
_HISTORY = re.compile(r"(?i)\b(founded|history|ago|anniversary|in business|established|since \d{4})\b")
_CUE_BEFORE = re.compile(
    r"(?i)(?:(?:experience|exp)\s*(?:required|needed|level)?\s*[:\-–]?|"
    r"minimum|min\.?|at\s+least|atleast|requires?|required|need|must\s+have|you\s+have|you\s+bring|having|with)"
    r"\s*(?:of\s+)?(?:a\s+)?$"
)
_CUE_AFTER = re.compile(
    r"(?i)^\s*(?:(?:of\s+)?(?:[\w'’-]+\s+){0,3}?(?:experience|exp)\b|in\s|"
    r"(?:building|developing|designing|working|writing|shipping|leading|managing)\b|"
    r"(?:required|needed|minimum|or more)\b)"
)
MAX_YEARS = 15
# Shorter text is a requisition id ("JR2028008") or a stub ("Please refer the post and apply."), not the posting.
TEXT_AT = 80


def has_text(description: str) -> bool:
    return len((description or "").strip()) >= TEXT_AT


def years_required(text: str) -> tuple[int | None, str]:
    """The experience the posting asks for, and the sentence it came from.

    A number counts only inside an experience requirement. A range counts as its low end,
    a number above 15 is ignored, and a sentence about company history never counts.
    With several requirements the largest wins. The classifier cannot compare numbers, so this stays in code.
    """
    best: tuple[int | None, str] = (None, "")
    for sentence in _SENTENCE.split(text or ""):
        sentence = sentence.strip()
        if not sentence or _HISTORY.search(sentence):
            continue
        for match in _YEARS.finditer(sentence):
            low = int(match.group(1))
            if low > MAX_YEARS:
                continue
            before, after = sentence[: match.start()], sentence[match.end() :]
            if not (_CUE_BEFORE.search(before) or _CUE_AFTER.match(after)):
                continue
            if best[0] is None or low > best[0]:
                best = (low, sentence)
    return best


def years_stated(text: str) -> int | None:
    return years_required(text)[0]


def shorten(text: str, limit: int = 1800) -> str:
    """Keep the role section. Drop benefits, legal, and recruiting boilerplate."""
    raw = " ".join((text or "").split())
    if not raw:
        return ""
    start = _ROLE_START.search(raw)
    if start and start.start() > 180:
        raw = raw[start.start() :]
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", raw):
        if _DROP.search(sentence):
            continue
        kept.append(sentence)
    body = " ".join(kept).strip()
    if len(body) <= limit:
        return body
    clipped = body[:limit].rsplit(" ", 1)[0]
    return clipped + " …"


def removed_boilerplate(original: str, short: str) -> bool:
    return len(short) + 200 < len(" ".join((original or "").split()))
