"""Keep-or-skip decisions from local Clef-flash or Claude Haiku. Claude only scores roles this marks keep.

profile/models.json picks the classifier: "clef" (the default) or "haiku".
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path

from hunt.inbox import load_json, read_resume, save_json
from hunt.search import load_search
from hunt.trim import shorten, years_required, years_stated

CACHE = "data/clef-cache.json"
MODELS = "profile/models.json"
DEFAULT_URL = "http://127.0.0.1:11434/v1/systemone"
DEFAULT_MODEL = "clef-flash"
KEEP_AT = 0.65
DEFAULT_PARALLEL = 3
FIT_AT = 2
FIT_LEVELS = (
    "Unrelated to the keywords",
    "Same field, wrong role",
    "A stretch against the resume",
    "A plausible match",
    "A natural hire if the resume is true",
)
CLASSIFIERS = {"clef": "Clef-flash", "haiku": "Haiku"}
CLASSIFIER_LABELS = tuple(CLASSIFIERS.values())
HAIKU_SCHEMA = {
    "type": "object",
    "properties": {
        "worth_reading": {"type": "boolean"},
        "fit": {"type": "integer", "enum": list(range(len(FIT_LEVELS)))},
    },
    "required": ["worth_reading", "fit"],
    "additionalProperties": False,
}

CLASSIFY_SPENT = "Today's classify budget is spent. New roles wait for tomorrow."


class GateError(Exception):
    pass


def load_cache(root: Path) -> dict:
    payload = load_json(root / CACHE, {})
    return payload if isinstance(payload, dict) else {}


def save_cache(root: Path, cache: dict) -> None:
    save_json(root / CACHE, cache)


def classifier(root: Path) -> str:
    try:
        payload = load_json(root / MODELS, {})
    except json.JSONDecodeError as exc:
        raise GateError(f"profile/models.json is not valid JSON: {exc}") from exc
    chosen = payload.get("classifier") if isinstance(payload, dict) else None
    return str(chosen).strip().lower() if chosen else "clef"


def label(root: Path) -> str:
    try:
        return CLASSIFIERS.get(classifier(root), CLASSIFIERS["clef"])
    except GateError:
        return CLASSIFIERS["clef"]


def set_classifier(root: Path, name: str) -> None:
    if name not in CLASSIFIERS:
        raise GateError(f"The classifier must be clef or haiku, not {name}.")
    try:
        payload = load_json(root / MODELS, {})
    except json.JSONDecodeError as exc:
        raise GateError(f"profile/models.json is not valid JSON: {exc}") from exc
    payload = payload if isinstance(payload, dict) else {}
    payload["classifier"] = name
    payload.setdefault("steps", {})
    save_json(root / MODELS, payload)


def current(hit, job: dict) -> bool:
    """A decision made on the title alone is redone once the posting text arrives."""
    if not isinstance(hit, dict):
        return False
    return hit.get("described", True) or not (job.get("description") or "").strip()


def cached(cache: dict, job: dict):
    """The decision for this role under its URL or any board URL merged into it."""
    urls = [job.get("url") or ""] + list(job.get("also_at") or [])
    return next((cache[url] for url in urls if url in cache), None)


def attach(jobs: list[dict], cache: dict) -> None:
    for job in jobs:
        hit = cached(cache, job)
        if not current(hit, job):
            continue
        job["jev_keep"] = hit.get("keep")
        job["jev_worth"] = hit.get("worth")
        job["jev_fit"] = hit.get("fit")
        job["jev_fit_label"] = hit.get("fit_label")
        job["jev_years"] = hit.get("years_stated")
        job["jev_over_years"] = hit.get("over_years")
        job["jev_years_sentence"] = hit.get("years_sentence") or ""
        job["jev_by"] = hit.get("by") or "clef"


def build_state(root: Path, job: dict) -> str:
    search = load_search(root) if (root / "profile/search.json").exists() else {}
    rubric = load_json(root / "profile/rubric.json", {})
    keywords = ", ".join(search.get("keywords") or [])
    years = rubric.get("max_years_without_cap")
    stated = years_stated(job.get("description") or "")
    return "\n".join(
        [
            f"Title: {job.get('title') or ''}",
            f"Company: {job.get('company') or ''}",
            f"Location: {job.get('location') or ''}",
            f"Keywords this person wants: {keywords}",
            f"Years they can offer before a role is a stretch: {years if years is not None else 'not set'}",
            f"Years the posting states: {stated if stated is not None else 'none stated'}",
            "",
            "Resume excerpt:",
            shorten(read_resume(root), limit=700) or "(no resume yet)",
            "",
            "Posting, with benefits and legal boilerplate removed:",
            shorten(job.get("description") or "") or "(no description)",
        ]
    )


def questions_for(root: Path) -> dict:
    search = load_search(root) if (root / "profile/search.json").exists() else {}
    keywords = ", ".join(search.get("keywords") or []) or "the resume"
    return {
        "worth_reading": {
            "type": "noul",
            "instructions": (
                "Is this a real job opening for the kind of work in the keywords, "
                "rather than benefits, legal text, recruiting, sales, or an internship?"
            ),
            "criteria": {
                "true": f"The posting describes work close to these keywords: {keywords}.",
                "false": "The text is boilerplate, or the role is sales, recruiting, or an internship.",
            },
        },
        "fit": {
            "type": "score",
            "instructions": "How close is this role to the resume excerpt and the keywords?",
            "criteria": list(FIT_LEVELS),
        },
    }


def decide(worth: float | None, fit: float | None) -> bool:
    if worth is None or worth < KEEP_AT:
        return False
    if fit is None:
        return True
    return fit >= FIT_AT


def fit_level(fit: float | None) -> int | None:
    """The FIT_LEVELS index a fit value names. Half rounds up, so the label and the gate agree."""
    if fit is None:
        return None
    return max(0, min(len(FIT_LEVELS) - 1, math.floor(fit + 0.5)))


def parse_result(payload: dict, job: dict) -> dict:
    answers = payload.get("answers") or {}
    worth = _noul(answers.get("worth_reading"))
    fit = _score_value(answers.get("fit"))
    level = fit_level(fit)
    label = FIT_LEVELS[level] if level is not None else ""
    years, sentence = years_required(job.get("description") or "")
    return {
        "url": job.get("url") or "",
        "worth": worth,
        "fit": fit,
        "fit_label": label,
        "keep": decide(worth, fit),
        "years_stated": years,
        "years_sentence": sentence,
        "over_years": False,
        "described": bool((job.get("description") or "").strip()),
    }


def apply_years_cap(record: dict, max_years) -> dict:
    stated = record.get("years_stated")
    try:
        cap = int(max_years) if max_years is not None else None
    except (TypeError, ValueError):
        cap = None
    record["over_years"] = bool(cap is not None and stated is not None and stated > cap)
    return record


def ask_clef(state: str, questions: dict) -> dict:
    """POST one System One request to Ollama. CLEF_URL and CLEF_MODEL override the defaults."""
    url = os.environ.get("CLEF_URL", DEFAULT_URL)
    model = os.environ.get("CLEF_MODEL", DEFAULT_MODEL)
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code == 404:
            raise GateError("clef-flash is not pulled. Run: ollama pull clef-flash") from exc
        raise GateError(f"Clef-flash returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GateError("Clef-flash is not reachable. Start Ollama, then run: ollama pull clef-flash") from exc
    except json.JSONDecodeError as exc:
        raise GateError("Clef-flash returned a response that was not JSON") from exc


def haiku_system(questions: dict) -> str:
    worth = questions["worth_reading"]
    fit = questions["fit"]
    levels = "\n".join(f"{index}: {text}" for index, text in enumerate(fit["criteria"]))
    return "\n".join(
        [
            "You screen one job posting for one person. The user message has the posting, their keywords, and a resume excerpt.",
            "",
            f"worth_reading: {worth['instructions']}",
            f"true when: {worth['criteria']['true']}",
            f"false when: {worth['criteria']['false']}",
            "",
            f"fit: {fit['instructions']} Pick one level.",
            levels,
        ]
    )


def haiku_predictor(root: Path, *, client=None):
    """One Claude client for a whole batch. Uses the classify step in profile/models.json."""
    from hunt.rewrite_api import RewriteError, _Runner, load_steps, make_client
    from hunt.sources import load_env

    load_env(root / ".env")
    try:
        step = load_steps(root)["classify"]
        runner = _Runner(client if client is not None else make_client())
    except RewriteError as exc:
        raise GateError(f"Haiku cannot run: {exc}") from exc

    from hunt import spend

    table = spend.prices(root)

    def predict(state: str, questions: dict) -> dict:
        if spend.room(root, "classify") <= 0:
            raise GateError(CLASSIFY_SPENT)
        def bill(stat) -> None:
            usage = {
                "input_tokens": stat.input_tokens,
                "output_tokens": stat.output_tokens,
                "cache_read_input_tokens": stat.cache_read_tokens,
                "cache_creation_input_tokens": stat.cache_write_tokens,
            }
            spend.record(root, "classify", spend.cost(table, stat.model, usage, batch=False))

        try:
            reply, stat = runner.ask_with_stat("classify", step, haiku_system(questions), state, HAIKU_SCHEMA)
        except RewriteError as exc:
            if exc.stat is not None:
                bill(exc.stat)
            raise GateError(str(exc)) from exc
        bill(stat)
        answers = {"worth_reading": {"noul": 1.0 if reply.get("worth_reading") is True else 0.0}}
        if isinstance(reply.get("fit"), (int, float)):
            answers["fit"] = {"score": float(reply["fit"])}
        return {"answers": answers}

    return predict


def clef_reachable(timeout: float = 3.0) -> bool:
    """Ollama answers and the Clef-flash model is pulled. A CLEF_URL that is not Ollama reads as unreachable."""
    base = os.environ.get("CLEF_URL", DEFAULT_URL).split("/v1/")[0]
    model = os.environ.get("CLEF_MODEL", DEFAULT_MODEL)
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout) as response:
            tags = json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return False
    names = {str(item.get("name") or "") for item in tags.get("models") or [] if isinstance(item, dict)}
    return any(name == model or name.split(":")[0] == model for name in names)


def active_classifier(root: Path, env: dict[str, str] | None = None) -> str:
    """The person's choice, except that Clef-flash falls back to Haiku when Ollama is down and a key is present."""
    chosen = classifier(root)
    if chosen != "clef" or env is None:
        return chosen
    if not (env.get("ANTHROPIC_API_KEY") or "").strip().startswith("sk-ant-"):
        return chosen
    return chosen if clef_reachable() else "haiku"


