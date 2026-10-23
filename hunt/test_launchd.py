import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from hunt.launchd import build_plist, install, label, uninstall
from hunt.test_macapp import FakeMac


class Recorder:
    def __init__(self, code: int = 0) -> None:
        self.calls = []
        self.code = code

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))

        class Done:
            returncode = self.code
            stdout = ""
            stderr = "boom" if self.code else ""

        return Done()


@unittest.skipIf(sys.platform == "win32", "launchd is macOS only")
class LaunchdTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()
        self.home = Path(tempfile.mkdtemp())

    def test_the_plist_runs_tick_every_fifteen_minutes(self):
        plist = build_plist(self.root, "/opt/homebrew/bin/uv")
        self.assertEqual(plist["Label"], label(self.root))
        self.assertEqual(
            plist["ProgramArguments"],
            ["/opt/homebrew/bin/uv", "run", "--directory", str(self.root), "python", "-m", "hunt", "tick"],
        )
        self.assertEqual(plist["StartInterval"], 900)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["StandardOutPath"], str(self.root / "data/schedule.out"))
        self.assertIn("/opt/homebrew/bin", plist["EnvironmentVariables"]["PATH"])

    def test_install_writes_the_agent_and_the_desk_script(self):
        runner = FakeMac()
        note = install(self.root, runner=runner, which=lambda name: "/opt/homebrew/bin/uv", home=self.home, uid=501)
        path = self.home / "Library/LaunchAgents" / f"{label(self.root)}.plist"
        self.assertEqual(plistlib.loads(path.read_bytes())["StartInterval"], 900)
        self.assertIn(["launchctl", "bootstrap", "gui/501", str(path)], runner.calls)
        script = self.root / "data/open-desk.command"
        self.assertIn("/opt/homebrew/bin/uv run python -m hunt", script.read_text(encoding="utf-8"))
        self.assertTrue(script.stat().st_mode & 0o100)
        self.assertTrue((self.root / "data/Hunt Desk.app/Contents/Info.plist").exists())
        self.assertIn("every 15 minutes", note)

    def test_install_without_uv_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            install(self.root, runner=Recorder(), which=lambda name: None, home=self.home, uid=501)
        self.assertFalse((self.home / "Library/LaunchAgents").exists())

    def test_a_launchctl_failure_removes_the_plist(self):
        with self.assertRaises(RuntimeError):
            install(self.root, runner=FakeMac(fail=("launchctl",)), which=lambda name: "/usr/local/bin/uv", home=self.home, uid=501)
        self.assertEqual(list((self.home / "Library/LaunchAgents").glob("*.plist")), [])

    def test_a_failed_app_build_does_not_fail_the_install(self):
        note = install(self.root, runner=FakeMac(fail=("osacompile",)), which=lambda name: "/usr/local/bin/uv", home=self.home, uid=501)
        self.assertIn("every 15 minutes", note)
        self.assertTrue((self.root / "data/open-desk.command").exists())

    def test_uninstall_boots_out_and_deletes(self):
        install(self.root, runner=FakeMac(), which=lambda name: "/usr/local/bin/uv", home=self.home, uid=501)
        runner = Recorder()
        uninstall(self.root, runner=runner, home=self.home, uid=501)
        self.assertEqual(runner.calls[0][:2], ["launchctl", "bootout"])
        self.assertEqual(list((self.home / "Library/LaunchAgents").glob("*.plist")), [])
