"""Resume rewrite through the Claude API. Each step has its own model."""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from hunt.onboard import read_upload
from hunt.resume_audit import latex_to_text, rewrite_plain
from hunt.sources import load_env

REPORT = "data/resume-rewrite.md"
ANSWERS = "data/resume-answers.md"
UPDATED = "resumes/updated-resume.md"
MASTER = "profile/og-resume.md"
BACKUP = "profile/og-resume.md.bak"
# PDF text often loses the space inside a heading: PROFESSIONALEXPERIENCE, TECHNICALSKILLS.
_EXPERIENCE = re.compile(
    r"^(?:#{1,3}\s*)?(?:professional\s*experience|work\s*experience|experience)\s*$",
    re.IGNORECASE,
)
_SECTION = re.compile(
    r"^(?:#{1,3}\s*)?(?:technical\s*projects|projects|selected\s*work|education|skills|technical\s*skills|certification|summary)\s*$",
    re.IGNORECASE,
)
PLACEHOLDER = "[metric]"
EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# Prices checked against the Claude API model table on 2026-10-06.
# Haiku extracts. Sonnet writes and judges. Opus does the voice pass.
DEFAULT_STEPS = {
    "inventory": {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 4000},
    "rewrite": {"model": "claude-sonnet-5-5", "effort": "high", "max_tokens": 4000},
    "humanize": {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 8000},
    "fact_check": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 2000},
    "preferences": {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 1500},
    "score": {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 8000},
    "tailor": {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 16000},
    "classify": {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 2000},
}

INVENTORY_SYSTEM = (
    "You inventory a resume. Do not rewrite it. Do not invent projects, tools, employers, dates, or numbers. "
    "Return at most 4 roles. For each role return title, company, and the current bullets. "
    "A paragraph under a role counts as a bullet when the resume has no dash marks. "
    "Also list up to 8 job titles this person could apply to, using only the resume, skills, and past work. "
    "Use titles a board would post. Do not add a seniority the resume does not show. "
    "For each bullet that sounds measurable but has no number, add one question. "
    "A question asks about scale, before and after, time, or how often. "
    "If a bullet needs no number, do not invent a question. An empty questions list is valid."
)
REWRITE_SYSTEM = (
    "Rewrite only the one role in the user message. Ignore every other role. "
    "Lead with what changed, then what they did, then the tool. Keep every fact, date, and number. "
    "If a result has no number in the resume or the supplied answers, write [metric]. Do not invent one. "
    "When a supplied answer says the number is unknown, write that bullet without a number instead. "
    "No spearheaded, leveraged, robust, or synergy. Two lines at most per bullet. "
    "Return title, company, and the rewritten bullets only."
)
HUMANIZE_SYSTEM = (
    "Combine the rewritten roles into one resume experience section. "
    "Reorder and rephrase so real work matches the job's words only where that is honest. "
    "Do not change employers, titles, or dates. Do not add a skill that is not in the resume. "
    "Where the job asks for something the resume does not show, add it to gaps. "
    "Make the prose plain: vary sentence length, remove em dashes, remove not-X-but-Y contrasts, "
    "remove chat leftovers, and do not make the text longer. Keep [metric] placeholders. "
    "Return the experience section as resume, and the honest gaps."
)
CHECK_SYSTEM = (
    "Compare the original resume with the rewrite. "
    "Set clean to true only if every employer, title, date, tool, and number in the rewrite "
    "appears in the resume or the user-supplied numbers, or is the placeholder [metric]. "
    "If anything was added, set clean to false and list it in invented. A missing check is not a pass."
)

