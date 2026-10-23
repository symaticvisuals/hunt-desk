import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.macapp import NOTE
from hunt.notify import Posted, message, post, send
from hunt.test_macapp import FakeMac


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


class OtherPlatformTests(unittest.TestCase):
    def test_other_platforms_say_so(self):
        with patch("hunt.system.is_mac", return_value=False), patch("hunt.system.is_windows", return_value=False):
            posted = post(Path(tempfile.mkdtemp()), "x", runner=FakeMac())
        self.assertEqual(posted, Posted(False, "Notifications run on macOS and Windows only."))
