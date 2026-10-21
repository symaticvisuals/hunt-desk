"""How well a classifier agrees with Claude's scores, measured on roles Claude already scored.

Runs on a scratch copy of the profile, so the real decision cache and spend ledger stay as they are. Every run is
appended to data/classify-eval.json, so a prompt change is measured against the run before it.

    uv run python -m hunt.classify_eval                   # Haiku and Clef-flash
    uv run python -m hunt.classify_eval haiku --limit 50 --note "full resume"
"""

from __future__ import annotations

import argparse
import csv
import shutil
import statistics
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from hunt.store import read_json, write_json

HISTORY = "data/classify-eval.json"
# Shorter text is a requisition id or a one-line stub, not a posting.
MIN_TEXT = 200
WORKERS = 3


def labelled(root: Path) -> list[tuple[dict, float]]:
    """Scored roles that still have their posting text, with Claude's final score. Any board URL of a role counts."""
    jobs = read_json(root / "data/inbox.json", [])
    by_url: dict[str, dict] = {}
    for job in jobs if isinstance(jobs, list) else []:
        for url in [job.get("url") or ""] + list(job.get("also_at") or []):
            if url:
                by_url.setdefault(url, job)
    path = root / "data/evaluated-jobs.csv"
    if not path.exists():
        return []
    pairs: list[tuple[dict, float]] = []
    seen: set[int] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            job = by_url.get(row.get("url") or "")
            try:
                score = float(row.get("final_score") or "")
            except ValueError:
                continue
            if job is None or id(job) in seen or len((job.get("description") or "").strip()) < MIN_TEXT:
                continue
            seen.add(id(job))
            pairs.append((job, score))
    return pairs


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, ties averaged. 0 when either side has no spread."""
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    if len(set(rx)) < 2 or len(set(ry)) < 2:
        return 0.0
    return statistics.correlation(rx, ry)


def metrics(results: list[tuple[bool, float | None, float]], positive_above: float) -> dict:
    """results holds (passed the classifier, fit, Claude's score). A positive is a score above positive_above."""
    positives = [row for row in results if row[2] > positive_above]
    passed = [row for row in results if row[0]]
    hits = [row for row in passed if row[2] > positive_above]
    fits = [(row[1], row[2]) for row in results if row[1] is not None]
    return {
        "roles": len(results),
        "passed": len(passed),
        "positives": len(positives),
        "recall": round(len(hits) / len(positives), 3) if positives else None,
        "precision": round(len(hits) / len(passed), 3) if passed else None,
        "spearman": round(spearman([fit for fit, _ in fits], [score for _, score in fits]), 3) if len(fits) > 2 else None,
    }


def scratch(root: Path) -> Path:
    """The profile and the inbox in a temporary folder, with an empty spend ledger and room in the classify cap."""
    work = Path(tempfile.mkdtemp(prefix="hunt-eval-"))
    shutil.copytree(root / "profile", work / "profile")
    (work / "data").mkdir()
    prefs = read_json(work / "profile/preferences.json", {})
    prefs = prefs if isinstance(prefs, dict) else {}
    prefs["budget"] = {"classify_usd_per_day": 5.0, "score_usd_per_day": 0.0}
    write_json(work / "profile/preferences.json", prefs)
    return work


def passes(record: dict, plausible_at: float) -> bool:
    """The desk's own rule: kept by the classifier, and not a stretch."""
    fit = record.get("fit")
    return record.get("keep") is True and (fit is None or fit >= plausible_at)


def run(root: Path, name: str, jobs: list[dict], on_progress=None, workers: int = WORKERS) -> list[dict]:
    from hunt import gate

    predict = gate.ask_clef if name == "clef" else gate.haiku_predictor(root)
    done = [0]

    def one(job: dict) -> dict:
        try:
            record = gate.evaluate(root, job, predictor=predict, by=name)
        except gate.GateError as exc:
            record = {"error": str(exc)}
        done[0] += 1
        if on_progress and done[0] % 25 == 0:
            on_progress(f"{name}: {done[0]}/{len(jobs)}")
        return record

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, jobs))


