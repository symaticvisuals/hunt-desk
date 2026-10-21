import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.cli import score_one
from hunt.evaluated import read_csv
from hunt.pdf import Compiled
from hunt.tailor import new_numbers, raw_prompt, rebuild_all, resume_flag, save_tailored, tailor_one, tailored_folder

JOB = {
    "company": "OpenAI",
    "title": "Forward Deployed Engineer (FDE), Financial Services",
    "url": "https://jobs.ashbyhq.com/openai/1",
    "apply_url": "https://jobs.ashbyhq.com/openai/1/application",
    "platform": "ashby",
    "job_id": "1",
    "source": "ashby",
    "location": "New York City; Remote",
    "description": "Work with banks to ship agents into production.",
}
MASTER = "# Ada\n\n- Shipped 4 multi-agent systems for 50+ customers.\n- Built the billing service.\n"


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "profile").mkdir()
    (root / "profile/og-resume.md").write_text(MASTER, encoding="utf-8")
    (root / "profile/rubric.json").write_text(json.dumps({"screenout_cap": 75}), encoding="utf-8")
    return root


def _reply(output: dict) -> str:
    return json.dumps([{"type": "system"}, {"type": "result", "subtype": "success", "is_error": False, "structured_output": output}])


class Queue:
    def __init__(self, *stdouts: str) -> None:
        self.stdouts = list(stdouts)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        stdout = self.stdouts.pop(0)

        class Done:
            returncode = 0
            stderr = ""

        Done.stdout = stdout
        return Done()


