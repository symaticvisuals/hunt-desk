"""Dedupe scored jobs. Role-agnostic: company, title, and platform job id."""

from __future__ import annotations

import csv
import re
from pathlib import Path

FIELDS = [
    "date",
    "company",
    "role",
    "base_score",
    "penalties",
    "final_score",
    "applied",
    "reached_interview",
    "status",
    "platform",
    "job_id",
    "url",
    "source",
    "location",
]

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
_GH_JID = re.compile(r"[?&]gh_jid=(\d+)", re.IGNORECASE)
_GH_PATH = re.compile(r"/jobs/(\d+)")
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def normalize(value: str) -> str:
    text = _PUNCT.sub(" ", (value or "").lower())
    text = _SPACE.sub(" ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def name_key(company: str, role: str) -> str:
    return f"{normalize(company)}|{normalize(role)}"


def extract_job_id(url: str, platform: str = "", explicit: str = "") -> str:
    if explicit:
        return explicit.strip().lower()
    if not url:
        return ""
    platform = (platform or "").lower()
    greenhouse = platform == "greenhouse" or "greenhouse.io" in url or "gh_jid=" in url
    if greenhouse:
        match = _GH_JID.search(url) or _GH_PATH.search(url)
        return match.group(1) if match else ""
    match = _UUID.search(url)
    return match.group(0).lower() if match else ""


def id_key(platform: str, job_id: str) -> str:
    if not job_id:
        return ""
    return f"{(platform or '').lower()}|{job_id.strip().lower()}"


def _applied(row: dict[str, str]) -> bool:
    return (row.get("applied") or "").strip().lower() == "yes"


def classify(candidates: list[dict[str, str]], existing: list[dict[str, str]]) -> list[str]:
    """Return NEW or DUP for each candidate, including duplicates inside the batch."""
    names: set[str] = set()
    ids: set[str] = set()
    for row in existing:
        names.add(name_key(row.get("company", ""), row.get("role", "")))
        key = id_key(row.get("platform", ""), row.get("job_id", ""))
        if key:
            ids.add(key)
    statuses: list[str] = []
    for candidate in candidates:
        platform = candidate.get("platform", "")
        job_id = extract_job_id(
            candidate.get("url", ""),
            platform,
            candidate.get("job_id", ""),
        )
        role = candidate.get("role") or candidate.get("title") or ""
        name = name_key(candidate.get("company", ""), role)
        ident = id_key(platform, job_id)
        duplicate = name in names or (ident != "" and ident in ids)
        statuses.append("DUP" if duplicate else "NEW")
        names.add(name)
        if ident:
            ids.add(ident)
    return statuses


def cleanup_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Merge rows that share a name key or a job id. Keep an Applied=Yes row when one exists."""
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_name: dict[str, int] = {}
    by_id: dict[str, int] = {}
    for index, row in enumerate(rows):
        name = name_key(row.get("company", ""), row.get("role", ""))
        if name in by_name:
            union(index, by_name[name])
        else:
            by_name[name] = index
        ident = id_key(
            row.get("platform", ""),
            extract_job_id(row.get("url", ""), row.get("platform", ""), row.get("job_id", "")),
        )
        if not ident:
            continue
        if ident in by_id:
            union(index, by_id[ident])
        else:
            by_id[ident] = index

    groups: dict[int, list[dict[str, str]]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append(row)

    kept: list[dict[str, str]] = []
    removed = 0
    for group in groups.values():
        applied = [row for row in group if _applied(row)]
        kept.append(applied[0] if applied else group[0])
        removed += len(group) - 1
    return kept, removed


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {field: (row.get(field) or "") for field in FIELDS}
            for row in csv.DictReader(handle)
            if any((row.get(field) or "").strip() for field in FIELDS)
        ]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})
