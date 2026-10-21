import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.cli import score_one
from hunt.evaluated import read_csv
from hunt.tailor import new_numbers, tailor_one, tailored_folder

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
    "resume_markdown": "# Ada\n\n- Shipped 4 multi-agent systems for 50+ customers, built for regulated teams.\n- Built the billing service.\n",
    "changes": ["Moved the multi-agent line to the top because the role is about agents in production."],
    "gaps": ["Financial services domain experience"],
}


def _score(final: float) -> str:
    return _reply({"jobs": [{"company": "OpenAI", "role": "FDE", "base_score": final, "penalties": "", "final_score": final, "url": JOB["url"]}]})


class TailorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("hunt.claude_cli.shutil.which", return_value="/usr/local/bin/claude")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_tailored_resume_job_and_notes_are_written_beside_each_other(self):
        root = _root()
        made = tailor_one(root, JOB, runner=Queue(_reply(TAILORED)))
        folder = tailored_folder(root, JOB)
        self.assertEqual(folder, root / "resumes/openai/forward-deployed-engineer-fde-financial-services")
        self.assertEqual(made.code, 0)
        self.assertIn("resumes/openai/forward-deployed-engineer-fde-financial-services/resume.md", made.message)
        self.assertIn("regulated teams", (folder / "resume.md").read_text())
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
        made = tailor_one(root, JOB, runner=Queue(_reply(dict(TAILORED, resume_markdown=MASTER + "- Saved 2,000 hours.\n"))))
        self.assertIn("2,000", made.message)
        self.assertIn("2,000", (tailored_folder(root, JOB) / "notes.md").read_text())

    def test_a_score_above_the_cutoff_tailors_right_away(self):
        root = _root()
        runner = Queue(_score(82), _reply(TAILORED))
        scored = score_one(root, JOB, runner=runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertIn("Tailored resume", scored.message)
        self.assertTrue((tailored_folder(root, JOB) / "resume.md").exists())

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
