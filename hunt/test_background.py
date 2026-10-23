import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.background import install, installed, uninstall
from hunt.cli import _attach_output


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()

    def test_each_platform_gets_its_backend(self):
        with patch("hunt.system.is_mac", return_value=True), patch("hunt.system.is_windows", return_value=False), patch("hunt.launchd.installed", return_value=True):
            self.assertTrue(installed(self.root))
        with patch("hunt.system.is_mac", return_value=False), patch("hunt.system.is_windows", return_value=True), patch("hunt.winsched.installed", return_value=True):
            self.assertTrue(installed(self.root))
        with patch("hunt.system.is_mac", return_value=False), patch("hunt.system.is_windows", return_value=True), patch("hunt.winsched.uninstall", return_value="The schedule is off."):
            self.assertEqual(uninstall(self.root), "The schedule is off.")

    def test_other_platforms_say_so(self):
        with patch("hunt.system.is_mac", return_value=False), patch("hunt.system.is_windows", return_value=False):
            self.assertFalse(installed(self.root))
            with self.assertRaisesRegex(RuntimeError, "Background checks run on macOS and Windows only."):
                install(self.root)
            with self.assertRaisesRegex(RuntimeError, "macOS and Windows only"):
                uninstall(self.root)

    def test_tick_output_goes_to_schedule_out_under_pythonw(self):
        with patch("sys.stdout", None), patch("sys.stderr", None):
            _attach_output(self.root)
            handle = sys.stdout
            print("hello")
            print("oops", file=sys.stderr)
            sys.stdout.flush()
        handle.close()
        text = (self.root / "data/schedule.out").read_text(encoding="utf-8")
        self.assertIn("hello", text)
        self.assertIn("oops", text)

    def test_a_console_keeps_its_output(self):
        before = sys.stdout
        _attach_output(self.root)
        self.assertIs(sys.stdout, before)
        self.assertFalse((self.root / "data/schedule.out").exists())
