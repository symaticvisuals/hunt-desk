"""Numbered menu and the commands a schedule can call without the menu."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hunt.evaluated import cleanup_rows, read_csv, write_csv
from hunt.search import format_jobs, repo_root
from hunt.sources import format_status, load_env, source_status

SCORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["jobs"],
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["company", "role", "base_score", "penalties", "final_score", "url"],
                "properties": {
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "base_score": {"type": "number"},
                    "penalties": {"type": "string"},
                    "final_score": {"type": "number"},
                    "url": {"type": "string"},
                },
            },
        }
    },
}


def _env(root: Path) -> dict[str, str]:
    load_env(root / ".env")
    return dict(os.environ)


def _search_config(root: Path) -> dict:
    return json.loads((root / "profile/search.json").read_text(encoding="utf-8"))


def cmd_sources(root: Path) -> int:
    search = _search_config(root)
    print(format_status(source_status(search.get("sources") or [], _env(root))))
    return 0


def cmd_search(root: Path) -> int:
    from hunt.schedule import run
    from hunt.store import Busy

    try:
        result = run(root, _env(root), full=True, on_progress=_progress)
    except Busy as exc:
        print(exc)
        return 1
    print(format_jobs(result.search))
    print(f"cache: {(root / 'data/last-search.json').relative_to(root)}")
    print(f"{len(result.added)} new in the inbox. {len(result.announced)} newly on the main list.")
    from hunt.gate import CLASSIFIER_LABELS

    for error in result.errors:
        if error.startswith(CLASSIFIER_LABELS):
            print(error)
            return 1
    return 0


def cmd_tick(root: Path, args: list[str]) -> int:
    from hunt.schedule import tick

    result = tick(root, _env(root), force="--force" in args)
    if result.skipped:
        print(f"tick skipped: {result.skipped}")
        return 0
    print(f"tick ran {', '.join(result.units)}: {len(result.added)} new, {len(result.announced)} worth a look")
    return 0


def cmd_batch(root: Path) -> int:
    from hunt.batch import BatchError, open_batches, poll

    waiting = open_batches(root)
    for record in waiting:
        print(f"{record['id']}  {record['kind']:6} {len(record['jobs'])} roles  sent {record.get('created', '')[:16]}")
    if not waiting:
        print("No open batches. Press A in the desk to score every role on the main list through the Batch API.")
        return 0
    _env(root)
    try:
        notes = poll(root)
    except BatchError as exc:
        print(exc)
        return 1
    for note in notes:
        print(note)
    if not notes:
        print("Still processing. Batches usually finish within an hour.")
    return 0


def cmd_schedule(root: Path, args: list[str]) -> int:
    from hunt.launchd import install, installed, uninstall
    from hunt.schedule import status_lines

    action = args[0] if args else "status"
    try:
        if action == "install":
            print(install(root))
            return 0
        if action == "uninstall":
            print(uninstall(root))
            return 0
    except RuntimeError as exc:
        print(exc)
        return 1
    if action != "status":
        print("Usage: schedule install | uninstall | status")
        return 1
    print("agent loaded" if installed(root) else "agent not installed. Run: uv run python -m hunt schedule install")
    for line in status_lines(root, _env(root)):
        print(line)
    return 0


def cmd_last(root: Path) -> int:
    path = root / "data/last-search.json"
    if not path.exists():
        print("No search yet. Run search first.")
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = payload.get("jobs") or []
    if not jobs:
        print("Last search found 0 new jobs.")
        return 0
    for job in jobs:
        print(f"{job.get('company')} — {job.get('title')}")
        print(f"  {job.get('url')}")
    print(f"{len(jobs)} jobs in {path.relative_to(root)}")
    return 0


def cmd_cleanup(root: Path) -> int:
    path = root / "data/evaluated-jobs.csv"
    rows = read_csv(path)
    kept, removed = cleanup_rows(rows)
    if removed:
        write_csv(path, kept)
    print(f"removed {removed} duplicate rows. {len(kept)} rows remain.")
    return 0


def cmd_dedupe(root: Path, stdin: str) -> int:
    from hunt.evaluated import classify

    existing = read_csv(root / "data/evaluated-jobs.csv")
    candidates = []
    for line in stdin.splitlines():
        if line.strip():
            candidates.append(json.loads(line))
    for status in classify(candidates, existing):
        print(status)
    return 0


SCORE_PROMPT = (
    "Score one job for the person whose files are in this repository. "
    "Read profile/og-resume.md, profile/rubric.json, and {packet} . "
    "Read profile/linkedin-profile.md for extra story, and profile/projects.md only when the posting wants someone "
    "who builds product, agents, or internal tools. Apply profile/rubric.json exactly as CLAUDE.md describes. "
    "The description in {packet} already has benefits and legal boilerplate removed. "
    "Do not fetch the posting, run commands, or edit files. "
    "Return exactly one job: company, role, base_score, penalties (the penalty and bonus ids, comma separated, "
    "empty when none), final_score, and url (the url from {packet} )."
)
SCORE_WORKERS = 3


@dataclass
class Scored:
    code: int
    message: str
    final_score: float | None = None


def score_one(root: Path, job: dict, *, runner=subprocess.run, client=None) -> Scored:
    """Ask Claude Code for one role's score, then write the row here. Without the claude CLI, a key sends it
    through the Messages API instead. 127 means neither is available."""
    from hunt.claude_cli import ClaudeMissing, ask, job_dir
    from hunt.trim import shorten

    folder = job_dir(root, job)
    packet_job = dict(job)
    packet_job["description"] = shorten(job.get("description") or "", limit=2200)
    (folder / "job.json").write_text(json.dumps(packet_job, indent=2), encoding="utf-8")
    packet = (folder / "job.json").relative_to(root)
    label = f"{job.get('company') or 'company'} — {job.get('title') or job.get('role') or 'role'}"
    try:
        output, problem = ask(
            root,
            SCORE_PROMPT.format(packet=packet),
            SCORE_SCHEMA,
            log=str((folder / "score.out").relative_to(root)),
            timeout=600,
            runner=runner,
        )
    except ClaudeMissing as exc:
        from hunt.tailor import api_key_ready

        if not api_key_ready(root):
            return Scored(127, str(exc))
        from hunt.batch import BatchError, run_now

        problem = ""
        try:
            output = run_now(root, "score", job, client=client)
        except BatchError as error:
            return Scored(1, f"Claude could not score {label}: {error}")
    if output is None:
        return Scored(1, f"Claude could not score {label}: {problem}")
    jobs = output.get("jobs") or []
    row = next((item for item in jobs if item.get("url") == job.get("url")), jobs[0] if jobs else None)
    if row is None:
        return Scored(1, f"Claude returned no score for {label}.")
    record_score(root, job, row)
    final = float(row["final_score"])
    message = f"{label}: {final:g} ({row.get('penalties') or 'no penalties'})"
    if final > screenout_cap(root):
        from hunt.tailor import tailor_now

        message += ". " + tailor_now(root, job, runner=runner, client=client).message
    return Scored(0, message, final)


def score_many(root: Path, jobs: list[dict], *, runner=subprocess.run, on_done=None) -> list[Scored]:
    """Score roles SCORE_WORKERS at a time, each in its own Claude Code session. Results come back in input order."""
    from concurrent.futures import ThreadPoolExecutor

    def one(job: dict) -> Scored:
        scored = score_one(root, job, runner=runner)
        if on_done:
            on_done(job, scored)
        return scored

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(SCORE_WORKERS, len(jobs))) as pool:
        return list(pool.map(one, jobs))


def screenout_cap(root: Path) -> float:
    path = root / "profile/rubric.json"
    try:
        rubric = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        rubric = {}
    return float(rubric.get("screenout_cap") or 75)


def record_score(root: Path, job: dict, row: dict) -> None:
    from datetime import date

    from hunt.evaluated import FIELDS
    from hunt.store import data_lock

    path = root / "data/evaluated-jobs.csv"
    fields = {
        "base_score": f"{float(row['base_score']):g}",
        "penalties": str(row.get("penalties") or ""),
        "final_score": f"{float(row['final_score']):g}",
    }
    with data_lock(root):
        rows = read_csv(path)
        existing = next((item for item in rows if item.get("url") == job.get("url")), None)
        if existing is not None:
            existing.update(fields)
        else:
            rows.append(
                {
                    "date": date.today().isoformat(),
                    "company": job.get("company") or row.get("company") or "",
                    "role": job.get("title") or job.get("role") or row.get("role") or "",
                    **fields,
                    "platform": job.get("platform") or "",
                    "job_id": str(job.get("job_id") or ""),
                    "url": job.get("url") or "",
                    "source": job.get("source") or "",
                    "location": job.get("location") or "",
                }
            )
        write_csv(path, [{name: item.get(name, "") for name in FIELDS} for item in rows])


def cmd_score(root: Path, *, confirm: bool) -> int:
    from hunt.follow import select_for_claude
    from hunt.inbox import load_jobs
    from hunt.live import check_live
    from hunt.screen import context, max_years

    jobs = load_jobs(root)
    if not jobs:
        print("No roles yet. Run search first.")
        return 0
    chosen, skipped = select_for_claude(jobs, max_years(root), check_live, context(root))
    for line in skipped:
        _progress(line)
    if not chosen:
        print("Nothing is worth a tailored resume. Press S on one row to score it anyway.")
        return 0
    if shutil.which("claude") is None:
        print("claude is not on PATH. The shortlist is in the desk.")
        return 0
    if confirm:
        answer = input(f"Score {len(chosen)} open roles with Claude? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("cancelled")
            return 0
    _progress(f"Scoring {len(chosen)} roles, {SCORE_WORKERS} at a time.")
    results = score_many(root, chosen, on_done=lambda job, scored: print(scored.message, flush=True))
    failed = [scored for scored in results if scored.code != 0]
    if any(scored.code == 127 for scored in failed):
        return 127
    print(f"Scored {len(results) - len(failed)} of {len(results)} roles.")
    if failed:
        return 1
    return 0


def cmd_sameness(root: Path) -> int:
    from hunt.sameness import measure, report

    print(report(measure(root)))
    return 0


def cmd_funnel(root: Path, args: list[str]) -> int:
    from hunt.funnel import format_funnel, funnel_counts, record_funnel

    label = ""
    if args[:1] == ["--record"] and len(args) == 2:
        label = args[1]
    elif args:
        print("Usage: python -m hunt funnel [--record LABEL]")
        return 2
    counts = funnel_counts(root)
    print(format_funnel(counts))
    if label:
        record_funnel(root, label, counts)
        print(f"Recorded as {label} in data/funnel.json")
    return 0


def cmd_paths(root: Path) -> int:
    print("Edit these files. The menu does not open an editor.")
    for relative in (
        "profile/og-resume.md",
        "profile/linkedin-profile.md",
        "profile/projects.md",
        "profile/search.json",
        "profile/rubric.json",
        "data/watchlist.json",
        ".env",
    ):
        print(f"  {root / relative}")
    return 0


def cmd_setup(root: Path, args: list[str]) -> int:
    from hunt.onboard import apply_setup, infer
    from hunt.overleaf import load_source, rewrite_project
    from hunt.resume_audit import audit, report

    rewrite = "--rewrite" in args
    files = [item for item in args if item != "--rewrite"]
    if files:
        try:
            loaded = load_source(root, files[0])
        except (OSError, RuntimeError, FileNotFoundError) as exc:
            print(exc)
            return 1
        text = loaded.text
        if rewrite and loaded.tex_path is not None:
            count = rewrite_project(loaded.tex_path)
            text = load_source(root, str(loaded.tex_path)).text
            print(f"Rewrote {count} line(s) in {loaded.tex_path}.")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        print("Run: uv run python -m hunt setup resume.pdf")
        print("Or an Overleaf URL: uv run python -m hunt setup https://www.overleaf.com/project/<id>")
        print("Add --rewrite to edit weak lines in a .tex file. Push is separate: uv run python -m hunt overleaf push <id>")
        print("Or open the desk and press ctrl+n.")
        return 2
    if len(text.strip()) < 80:
        print("That resume is too short to set up from.")
        return 1
    print(report(audit(text)))
    found = infer(text)
    if not found.keywords:
        print("No role titles found. Add a line like 'Software Engineer' and try again.")
        return 1
    apply_setup(root, text, found)
    print("Keywords: " + ", ".join(found.keywords))
    if found.years_cap is None:
        print("Years cap: unchanged")
    else:
        print(f"Years cap: {found.years_cap}")
    return 0


def cmd_overleaf(root: Path, args: list[str]) -> int:
    from hunt.overleaf import project_id, push_project

    if not args or args[0] != "push" or len(args) < 2:
        print("Usage: python -m hunt overleaf push <project-id-or-folder>")
        return 2
    target = args[1]
    pid = project_id(target)
    folder = root / "resumes" / "overleaf" / pid if pid else Path(target)
    try:
        push_project(folder, "Rewrite resume lines from hunt audit")
    except RuntimeError as exc:
        print(exc)
        return 1
    print(f"Pushed {folder}")
    return 0


def cmd_clef(root: Path, args: list[str]) -> int:
    from hunt.gate import GateError, evaluate_many, label, redo_wrong_years
    from hunt.inbox import load_jobs

    limit = 15
    redo = False
    rest = list(args)
    while rest:
        item = rest.pop(0)
        if item == "--limit" and rest:
            limit = int(rest.pop(0))
        elif item == "--redo-years":
            redo = True
        else:
            print("Usage: python -m hunt classify [--limit N] [--redo-years]")
            return 2
    jobs = load_jobs(root)
    if not jobs:
        print("No roles yet. Run: uv run python -m hunt search")
        return 0
    if redo:
        print(f"{redo_wrong_years(root, jobs)} decisions used the wrong years. They are queued for the classifier.")
        jobs = load_jobs(root)
    try:
        results = evaluate_many(root, jobs, limit=limit, on_progress=_progress)
    except GateError as exc:
        print(exc)
        return 1
    kept = sum(1 for row in results if row.get("keep"))
    print(f"{label(root)} checked {len(results)}. {kept} are worth a Claude score.")
    print("Skipped roles stay out of s until you press S on one of them.")
    return 0


def _ensure_anthropic_key(root: Path) -> bool:
    import getpass

    from hunt.inbox import anthropic_key_present, store_anthropic_key

    if anthropic_key_present(root):
        return True
    if not sys.stdin.isatty():
        print("Set ANTHROPIC_API_KEY in .env.")
        return False
    value = getpass.getpass("Anthropic API key: ").strip()
    if not value:
        print("No API key entered.")
        return False
    store_anthropic_key(root, value)
    return True


def _ensure_workspace(root: Path) -> bool:
    from hunt.claude_client import ClaudeAuthError, lookup_workspace, store_workspace_id

    try:
        found = lookup_workspace(root)
    except ClaudeAuthError as exc:
        print(exc)
        return False
    if found.selected:
        if found.choices:
            print(f"Using workspace {found.choices[0][1]}")
        return True
    if not sys.stdin.isatty():
        print("Set ANTHROPIC_WORKSPACE_ID in .env. The id is on https://platform.claude.com/settings/workspaces")
        return False
    if found.choices:
        for index, (identifier, name) in enumerate(found.choices, start=1):
            print(f"{index}  {name}  {identifier}")
        raw = input("Workspace number: ").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= len(found.choices):
            print("No workspace selected.")
            return False
        store_workspace_id(root, found.choices[int(raw) - 1][0])
        return True
    print("This key needs a workspace id. Copy it from https://platform.claude.com/settings/workspaces")
    value = input("Workspace id: ").strip()
    if not value:
        print("No workspace id entered.")
        return False
    store_workspace_id(root, value)
    return True


def _progress(message: str) -> None:
    print(message, flush=True)


def _answer_and_continue(root: Path, payload: dict, result):
    from hunt.rewrite_api import run_resume_rewrite, write_answers

    if not sys.stdin.isatty():
        print(result.summary)
        for question in result.questions:
            print(f"Q: {question}")
        return result
    replies = []
    for question in result.questions:
        print(question)
        replies.append(input("Answer: ").strip() or "unknown")
    text = write_answers(root, result.questions, replies)
    nxt = dict(payload)
    nxt["answers"] = text
    return run_resume_rewrite(root, nxt, on_progress=_progress)


def cmd_workflows(root: Path) -> int:
    from hunt.workflows import discover

    rows = discover(root)
    if not rows:
        print("No workflows in .grok/workflows or .claude/workflows.")
        return 0
    for workflow in rows:
        print(f"{workflow.engine:8} {workflow.name:20} {workflow.description}")
    return 0


def cmd_run(root: Path, args: list[str]) -> int:
    from hunt.rewrite_api import RewriteError, run_resume_rewrite
    from hunt.workflows import command, discover

    if len(args) < 2:
        print("Usage: python -m hunt run <api|managed|grok|claude> <workflow-name>")
        return 2
    engine, name = args[0], args[1]
    match = next((item for item in discover(root) if item.engine == engine and item.name == name), None)
    if match is None:
        print(f"No {engine} workflow named {name}.")
        return 1
    payload = {"resume": "profile/og-resume.md", "job": "", "answers": ""}
    if len(args) > 2:
        payload.update(json.loads(args[2]))
    if match.engine in {"api", "managed"} and not _ensure_anthropic_key(root):
        return 1
    if match.engine in {"api", "managed"} and not _ensure_workspace(root):
        return 1
    if match.engine == "api":
        try:
            result = run_resume_rewrite(root, payload, on_progress=_progress)
        except RewriteError as exc:
            print(exc)
            return 1
        if result.paused:
            result = _answer_and_continue(root, payload, result)
        print(result.summary)
        if result.next_steps:
            print(result.next_steps)
        if result.updated:
            print(result.updated)
        print(result.path)
        if result.clean:
            from hunt.follow import run_after_rewrite

            print(run_after_rewrite(root, on_progress=_progress))
        return 0
    if match.engine == "managed":
        from hunt.managed_rewrite import ManagedError, run_managed_rewrite

        try:
            result = run_managed_rewrite(root, payload, on_progress=_progress)
        except ManagedError as exc:
            print(exc)
            return 1
        print(result.summary)
        print(result.path)
        print(result.trace)
        return 0
    try:
        cmd = command(match, root, payload)
    except RuntimeError as exc:
        print(exc)
        return 1
    completed = subprocess.run(cmd, cwd=root)
    return completed.returncode


def _find_job(root: Path, url: str) -> dict | None:
    from hunt.inbox import load_jobs

    return next((job for job in load_jobs(root) if url in [job.get("url"), job.get("apply_url"), *(job.get("also_at") or [])]), None)


def cmd_resume(root: Path, args: list[str]) -> int:
    from hunt.ats import report
    from hunt.pdf import NoTeX
    from hunt.tailor import check_tex, master_text, raw_prompt

    if len(args) >= 2 and args[0] == "prompt":
        job = _find_job(root, args[1])
        if job is None:
            print(f"No role in the inbox has the URL {args[1]}.")
            return 1
        print(raw_prompt(root, job))
        return 0
    if len(args) >= 2 and args[0] == "check":
        path = Path(args[1])
        if not path.is_file():
            print(f"No file at {path}.")
            return 1
        job_text = None
        if "--job" in args:
            at = args.index("--job")
            job = _find_job(root, args[at + 1]) if at + 1 < len(args) else None
            if job is None:
                print("No role in the inbox has that URL.")
                return 1
            job_text = job.get("description") or ""
        tex = path.read_text(encoding="utf-8")
        try:
            compiled, checks, sizes = check_tex(tex, master_text(root), job_text)
        except NoTeX as exc:
            print(exc)
            return 1
        if not compiled.ok:
            print("The TeX engine stopped:")
            print(compiled.log_tail)
            return 1
        print(f"{compiled.pages} page{'s' if compiled.pages != 1 else ''}")
        print(report(checks))
        if compiled.pages > 1:
            print("\nLines per section:")
            for title, count in sizes.items():
                print(f"  {title}: {count}")
        return 0
    print("Usage: hunt resume prompt <url>")
    print("       hunt resume check <file.tex> [--job <url>]")
    return 2


def cmd_tex_check(root: Path) -> int:
    from hunt.latex import render
    from hunt.pdf import NoTeX, compile_tex, find_tex

    sample = json.loads((root / "hunt/testdata/sample-resume.json").read_text(encoding="utf-8"))
    try:
        compiled = compile_tex(render(sample))
    except NoTeX as exc:
        print(exc)
        return 1
    engine = (find_tex() or ("TeX", ""))[0]
    if not compiled.ok:
        print(f"{engine} could not compile the sample:")
        print(compiled.log_tail)
        return 1
    print(f"{engine} works: the sample compiles to {compiled.pages} page(s).")
    return 0


def cmd_seed(root: Path) -> int:
    from hunt.seed import seed

    copied = seed(root)
    for path in copied:
        print(f"created {path}")
    if not copied:
        print("Nothing to copy. Your files are already in place.")
    return 0


def cmd_desk(root: Path) -> int:
    try:
        from hunt.desk import HuntApp
    except ImportError:
        print("The desk needs Textual. From this directory, run: uv run python -m hunt")
        return 1
    HuntApp(root).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    root = repo_root()
    if len(argv) == 1:
        return cmd_desk(root)
    command = argv[1]
    if command == "search":
        return cmd_search(root)
    if command == "tick":
        return cmd_tick(root, argv[2:])
    if command == "batch":
        return cmd_batch(root)
    if command == "schedule":
        return cmd_schedule(root, argv[2:])
    if command == "sources":
        return cmd_sources(root)
    if command == "last":
        return cmd_last(root)
    if command == "cleanup":
        return cmd_cleanup(root)
    if command == "dedupe":
        return cmd_dedupe(root, sys.stdin.read())
    if command == "score":
        return cmd_score(root, confirm=False)
    if command in {"classify", "clef", "laya", "jev"}:
        return cmd_clef(root, argv[2:])
    if command == "setup":
        return cmd_setup(root, argv[2:])
    if command == "overleaf":
        return cmd_overleaf(root, argv[2:])
    if command == "workflows":
        return cmd_workflows(root)
    if command == "run":
        return cmd_run(root, argv[2:])
    if command == "paths":
        return cmd_paths(root)
    if command == "sameness":
        return cmd_sameness(root)
    if command == "funnel":
        return cmd_funnel(root, argv[2:])
    if command == "seed":
        return cmd_seed(root)
    if command == "tex-check":
        return cmd_tex_check(root)
    if command == "resume":
        return cmd_resume(root, argv[2:])
    print("Commands: search, tick, schedule, batch, sources, last, cleanup, dedupe, classify, setup, overleaf, workflows, run, score, paths, sameness, funnel, seed, tex-check, resume")
    print("Run with no command to open the desk.")
    return 1
