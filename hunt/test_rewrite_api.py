import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.rewrite_api import RewriteError, load_steps, run_resume_rewrite, splice_experience
from hunt.workflows import command, discover


RESUME = """# Ada Lovelace
Engineer, 2019-2025

## Experience

### Example Corp
Software engineer, 2022-2025
- Shipped the billing service in Python and Postgres.
- Cut the nightly job from hours to minutes.

## Education

BSc Mathematics, 2019
"""

PDF_TEXT = """ADA LOVELACE
SUMMARY
Engineer who ships billing systems.
PROFESSIONALEXPERIENCE
Software engineer, Example Corp 2022 – 2025
Shipped the billing service in Python and Postgres.
TECHNICALPROJECTS
Loom compiler, a toy language for looms.
EDUCATION
BSc Mathematics, 2019
"""

NO_HEADING = """# Ada Lovelace

## Example Corp
Software engineer, 2022-2025
- Shipped the billing service in Python and Postgres.
"""


class Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class Usage:
    input_tokens = 11
    output_tokens = 7
    cache_read_input_tokens = 3
    cache_creation_input_tokens = 1


class Message:
    def __init__(self, text: str, model: str, stop_reason: str = "end_turn") -> None:
        self.content = [Block(text)]
        self.model = model
        self.stop_reason = stop_reason
        self.usage = Usage()


class Stream:
    def __init__(self, message: Message) -> None:
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get_final_message(self):
        return self._message


class FakeMessages:
    def __init__(self, replies) -> None:
        self.replies = replies
        self.kwargs = []

    def stream(self, **kwargs):
        self.kwargs.append(kwargs)
        system = kwargs["system"][0]["text"]
        reply = next(value for key, value in self.replies.items() if system.startswith(key))
        if isinstance(reply, Exception):
            raise reply
        return Stream(Message(reply, kwargs["model"]))


class FakeClient:
    def __init__(self, replies) -> None:
        self.messages = FakeMessages(replies)


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    resume = root / "profile"
    resume.mkdir()
    (resume / "og-resume.md").write_text(RESUME, encoding="utf-8")
    (root / "data").mkdir()
    return root


def _replies(**overrides) -> dict:
    base = {
        "You inventory": json.dumps(
            {
                "roles": [
                    {
                        "title": "Software engineer",
                        "company": "Example Corp",
                        "bullets": ["Shipped the billing service in Python and Postgres."],
                    }
                ],
                "questions": [],
            }
        ),
        "Rewrite only": json.dumps(
            {
                "title": "Software engineer",
                "company": "Example Corp",
                "bullets": ["Shipped the billing service in Python and Postgres."],
            }
        ),
        "Combine the rewritten": json.dumps(
            {
                "resume": "Shipped the billing service in Python and Postgres.",
                "gaps": [],
            }
        ),
        "Compare the original": json.dumps({"clean": True, "invented": []}),
    }
    base.update(overrides)
    return base


