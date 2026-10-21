import json
import re
import unittest
from pathlib import Path

from hunt.latex import escape, escape_url, preamble, present_sections, render

SAMPLE = Path(__file__).resolve().parent / "testdata/sample-resume.json"


def _sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _body(resume: dict) -> str:
    return render(resume).split("\\begin{document}", 1)[1]


class EscapeTests(unittest.TestCase):
    def test_unsettable_characters_are_mapped_or_dropped(self):
        self.assertEqual(escape("Grew ARR 3× → $2M — fast 😀"), r"Grew ARR 3x to \$2M, fast")
        self.assertEqual(escape("“one-tap” user’s 漢字 flow"), "``one-tap'' user's flow")

    def test_double_quotes_open_and_close(self):
        self.assertEqual(escape('Wrote PRDs for the "one-tap" flow'), "Wrote PRDs for the ``one-tap'' flow")
        self.assertEqual(escape('("Beta") launch'), "(``Beta'') launch")

    def test_latin_extended_names_survive_and_control_characters_do_not(self):
        self.assertEqual(escape("Łukasz Dvořák · İstanbul · Nguyễn · Şahin"), "Łukasz Dvořák · İstanbul · Nguyễn · Şahin")
        self.assertEqual(escape("Jose\u0301"), "José")
        self.assertEqual(escape("a\x85b"), "ab")

    def test_latin_extended_names_build_into_the_pdf(self):
        from hunt.ats import pdf_text
        from hunt.pdf import compile_tex, find_tex

        if find_tex() is None:
            self.skipTest("no TeX engine on this machine")
        resume = {
            "candidate_level": "entry",
            "header": {"name": "Łukasz Dvořák", "email": "l@x.io", "phone": "+48 600 100 200 3"},
            "experience": [{"company": "Nguyễn & Şahin", "title": "Analyst", "dates": "2024", "bullets": [{"text": "Built İstanbul reports.", "priority": 1}]}],
        }
        compiled = compile_tex(render(resume))
        self.assertTrue(compiled.ok, compiled.log_tail)
        text = pdf_text(compiled.pdf)
        for word in ("Łukasz", "Dvořák", "Şahin", "İstanbul"):
            self.assertIn(word, text)

    def test_specials_are_escaped_in_text_and_urls(self):
        self.assertEqual(escape("R&D 100% #1 a_b {x} ~ ^"), r"R\&D 100\% \#1 a\_b \{x\} \textasciitilde{} \textasciicircum{}")
        self.assertEqual(escape("C:\\temp"), r"C:\textbackslash{}temp")
        self.assertEqual(escape_url("https://priya.dev/#work?a=1%"), r"https://priya.dev/\#work?a=1\%")
        self.assertEqual(escape_url("https://x.io/a{b}\\c#d%"), r"https://x.io/a\%7Bb\%7D\%5Cc\#d\%")


class RenderTests(unittest.TestCase):
    def test_the_preamble_guards_the_pdftex_only_lines(self):
        text = preamble()
        self.assertIn("\\ifdefined\\pdfgentounicode", text)
        self.assertNotIn("\\begin{document}", text)
        guarded = text.split("\\ifdefined\\pdfgentounicode", 1)[0]
        self.assertNotIn("\\input{glyphtounicode}", guarded)

    def test_the_sample_renders_in_mid_level_order_with_nothing_left_unescaped(self):
        resume = _sample()
        body = _body(resume)
        self.assertEqual(present_sections(resume), ["summary", "experience", "projects", "skills", "education"])
        self.assertEqual(re.findall(r"\\section\{(\w+)\}", body), ["Summary", "Experience", "Projects", "Skills", "Education"])
        text_only = re.sub(r"\\href\{[^}]*\}", "", re.sub(r"\$\|\$", "", body))
        self.assertEqual(re.findall(r"(?<!\\)[&%$#_]", text_only), [])
        self.assertEqual({ch for ch in body if ord(ch) > 127}, set())
        self.assertIn(r"\href{mailto:priya_n@example.com}{\underline{priya\_n@example.com}}", body)
        self.assertIn(r"\href{https://priya.dev/\#work?a=1\%}", body)
        self.assertIn("Jan 2022 -- Present", body)

    def test_missing_contact_and_sections_leave_no_gaps(self):
        resume = {
            "candidate_level": "entry",
            "header": {"name": "Sam", "email": "sam@x.io"},
            "experience": [{"company": "X", "title": "Analyst", "dates": "2024", "bullets": []}],
        }
        body = _body(resume)
        self.assertNotIn("$|$", body)
        self.assertEqual(re.findall(r"\\section\{(\w+)\}", body), ["Experience"])

    def test_a_new_grad_puts_education_first(self):
        resume = dict(_sample(), candidate_level="new_grad")
        self.assertEqual(present_sections(resume), ["education", "experience", "projects", "skills"])


if __name__ == "__main__":
    unittest.main()
