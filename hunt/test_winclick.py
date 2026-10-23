import tempfile
import unittest
from pathlib import Path

from hunt.system import digest
from hunt.winclick import desk_cmd, ensure, protocol_command, remove, scheme


class FakeRegistry:
    """HKEY_CURRENT_USER as a dict keyed by (key, value name)."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, key: str, name: str) -> str | None:
        return self.values.get((key, name))

    def set(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def delete_tree(self, key: str) -> None:
        for stored in [stored for stored in self.values if stored[0] == key or stored[0].startswith(key + "\\")]:
            del self.values[stored]


def _root(name: str = "hunt") -> Path:
    root = Path(tempfile.mkdtemp()) / name
    (root / "data").mkdir(parents=True)
    return root


class WinClickTests(unittest.TestCase):
    def setUp(self):
        self.root = _root()
        self.reg = FakeRegistry()

    def test_ensure_registers_the_protocol_and_writes_the_desk_script(self):
        link = ensure(self.root, which=lambda n: r"C:\Users\x\.local\bin\uv.exe", registry=self.reg)
        self.assertEqual(link, f"hunt-desk-{digest(self.root)}")
        self.assertEqual(self.reg.get(f"Software\\Classes\\{link}", ""), "URL:Hunt Desk")
        self.assertEqual(self.reg.get(f"Software\\Classes\\{link}", "URL Protocol"), "")
        self.assertEqual(self.reg.get(f"Software\\Classes\\{link}\\shell\\open\\command", ""), protocol_command(self.root))
        body = (self.root / "data/open-desk.cmd").read_bytes().decode("utf-8")
        self.assertIn('"C:\\Users\\x\\.local\\bin\\uv.exe" run python -m hunt\r\n', body)
        self.assertIn(f'cd /d "{self.root}"\r\n', body)
        self.assertIn("chcp 65001 >nul\r\n", body)
        self.assertIn("set PYTHONUTF8=1\r\n", body)
        self.assertNotIn("\n", body.replace("\r\n", ""))

    def test_without_uv_the_desk_runs_on_the_venv_python(self):
        self.assertIn(f'"{self.root / ".venv" / "Scripts" / "python.exe"}" -m hunt', desk_cmd(self.root, None))

    def test_a_root_with_a_space_and_accent_is_quoted(self):
        root = _root("José Projects")
        command = protocol_command(root)
        self.assertTrue(command.endswith(f'/c "{root / "data" / "open-desk.cmd"}"'))
        self.assertTrue(command.startswith('"'))
        ensure(root, which=lambda n: None, registry=self.reg)
        self.assertIn(f'cd /d "{root}"', (root / "data/open-desk.cmd").read_bytes().decode("utf-8"))

    def test_ensure_leaves_matching_values_alone(self):
        ensure(self.root, which=lambda n: None, registry=self.reg)
        written = []
        self.reg.set = lambda key, name, value: written.append(key)
        ensure(self.root, which=lambda n: None, registry=self.reg)
        self.assertEqual(written, [])

    def test_remove_deletes_the_protocol_tree(self):
        ensure(self.root, which=lambda n: None, registry=self.reg)
        self.reg.set("Software\\Classes\\other", "", "keep")
        remove(self.root, registry=self.reg)
        self.assertEqual(self.reg.values, {("Software\\Classes\\other", ""): "keep"})

    def test_the_scheme_is_a_valid_url_scheme(self):
        self.assertRegex(scheme(self.root), r"^[a-z][a-z0-9+.-]*$")
