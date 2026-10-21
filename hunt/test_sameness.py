import io
import tempfile
import unittest
import json
from contextlib import redirect_stdout
from pathlib import Path

from hunt.cli import cmd_sameness
from hunt.sameness import measure, report

BILLING = """# Ada Lovelace

## Experience

- Shipped the billing service in Python and Postgres for twelve regional banks.
- Cut the nightly reconciliation job from six hours to twenty minutes.
- Mentored four engineers through their first production on-call rotation.
"""

BILLING_REORDERED = """# Ada Lovelace

## Professional experience

- Reduced the nightly reconciliation job from six hours to twenty minutes.
- Mentored four engineers through their first production on-call rotation.
- Shipped the Python and Postgres billing service used by twelve regional banks.
"""

COMPILER = """# Ada Lovelace

## Experience

- Wrote a compiler for a loom pattern language with a custom type checker.
- Published three papers on analytical engine programs and their notation.
- Taught weekly workshops on mechanical computation for London students.
"""


def _root(resumes: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for folder, text in resumes.items():
        path = root / "resumes" / folder / "resume.md"
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
    return root


class SamenessTests(unittest.TestCase):
    def test_the_same_lines_reworded_and_reordered_count_as_alike(self):
        found = measure(_root({"acme/engineer": BILLING, "globex/engineer": BILLING_REORDERED}))
        self.assertEqual(len(found.pairs), 1)
        self.assertGreaterEqual(found.pairs[0].shared, 0.9)

    def test_resumes_built_from_different_lines_are_not_alike(self):
        found = measure(_root({"acme/engineer": BILLING, "initech/researcher": COMPILER}))
        self.assertLessEqual(found.pairs[0].shared, 0.1)

    def test_the_report_puts_the_most_alike_pair_first(self):
        root = _root({"acme/engineer": BILLING, "globex/engineer": BILLING_REORDERED, "initech/researcher": COMPILER})
        text = report(measure(root))
        self.assertIn("3 tailored resumes", text)
        most = text.index("acme/engineer and globex/engineer")
        self.assertLess(most, text.index("initech/researcher"))

    def test_resumes_that_repeat_most_lines_are_called_out(self):
        text = report(measure(_root({"acme/engineer": BILLING, "globex/engineer": BILLING_REORDERED})))
        self.assertIn("mostly repeat the same lines", text)

    def test_json_resumes_are_read_too(self):
        root = _root({"acme/engineer": BILLING})
        bullets = [line[2:] for line in BILLING_REORDERED.splitlines() if line.startswith("- ")]
        resume = {"header": {"name": "Ada Lovelace"}, "experience": [{"company": "Beta", "title": "Engineer", "bullets": [{"text": text, "priority": 1} for text in bullets]}]}
        path = root / "resumes/beta/engineer/resume.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(resume), encoding="utf-8")
        found = measure(root)
        self.assertEqual(found.resumes, ["acme/engineer", "beta/engineer"])
        self.assertEqual(len(found.pairs), 1)
        self.assertGreater(found.pairs[0].shared, 0.5)

    def test_fewer_than_two_resumes_have_nothing_to_compare(self):
        text = report(measure(_root({"acme/engineer": BILLING})))
        self.assertIn("Nothing to compare", text)

    def test_the_command_prints_the_report(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd_sameness(_root({"acme/engineer": BILLING, "globex/engineer": BILLING_REORDERED}))
        self.assertEqual(code, 0)
        self.assertIn("2 tailored resumes", out.getvalue())


if __name__ == "__main__":
    unittest.main()
