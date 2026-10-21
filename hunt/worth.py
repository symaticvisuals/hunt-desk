"""Is a posting worth an application? A second axis beside fit, with one reason and an undo path.

Rules live in profile/worth.json. A class the person set for a company lives in data/companies.json,
and a role the person kept anyway lives in data/worth-overrides.json.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hunt.store import data_lock, read_json, write_json

RULES = "profile/worth.json"
COMPANIES = "data/companies.json"
OVERRIDES = "data/worth-overrides.json"
CLASSES = ("product", "services", "staffing", "training")
_WORD_CLASSES = ("staffing", "training", "services")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|[\n•·]+")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_DOTTED = re.compile(r"\bu\.s\.(?:a\.)?")

DEFAULT_RULES = {
    "company_words": {
        "staffing": ["staffing", "talent", "recruit", "placement", "manpower", "hr solutions"],
        "training": ["academy", "institute", "training", "coaching", "education", "edtech"],
        "services": ["consulting", "consultancy", "outsourcing", "it services"],
    },
    "class_verdicts": {"staffing": "skip", "training": "skip", "services": "maybe", "product": "apply", "unknown": "apply"},
    "remote_only": {
        "open_when": ["worldwide", "anywhere in the world", "work from anywhere", "global remote"],
        "closed_when": [
            "authorized to work in the us",
            "authorized to work in the united states",
            "us work authorization",
            "us-based",
            "based in the us",
            "based in the united states",
            "w-2",
            "w2 only",
            "1099",
            "must reside in",
            "must be located in the us",
            "must live in the us",
            "us citizen",
            "green card",
            "overlap with est",
            "overlap with pst",
            "est business hours",
            "pst business hours",
        ],
    },
    "empty_description_tries": 2,
    "old_when_found_days": 14,
    "max_posted_days": 0,
}


@dataclass(frozen=True)
class Worth:
    verdict: str
    reason: str
    evidence: str = ""


def load_rules(root: Path) -> tuple[dict, str]:
    """The person's rules over the defaults. A file that does not parse falls back to the defaults, with a warning."""
    try:
        custom = read_json(root / RULES, {})
    except json.JSONDecodeError:
        return copy.deepcopy(DEFAULT_RULES), f"{RULES} is not valid JSON; using defaults"
    return _merge(DEFAULT_RULES, custom if isinstance(custom, dict) else {}), ""


def set_max_posted_days(root: Path, days: int) -> None:
    """Step 2's age field. Only this rule is written; the rest keep falling back to the defaults."""
    with data_lock(root):
        try:
            custom = read_json(root / RULES, {})
        except json.JSONDecodeError:
            custom = {}
        custom = custom if isinstance(custom, dict) else {}
        custom["max_posted_days"] = max(0, int(days))
        write_json(root / RULES, custom)


def load_companies(root: Path) -> dict[str, dict]:
    try:
        payload = read_json(root / COMPANIES, {})
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def set_company_class(root: Path, company: str, klass: str) -> None:
    from hunt.evaluated import normalize

    if klass not in CLASSES:
        raise ValueError(f"A company class is one of {', '.join(CLASSES)}, not {klass}.")
    with data_lock(root):
        companies = load_companies(root)
        companies[normalize(company)] = {"name": company, "class": klass}
        write_json(root / COMPANIES, companies)


def load_overrides(root: Path) -> set[str]:
    try:
        payload = read_json(root / OVERRIDES, [])
    except json.JSONDecodeError:
        return set()
    return {str(url) for url in payload if url} if isinstance(payload, list) else set()


def keep_anyway(root: Path, job: dict) -> None:
    urls = [url for url in [job.get("url") or ""] + list(job.get("also_at") or []) if url]
    with data_lock(root):
        kept = sorted(load_overrides(root) | set(urls))
        write_json(root / OVERRIDES, kept)


def company_class(company: str, rules: dict, companies: dict) -> str:
    from hunt.evaluated import normalize

    chosen = (companies.get(normalize(company)) or {}).get("class")
    if chosen in CLASSES:
        return chosen
    name = (company or "").lower()
    words = rules.get("company_words") or {}
    for klass in _WORD_CLASSES:
        if any(re.search(rf"\b{re.escape(str(word).lower())}", name) for word in words.get(klass) or [] if str(word).strip()):
            return klass
    return "unknown"


def remote_check(text: str, rules: dict, countries: list[str] | tuple[str, ...] = ()) -> Worth:
    """For a remote role in a remote-only country: is it open to someone in one of the person's countries?"""
    from hunt.preferences import country_terms

    remote = rules.get("remote_only") or {}
    opened = list(remote.get("open_when") or []) + [term for country in countries for term in country_terms(country)]
    sentences = [sentence.strip() for sentence in _SENTENCE.split(text or "") if sentence.strip()]
    for phrases, verdict, reason in ((opened, "apply", ""), (remote.get("closed_when"), "skip", "remote US: US only")):
        for sentence in sentences:
            if any(_says(sentence, phrase) for phrase in phrases or []):
                return Worth(verdict, reason, sentence)
    return Worth("maybe", "remote US: unclear")


def is_remote_only(location: str, work_mode: str, rule: dict) -> bool:
    from hunt.preferences import in_country, work_mode_of

    if work_mode_of(location, work_mode) != "remote":
        return False
    if any(in_country(location, country) for country in rule.get("countries") or []):
        return False
    return any(in_country(location, country) for country in rule.get("remote_only_countries") or [])


def assess(job: dict, rules: dict, companies: dict, overrides: set[str], search: dict) -> Worth:
    urls = [url for url in [job.get("url") or ""] + list(job.get("also_at") or []) if url]
    if any(url in overrides for url in urls):
        return Worth("apply", "kept by you")
    klass = company_class(job.get("company") or "", rules, companies)
    verdict = (rules.get("class_verdicts") or {}).get(klass, "apply")
    if verdict == "skip":
        return Worth("skip", klass)
    if not (job.get("description") or "").strip() and _int(job.get("description_tries")) >= _int(rules.get("empty_description_tries")):
        return Worth("skip", "no description")
    rule = (search or {}).get("location") or {}
    if is_remote_only(job.get("location") or "", job.get("work_mode") or "", rule):
        remote = remote_check(job.get("description") or "", rules, rule.get("countries") or [])
        if remote.verdict != "apply":
            return remote
    if verdict == "maybe":
        return Worth("maybe", klass)
    if _age_when_found(job) > _int(rules.get("old_when_found_days")):
        return Worth("maybe", "old when found")
    return Worth("apply", "")


def _says(sentence: str, phrase: str) -> bool:
    """A phrase or its plural, with U.S. and U.S.A. read as us and usa."""
    text = str(phrase or "").strip().lower()
    plain = _DOTTED.sub(lambda match: match.group().replace(".", ""), sentence.lower())
    return bool(text) and re.search(rf"(?<![a-z0-9]){re.escape(text)}s?(?![a-z0-9])", plain) is not None


def _age_when_found(job: dict) -> int:
    posted = _ISO.search(str(job.get("posted_at") or ""))
    found = _ISO.search(str(job.get("first_seen") or ""))
    if not posted or not found:
        return 0
    return (date.fromisoformat(found.group()) - date.fromisoformat(posted.group())).days


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _merge(base: dict, extra: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged
