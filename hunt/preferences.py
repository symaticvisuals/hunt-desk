"""What the person wants from a job. One file, written into search.json and rubric.json on save."""

from __future__ import annotations

import json
import re
from pathlib import Path

from hunt.store import read_json, write_json

PREFERENCES = "profile/preferences.json"
SENIORITY = ("junior", "mid", "senior", "lead", "staff", "principal", "leadership")
WORK_MODES = ("onsite", "hybrid", "remote")
JOB_TYPES = ("fulltime", "parttime", "contract", "internship")
SIZES = ("any", "startup", "big")
ALIASES = {
    "bangalore": ("bengaluru",),
    "gurgaon": ("gurugram",),
    "delhi": ("new delhi", "delhi ncr"),
    "mumbai": ("bombay", "navi mumbai"),
}
_LEVEL_WORDS = {
    "intern": "junior",
    "junior": "junior",
    "jr": "junior",
    "associate": "junior",
    "senior": "senior",
    "sr": "senior",
    "lead": "lead",
    "staff": "staff",
    "principal": "principal",
    "head": "leadership",
    "director": "leadership",
    "vp": "leadership",
    "chief": "leadership",
    "cto": "leadership",
}
COUNTRY_ALIASES = {
    "united states": ("usa", "us", "u.s.", "u.s.a.", "united states of america"),
    "united kingdom": ("uk", "u.k.", "great britain", "england"),
}
_US_STATE = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b"
)
_US_CITIES = (
    "san francisco", "new york", "new york city", "seattle", "austin", "boston", "chicago", "los angeles", "denver",
    "atlanta", "miami", "san jose", "palo alto", "mountain view", "menlo park", "sunnyvale", "san diego", "portland",
    "philadelphia", "dallas", "houston", "pittsburgh", "salt lake city", "raleigh", "nashville", "minneapolis", "brooklyn",
)
_INDIA_PLACES = (
    "bangalore", "bengaluru", "mumbai", "bombay", "navi mumbai", "delhi", "new delhi", "ncr", "gurgaon", "gurugram",
    "noida", "greater noida", "faridabad", "ghaziabad", "pune", "hyderabad", "secunderabad", "chennai", "kolkata",
    "ahmedabad", "jaipur", "kochi", "cochin", "chandigarh", "mohali", "indore", "coimbatore", "thiruvananthapuram",
    "trivandrum", "nagpur", "lucknow", "bhubaneswar", "vadodara", "mysore", "mysuru", "goa", "visakhapatnam",
    "karnataka", "maharashtra", "haryana", "telangana", "tamil nadu", "uttar pradesh", "west bengal", "gujarat",
    "kerala", "rajasthan", "punjab", "madhya pradesh", "andhra pradesh", "odisha",
)
_INDIA_STATE = re.compile(r"(?:^|[,;(]\s*)(DL|KA|MH|HR|TG|TS|TN|UP|WB|GJ|KL|RJ|PB|MP|AP|OD)\b(?=\s*(?:,|;|\)|$))")
_ANYWHERE = {"remote", "anywhere", "global", "worldwide", "work", "from", "home", "wfh", "flexible", "hybrid", "fully", "100"}
_ANNUAL = {"yearly": 1, "year": 1, "annual": 1, "monthly": 12, "month": 12, "weekly": 52, "week": 52, "daily": 260, "day": 260, "hourly": 2080, "hour": 2080}

PREFERENCES_SYSTEM = (
    "You read one resume and propose job-search preferences for the person it describes. "
    "Propose only what the resume supports. "
    "roles: job titles this person would be a natural hire for, best fit first, at most eight. "
    "seniority: the levels they would apply at. "
    "max_years: the years of experience the resume shows plus two, or null when it has no dates. "
    "work_modes: all three unless the resume states a preference. "
    "job_types: fulltime and contract unless the resume suggests otherwise; internship only for a student. "
    "exclude_titles: title words for roles they would never take, such as intern. "
    "cities, countries, remote_only_countries, pay_floor_amount, and pay_currency: fill these only when the resume states them. Otherwise leave them empty. "
    "company_size: any, unless the resume shows a clear preference."
)