TAILORED = {
    "candidate_level": "mid",
    "header": {"name": "Ada", "email": "ada@example.com", "phone": "", "location": "", "links": []},
    "summary": "",
    "experience": [
        {
            "company": "Acme",
            "title": "Engineer",
            "location": "",
            "dates": "",
            "bullets": [
                {"text": "Shipped 4 multi-agent systems for 50+ customers, built for regulated teams.", "priority": 1},
                {"text": "Built the billing service.", "priority": 2},
            ],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [],
    "jd_keywords": ["agents"],
    "changes": ["Moved the multi-agent line to the top because the role is about agents in production."],
    "gaps": ["Financial services domain experience"],
}


def _with_bullet(text: str) -> dict:
    output = json.loads(json.dumps(TAILORED))
    output["experience"][0]["bullets"].append({"text": text, "priority": 3})
    return output


def _one_page_pdf(tex: str) -> Compiled:
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return Compiled(ok=True, pages=1, pdf=buffer.getvalue())


def _score(final: float) -> str:
    return _reply({"jobs": [{"company": "OpenAI", "role": "FDE", "base_score": final, "penalties": "", "final_score": final, "url": JOB["url"]}]})


class TailorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("hunt.claude_cli.shutil.which", return_value="/usr/local/bin/claude")
        patcher.start()
        self.addCleanup(patcher.stop)
        no_tex = patch("hunt.pdf.find_tex", return_value=None)
        no_tex.start()
        self.addCleanup(no_tex.stop)

    def test_the_tailored_resume_job_and_notes_are_written_beside_each_other(self):
        root = _root()
        made = tailor_one(root, JOB, runner=Queue(_reply(TAILORED)))
        folder = tailored_folder(root, JOB)
        self.assertEqual(folder, root / "resumes/openai/forward-deployed-engineer-fde-financial-services")
        self.assertEqual(made.code, 0)
        self.assertIn("resumes/openai/forward-deployed-engineer-fde-financial-services/resume.tex", made.message)
        saved = json.loads((folder / "resume.json").read_text())
        self.assertIn("regulated teams", saved["experience"][0]["bullets"][0]["text"])
        self.assertIn("regulated teams", (folder / "resume.tex").read_text())
        jd = (folder / "jd.md").read_text()
        self.assertIn("Work with banks", jd)
        self.assertIn("https://jobs.ashbyhq.com/openai/1/application", jd)
        notes = (folder / "notes.md").read_text()
        self.assertIn("Moved the multi-agent line", notes)
        self.assertIn("Financial services domain experience", notes)
        self.assertEqual(MASTER, (root / "profile/og-resume.md").read_text())

    def test_a_number_the_master_resume_does_not_have_is_flagged(self):
        self.assertEqual(new_numbers(MASTER, "Shipped 4 systems for 50+ customers, cut costs 30%."), ["30%"])
        root = _root()
        made = tailor_one(root, JOB, runner=Queue(_reply(_with_bullet("Saved 2,000 hours."))))
        self.assertIn("2,000", made.message)
        self.assertIn("2,000", (tailored_folder(root, JOB) / "notes.md").read_text())

    def test_a_score_above_the_cutoff_tailors_right_away(self):
        root = _root()
        runner = Queue(_score(82), _reply(TAILORED))
        scored = score_one(root, JOB, runner=runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertIn("Tailored resume", scored.message)
        self.assertTrue((tailored_folder(root, JOB) / "resume.json").exists())

    def test_a_score_at_or_below_the_cutoff_is_not_tailored(self):
        root = _root()
        runner = Queue(_score(75))
        score_one(root, JOB, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        self.assertFalse(tailored_folder(root, JOB).exists())

    def test_a_failed_tailoring_keeps_the_score(self):
        root = _root()
        failed = json.dumps([{"type": "result", "subtype": "error_max_turns", "is_error": True}])
        scored = score_one(root, JOB, runner=Queue(_score(88), failed))
        self.assertEqual(scored.code, 0)
        self.assertIn("Tailoring failed", scored.message)
        self.assertEqual(read_csv(root / "data/evaluated-jobs.csv")[0]["final_score"], "88")


class LatexTailorTests(unittest.TestCase):
    def setUp(self):
        no_tex = patch("hunt.pdf.find_tex", return_value=None)
        no_tex.start()
        self.addCleanup(no_tex.stop)

    def test_without_tex_the_json_and_tex_are_kept_and_the_role_is_flagged(self):
        root = _root()
        result = save_tailored(root, JOB, TAILORED)
        folder = tailored_folder(root, JOB)
        self.assertTrue((folder / "resume.json").exists() and (folder / "resume.tex").exists())
        self.assertFalse((folder / "resume.pdf").exists())
        self.assertIn("PDF not built", (folder / "notes.md").read_text(encoding="utf-8"))
        self.assertTrue(result.warn)
        self.assertTrue(resume_flag(root, JOB))

    def test_a_built_pdf_records_its_checks(self):
        root = _root()
        save_tailored(root, JOB, TAILORED, compile=_one_page_pdf)
        folder = tailored_folder(root, JOB)
        saved = json.loads((folder / "ats.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["pdf"], saved["pages"]), (True, 1))
        self.assertTrue((folder / "resume.pdf").exists())
        self.assertIn("## ATS check", (folder / "notes.md").read_text(encoding="utf-8"))

    def test_resumes_written_without_tex_are_rebuilt_once_tex_is_there(self):
        root = _root()
        save_tailored(root, JOB, TAILORED)
        folder = tailored_folder(root, JOB)
        self.assertIn("hunt resume build", (folder / "notes.md").read_text(encoding="utf-8"))
        made = rebuild_all(root, compile=_one_page_pdf)
        self.assertEqual(len(made), 1)
        self.assertTrue((folder / "resume.pdf").exists())
        self.assertTrue(json.loads((folder / "ats.json").read_text(encoding="utf-8"))["pdf"])
        notes = (folder / "notes.md").read_text(encoding="utf-8")
        self.assertNotIn("PDF not built", notes)
        self.assertIn("## ATS check", notes)
        self.assertIn("Moved the multi-agent line", notes)
        self.assertEqual(rebuild_all(root, compile=_one_page_pdf), [])

    def test_an_empty_answer_writes_nothing(self):
        root = _root()
        self.assertEqual(save_tailored(root, JOB, {"experience": []}).code, 1)
        self.assertFalse(tailored_folder(root, JOB).exists())


class RawPromptTests(unittest.TestCase):
    def test_the_raw_prompt_has_no_placeholder_left(self):
        root = _root()
        text = raw_prompt(root, JOB)
        self.assertNotIn("{{", text)
        self.assertIn("Not scored", text)
        self.assertIn("\\documentclass", text)
        self.assertIn("Work with banks", text)
        self.assertIn("Shipped 4 multi-agent systems", text)

    def test_a_scored_role_carries_its_score(self):
        root = _root()
        (root / "data/evaluated-jobs.csv").write_text(
            "date,company,role,base_score,penalties,final_score,applied,reached_interview,status,platform,job_id,url,source,location\n"
            f"2026-10-21,OpenAI,FDE,84,production_systems,89,,,,ashby,1,{JOB['url']},ashby,NYC\n",
            encoding="utf-8",
        )
        self.assertIn("final score 89", raw_prompt(root, JOB))
