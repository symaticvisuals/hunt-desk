"""How alike the tailored resumes are. A reworded or reordered copy of a line still counts as the same line."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

# Two lines make the same claim when this share of the shorter line's content words appears in the other.
LINE_MATCH = 0.6
# At or above this average, the resumes read as one resume reshuffled.
ALIKE = 0.8
MIN_WORDS = 4
_BULLET = re.compile(r"^\s*[-*•]\s+")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the and for with that this from into across over their them they was were are has have had its our your "
    "which while then than when what each every also more most such only both very".split()
)


@dataclass(frozen=True)
class Pair:
    first: str
    second: str
    shared: float


@dataclass
class Sameness:
    resumes: list[str] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return sum(pair.shared for pair in self.pairs) / len(self.pairs) if self.pairs else 0.0


def _words(line: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(line.lower()) if len(word) > 2 and word not in _STOP)


def lines(text: str) -> list[frozenset[str]]:
    """The bullet lines as sets of content words. A resume without bullets falls back to every line."""
    raw = (text or "").splitlines()
    picked = [line for line in raw if _BULLET.match(line)] or raw
    return [words for words in map(_words, picked) if len(words) >= MIN_WORDS]


def _same(first: frozenset[str], second: frozenset[str]) -> bool:
    return len(first & second) / min(len(first), len(second)) >= LINE_MATCH


def shared_lines(first: list[frozenset[str]], second: list[frozenset[str]]) -> float:
    """Share of both resumes' lines that have a match in the other one."""
    if not first or not second:
        return 0.0
    hits = sum(any(_same(line, other) for other in second) for line in first)
    hits += sum(any(_same(line, other) for other in first) for line in second)
    return hits / (len(first) + len(second))


def measure(root: Path) -> Sameness:
    folder = root / "resumes"
    found = {
        str(path.parent.relative_to(folder)): lines(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*/*/resume.md"))
    }
    pairs = [Pair(first, second, shared_lines(found[first], found[second])) for first, second in itertools.combinations(found, 2)]
    pairs.sort(key=lambda pair: pair.shared, reverse=True)
    return Sameness(list(found), pairs)


def report(found: Sameness, shown: int = 3) -> str:
    if len(found.resumes) < 2:
        return "Fewer than two tailored resumes in resumes/. Nothing to compare."
    lowest, highest = found.pairs[-1].shared, found.pairs[0].shared
    out = [
        f"{len(found.resumes)} tailored resumes in resumes/.",
        f"On average, any two share {_percent(found.mean)} of their lines (lowest {_percent(lowest)}, highest {_percent(highest)}).",
    ]
    if found.mean >= ALIKE:
        out.append("They mostly repeat the same lines. Tailoring is reordering them more than choosing between them.")
    out += ["", "Most alike:"] + [_row(pair) for pair in found.pairs[:shown]]
    rest = found.pairs[max(shown, len(found.pairs) - shown):]
    if rest:
        out += ["", "Least alike:"] + [_row(pair) for pair in rest]
    return "\n".join(out)


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _row(pair: Pair) -> str:
    return f"- {_percent(pair.shared)}  {pair.first} and {pair.second}"