_LIST = {"type": "array", "items": {"type": "string"}}
PREFERENCES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "roles",
        "seniority",
        "max_years",
        "cities",
        "countries",
        "remote_only_countries",
        "work_modes",
        "job_types",
        "exclude_titles",
        "pay_floor_amount",
        "pay_currency",
        "company_size",
    ],
    "properties": {
        "roles": _LIST,
        "seniority": {"type": "array", "items": {"type": "string", "enum": list(SENIORITY)}},
        "max_years": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "cities": _LIST,
        "countries": _LIST,
        "remote_only_countries": _LIST,
        "work_modes": {"type": "array", "items": {"type": "string", "enum": list(WORK_MODES)}},
        "job_types": {"type": "array", "items": {"type": "string", "enum": list(JOB_TYPES)}},
        "exclude_titles": _LIST,
        "pay_floor_amount": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "pay_currency": {"type": "string"},
        "company_size": {"type": "string", "enum": list(SIZES)},
    },
}


def defaults() -> dict:
    return {
        "confirmed": False,
        "roles": [],
        "seniority": ["junior", "mid", "senior", "lead"],
        "max_years": None,
        "cities": [],
        "countries": [],
        "remote_only_countries": [],
        "work_modes": list(WORK_MODES),
        "pay_floor": {"amount": None, "currency": "", "period": "year"},
        "company_size": "any",
        "job_types": [],
        "exclude_titles": ["intern", "internship"],
    }


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _choices(value, allowed) -> list[str]:
    return [item.lower() for item in _strings(value) if item.lower() in allowed]


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def normalize(raw: dict) -> dict:
    prefs = defaults()
    raw = raw if isinstance(raw, dict) else {}
    prefs["confirmed"] = raw.get("confirmed") is True
    for name in ("roles", "cities", "countries", "remote_only_countries", "exclude_titles"):
        if name in raw:
            prefs[name] = _strings(raw.get(name))
    if "seniority" in raw:
        prefs["seniority"] = _choices(raw.get("seniority"), SENIORITY)
    if "work_modes" in raw:
        prefs["work_modes"] = _choices(raw.get("work_modes"), WORK_MODES)
    if "job_types" in raw:
        prefs["job_types"] = _choices(raw.get("job_types"), JOB_TYPES)
    years = _number(raw.get("max_years"))
    prefs["max_years"] = int(years) if years is not None else None
    size = str(raw.get("company_size") or "any").lower()
    prefs["company_size"] = size if size in SIZES else "any"
    floor = raw.get("pay_floor") if isinstance(raw.get("pay_floor"), dict) else {}
    period = str(floor.get("period") or "year").lower()
    prefs["pay_floor"] = {
        "amount": _number(floor.get("amount")),
        "currency": str(floor.get("currency") or "").strip().upper(),
        "period": period if period in {"year", "month"} else "year",
    }
    return prefs


def load(root: Path) -> dict | None:
    path = root / PREFERENCES
    if not path.is_file():
        return None
    try:
        return normalize(read_json(path, {}))
    except json.JSONDecodeError:
        return None


def is_confirmed(root: Path) -> bool:
    prefs = load(root)
    return bool(prefs and prefs["confirmed"])


def migrate(root: Path) -> dict:
    """Build an unconfirmed preferences file from today's settings, once."""
    existing = load(root)
    if existing is not None:
        return existing
    search = read_json(root / "profile/search.json", {})
    rubric = read_json(root / "profile/rubric.json", {})
    jobspy = ((search.get("source_options") or {}).get("jobspy") or {}) if isinstance(search, dict) else {}
    location = (search.get("location") or {}) if isinstance(search, dict) else {}
    comp = (rubric.get("comp") or {}) if isinstance(rubric, dict) else {}
    raw = {
        "confirmed": False,
        "roles": list(search.get("keywords") or []),
        "cities": jobspy.get("locations") or location.get("cities") or [],
        "countries": location.get("countries") or [],
        "max_years": rubric.get("max_years_without_cap"),
        "pay_floor": {"amount": comp.get("min"), "currency": comp.get("currency") or "", "period": "year"},
        "exclude_titles": search.get("exclude_title") or [],
    }
    if location.get("mode") == "remote_only":
        raw["work_modes"] = ["remote"]
    prefs = normalize(raw)
    write_json(root / PREFERENCES, prefs)
    return prefs


