"""Compile a resume with the TeX engine and keep it to one page by dropping the least important lines first."""

from __future__ import annotations

import copy
import glob
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from hunt import latex

# Tectonic passed the ATS text checks on 2026-10-21; pdflatex is the fallback.
ENGINES: tuple[str, ...] = ("tectonic", "pdflatex")
# launchd runs ticks with /usr/bin:/bin, so the engine is also looked for where installers put it.
SEARCH_DIRS: tuple[str, ...] = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/Library/TinyTeX/bin/*", "/Library/TeX/texbin")
FLOOR = 2


class NoTeX(Exception):
    pass


@dataclass
class Compiled:
    ok: bool
    pages: int = 0
    pdf: bytes = b""
    log_tail: str = ""


@dataclass
class Fitted:
    resume: dict
    tex: str
    compiled: Compiled
    cuts: list[str] = field(default_factory=list)
    over: bool = False


def _dirs() -> list[Path]:
    found: list[Path] = []
    for pattern in SEARCH_DIRS:
        found += [Path(match) for match in sorted(glob.glob(os.path.expanduser(pattern)))]
    return found


def find_tex(dirs: list[Path] | None = None) -> tuple[str, str] | None:
    folders = _dirs() if dirs is None else dirs
    for engine in ENGINES:
        on_path = shutil.which(engine)
        if on_path:
            return engine, on_path
        for folder in folders:
            candidate = folder / engine
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return engine, str(candidate)
    return None


def page_count(data: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(data)).pages)


def compile_tex(tex: str, *, engine: tuple[str, str] | None = None) -> Compiled:
    chosen = engine or find_tex()
    if chosen is None:
        raise NoTeX("No TeX engine found. Run ./start to install one.")
    name, path = chosen
    with tempfile.TemporaryDirectory() as work:
        (Path(work) / "resume.tex").write_text(tex, encoding="utf-8")
        if name == "tectonic":
            command = [path, "--outdir", work, "--chatter", "minimal", "resume.tex"]
        else:
            command = [path, "-interaction=nonstopmode", "-halt-on-error", "resume.tex"]
        try:
            done = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=120)
            output = (done.stdout or "") + (done.stderr or "")
            code = done.returncode
        except subprocess.TimeoutExpired:
            output, code = "The TeX engine took longer than 120 seconds.", 1
        pdf = Path(work) / "resume.pdf"
        if code != 0 or not pdf.exists():
            log = Path(work) / "resume.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else output
            return Compiled(ok=False, log_tail="\n".join(text.splitlines()[-40:]))
        data = pdf.read_bytes()
    return Compiled(ok=True, pages=page_count(data), pdf=data)


def _cut_one(resume: dict) -> str | None:
    """Drop the bullet with the highest priority number from an entry above the floor, the later entry on a tie.
    With every entry at the floor, drop a whole project instead."""
    keys = [
        (int(bullet.get("priority") or 0), index, position)
        for index, entry in enumerate(resume.get("experience") or [])
        if len(entry.get("bullets") or []) > FLOOR
        for position, bullet in enumerate(entry["bullets"])
    ]
    if keys:
        best = max(keys)
        entry = resume["experience"][best[1]]
        bullet = entry["bullets"].pop(best[2])
        return f"{entry.get('company') or ''}: {bullet.get('text') or ''}"
    projects = resume.get("projects") or []
    if not projects:
        return None
    index = max(range(len(projects)), key=lambda at: (int(projects[at].get("priority") or 0), at))
    gone = projects.pop(index)
    return f"project: {gone.get('name') or ''}"


def fit_one_page(resume: dict, *, compile=compile_tex, render=latex.render) -> Fitted:
    """Never changes fonts, margins, or spacing. Over one page after every cut, it keeps the last PDF and says so."""
    current = copy.deepcopy(resume)
    cuts: list[str] = []
    while True:
        tex = render(current)
        compiled = compile(tex)
        if not compiled.ok or compiled.pages <= 1:
            return Fitted(current, tex, compiled, cuts, False)
        cut = _cut_one(current)
        if cut is None:
            return Fitted(current, tex, compiled, cuts, True)
        cuts.append(cut)
