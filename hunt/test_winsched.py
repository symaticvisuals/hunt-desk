import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from hunt.system import digest
from hunt.test_winclick import FakeRegistry
from hunt.winsched import NS, build_xml, install, installed, task_name, uninstall


class Recorder:
    """Answers schtasks. On /Create it keeps the XML file's bytes, which install deletes afterwards."""

    def __init__(self, code: int = 0) -> None:
        self.calls = []
        self.kwargs = []
        self.code = code
        self.xml_bytes = b""

    def __call__(self, args, **kwargs):
        args = [str(arg) for arg in args]
        self.calls.append(args)
        self.kwargs.append(kwargs)
        if "/XML" in args:
            self.xml_bytes = Path(args[args.index("/XML") + 1]).read_bytes()
        code = self.code

        class Done:
            returncode = code
            stdout = ""
            stderr = "ERROR: Access is denied." if code else ""

        return Done()


def _root(name: str = "hunt") -> Path:
    root = Path(tempfile.mkdtemp()) / name
    (root / "data").mkdir(parents=True)
    return root


def _venv(root: Path) -> None:
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/Scripts/pythonw.exe").write_bytes(b"")


class TaskSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.root = _root()

    def test_the_task_runs_tick_every_fifteen_minutes_on_battery_without_a_console(self):
        xml = build_xml(self.root, r"C:\r\.venv\Scripts\pythonw.exe", datetime(2026, 10, 23, 9, 30))
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-16"?>'))
        tree = ET.fromstring(xml.split("?>", 1)[1])
        ns = {"t": NS}
        self.assertEqual(tree.find(".//t:Repetition/t:Interval", ns).text, "PT15M")
        self.assertEqual(tree.find(".//t:Repetition/t:StopAtDurationEnd", ns).text, "false")
        self.assertEqual(tree.find(".//t:StartBoundary", ns).text, "2026-10-23T09:30:00")
        self.assertEqual(tree.find(".//t:DisallowStartIfOnBatteries", ns).text, "false")
        self.assertEqual(tree.find(".//t:StopIfGoingOnBatteries", ns).text, "false")
        self.assertEqual(tree.find(".//t:StartWhenAvailable", ns).text, "true")
        self.assertEqual(tree.find(".//t:MultipleInstancesPolicy", ns).text, "IgnoreNew")
        self.assertEqual(tree.find(".//t:ExecutionTimeLimit", ns).text, "PT2H")
        self.assertEqual(tree.find(".//t:LogonType", ns).text, "InteractiveToken")
        self.assertEqual(tree.find(".//t:RunLevel", ns).text, "LeastPrivilege")
        self.assertEqual(tree.find(".//t:Exec/t:Command", ns).text, r"C:\r\.venv\Scripts\pythonw.exe")
        self.assertEqual(tree.find(".//t:Exec/t:Arguments", ns).text, "-X utf8 -m hunt tick")
        self.assertEqual(tree.find(".//t:Exec/t:WorkingDirectory", ns).text, str(self.root))
        self.assertNotIn("ns0:", xml)

    def test_install_creates_the_task_from_utf16_xml_and_registers_the_click(self):
        _venv(self.root)
        runner = Recorder()
        note = install(self.root, runner=runner, which=lambda n: None, registry=FakeRegistry(), now=datetime(2026, 10, 23, 9, 30))
        self.assertEqual(runner.calls[0][:4], ["schtasks", "/Create", "/TN", f"hunt-tick-{digest(self.root)}"])
        self.assertEqual(runner.calls[0][-1], "/F")
        self.assertTrue(runner.xml_bytes.startswith(b"\xff\xfe"))
        self.assertIn("<Interval>PT15M</Interval>", runner.xml_bytes.decode("utf-16"))
        self.assertEqual(runner.kwargs[0]["encoding"], "utf-8")
        self.assertIn("every 15 minutes", note)
        self.assertTrue((self.root / "data/open-desk.cmd").exists())
        self.assertFalse(Path(runner.calls[0][runner.calls[0].index("/XML") + 1]).exists())

    def test_install_without_a_venv_raises_and_creates_nothing(self):
        runner = Recorder()
        with self.assertRaisesRegex(RuntimeError, "start.cmd"):
            install(self.root, runner=runner, which=lambda n: None, registry=FakeRegistry())
        self.assertEqual(runner.calls, [])

    def test_a_schtasks_failure_raises(self):
        _venv(self.root)
        registry = FakeRegistry()
        with self.assertRaisesRegex(RuntimeError, "Task Scheduler could not add the schedule: ERROR: Access is denied."):
            install(self.root, runner=Recorder(code=1), which=lambda n: None, registry=registry)
        self.assertEqual(registry.values, {})

    def test_uninstall_deletes_the_task_and_the_protocol(self):
        _venv(self.root)
        registry = FakeRegistry()
        install(self.root, runner=Recorder(), which=lambda n: None, registry=registry)
        runner = Recorder()
        self.assertEqual(uninstall(self.root, runner=runner, registry=registry), "The schedule is off.")
        self.assertEqual(runner.calls[0], ["schtasks", "/Delete", "/TN", task_name(self.root), "/F"])
        self.assertEqual(registry.values, {})
        self.assertEqual(uninstall(self.root, runner=Recorder(code=1), registry=registry), "The schedule was not installed.")

    def test_a_registry_error_does_not_fail_the_schedule(self):
        _venv(self.root)
        registry = FakeRegistry()

        def refuse(*args):
            raise PermissionError(5, "Access is denied")

        registry.set = refuse
        registry.delete_tree = refuse
        self.assertIn("every 15 minutes", install(self.root, runner=Recorder(), which=lambda n: None, registry=registry))
        self.assertEqual(uninstall(self.root, runner=Recorder(), registry=registry), "The schedule is off.")

    def test_installed_asks_task_scheduler(self):
        runner = Recorder()
        self.assertTrue(installed(self.root, runner=runner))
        self.assertEqual(runner.calls[0], ["schtasks", "/Query", "/TN", task_name(self.root)])
        self.assertFalse(installed(self.root, runner=Recorder(code=1)))

    def test_an_accented_root_survives_the_xml(self):
        root = _root("José Projects")
        xml = build_xml(root, str(root / ".venv/Scripts/pythonw.exe"), datetime(2026, 10, 23))
        self.assertIn(str(root), xml)
        self.assertEqual(ET.fromstring(xml.split("?>", 1)[1]).find(".//t:WorkingDirectory", {"t": NS}).text, str(root))
