"""Does a job title match a target role? The role's key words in any order, with common title variants folded together."""

from __future__ import annotations

import re

_SPLIT = re.compile(r"[^a-z0-9+#.]+")
# One title word that boards write several ways.
_EXPAND = {
    "fullstack": ["full", "stack"],
    "frontend": ["front", "end"],
    "backend": ["back", "end"],
    "swe": ["software", "engineer"],
    "sde": ["software", "engineer"],
    "fde": ["forward", "deployed", "engineer"],
    "ml": ["machine", "learning"],
    "genai": ["generative", "ai"],
    "gen": ["generative"],
    "nlp": ["natural", "language", "processing"],
}
_SAME = {
    "engineering": "engineer",
    "engg": "engineer",
    "eng": "engineer",
    "engr": "engineer",
    "developer": "engineer",
    "dev": "engineer",
    "programmer": "engineer",
    "llms": "llm",
}
# Words a target role often has that titles drop. Ignored when the role still has something specific left.
_FILLER = {"software", "application", "app"}


def _stem(word: str) -> str:
    word = word.strip(".")
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return _SAME.get(word, word)


def title_words(text: str) -> list[str]:
    words: list[str] = []
    for raw in _SPLIT.split((text or "").lower()):
        raw = raw.strip(".")
        if not raw:
            continue
        for part in _EXPAND.get(raw, [raw]):
            stemmed = _stem(part)
            if stemmed:
                words.append(stemmed)
    return words


def role_matches(title: str, role: str) -> bool:
    need = set(title_words(role))
    if not need:
        return False
    specific = need - _FILLER
    if specific - {"engineer"}:
        need = specific
    return need <= set(title_words(title))


def excluded(title: str, phrases: list[str]) -> bool:
    text = f" {' '.join(title_words(title))} "
    return any(f" {' '.join(title_words(phrase))} " in text for phrase in phrases if title_words(phrase))
