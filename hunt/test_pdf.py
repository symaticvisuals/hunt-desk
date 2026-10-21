import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.pdf import ENGINES, Compiled, NoTeX, compile_tex, find_tex, fit_one_page

SAMPLE = Path(__file__).resolve().parent / "testdata/sample-resume.json"


def _resume() -> dict:
    resume = json.loads(SAMPLE.read_text(encoding="utf-8"))
    resume["projects"].append({"name": "Pricing simulator 2", "stack": "", "dates": "2024", "priority": 2, "bullets": []})
    return resume


def _bullets(resume: dict) -> int:
    return sum(len(entry["bullets"]) for entry in resume["experience"])


def _pages_while(predicate):
    def compile(tex: str) -> Compiled:
        return Compiled(ok=True, pages=2 if predicate(json.loads(tex)) else 1, pdf=b"%PDF")

    return compile


class EngineTests(unittest.TestCase):
    def test_the_engine_is_found_outside_launchd_path(self):
        folder = Path(tempfile.mkdtemp())
        fake = folder / ENGINES[0]
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o755)
        with patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}):
            self.assertEqual(find_tex([folder]), (ENGINES[0], str(fake)))
            self.assertIsNone(find_tex([Path(tempfile.mkdtemp())]))

    def test_no_engine_raises_notex(self):
        with patch("hunt.pdf.find_tex", return_value=None), self.assertRaises(NoTeX):
            compile_tex("x")


class FitTests(unittest.TestCase):
    def test_the_least_important_bullet_goes_first_and_ties_take_the_later_entry(self):
        resume = _resume()
        acme = [bullet["text"] for bullet in resume["experience"][0]["bullets"]]
        beta = [bullet["text"] for bullet in resume["experience"][1]["bullets"]]
        fitted = fit_one_page(resume, compile=_pages_while(lambda r: _bullets(r) > 5), render=json.dumps)
        self.assertEqual(fitted.cuts, ["Acme & Co: " + acme[3], "Beta Labs: " + beta[2]])
        self.assertFalse(fitted.over)
        self.assertEqual(_bullets(resume), 7)

    def test_projects_go_once_every_entry_is_at_two_bullets(self):
        fitted = fit_one_page(_resume(), compile=_pages_while(lambda r: bool(r["projects"]) or _bullets(r) > 4), render=json.dumps)
        self.assertEqual(fitted.cuts[-2:], ["project: Pricing simulator 2", "project: Pricing simulator"])
        self.assertEqual(fitted.resume["projects"], [])
        self.assertEqual([len(entry["bullets"]) for entry in fitted.resume["experience"]], [2, 2])

    def test_still_over_one_page_keeps_the_last_pdf(self):
        fitted = fit_one_page(_resume(), compile=_pages_while(lambda r: True), render=json.dumps)
        self.assertTrue(fitted.over)
        self.assertEqual(fitted.compiled.pages, 2)


if __name__ == "__main__":
    unittest.main()
