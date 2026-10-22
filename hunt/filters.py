"""The desk's filter bar. Every token must match. A leading - inverts one."""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

HINT = "words, score>80, score:none, src:workday, why:stretch, days:7, maybe, -word"

_SCORE = re.compile(r"^score(>=|<=|>|<|:)(.*)$")
_COMPARE = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, ":": operator.ge}


@dataclass
class Row:
    text: str
    source: str
    reason: str
    score: float | None
    born: date | None
    maybe: Callable[[], bool]


Test = Callable[[Row], bool]


@dataclass
class Query:
    tests: list[Test] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.tests)

    def matches(self, row: Row) -> bool:
        return all(test(row) for test in self.tests)


def parse(text: str, today: date) -> Query:
    query = Query()
    for token in (text or "").lower().split():
        negate = token.startswith("-") and len(token) > 1
        test, problem = _token(token[1:] if negate else token, today)
        if test is None:
            query.problems.append(problem)
        elif negate:
            query.tests.append(lambda row, test=test: not test(row))
        else:
            query.tests.append(test)
    return query


def _token(body: str, today: date) -> tuple[Test | None, str]:
    found = _SCORE.match(body)
    if found:
        sign, value = found.groups()
        if sign == ":" and value == "none":
            return (lambda row: row.score is None), ""
        try:
            limit = float(value)
        except ValueError:
            return None, "score needs a number, like score>80"
        compare = _COMPARE[sign]
        return (lambda row: row.score is not None and compare(row.score, limit)), ""
    name, sep, value = body.partition(":")
    if sep and name in ("src", "source"):
        return (lambda row: value in row.source), ""
    if sep and name in ("why", "reason"):
        return (lambda row: value in row.reason), ""
    if sep and name == "days":
        if not value.isdigit():
            return None, "days needs a number, like days:7"
        since = today - timedelta(days=int(value))
        return (lambda row: row.born is not None and row.born >= since), ""
    if body == "maybe":
        return (lambda row: row.maybe()), ""
    return (lambda row: body in row.text), ""


def row_for(job: dict, reason: str, maybe: Callable[[], bool]) -> Row:
    """reason is the hidden reason or the applied status. maybe runs only when the query asks for it."""
    source = " ".join(str(job.get(name) or "") for name in ("source", "platform")).lower()
    title = job.get("title") or job.get("role") or ""
    text = " ".join(str(part or "") for part in (job.get("company"), title, job.get("location"), source, reason)).lower()
    return Row(text=text, source=source, reason=(reason or "").lower(), score=_score(job), born=_born(job), maybe=maybe)


def _score(job: dict) -> float | None:
    try:
        return float(str(job.get("final_score") or "").strip())
    except ValueError:
        return None


def _born(job: dict) -> date | None:
    """The posting date, or the day hunt first saw an undated role."""
    stamp = str(job.get("posted_at") or job.get("first_seen") or "")[:10]
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None