def save(root: Path, prefs: dict) -> dict:
    prefs = normalize(dict(prefs, confirmed=True))
    write_json(root / PREFERENCES, prefs)
    compile_preferences(root, prefs)
    return prefs


def compile_preferences(root: Path, prefs: dict) -> None:
    """Write the fields preferences own. Sources, the watchlist, and the rubric's math stay as they are."""
    prefs = normalize(prefs)
    search_path = root / "profile/search.json"
    search = read_json(search_path, {})
    search = search if isinstance(search, dict) else {}
    search["keywords"] = list(prefs["roles"])
    search["exclude_title"] = list(prefs["exclude_titles"])
    search["location"] = {
        "mode": "places",
        "cities": list(prefs["cities"]),
        "countries": list(prefs["countries"]),
        "remote_only_countries": list(prefs["remote_only_countries"]),
        "work_modes": list(prefs["work_modes"]),
    }
    options = search.setdefault("source_options", {})
    jobspy = options.setdefault("jobspy", {})
    home = [name for name in prefs["countries"] if _jobspy_country(name)]
    remote_only = [name for name in prefs["remote_only_countries"] if _jobspy_country(name)]
    # No cities means the whole country: search it by name.
    places = list(prefs["cities"]) or home
    jobspy["locations"] = [] if prefs["work_modes"] == ["remote"] else places
    country = next(iter(home + remote_only), None)
    if country:
        jobspy["country_indeed"] = country
    remote = "remote" in prefs["work_modes"]
    jobspy["remote_countries"] = ((home if prefs["cities"] else []) + remote_only) if remote else []
    search.setdefault("sources", [])
    write_json(search_path, search)

    rubric_path = root / "profile/rubric.json"
    rubric = read_json(rubric_path, {})
    rubric = rubric if isinstance(rubric, dict) else {}
    if prefs["max_years"] is not None:
        rubric["max_years_without_cap"] = prefs["max_years"]
    comp = dict(rubric.get("comp") or {})
    comp.setdefault("max", None)
    floor = prefs["pay_floor"]
    amount = floor["amount"]
    if amount is not None and floor["period"] == "month":
        amount = amount * 12
    comp["min"] = amount
    comp["currency"] = floor["currency"] or None
    rubric["comp"] = comp
    write_json(rubric_path, rubric)


def _jobspy_country(name: str) -> bool:
    """Indeed and Glassdoor pick their country site from this. Unknown names would fail every search."""
    try:
        from jobspy.model import Country

        Country.from_string(name)
    except Exception:
        return False
    return True


def propose(root: Path, *, client=None) -> tuple[dict, str]:
    """Preferences proposed from the master resume, on top of the current file. Falls back to local reading."""
    from hunt.inbox import read_resume

    current = load(root) or migrate(root)
    resume = read_resume(root)
    try:
        from hunt.rewrite_api import _Runner, load_steps, make_client
        from hunt.sources import load_env

        load_env(root / ".env")
        step = load_steps(root)["preferences"]
        runner = _Runner(client if client is not None else make_client())
        reply = runner.ask("preferences", step, PREFERENCES_SYSTEM, resume, PREFERENCES_SCHEMA)
    except Exception as exc:
        return _local(current, resume), f"Filled from your resume without Claude ({exc}). Check every field."
    merged = dict(current)
    for name in ("roles", "seniority", "cities", "countries", "remote_only_countries", "work_modes", "job_types", "exclude_titles"):
        value = _strings(reply.get(name))
        if value:
            merged[name] = value
    if reply.get("max_years") is not None:
        merged["max_years"] = reply.get("max_years")
    if reply.get("pay_floor_amount") is not None:
        merged["pay_floor"] = {
            "amount": reply.get("pay_floor_amount"),
            "currency": reply.get("pay_currency") or current["pay_floor"]["currency"],
            "period": "year",
        }
    if reply.get("company_size") in SIZES:
        merged["company_size"] = reply["company_size"]
    merged["confirmed"] = False
    return normalize(merged), "Claude proposed these from your resume. Fields it left empty keep your current values."