def make_predictor(root: Path, env: dict[str, str] | None = None):
    chosen = active_classifier(root, env)
    if chosen == "clef":
        return ask_clef
    if chosen == "haiku":
        return haiku_predictor(root)
    raise GateError(f"profile/models.json classifier must be clef or haiku, not {chosen}.")


def evaluate(root: Path, job: dict, *, predictor=None, by: str | None = None) -> dict:
    predict = predictor or make_predictor(root)
    try:
        payload = predict(build_state(root, job), questions_for(root))
    except GateError:
        raise
    except Exception as exc:
        raise GateError(str(exc)) from exc
    rubric = load_json(root / "profile/rubric.json", {})
    record = apply_years_cap(parse_result(payload, job), rubric.get("max_years_without_cap"))
    record["by"] = by or classifier(root)
    if record["url"]:
        from hunt.store import data_lock

        with data_lock(root):
            cache = load_cache(root)
            cache[record["url"]] = record
            save_cache(root, cache)
    return record


def redo_wrong_years(root: Path, jobs: list[dict]) -> int:
    """Forget decisions whose classifier was told a different years figure than the posting states now."""
    from hunt.store import data_lock

    removed = 0
    with data_lock(root):
        cache = load_cache(root)
        for job in jobs:
            urls = [url for url in [job.get("url") or ""] + list(job.get("also_at") or []) if url in cache]
            stale = [url for url in urls if isinstance(cache[url], dict) and "years_stated" in cache[url]
                     and cache[url]["years_stated"] != years_stated(job.get("description") or "")]
            if stale:
                for url in urls:
                    cache.pop(url, None)
                removed += 1
        if removed:
            save_cache(root, cache)
    return removed