class RewriteApiTests(unittest.TestCase):
    def test_each_step_uses_its_model(self):
        root = _root()
        client = FakeClient(_replies())
        notes = []
        result = run_resume_rewrite(
            root,
            {"resume": "profile/og-resume.md"},
            client=client,
            on_progress=notes.append,
        )
        models = [item["model"] for item in client.messages.kwargs]
        efforts = [item["output_config"]["effort"] for item in client.messages.kwargs]
        self.assertEqual(
            models,
            ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-sonnet-5-5"],
        )
        self.assertEqual(efforts, ["low", "high", "medium", "medium"])
        self.assertTrue(result.clean)
        self.assertFalse(result.paused)
        self.assertIn("passed the fact check", result.path.read_text(encoding="utf-8"))
        self.assertEqual((root / "profile" / "og-resume.md.bak").read_text(encoding="utf-8"), RESUME)
        updated = (root / "resumes" / "updated-resume.md").read_text(encoding="utf-8")
        self.assertIn("Ada Lovelace", updated)
        self.assertIn("Shipped the billing service", updated)
        self.assertIn("resumes/updated-resume.md", result.next_steps)
        self.assertEqual(client.messages.kwargs[0]["system"][0]["cache_control"]["type"], "ephemeral")
        self.assertTrue(any(line.startswith("Inventory ·") for line in notes))
        self.assertTrue(any(line.startswith("Rewrite 1/") for line in notes))
        self.assertTrue(any(line.startswith("Humanize ·") for line in notes))
        self.assertTrue(any(line.startswith("Fact check ·") for line in notes))

    def test_missing_numbers_stop_before_rewrite(self):
        root = _root()
        client = FakeClient(
            _replies(
                **{
                    "You inventory": json.dumps(
                        {
                            "roles": [
                                {
                                    "title": "Software engineer",
                                    "company": "Example Corp",
                                    "bullets": ["Cut the nightly job."],
                                }
                            ],
                            "questions": ["How many hours did the nightly job take before?"],
                        }
                    )
                }
            )
        )
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=client)
        self.assertTrue(result.paused)
        self.assertEqual(len(client.messages.kwargs), 1)
        report = result.path.read_text(encoding="utf-8")
        self.assertIn("How many hours", report)
        answers = (root / "data" / "resume-answers.md").read_text(encoding="utf-8")
        self.assertIn("Q: How many hours did the nightly job take before?", answers)
        self.assertIn("A:", answers)

    def test_positions_do_not_pause_the_rewrite_or_touch_keywords(self):
        root = _root()
        (root / "profile" / "search.json").write_text(json.dumps({"keywords": ["engineer"]}), encoding="utf-8")
        client = FakeClient(
            _replies(
                **{
                    "You inventory": json.dumps(
                        {
                            "roles": [
                                {
                                    "title": "Software engineer",
                                    "company": "Example Corp",
                                    "bullets": ["Shipped the billing service in Python and Postgres."],
                                }
                            ],
                            "questions": [],
                            "positions": ["forward deployed engineer", "backend engineer"],
                        }
                    )
                }
            )
        )
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=client)
        self.assertFalse(result.paused)
        self.assertEqual(result.positions, ["forward deployed engineer", "backend engineer"])
        saved = json.loads((root / "profile" / "search.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["keywords"], ["engineer"])

    def test_answers_file_lets_the_rewrite_continue(self):
        root = _root()
        (root / "data" / "resume-answers.md").write_text("The nightly job took 6 hours.\n", encoding="utf-8")
        client = FakeClient(
            _replies(
                **{
                    "You inventory": json.dumps(
                        {
                            "roles": [
                                {
                                    "title": "Software engineer",
                                    "company": "Example Corp",
                                    "bullets": ["Cut the nightly job."],
                                }
                            ],
                            "questions": ["How many hours did the nightly job take before?"],
                        }
                    )
                }
            )
        )
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=client)
        self.assertFalse(result.paused)
        self.assertEqual(len(client.messages.kwargs), 4)
        rewrite_user = client.messages.kwargs[1]["messages"][0]["content"]
        self.assertIn("6 hours", rewrite_user)

    def test_failed_fact_check_stays_closed(self):
        root = _root()
        client = FakeClient(
            _replies(**{"Compare the original": json.dumps({"clean": False, "invented": ["Added Kubernetes"]})})
        )
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=client)
        self.assertFalse(result.clean)
        self.assertIn("Added Kubernetes", result.path.read_text(encoding="utf-8"))
        self.assertEqual((root / "profile" / "og-resume.md").read_text(encoding="utf-8"), RESUME)

    def test_a_pdf_heading_without_spaces_is_replaced_in_place(self):
        merged = splice_experience(PDF_TEXT, "- Shipped the billing service in Python and Postgres, on time.")
        self.assertEqual(merged.count("Shipped the billing service"), 1)
        self.assertIn("on time", merged)
        self.assertIn("Loom compiler", merged)
        self.assertIn("BSc Mathematics, 2019", merged)
        self.assertNotIn("## Experience", merged)

    def test_a_resume_with_no_experience_heading_keeps_the_master(self):
        root = _root()
        (root / "profile" / "og-resume.md").write_text(NO_HEADING, encoding="utf-8")
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=FakeClient(_replies()))
        self.assertTrue(result.clean)
        self.assertFalse(result.saved)
        self.assertEqual((root / "profile" / "og-resume.md").read_text(encoding="utf-8"), NO_HEADING)
        self.assertFalse((root / "profile" / "og-resume.md.bak").exists())
        draft = (root / "resumes" / "updated-resume.md").read_text(encoding="utf-8")
        self.assertEqual(draft.count("Shipped the billing service"), 1)
        self.assertIn("No experience section", result.next_steps)

    def test_an_unfilled_metric_keeps_the_master(self):
        root = _root()
        client = FakeClient(
            _replies(**{"Combine the rewritten": json.dumps({"resume": "- Cut the nightly job by [metric].", "gaps": []})})
        )
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=client)
        self.assertTrue(result.clean)
        self.assertFalse(result.saved)
        self.assertEqual((root / "profile" / "og-resume.md").read_text(encoding="utf-8"), RESUME)
        self.assertIn("[metric]", (root / "resumes" / "updated-resume.md").read_text(encoding="utf-8"))
        self.assertIn("[metric]", result.next_steps)

    def test_each_save_backs_up_the_master_it_replaces(self):
        root = _root()
        (root / "profile" / "og-resume.md.bak").write_text("An older master.\n", encoding="utf-8")
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=FakeClient(_replies()))
        self.assertTrue(result.saved)
        self.assertEqual((root / "profile" / "og-resume.md.bak").read_text(encoding="utf-8"), RESUME)

    def test_the_report_says_the_master_was_replaced(self):
        root = _root()
        result = run_resume_rewrite(root, {"resume": "profile/og-resume.md"}, client=FakeClient(_replies()))
        report = result.path.read_text(encoding="utf-8")
        self.assertIn("It is also saved as profile/og-resume.md", report)
        self.assertNotIn("paste them into the resume yourself", report)
        self.assertNotIn("Source file left unchanged", report)

    def test_a_pdf_is_read_as_text_before_inventory(self):
        root = _root()
        pdf = root / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        client = FakeClient(_replies())
        with patch("hunt.rewrite_api.read_upload", return_value=RESUME) as read:
            result = run_resume_rewrite(root, {"resume": str(pdf)}, client=client)
        read.assert_called_once_with(pdf)
        sent = client.messages.kwargs[0]["messages"][0]["content"]
        self.assertIn("Example Corp", sent)
        self.assertTrue(result.clean)

    def test_bad_effort_is_rejected(self):
        root = _root()
        (root / "profile" / "models.json").write_text(
            json.dumps({"steps": {"inventory": {"effort": "none"}}}),
            encoding="utf-8",
        )
        with self.assertRaises(RewriteError):
            load_steps(root)

    def test_api_row_is_listed_and_is_not_a_cli_session(self):
        root = _root()
        found = {item.label(): item for item in discover(root)}
        self.assertIn("api:resume-rewrite", found)
        with self.assertRaises(RuntimeError):
            command(found["api:resume-rewrite"], root, {"resume": "profile/og-resume.md"})