def _local(current: dict, resume: str) -> dict:
    from hunt.onboard import infer

    found = infer(resume)
    merged = dict(current)
    if found.keywords:
        merged["roles"] = found.keywords
    if found.years_cap is not None:
        merged["max_years"] = found.years_cap
    merged["confirmed"] = False
    return normalize(merged)


def _mentions(text: str, term: str) -> bool:
    term = term.strip().lower()
    return bool(term) and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def city_terms(city: str) -> tuple[str, ...]:
    name = city.strip().lower()
    for key, aliases in ALIASES.items():
        if name == key or name in aliases:
            return (key, *aliases)
    return (name,)


def country_terms(country: str) -> tuple[str, ...]:
    name = country.strip().lower()
    for key, aliases in COUNTRY_ALIASES.items():
        if name == key or name in aliases:
            return (key, *aliases)
    return (name,)


def in_country(location: str, country: str) -> bool:
    text = (location or "").lower()
    terms = country_terms(country)
    if any(_mentions(text, term) for term in terms):
        return True
    if terms[0] == "united states":
        return bool(_US_STATE.search(location or "")) or any(_mentions(text, city) for city in _US_CITIES)
    if terms[0] == "india":
        return bool(_INDIA_STATE.search(location or "")) or any(_mentions(text, place) for place in _INDIA_PLACES)
    return False


def work_mode_of(location: str, work_mode: str = "") -> str:
    text = f"{location or ''} {work_mode or ''}".lower()
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    return "onsite"


def places_ok(location: str, work_mode: str, rule: dict) -> bool:
    modes = set(rule.get("work_modes") or WORK_MODES)
    cities = [city for city in rule.get("cities") or [] if str(city).strip()]
    countries = [country for country in rule.get("countries") or [] if str(country).strip()]
    remote_only = [country for country in rule.get("remote_only_countries") or [] if str(country).strip()]
    text = (location or "").lower()
    mode = work_mode_of(location, work_mode)
    if mode not in modes:
        return False
    in_city = any(_mentions(text, term) for city in cities for term in city_terms(city))
    in_target = any(in_country(location, country) for country in countries)
    if mode == "remote":
        rest = [word for word in re.findall(r"[a-z0-9]+", text) if word not in _ANYWHERE]
        in_remote_only = any(in_country(location, country) for country in remote_only)
        return not rest or not (cities or countries or remote_only) or in_city or in_target or in_remote_only
    if cities:
        return in_city
    if countries:
        return in_target
    return not remote_only


def seniority_of(title: str) -> set[str]:
    words = re.findall(r"[a-z]+", (title or "").lower())
    levels = {_LEVEL_WORDS[word] for word in words if word in _LEVEL_WORDS}
    if "vice" in words and "president" in words:
        levels.add("leadership")
    return levels or {"mid"}


def seniority_ok(title: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    return bool(seniority_of(title) & set(allowed))


def job_type_ok(job: dict, prefs: dict) -> bool:
    wanted = set(prefs.get("job_types") or [])
    stated = {part.strip().lower() for part in str(job.get("job_type") or "").split(",") if part.strip()}
    return not wanted or not stated or bool(stated & wanted)


def pay_ok(job: dict, prefs: dict) -> bool:
    floor = prefs.get("pay_floor") or {}
    amount = floor.get("amount")
    if amount is None:
        return True
    top = job.get("pay_max") if job.get("pay_max") is not None else job.get("pay_min")
    currency = str(job.get("pay_currency") or "").upper()
    if top is None or not currency or currency != str(floor.get("currency") or "").upper():
        return True
    yearly = float(top) * _ANNUAL.get(str(job.get("pay_interval") or "yearly").lower(), 1)
    wanted = float(amount) * (12 if floor.get("period") == "month" else 1)
    return yearly >= wanted


def size_ok(job: dict, prefs: dict) -> bool:
    size = prefs.get("company_size") or "any"
    if size == "any" or not job.get("size_known"):
        return True
    big = bool(job.get("big"))
    return not big if size == "startup" else big
