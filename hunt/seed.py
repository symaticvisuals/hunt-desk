"""Copy the shipped defaults into place on a fresh clone. A file that already exists is never overwritten."""

from __future__ import annotations

import shutil
from pathlib import Path

DEFAULTS = (
    ("profile/defaults/og-resume.md", "profile/og-resume.md"),
    ("profile/defaults/rubric.json", "profile/rubric.json"),
    ("profile/defaults/search.json", "profile/search.json"),
    ("profile/defaults/models.json", "profile/models.json"),
    ("profile/defaults/worth.json", "profile/worth.json"),
    ("data/defaults/watchlist.json", "data/watchlist.json"),
)


def seed(root: Path) -> list[str]:
    copied: list[str] = []
    for source, target in DEFAULTS:
        src, dest = root / source, root / target
        if dest.exists() or not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied.append(target)
    return copied
