import ast
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.launchd import label
from hunt.system import digest, no_window, open_path, powershell

PACKAGE = Path(__file__).resolve().parent


class Recorder:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))


def _modules() -> list[Path]:
    return sorted(path for path in PACKAGE.glob("*.py") if not path.name.startswith("test_"))


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _text_mode(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else _keywords(call).get("mode")
    if mode is None:
        return True
    return not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value)


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_digest_matches_the_launchd_label(self):
        self.assertEqual(label(self.root), f"com.hunt.tick.{digest(self.root)}")
        self.assertEqual(len(digest(self.root)), 8)

    def test_no_window_is_empty_off_windows_and_set_on_windows(self):
        with patch("hunt.system.is_windows", return_value=False):
            self.assertEqual(no_window(), {})
        with patch("hunt.system.is_windows", return_value=True):
            self.assertEqual(no_window(), {"creationflags": 0x08000000})

    def test_powershell_encodes_the_script_as_utf16(self):
        command = powershell("Write-Output 'é'", sta=True)
        self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA"])
        self.assertEqual(command[-2], "-EncodedCommand")
        self.assertEqual(base64.b64decode(command[-1]).decode("utf-16-le"), "Write-Output 'é'")
        self.assertNotIn("-STA", powershell("x"))

    def test_open_path_uses_open_on_a_mac(self):
        runner = Recorder()
        with patch("hunt.system.is_windows", return_value=False), patch("hunt.system.is_mac", return_value=True):
            open_path(Path("/tmp/r.pdf"), runner=runner)
        self.assertEqual(runner.calls, [["open", "/tmp/r.pdf"]])

    def test_open_path_uses_startfile_on_windows(self):
        opened = []
        with patch("hunt.system.is_windows", return_value=True), patch("hunt.system._startfile", side_effect=opened.append):
            open_path(Path("C:/r.pdf"), runner=Recorder())
        self.assertEqual(opened, [str(Path("C:/r.pdf"))])

    def test_every_text_subprocess_call_pins_utf8(self):
        offenders = []
        for path in _modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and _is_true(_keywords(node).get("text")) and "encoding" not in _keywords(node):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_every_text_file_call_pins_an_encoding(self):
        offenders = []
        for path in _modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or "encoding" in _keywords(node):
                    continue
                builtin_open = isinstance(node.func, ast.Name) and node.func.id == "open" and _text_mode(node)
                path_text = isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "write_text"}
                if builtin_open or path_text:
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])