def evaluate(
    root: Path, names: list[str], *, limit: int | None = None, note: str = "", on_progress=print, workers: int = WORKERS
) -> list[dict]:
    from hunt import spend
    from hunt.screen import PLAUSIBLE_AT, _number
    from hunt.sources import load_env

    load_env(root / ".env")
    rubric = read_json(root / "profile/rubric.json", {})
    plausible_at = _number(rubric.get("plausible_at"), PLAUSIBLE_AT)
    positive_above = float(rubric.get("screenout_cap") or 79)
    pairs = labelled(root)[:limit] if limit else labelled(root)
    jobs = [job for job, _ in pairs]
    runs: list[dict] = []
    work = scratch(root)
    try:
        for name in names:
            records = run(work, name, jobs, on_progress, workers)
            errors = [record["error"] for record in records if "error" in record]
            judged = [(record, score, job) for record, (job, score) in zip(records, pairs) if "error" not in record]
            found = metrics([(passes(record, plausible_at), record.get("fit"), score) for record, score, _ in judged], positive_above)
            missed = [
                {"company": job.get("company"), "title": job.get("title"), "fit": record.get("fit"), "score": score, "reason": record.get("reason", "")}
                for record, score, job in judged
                if score > positive_above and not passes(record, plausible_at)
            ]
            entry = {
                "at": datetime.now(timezone.utc).isoformat(),
                "classifier": name,
                "note": note,
                "errors": len(errors),
                "cost_usd": round(spend.spent(work, "classify"), 4) if name == "haiku" else 0.0,
                **found,
                "missed": missed,
            }
            if errors:
                on_progress(f"{name}: {len(errors)} errors, first: {errors[0]}")
            runs.append(entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    history = read_json(root / HISTORY, [])
    write_json(root / HISTORY, (history if isinstance(history, list) else []) + runs)
    return runs


def format_runs(runs: list[dict]) -> str:
    lines = [f"{'classifier':10} {'roles':>5} {'passed':>6} {'80+':>4} {'recall':>6} {'precision':>9} {'rank corr':>9} {'cost':>7}"]
    for entry in runs:
        lines.append(
            f"{entry['classifier']:10} {entry['roles']:5} {entry['passed']:6} {entry['positives']:4} "
            f"{entry['recall'] if entry['recall'] is not None else '-':>6} {entry['precision'] if entry['precision'] is not None else '-':>9} "
            f"{entry['spearman'] if entry['spearman'] is not None else '-':>9} ${entry['cost_usd']:.3f}"
        )
    for entry in runs:
        if entry["missed"]:
            lines.append(f"\n{entry['classifier']} hid {len(entry['missed'])} roles Claude scored above the cutoff:")
            for miss in entry["missed"][:12]:
                fit = f"{miss['fit']:.1f}" if isinstance(miss["fit"], (int, float)) else "-"
                lines.append(f"  fit {fit}  score {miss['score']:.0f}  {miss['company']} — {miss['title']}  {miss['reason']}".rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from hunt.search import repo_root

    parser = argparse.ArgumentParser(prog="python -m hunt.classify_eval")
    parser.add_argument("classifiers", nargs="*", default=["haiku", "clef"], choices=["haiku", "clef"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--note", default="")
    parser.add_argument("--root", type=Path, default=None, help="the checkout whose profile and data to read")
    parser.add_argument("--workers", type=int, default=WORKERS, help="requests in flight; 1 for a local Ollama that drops connections")
    args = parser.parse_args(argv)
    runs = evaluate(args.root or repo_root(), args.classifiers, limit=args.limit, note=args.note, workers=args.workers)
    print(format_runs(runs))


if __name__ == "__main__":
    main()
