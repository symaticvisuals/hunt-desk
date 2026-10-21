"""Local screens before Claude. Years at a big organisation, a dead posting, or no apply link never reach a score."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_CLOSED = re.compile(
    r"(?i)(no longer accepting|position has been filled|this (?:job|role|position) (?:is|has been) closed|"
    r"posting is closed|no longer available|applications are closed)"
)
_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DAYS = re.compile(r"(?i)(\d+)\s*\+?\s*days?")

# Below this classifier fit a role is a stretch, not a resume to tailor. rubric.json plausible_at overrides it.
PLAUSIBLE_AT = 2.5


def posted_on(value, today: date | None = None) -> str:
    """Normalize a board timestamp to YYYY-MM-DD. Empty when the board gave no date."""
    today = today or datetime.now(timezone.utc).date()
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    found = _ISO.search(text)
    if found:
        return found.group(1)
    lowered = text.lower()
    if "today" in lowered:
        return today.isoformat()
    if "yesterday" in lowered:
        return (today - timedelta(days=1)).isoformat()
    days = _DAYS.search(lowered)
    if days:
        return (today - timedelta(days=int(days.group(1)))).isoformat()
    return ""


def newest_first(job) -> tuple[str, float]:
    """Sort key, reversed. An undated role counts from the day hunt first saw it, and the later find wins a tie."""
    seen = _text(job, "first_seen")
    try:
        found = datetime.fromisoformat(seen).timestamp() if seen else 0.0
    except ValueError:
        found = 0.0
    return _text(job, "posted_at") or seen[:10], found


def looks_closed(text: str) -> bool:
    return bool(_CLOSED.search(text or ""))


def years_blocks(job, max_years) -> bool:
    """A big organisation's explicit years ask above the cap removes the role."""
    from hunt.trim import years_stated

    if not _flag(job, "big"):
        return False
    try:
        cap = int(max_years)
    except (TypeError, ValueError):
        return False
    stated = years_stated(_text(job, "description"))
    return stated is not None and stated > cap


STALE_DAYS = 14
# A board that still listed the role this recently means it is open, however old the posting is.
SEEN_DAYS = 3


def _default_worth_rules() -> dict:
    import copy

    from hunt.worth import DEFAULT_RULES

    return copy.deepcopy(DEFAULT_RULES)


@dataclass
class Context:
    prefs: dict
    search: dict
    max_years: int | None
    screenout: float
    dismissed: dict
    today: date
    plausible_at: float = PLAUSIBLE_AT
    worth_rules: dict = field(default_factory=_default_worth_rules)
    companies: dict = field(default_factory=dict)
    overrides: set = field(default_factory=set)
    worth_warning: str = ""


def context(root: Path, today: date | None = None) -> Context:
    from hunt.preferences import load, normalize
    from hunt.search import load_search
    from hunt.store import load_dismissed
    from hunt.worth import load_companies, load_overrides, load_rules

    prefs = load(root)
    if prefs is None:
        prefs = normalize({"seniority": [], "exclude_titles": []})
    search = load_search(root) if (root / "profile/search.json").exists() else {"keywords": [], "exclude_title": [], "location": {"mode": "any"}}
    rules, warning = load_rules(root)
    rubric = {}
    path = root / "profile" / "rubric.json"
    if path.is_file():
        try:
            rubric = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rubric = {}
    return Context(
        prefs=prefs,
        search=search,
        max_years=max_years(root),
        screenout=float(rubric.get("screenout_cap") or 75),
        dismissed=load_dismissed(root),
        today=today or datetime.now(timezone.utc).date(),
        plausible_at=_number(rubric.get("plausible_at"), PLAUSIBLE_AT),
        worth_rules=rules,
        companies=load_companies(root),
        overrides=load_overrides(root),
        worth_warning=warning,
    )


def hidden_reason(job, ctx: Context, *, for_claude: bool = False) -> str:
    """Empty for a role on the main list. Otherwise the first rule it fails."""
    from hunt.preferences import job_type_ok, pay_ok, seniority_ok, size_ok
    from hunt.search import location_ok, title_matches
    from hunt.store import is_dismissed

    title = _text(job, "title") or _text(job, "role")
    if is_dismissed(job, ctx.dismissed):
        return "dismissed"
    if str(_get(job, "applied") or "").lower() == "yes":
        return "applied"
    if _stale(job, ctx.today):
        return "stale"
    if _too_old(job, ctx):
        return "posted too long ago"
    if not title_matches(title, ctx.search):
        return "not a target role"
    if not seniority_ok(title, ctx.prefs.get("seniority") or []):
        return "seniority"
    if not location_ok(_text(job, "location"), ctx.search, _text(job, "work_mode")):
        return "location"
    if not job_type_ok(job, ctx.prefs):
        return "job type"
    if not pay_ok(job, ctx.prefs):
        return "below pay floor"
    if not size_ok(job, ctx.prefs):
        return "company size"
    if not (_text(job, "apply_url") or _text(job, "url")) or _text(job, "live") == "closed" or looks_closed(_text(job, "description")):
        return "closed"
    if years_blocks(job, ctx.max_years):
        return "years"
    worth = worth_of(job, ctx)
    if worth.verdict == "skip":
        return worth.reason
    score = _score(job)
    if score is not None:
        if not for_claude and score <= ctx.screenout:
            return "scored low"
        return ""
    keep = _get(job, "jev_keep")
    if keep is None:
        keep = _get(job, "keep")
    if keep is None:
        return "not classified yet"
    if keep is not True:
        return "not a match"
    if _fit(job) < ctx.plausible_at:
        return "stretch"
    return ""