_OBJECT = {"type": "object", "additionalProperties": False}
INVENTORY_SCHEMA = {
    **_OBJECT,
    "required": ["roles", "questions", "positions"],
    "properties": {
        "roles": {
            "type": "array",
            "items": {
                **_OBJECT,
                "required": ["title", "company", "bullets"],
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "positions": {"type": "array", "items": {"type": "string"}},
    },
}
ROLE_SCHEMA = {
    **_OBJECT,
    "required": ["title", "company", "bullets"],
    "properties": {
        "title": {"type": "string"},
        "company": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
    },
}
DRAFT_SCHEMA = {
    **_OBJECT,
    "required": ["resume", "gaps"],
    "properties": {
        "resume": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
}
CHECK_SCHEMA = {
    **_OBJECT,
    "required": ["clean", "invented"],
    "properties": {
        "clean": {"type": "boolean"},
        "invented": {"type": "array", "items": {"type": "string"}},
    },
}


class RewriteError(Exception):
    pass


@dataclass
class CallStat:
    step: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class RewriteResult:
    summary: str
    path: Path
    clean: bool
    paused: bool
    questions: list[str] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)
    calls: list[CallStat] = field(default_factory=list)
    updated: str = ""
    next_steps: str = ""
    saved: bool = False


@dataclass
class Published:
    updated: str
    saved: bool
    reason: str = ""


def load_steps(root: Path) -> dict[str, dict]:
    steps = {name: dict(spec) for name, spec in DEFAULT_STEPS.items()}
    path = root / "profile" / "models.json"
    if not path.is_file():
        return steps
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RewriteError(f"profile/models.json is not valid JSON: {exc}") from exc
    raw = payload.get("steps") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise RewriteError("profile/models.json needs a steps object.")
    for name, spec in raw.items():
        if name not in steps or not isinstance(spec, dict):
            continue
        model = spec.get("model")
        if isinstance(model, str) and model.strip():
            steps[name]["model"] = model.strip()
        if "effort" in spec:
            effort = spec.get("effort")
            if effort not in EFFORTS:
                raise RewriteError(f"{name} effort must be low, medium, high, xhigh, or max.")
            steps[name]["effort"] = effort
        tokens = spec.get("max_tokens")
        if isinstance(tokens, int) and tokens > 0:
            steps[name]["max_tokens"] = tokens
    return steps


def make_client():
    from hunt.claude_client import ClaudeAuthError, make_client as build

    try:
        return build()
    except ClaudeAuthError as exc:
        raise RewriteError(str(exc)) from exc


def run_resume_rewrite(root: Path, payload: dict, *, client=None, on_progress=None) -> RewriteResult:
    load_env(root / ".env")
    steps = load_steps(root)
    resume_path, resume = _resume_text(root, str(payload.get("resume") or "profile/og-resume.md"))
    job = str(payload.get("job") or "")
    answers = _answers(root, payload)
    runner = _Runner(client if client is not None else make_client())

    def report(message: str) -> None:
        if on_progress:
            on_progress(message)

    report(f"Reading {resume_path.name}")
    report(f"Inventory · {steps['inventory']['model']}")
    inventory = runner.ask(
        "inventory",
        steps["inventory"],
        INVENTORY_SYSTEM,
        resume,
        INVENTORY_SCHEMA,
    )
    roles = _roles(inventory)
    questions = _strings(inventory.get("questions"), 12)
    positions = _strings(inventory.get("positions"), 8)
    report(f"Inventory done · {len(roles)} roles · {len(questions)} questions · {len(positions)} positions")
    if not roles:
        return _finish(
            root,
            runner,
            summary="No roles found in the resume file.",
            resume_text="",
            questions=questions,
            positions=positions,
            gaps=[],
            invented=[],
            clean=False,
            paused=False,
            source=resume_path,
        )
    if _needs_answers(questions, answers):
        write_answer_template(root, questions)
        report("Waiting for your numbers.")
        return _finish(
            root,
            runner,
            summary="Fill the missing numbers. Unknown is a valid answer.",
            resume_text="",
            questions=questions,
            positions=positions,
            gaps=[],
            invented=[],
            clean=False,
            paused=True,
            source=resume_path,
        )

    rewritten = _rewrite_roles(runner, steps["rewrite"], resume, answers, roles, report)
    report(f"Humanize · {steps['humanize']['model']}")
    draft = runner.ask(
        "humanize",
        steps["humanize"],
        HUMANIZE_SYSTEM,
        _human_user(resume, job, rewritten),
        DRAFT_SCHEMA,
    )
    resume_text = rewrite_plain(str(draft.get("resume") or "").strip())
    gaps = _strings(draft.get("gaps"), 12)
    report(f"Fact check · {steps['fact_check']['model']}")
    verdict = runner.ask(
        "fact_check",
        steps["fact_check"],
        CHECK_SYSTEM,
        _check_user(resume, answers, rewritten, resume_text),
        CHECK_SCHEMA,
    )
    clean = verdict.get("clean") is True
    invented = _strings(verdict.get("invented"), 12)
    if not clean and not invented:
        invented = ["The fact check did not pass."]
    summary = "Rewrite passed the fact check." if clean else "Rewrite failed the fact check. Do not paste it in."
    published = publish_resume(root, resume, resume_text, clean)
    steps_text = _next_steps(clean, published)
    report(f"Updated resume · {published.updated}")
    report(steps_text)
    return _finish(
        root,
        runner,
        summary=summary,
        resume_text=resume_text,
        questions=questions,
        positions=positions,
        gaps=gaps,
        invented=invented,
        clean=clean,
        paused=False,
        source=resume_path,
        updated=published.updated,
        next_steps=steps_text,
        saved=published.saved,
    )


class _Runner:
    def __init__(self, client) -> None:
        self.client = client
        self.calls: list[CallStat] = []
        self._lock = threading.Lock()

    def ask(self, step_name: str, step: dict, system: str, user: str, schema: dict) -> dict:
        try:
            with self.client.messages.stream(
                model=step["model"],
                max_tokens=step["max_tokens"],
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": step["effort"],
                    "format": {"type": "json_schema", "schema": schema},
                },
            ) as stream:
                message = stream.get_final_message()
        except RewriteError:
            raise
        except Exception as exc:
            raise RewriteError(_safe_error(step_name, exc)) from exc
        reason = getattr(message, "stop_reason", None)
        if reason == "max_tokens":
            raise RewriteError(f"{step_name} hit max_tokens. Raise that step in profile/models.json.")
        if reason == "refusal":
            raise RewriteError(f"Claude refused the {step_name} step.")
        data = _parse_json(_text(message))
        stat = CallStat(step_name, str(getattr(message, "model", None) or step["model"]), *_usage(message))
        with self._lock:
            self.calls.append(stat)
        return data


def _rewrite_roles(runner: _Runner, step: dict, resume: str, answers: str, roles: list[dict], report) -> list[dict]:
    total = len(roles)

    def one(index: int, role: dict) -> dict:
        company = role.get("company") or "role"
        report(f"Rewrite {index}/{total} · {company} · {step['model']}")
        user = "\n".join(
            [
                "Resume:",
                resume,
                "",
                "Role to rewrite:",
                json.dumps(role, ensure_ascii=False),
                "",
                "Numbers the user supplied:",
                answers or "(none)",
            ]
        )
        result = runner.ask("rewrite", step, REWRITE_SYSTEM, user, ROLE_SCHEMA)
        bullets = _strings(result.get("bullets"), 8)
        if not bullets:
            raise RewriteError(f"Rewrite returned no bullets for {role.get('company') or 'a role'}.")
        report(f"Rewrite done · {company}")
        return {
            "title": str(result.get("title") or role.get("title") or ""),
            "company": str(result.get("company") or role.get("company") or ""),
            "bullets": bullets,
        }

    with ThreadPoolExecutor(max_workers=min(4, total)) as pool:
        return list(pool.map(lambda pair: one(*pair), enumerate(roles, start=1)))


def _resume_text(root: Path, raw: str) -> tuple[Path, str]:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise RewriteError(f"Resume file not found: {raw}")
    try:
        text = read_upload(path)
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise RewriteError(str(exc)) from exc
    if path.suffix.lower() == ".tex":
        text = latex_to_text(text)
    if len(text.strip()) < 40:
        raise RewriteError("The resume file is too short to inventory.")
    return path, text


def _answers(root: Path, payload: dict) -> str:
    typed = str(payload.get("answers") or "").strip()
    if typed:
        return typed
    path = root / ANSWERS
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if _blank_template(text):
        return ""
    return text


def _needs_answers(questions: list[str], answers: str) -> bool:
    return bool(questions) and not answers.strip()


def _blank_template(text: str) -> bool:
    found = [line[2:].strip() for line in text.splitlines() if line.startswith("A:")]
    return bool(found) and all(not item for item in found)


def splice_experience(original: str, experience: str) -> str | None:
    """Keep every section except the experience block, which becomes the checked rewrite.

    None when the resume has no experience heading: appending would leave the old block in place.
    """
    lines = (original or "").splitlines()
    body = [line for line in experience.strip().splitlines()]
    if body and _EXPERIENCE.match(body[0].strip()):
        body = body[1:]
    start = next((index for index, line in enumerate(lines) if _EXPERIENCE.match(line.strip())), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _SECTION.match(lines[index].strip()):
            end = index
            break
    merged = lines[: start + 1] + [""] + body + [""] + lines[end:]
    return "\n".join(merged).rstrip() + "\n"


def publish_resume(root: Path, original: str, experience: str, clean: bool) -> Published:
    """Write the draft, and replace the master only when the draft is whole and checked."""
    text = splice_experience(original, experience)
    path = root / UPDATED
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else experience.strip() + "\n", encoding="utf-8")
    if not clean:
        return Published(UPDATED, False, "fact check")
    if text is None:
        return Published(UPDATED, False, "no experience")
    if PLACEHOLDER in experience:
        return Published(UPDATED, False, "placeholder")
    master = root / MASTER
    if master.is_file():
        (root / BACKUP).write_text(master.read_text(encoding="utf-8"), encoding="utf-8")
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(text, encoding="utf-8")
    return Published(UPDATED, True)


def _next_steps(clean: bool, published: Published) -> str:
    updated = published.updated
    if not clean:
        return (
            f"The draft is {updated}. The fact check failed, so {MASTER} was left unchanged. "
            "Remove the invented lines and run the rewrite again. Search and scoring did not start."
        )
    if published.reason == "no experience":
        return (
            f"The rewritten experience is in {updated}. No experience section was found in the resume to replace, "
            f"so {MASTER} was left unchanged. Paste the bullets in yourself."
        )
    if published.reason == "placeholder":
        return (
            f"The draft is {updated}. It still has {PLACEHOLDER} where a number is missing, so {MASTER} was left "
            f"unchanged. Put a real number in each {PLACEHOLDER} or cut that phrase, then paste the bullets in yourself."
        )
    return (
        f"Updated resume: {updated}. It is also saved as {MASTER}. The previous master is {BACKUP}. "
        "Next the app searches the boards, Clef-flash drops weak roles, and Claude scores the ones worth a look. "
        "You still press a yourself when you apply."
    )


def write_answer_template(root: Path, questions: list[str]) -> Path:
    path = root / ANSWERS
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Answers", ""]
    if not questions:
        lines.append("No numbers were missing.")
    for index, question in enumerate(questions, start=1):
        lines.extend([f"Q: {question}", "A:", ""])
        if index != len(questions):
            lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_answers(root: Path, questions: list[str], replies: list[str]) -> str:
    lines = ["# Answers", ""]
    block = []
    for question, reply in zip(questions, replies):
        value = (reply or "").strip() or "unknown"
        pair = f"Q: {question}\nA: {value}"
        block.append(pair)
    text = "\n\n".join(block)
    body = "# Answers\n\n" + text + ("\n" if text else "")
    path = root / ANSWERS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return text


def _roles(inventory: dict) -> list[dict]:
    found = []
    for role in inventory.get("roles") or []:
        if not isinstance(role, dict):
            continue
        bullets = _strings(role.get("bullets"), 8)
        if not bullets:
            continue
        found.append(
            {
                "title": str(role.get("title") or ""),
                "company": str(role.get("company") or ""),
                "bullets": bullets,
            }
        )
        if len(found) == 4:
            break
    return found


def _strings(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) == limit:
            break
    return out


def _human_user(resume: str, job: str, roles: list[dict]) -> str:
    return "\n".join(
        [
            "Original resume:",
            resume,
            "",
            "Rewritten roles:",
            json.dumps(roles, ensure_ascii=False),
            "",
            "Target job, empty if none:",
            job,
        ]
    )


def _check_user(resume: str, answers: str, roles: list[dict], draft: str) -> str:
    return "\n".join(
        [
            "Original resume:",
            resume,
            "",
            "Rewritten roles:",
            json.dumps(roles, ensure_ascii=False),
            "",
            "Draft:",
            draft,
            "",
            "User-supplied numbers:",
            answers or "(none)",
        ]
    )


def _text(message) -> str:
    parts = []
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            parts.append(block["text"] if isinstance(block, dict) else block.text)
    if not parts:
        raise RewriteError("Claude returned no text.")
    return "\n".join(parts).strip()


def _parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RewriteError("Claude returned text that is not JSON.") from exc
    if not isinstance(data, dict):
        raise RewriteError("Claude returned JSON that is not an object.")
    return data


def _usage(message) -> tuple[int, int, int, int]:
    usage = getattr(message, "usage", None)

    def grab(name: str) -> int:
        if usage is None:
            return 0
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return (
        grab("input_tokens"),
        grab("output_tokens"),
        grab("cache_read_input_tokens"),
        grab("cache_creation_input_tokens"),
    )


def _safe_error(step: str, exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc)
    secret = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if secret and secret in message:
        message = message.replace(secret, "[redacted]")
    return f"Claude API failed during {step}: {type(exc).__name__}: {message}"


def _finish(
    root: Path,
    runner: _Runner,
    *,
    summary: str,
    resume_text: str,
    questions: list[str],
    positions: list[str],
    gaps: list[str],
    invented: list[str],
    clean: bool,
    paused: bool,
    source: Path,
    updated: str = "",
    next_steps: str = "",
    saved: bool = False,
) -> RewriteResult:
    path = root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _report(
            summary=summary,
            resume_text=resume_text,
            questions=questions,
            positions=positions,
            gaps=gaps,
            invented=invented,
            clean=clean,
            paused=paused,
            calls=runner.calls,
            source=source,
            updated=updated,
            next_steps=next_steps,
            source_replaced=saved and source.resolve() == (root / MASTER).resolve(),
        ),
        encoding="utf-8",
    )
    return RewriteResult(
        summary=summary,
        path=path,
        clean=clean,
        paused=paused,
        questions=list(questions),
        positions=list(positions),
        calls=list(runner.calls),
        updated=updated,
        next_steps=next_steps,
        saved=saved,
    )


