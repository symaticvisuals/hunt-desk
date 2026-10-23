import base64
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.macapp import NOTE
from hunt.notify import Posted, message, post, send
from hunt.system import digest
from hunt.test_macapp import FakeMac
from hunt.test_winclick import FakeRegistry


class MacRunner(FakeMac):
    """FakeMac that also answers `open`, keeping the note each app instance would read."""

    def __init__(self, root: Path, fail: tuple[str, ...] = (), timeout: bool = False) -> None:
        super().__init__(fail)
        self.root = root
        self.notes = []
        self.timeout = timeout

    def __call__(self, args, **kwargs):
        if args[0] == "open":
            if self.timeout:
                self.calls.append(list(args))
                raise subprocess.TimeoutExpired(args, 15)
            self.notes.append((self.root / NOTE).read_text(encoding="utf-8"))
        return super().__call__(args, **kwargs)


class Recorder:
    def __init__(self, code: int = 0) -> None:
        self.calls = []
        self.kwargs = []
        self.code = code

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        code = self.code

        class Done:
            returncode = code
            stdout = ""
            stderr = "first line\nShow failed: no desktop" if code else ""

        return Done()


class MessageTests(unittest.TestCase):
    def test_message_names_the_first_role(self):
        jobs = [{"company": "Cisco", "title": "Forward Deployed Engineer"}, {"company": "Acme", "title": "AI Engineer"}, {"company": "B", "title": "C"}]
        self.assertEqual(message(jobs[:1])[1], "1 new role worth a look · Cisco — Forward Deployed Engineer")
        self.assertEqual(message(jobs)[1], "3 new roles worth a look · Cisco — Forward Deployed Engineer, +2")


@unittest.skipIf(sys.platform == "win32", "Hunt Desk.app is macOS only")
class MacBannerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()
        self.mac = MacRunner(self.root)
        patches = [patch("hunt.system.is_mac", return_value=True), patch("hunt.system.is_windows", return_value=False)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_a_banner_goes_through_hunt_desk_with_utf8_text(self):
        posted = send(self.root, [{"company": "AT&T", "title": "Engineer — Platform"}], runner=self.mac, which=lambda n: None)
        self.assertEqual(posted, Posted(True))
        open_call = self.mac.calls[-1]
        self.assertEqual(open_call[:7], ["open", "-W", "-g", "-n", "--env", "HUNT_POST=1", "-a"])
        self.assertEqual(open_call[7], str(self.root / "data/Hunt Desk.app"))
        self.assertEqual(self.mac.notes[-1], "Job hunt\n1 new role worth a look · AT&T — Engineer — Platform\n")

    def test_a_newline_in_the_body_stays_on_one_line(self):
        post(self.root, "line one\nline two", runner=self.mac, which=lambda n: None)
        self.assertEqual(self.mac.notes[-1].splitlines(), ["Job hunt", "line one line two"])

    def test_two_posts_each_hand_over_their_own_note(self):
        post(self.root, "roles", runner=self.mac, which=lambda n: None)
        post(self.root, "batch done", runner=self.mac, which=lambda n: None)
        self.assertEqual([note.splitlines()[1] for note in self.mac.notes], ["roles", "batch done"])

    def test_an_old_install_gets_the_app_on_the_next_banner(self):
        (self.root / "data/open-desk.command").write_text("#!/bin/sh\n", encoding="utf-8")
        post(self.root, "x", runner=self.mac, which=lambda n: None)
        self.assertTrue((self.root / "data/Hunt Desk.app/Contents/Info.plist").exists())

    def test_a_failed_build_falls_back_to_osascript(self):
        runner = MacRunner(self.root, fail=("osacompile",))
        posted = post(self.root, 'Say "Hi"', runner=runner, which=lambda n: None)
        self.assertFalse(posted.ok)
        self.assertIn("osacompile", posted.reason)
        self.assertEqual(runner.calls[-1][:2], ["osascript", "-e"])
        self.assertIn('Say \\"Hi\\"', runner.calls[-1][2])

    def test_a_timeout_does_not_raise(self):
        runner = MacRunner(self.root, timeout=True)
        posted = post(self.root, "x", runner=runner, which=lambda n: None)
        self.assertFalse(posted.ok)
        self.assertEqual(runner.calls[-1][:2], ["osascript", "-e"])

    def test_no_jobs_posts_nothing(self):
        self.assertEqual(send(self.root, [], runner=self.mac, which=lambda n: None), Posted(True))
        self.assertEqual(self.mac.calls, [])


class WindowsToastTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()
        patches = [patch("hunt.system.is_mac", return_value=False), patch("hunt.system.is_windows", return_value=True)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_the_toast_escapes_text_and_links_to_the_desk(self):
        runner = Recorder()
        posted = post(self.root, "AT&T <Acme> O'Reilly \"x\" 🚀", runner=runner, registry=FakeRegistry(), which=lambda n: None)
        self.assertTrue(posted.ok)
        self.assertEqual(runner.calls[0][0], "powershell.exe")
        script = base64.b64decode(runner.calls[0][-1]).decode("utf-16-le")
        self.assertIn("AT&amp;T &lt;Acme&gt; O''Reilly &quot;x&quot; 🚀", script)
        self.assertIn(f'launch="hunt-desk-{digest(self.root)}:open"', script)
        self.assertIn("$ErrorActionPreference = 'Stop'", script)
        self.assertIn("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe", script)
        self.assertEqual(runner.kwargs[0]["creationflags"], 0x08000000)
        self.assertEqual(runner.kwargs[0]["encoding"], "utf-8")

    def test_the_toast_registers_the_click_target(self):
        registry = FakeRegistry()
        post(self.root, "x", runner=Recorder(), registry=registry, which=lambda n: None)
        self.assertTrue((self.root / "data/open-desk.cmd").exists())
        self.assertTrue(registry.values)

    def test_a_failed_toast_reports_why(self):
        posted = post(self.root, "x", runner=Recorder(code=1), registry=FakeRegistry(), which=lambda n: None)
        self.assertEqual(posted, Posted(False, "Show failed: no desktop"))

    def test_a_failure_reason_skips_powershell_clixml(self):
        runner = Recorder(code=1)
        runner_call = runner.__call__

        def noisy(args, **kwargs):
            done = runner_call(args, **kwargs)
            done.stderr = '#< CLIXML\r\n<Objs Version="1.1.0.1"><Obj S="progress"/></Objs>\r\nElement not found.\r\n<Objs Version="1.1.0.1"></Objs>'
            return done

        posted = post(self.root, "x", runner=noisy, registry=FakeRegistry(), which=lambda n: None)
        self.assertEqual(posted, Posted(False, "Element not found."))
        script = base64.b64decode(runner.calls[0][-1]).decode("utf-16-le")
        self.assertIn("[Console]::Error.WriteLine($_.Exception.Message)", script)

    def test_a_registry_failure_still_shows_the_toast(self):
        registry = FakeRegistry()

        def refuse(key, name, value):
            raise PermissionError(5, "denied")

        registry.set = refuse
        runner = Recorder()
        self.assertTrue(post(self.root, "x", runner=runner, registry=registry, which=lambda n: None).ok)
        self.assertEqual(len(runner.calls), 1)


class OtherPlatformTests(unittest.TestCase):
    def test_other_platforms_say_so(self):
        with patch("hunt.system.is_mac", return_value=False), patch("hunt.system.is_windows", return_value=False):
            posted = post(Path(tempfile.mkdtemp()), "x", runner=FakeMac())
        self.assertEqual(posted, Posted(False, "Notifications run on macOS and Windows only."))