# Reasons that hide a role before its text matters. Fetching a description for these is wasted.
CHEAP_REASONS = ("dismissed", "applied", "stale", "posted too long ago", "not a target role", "seniority", "location", "job type", "below pay floor", "company size", "closed")


def worth_of(job, ctx: Context):
    from hunt.worth import assess

    row = job if isinstance(job, dict) else job.as_dict()
    return assess(row, ctx.worth_rules, ctx.companies, ctx.overrides, ctx.search)


def needs_text(job, ctx: Context) -> bool:
    """A role whose text the classifier or the worth rules would read, and that has not been fetched twice in vain."""
    if _text(job, "description").strip():
        return False
    try:
        tries = int(_get(job, "description_tries") or 0)
        limit = int(ctx.worth_rules.get("empty_description_tries") or 0)
    except (TypeError, ValueError):
        tries, limit = 0, 0
    if tries >= limit or hidden_reason(job, ctx) in CHEAP_REASONS:
        return False
    return worth_of(job, ctx).verdict != "skip"


def matters(job, ctx: Context) -> bool:
    return not hidden_reason(job, ctx)


def _score(job) -> float | None:
    try:
        return float(str(_get(job, "final_score") or "").strip())
    except ValueError:
        return None


def _fit(job) -> float:
    fit = _get(job, "jev_fit")
    if fit is None:
        fit = _get(job, "fit")
    try:
        return float(fit)
    except (TypeError, ValueError):
        return -1.0


def _day(stamp: str) -> date | None:
    found = _ISO.search(stamp or "")
    return date.fromisoformat(found.group(1)) if found else None


def _stale(job, today: date) -> bool:
    born = _day(_text(job, "posted_at") or _text(job, "first_seen"))
    if born is None or born >= today - timedelta(days=STALE_DAYS):
        return False
    seen = _day(_text(job, "last_seen"))
    return seen is None or seen < today - timedelta(days=SEEN_DAYS)


def _too_old(job, ctx: Context) -> bool:
    """worth.json max_posted_days hides a role by posting date alone, unlike stale. 0 turns it off."""
    try:
        limit = int(ctx.worth_rules.get("max_posted_days") or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return False
    born = _day(_text(job, "posted_at") or _text(job, "first_seen"))
    return born is not None and born < ctx.today - timedelta(days=limit)


def worth_custom_resume(job, max_years, ctx: Context | None = None) -> tuple[bool, str]:
    """Claude tailors a resume only for a live, applicable, plausible match."""
    if ctx is not None:
        reason = hidden_reason(job, ctx, for_claude=True)
        return not reason, reason
    if not (_text(job, "apply_url") or _text(job, "url")):
        return False, "no apply link"
    if str(_text(job, "live") or "") == "closed" or looks_closed(_text(job, "description")):
        return False, "closed"
    if years_blocks(job, max_years):
        return False, "years"
    if _flag(job, "jev_keep") is not True and _get(job, "keep") is not True:
        return False, "not kept"
    fit = _get(job, "jev_fit")
    if fit is None:
        fit = _get(job, "fit")
    try:
        fit_value = float(fit)
    except (TypeError, ValueError):
        return False, "no fit"
    if fit_value < PLAUSIBLE_AT:
        return False, "stretch"
    return True, ""


def prepare(jobs: list, max_years) -> tuple[list, list[str]]:
    """Drop closed text, missing links, and big-org years screens. Newest posting first."""
    kept = []
    notes = {"years": 0, "closed": 0, "link": 0}
    for job in jobs:
        if not (_text(job, "apply_url") or _text(job, "url")):
            notes["link"] += 1
            continue
        if looks_closed(_text(job, "description")):
            notes["closed"] += 1
            continue
        if years_blocks(job, max_years):
            notes["years"] += 1
            continue
        if not _text(job, "apply_url"):
            _set_apply(job)
        kept.append(job)
    kept.sort(key=newest_first, reverse=True)
    lines = []
    if notes["years"]:
        lines.append(f"big-org years screen dropped {notes['years']}")
    if notes["closed"]:
        lines.append(f"closed postings dropped {notes['closed']}")
    if notes["link"]:
        lines.append(f"listings without an apply link dropped {notes['link']}")
    return kept, lines


def max_years(root: Path) -> int | None:
    path = root / "profile" / "rubric.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("max_years_without_cap")
        return int(value) if value is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _number(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _get(job, name: str):
    if isinstance(job, dict):
        return job.get(name)
    return getattr(job, name, None)


def _text(job, name: str) -> str:
    value = _get(job, name)
    return "" if value is None else str(value)


def _flag(job, name: str) -> bool:
    value = _get(job, name)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _set_apply(job) -> None:
    url = _text(job, "url")
    if isinstance(job, dict):
        job["apply_url"] = url
    else:
        job.apply_url = url
