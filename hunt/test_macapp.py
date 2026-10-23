import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hunt.macapp import APP, current, ensure, source
from hunt.system import digest


class FakeMac:
    """Records calls. osacompile leaves a bundle with an Info.plist; any tool in `fail` exits 1."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls = []
        self.fail = fail

    def __call__(self, args, **kwargs):
        args = [str(arg) for arg in args]
        self.calls.append(args)
        code = 1 if args[0] in self.fail else 0
        if args[0] == "osacompile" and not code:
            info = Path(args[args.index("-o") + 1]) / "Contents/Info.plist"
            info.parent.mkdir(parents=True, exist_ok=True)
            info.write_bytes(plistlib.dumps({"CFBundleName": "x"}))
        return self.done(args, code)

    def done(self, args, code):
        class Done:
            returncode = code
            stdout = ""
            stderr = f"{args[0]} broke" if code else ""

        return Done()


def _root(name: str = "hunt") -> Path:
    root = Path(tempfile.mkdtemp()) / name
    (root / "data").mkdir(parents=True)
    return root


@unittest.skipIf(sys.platform == "win32", "Hunt Desk.app is macOS only")
class HuntDeskAppTests(unittest.TestCase):
    def setUp(self):
        self.root = _root()
        self.mac = FakeMac()

    def test_ensure_builds_a_signed_app_with_its_own_bundle_id(self):
        app = ensure(self.root, runner=self.mac, which=lambda name: "/opt/homebrew/bin/uv")
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["CFBundleIdentifier"], f"com.hunt.desk.{digest(self.root)}")
        self.assertEqual(info["CFBundleName"], "Hunt Desk")
        self.assertTrue(info["LSUIElement"])
        self.assertEqual([call[0] for call in self.mac.calls], ["osacompile", "codesign"])
        self.assertEqual(self.mac.calls[1][:3], ["codesign", "--force", "--sign"])
        self.assertEqual(app, self.root / APP)
        self.assertFalse((self.root / "data/.hunt-desk-build").exists())
        self.assertTrue(current(self.root))

    def test_ensure_is_a_no_op_when_current(self):
        ensure(self.root, runner=self.mac, which=lambda name: None)
        self.mac.calls.clear()
        ensure(self.root, runner=self.mac, which=lambda name: None)
        self.assertEqual(self.mac.calls, [])

    def test_a_moved_repo_rebuilds(self):
        ensure(self.root, runner=self.mac, which=lambda name: None)
        moved = self.root.parent / f"{self.root.name}-moved"
        self.root.rename(moved)
        self.mac.calls.clear()
        ensure(moved, runner=self.mac, which=lambda name: None)
        self.assertEqual(self.mac.calls[0][0], "osacompile")
        self.assertIn(f"cd {shlex.quote(str(moved))} &&", (moved / "data/open-desk.command").read_text(encoding="utf-8"))

    def test_the_desk_script_falls_back_to_this_python_without_uv(self):
        ensure(self.root, runner=self.mac, which=lambda name: None)
        script = self.root / "data/open-desk.command"
        self.assertIn(f"{shlex.quote(sys.executable)} -m hunt", script.read_text(encoding="utf-8"))
        self.assertTrue(script.stat().st_mode & 0o100)

    def test_a_current_desk_script_is_left_alone(self):
        script = self.root / "data/open-desk.command"
        script.write_text(f"#!/bin/sh\ncd {shlex.quote(str(self.root))} && /opt/homebrew/bin/uv run python -m hunt\n", encoding="utf-8")
        ensure(self.root, runner=self.mac, which=lambda name: None)
        self.assertIn("/opt/homebrew/bin/uv run", script.read_text(encoding="utf-8"))

    def test_a_root_with_a_space_and_accent_is_quoted(self):
        root = _root("Dé v")
        ensure(root, runner=self.mac, which=lambda name: "/opt/homebrew/bin/uv")
        self.assertIn(f"cd {shlex.quote(str(root))} && /opt/homebrew/bin/uv run python -m hunt", (root / "data/open-desk.command").read_text(encoding="utf-8"))

    def test_a_failed_codesign_keeps_the_old_app(self):
        old = self.root / APP / "Contents/Info.plist"
        old.parent.mkdir(parents=True)
        old.write_bytes(plistlib.dumps({"CFBundleName": "old"}))
        with self.assertRaisesRegex(RuntimeError, "codesign could not build Hunt Desk.app"):
            ensure(self.root, runner=FakeMac(fail=("codesign",)), which=lambda name: None)
        self.assertEqual(plistlib.loads(old.read_bytes()), {"CFBundleName": "old"})
        self.assertFalse((self.root / "data/.hunt-desk-build").exists())

    def test_a_failed_osacompile_names_the_tool(self):
        with self.assertRaisesRegex(RuntimeError, "osacompile"):
            ensure(self.root, runner=FakeMac(fail=("osacompile",)), which=lambda name: None)
        self.assertFalse((self.root / APP).exists())

    def test_the_script_has_no_baked_path_and_is_ascii(self):
        self.assertTrue(source().isascii())
        self.assertIn('system attribute "HUNT_POST"', source())
        self.assertIn("open-desk.command", source())

    @unittest.skipUnless(shutil.which("osacompile"), "needs osacompile")
    def test_the_script_compiles(self):
        work = Path(tempfile.mkdtemp())
        (work / "s.applescript").write_text(source(), encoding="utf-8")
        done = subprocess.run(["osacompile", "-o", str(work / "S.app"), str(work / "s.applescript")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
