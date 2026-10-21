"""Read an Overleaf project through its Git remote and rewrite the .tex in place."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hunt.onboard import read_upload
from hunt.resume_audit import latex_to_text, rewrite_tex

_ID = re.compile(r"(?:git\.)?overleaf\.com/(?:project/|git/)?([0-9a-fA-F]{8,})")


@dataclass
class LoadedResume:
    text: str
    tex_path: Path | None = None
    project_dir: Path | None = None


def project_id(value: str) -> str | None:
    match = _ID.search(value or "")
    return match.group(1) if match else None


def load_source(root: Path, spec: str) -> LoadedResume:
    """Load a file path, a folder of .tex files, or an Overleaf project URL."""
    pid = project_id(spec)
    if pid:
        folder = fetch_project(root, pid)
        tex = find_main_tex(folder)
        return LoadedResume(latex_to_text(tex.read_text(encoding="utf-8", errors="replace")), tex, folder)
    path = Path(spec).expanduser()
    if path.is_dir():
        tex = find_main_tex(path)
        return LoadedResume(latex_to_text(tex.read_text(encoding="utf-8", errors="replace")), tex, path)
    if path.suffix.lower() == ".tex":
        return LoadedResume(latex_to_text(path.read_text(encoding="utf-8", errors="replace")), path, path.parent)
    return LoadedResume(read_upload(path))


def fetch_project(root: Path, pid: str) -> Path:
    dest = root / "resumes" / "overleaf" / pid
    if (dest / ".git").exists():
        completed = subprocess.run(
            ["git", "-C", str(dest), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or "Could not update the Overleaf clone.")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("OVERLEAF_GIT_TOKEN", "").strip()
    remote = f"https://git:{token}@git.overleaf.com/{pid}" if token else f"https://git.overleaf.com/{pid}"
    completed = subprocess.run(
        ["git", "clone", remote, str(dest)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Could not clone that Overleaf project. In Overleaf, open Menu, then Git, and copy the token. "
            "Put it in OVERLEAF_GIT_TOKEN. The username is git."
        )
    return dest


def find_main_tex(folder: Path) -> Path:
    files = [path for path in folder.rglob("*.tex") if ".git" not in path.parts]
    for name in ("main.tex", "resume.tex", "cv.tex"):
        for path in files:
            if path.name == name:
                return path
    if not files:
        raise RuntimeError(f"No .tex file in {folder}")
    return files[0]


def rewrite_project(tex_path: Path) -> int:
    original = tex_path.read_text(encoding="utf-8", errors="replace")
    rewritten, count = rewrite_tex(original)
    if count and rewritten != original:
        backup = tex_path.with_suffix(tex_path.suffix + ".bak")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        tex_path.write_text(rewritten, encoding="utf-8")
    return count


def push_project(project: Path, message: str) -> None:
    if not (project / ".git").exists():
        raise RuntimeError(f"{project} is not an Overleaf clone.")
    add = subprocess.run(["git", "-C", str(project), "add", "-A"], capture_output=True, text=True, check=False)
    if add.returncode != 0:
        raise RuntimeError((add.stderr or "git add failed").strip())
    commit = subprocess.run(
        ["git", "-C", str(project), "commit", "-m", message],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        raise RuntimeError((commit.stderr or commit.stdout or "git commit failed").strip())
    branch = _branch(project)
    push = subprocess.run(
        ["git", "-C", str(project), "push", "origin", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if push.returncode != 0:
        raise RuntimeError((push.stderr or push.stdout or "git push failed").strip())


def _branch(project: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    name = completed.stdout.strip()
    return name or "main"
