import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from hunt.launchd import build_plist, install, label, uninstall
from hunt.test_macapp import FakeMac
from hunt.notify import message, send


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


class NotifyTests(unittest.TestCase):
    def test_message_names_the_first_role(self):
        jobs = [{"company": "Cisco", "title": "Forward Deployed Engineer"}, {"company": "Acme", "title": "AI Engineer"}, {"company": "B", "title": "C"}]
        self.assertEqual(message(jobs[:1])[1], "1 new role worth a look · Cisco — Forward Deployed Engineer")
        self.assertEqual(message(jobs)[1], "3 new roles worth a look · Cisco — Forward Deployed Engineer, +2")

    def test_terminal_notifier_opens_the_desk_when_present(self):
        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        (root / "data/open-desk.command").write_text("#!/bin/sh\n")
        runner = Recorder()
        send(root, [{"company": "Cisco", "title": "FDE"}], runner=runner, which=lambda name: "/opt/homebrew/bin/terminal-notifier")
        call = runner.calls[0]
        self.assertEqual(call[0], "/opt/homebrew/bin/terminal-notifier")
        self.assertIn("-execute", call)

    def test_osascript_is_the_fallback(self):
        runner = Recorder()
        send(Path(tempfile.mkdtemp()), [{"company": 'Say "Hi"', "title": "FDE"}], runner=runner, which=lambda name: None)
        call = runner.calls[0]
        self.assertEqual(call[:2], ["osascript", "-e"])
        self.assertIn('Say \\"Hi\\"', call[2])