def classify_pending(root: Path, jobs: list[dict], on_progress=None, env: dict[str, str] | None = None) -> list[dict]:
    """Classify every role that is not cached. Haiku stops at the daily classify cap."""
    if not jobs:
        return []
    return evaluate_many(root, jobs, limit=len(jobs), predictor=None, on_progress=on_progress, env=env)


def workers() -> int:
    """Classifier requests in flight at once. CLEF_PARALLEL overrides. Ollama queues what it cannot serve together."""
    try:
        return max(1, int(os.environ.get("CLEF_PARALLEL") or DEFAULT_PARALLEL))
    except ValueError:
        return DEFAULT_PARALLEL


def evaluate_many(root: Path, jobs: list[dict], *, limit: int, predictor=None, on_progress=None, env: dict[str, str] | None = None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    cache = load_cache(root)
    pending = [job for job in jobs if not current(cached(cache, job), job)][:limit]
    if not pending:
        return []
    chosen = active_classifier(root, env) if predictor is None else None
    predict = predictor or make_predictor(root, env)
    name = CLASSIFIERS.get(chosen or "", label(root))

    def one(job: dict) -> dict:
        if on_progress:
            on_progress(f"{name} · {job.get('company') or 'company'} — {job.get('title') or 'role'}")
        return evaluate(root, job, predictor=predict, by=chosen)

    with ThreadPoolExecutor(max_workers=min(workers(), len(pending))) as pool:
        return list(pool.map(one, pending))


def _noul(node) -> float | None:
    if not isinstance(node, dict) or "noul" not in node:
        return None
    return float(node["noul"])


def _score_value(node) -> float | None:
    if not isinstance(node, dict) or "score" not in node:
        return None
    return float(node["score"])
