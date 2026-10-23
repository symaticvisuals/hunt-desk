import base64
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from hunt.onboard import _textutil, apply_setup, infer, pick_resume_file


RESUME = """
# Ada Lovelace

## Experience

### Forward Deployed Engineer, Analytical Engines
London. 2024 — now
- Shipped production systems with customer teams.

### Software Engineer, Difference Engine
London. 2021 — 2024
- Built full stack tools for operators.

## Education
B.Sc. Mathematics. 2016 — 2020

## Skills
Python, applied AI, PostgreSQL
"""


class OnboardTests(unittest.TestCase):
    def test_infer_uses_roles_and_ignores_school_dates(self):
        found = infer(RESUME, today=date(2026, 10, 15))
        self.assertIn("forward deployed", found.keywords)
        self.assertIn("software engineer", found.keywords)
        self.assertIn("applied ai", found.keywords)
        self.assertIn("full stack", found.keywords)
        self.assertEqual(found.years_cap, 7)
        self.assertTrue(any("Forward Deployed" in role for role in found.roles))

    def test_a_product_resume_gives_product_titles_and_years(self):
        found = infer(
            "Jane Doe\nSenior Product Manager | Acme\n2019 – Present\nProduct Manager, Beta Corp\n2016 - 2019\n",
            today=date(2026, 10, 21),
        )
        self.assertIn("product manager", found.keywords)
        self.assertNotIn("software engineer", found.keywords)
        self.assertEqual(found.years_cap, 12)

    def test_date_ranges_with_month_names_count(self):
        text = (
            "Senior Product Manager | Finbox | Bengaluru\nJan 2022 – Present\n"
            "Product Manager | Razorpay | Bengaluru\nJul 2019 – Dec 2021\n"
            "Associate Product Manager | Swiggy | Bengaluru\nJun 2017 – Jun 2019\n"
        )
        self.assertEqual(infer(text, today=date(2026, 10, 21)).years_cap, 11)

    def test_only_an_engineering_word_brings_the_software_engineer_fallback(self):
        self.assertEqual(infer("Notes\n" + "Manager " * 20).keywords, [])
        self.assertEqual(infer("Notes\n" + "Engineer " * 20).keywords, ["software engineer"])

    def test_apply_setup_rewrites_keywords_and_keeps_sources(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "data").mkdir()
        (root / "profile/search.json").write_text(
            json.dumps({"keywords": ["accountant"], "exclude_title": ["intern"], "location": {"mode": "any"}, "sources": ["greenhouse"]}),
            encoding="utf-8",
        )
        (root / "profile/rubric.json").write_text(json.dumps({"natural_hire_at": 80, "comp": {"min": None}}), encoding="utf-8")
        found = infer(RESUME, today=date(2026, 10, 15))
        apply_setup(root, RESUME, found)
        search = json.loads((root / "profile/search.json").read_text(encoding="utf-8"))
        rubric = json.loads((root / "profile/rubric.json").read_text(encoding="utf-8"))
        self.assertEqual(search["sources"], ["greenhouse"])
        self.assertIn("software engineer", search["keywords"])
        self.assertNotIn("accountant", search["keywords"])
        self.assertEqual(rubric["max_years_without_cap"], 7)
        self.assertIn("Forward Deployed Engineer", (root / "profile/og-resume.md").read_text(encoding="utf-8"))


class Recorder:
    def __init__(self, stdout: str = "", code: int = 0) -> None:
        self.calls = []
        self.kwargs = []
        self.stdout = stdout
        self.code = code

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.kwargs.append(kwargs)
        out, code = self.stdout, self.code

        class Done:
            returncode = code
            stdout = out
            stderr = ""

        return Done()


class WindowsResumeTests(unittest.TestCase):
    def test_the_windows_picker_returns_the_chosen_path(self):
        runner = Recorder(stdout="C:\\Users\\José\\resume.pdf\r\n")
        with patch("hunt.system.is_windows", return_value=True):
            self.assertEqual(pick_resume_file(runner=runner), "C:\\Users\\José\\resume.pdf")
        self.assertEqual(runner.calls[0][0], "powershell.exe")
        self.assertIn("-STA", runner.calls[0])
        script = base64.b64decode(runner.calls[0][-1]).decode("utf-16-le")
        self.assertIn("OpenFileDialog", script)
        self.assertIn("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8", script)
        self.assertEqual(runner.kwargs[0]["encoding"], "utf-8")
        self.assertEqual(runner.kwargs[0]["creationflags"], 0x08000000)

    def test_a_localized_windows_picker_cannot_raise(self):
        runner = Recorder(stdout="")
        with patch("hunt.system.is_windows", return_value=True):
            pick_resume_file(runner=runner)
        self.assertEqual(runner.kwargs[0]["errors"], "replace")
        self.assertIn("$ProgressPreference = 'SilentlyContinue'", base64.b64decode(runner.calls[0][-1]).decode("utf-16-le"))

    def test_a_cancelled_windows_picker_returns_none(self):
        with patch("hunt.system.is_windows", return_value=True):
            self.assertIsNone(pick_resume_file(runner=Recorder(stdout="")))
            self.assertIsNone(pick_resume_file(runner=Recorder(stdout="C:\\x.pdf", code=1)))

    def test_the_mac_picker_still_uses_applescript(self):
        runner = Recorder(stdout="/Users/x/resume.pdf\n")
        with patch("hunt.system.is_windows", return_value=False):
            self.assertEqual(pick_resume_file(runner=runner), "/Users/x/resume.pdf")
        self.assertEqual(runner.calls[0][0], "osascript")

    def test_a_docx_on_windows_says_how_to_load_it(self):
        with patch("hunt.system.is_windows", return_value=True), self.assertRaisesRegex(RuntimeError, "PDF or Markdown"):
            _textutil(Path("resume.docx"))