def _report(
    *,
    summary: str,
    resume_text: str,
    questions: list[str],
    positions: list[str],
    gaps: list[str],
    invented: list[str],
    clean: bool,
    paused: bool,
    calls: list[CallStat],
    source: Path,
    updated: str = "",
    next_steps: str = "",
    source_replaced: bool = False,
) -> str:
    lines = ["# Resume rewrite", "", summary, ""]
    if next_steps:
        lines.extend(["", next_steps, ""])
    if not clean and not paused:
        lines.append("Do not paste this in until the invented lines are removed.")
    if not source_replaced:
        lines.append(f"Source file left unchanged: {source.name}")
    lines.append("")
    if calls:
        lines.append("## Models")
        for call in calls:
            lines.append(
                f"- {call.step}: {call.model} ({call.input_tokens} in, {call.output_tokens} out)"
            )
        lines.append(
            "Tokens "
            f"in {sum(item.input_tokens for item in calls)}, "
            f"out {sum(item.output_tokens for item in calls)}, "
            f"cache read {sum(item.cache_read_tokens for item in calls)}."
        )
        lines.append("")
    if resume_text:
        lines.extend([resume_text, ""])
    lines.append("## Positions")
    lines.extend(_bullets(positions))
    lines.append("")
    lines.append("## Questions")
    lines.extend(_bullets(questions))
    lines.append("")
    lines.append("## Gaps")
    lines.extend(_bullets(gaps))
    if invented:
        lines.extend(["", "## Invented"])
        lines.extend(_bullets(invented))
    lines.append("")
    return "\n".join(lines)


def _bullets(items: list[str]) -> list[str]:
    if not items:
        return ["None listed."]
    return [f"- {item}" for item in items]
