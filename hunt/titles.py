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
    "pm": ["product", "manager"],
    "apm": ["associate", "product", "manager"],
    "gpm": ["group", "product", "manager"],
    "tpm": ["technical", "program", "manager"],
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
    "agentic": "agent",
    "mgr": "manager",
}
# Words a target role often has that titles drop. Ignored when the role still has something specific left.
_FILLER = {"software", "application", "app"}
# A title with one of these words is also an AI title. One way only: an LLM role still needs LLM in the title.
_IMPLIES_AI = {"llm", "agent"}


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _stem(word: str) -> str:
    word = _singular(word.strip("."))
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
    have = set(title_words(title))
    if have & _IMPLIES_AI:
        have.add("ai")
    return need <= have


def search_phrases(role: str) -> list[str]:
    """The role for a search engine that matches exact words only: its specific words, then the other spellings boards use."""
    raw = [word.strip(".") for word in _SPLIT.split((role or "").lower()) if word.strip(".")]
    core = [word for word in raw if _stem(word) != "engineer" and word not in _FILLER] or raw
    variants = [core, [_singular(word) for word in core]]
    for short, long in _EXPAND.items():
        words = [part for part in long if part != "engineer"]
        if len(words) < 2:
            continue
        if set(words) <= set(core):
            variants.append([word for word in core if word not in words] + [short])
        elif short in core:
            variants.append([part for word in core for part in (words if word == short else [word])])
    phrases: list[str] = []
    for words in variants:
        phrase = " ".join(words)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def excluded(title: str, phrases: list[str]) -> bool:
    text = f" {' '.join(title_words(title))} "
    return any(f" {' '.join(title_words(phrase))} " in text for phrase in phrases if title_words(phrase))
