import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.cli import score_one
from hunt.evaluated import read_csv

JOB = {
    "company": "HT Digital Streams",
    "title": "Forward Deployed Engineer (AI-Native)",
    "url": "https://www.linkedin.com/jobs/view/1",
    "platform": "linkedin",
    "job_id": "li-1",
    "source": "jobspy",
    "location": "New Delhi, India",
    "description": "Embedded build. Ship prototypes with customers.",
}


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-from-env-file\nANTHROPIC_WORKSPACE_ID=wrkspc_1\n", encoding="utf-8")
    return root


def _output(result: dict) -> str:
    return json.dumps([{"type": "system", "tools": ["Read", "Edit"]}, {"type": "assistant"}, dict({"type": "result"}, **result)])


class Runner:
    def __init__(self, stdout: str, code: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.code = code
        self.stderr = stderr
        self.kwargs = {}
        self.args = []

    def __call__(self, args, **kwargs):
        self.args = list(args)
        self.kwargs = kwargs
        outer = self

        class Done:
            returncode = outer.code
            stdout = outer.stdout
            stderr = outer.stderr

        return Done()


class ScoreOneTests(unittest.TestCase):
    def setUp(self):
        self.saved = {name: os.environ.get(name) for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID")}
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env-file"
        os.environ["ANTHROPIC_WORKSPACE_ID"] = "wrkspc_1"

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def score(self, runner, root=None):
        root = root or _root()
        with patch("hunt.claude_cli.shutil.which", return_value="/usr/local/bin/claude"):
            return root, score_one(root, JOB, runner=runner)

    def test_the_score_is_written_by_hunt_and_nothing_reaches_the_terminal(self):
        runner = Runner(
            _output(
                {
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": {
                        "jobs": [
                            {
                                "company": "HT Digital Streams",
                                "role": "Forward Deployed Engineer (AI-Native)",
                                "base_score": 84,
                                "penalties": "production_systems",
                                "final_score": 89,
                                "url": JOB["url"],
                            }
                        ]
                    },
                }
            )
        )
        root, scored = self.score(runner)
        self.assertEqual(scored.code, 0)
        self.assertEqual(scored.final_score, 89)
        self.assertIn("89", scored.message)
        self.assertTrue(runner.kwargs["capture_output"])
        rows = read_csv(root / "data/evaluated-jobs.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["final_score"], rows[0]["base_score"], rows[0]["penalties"]), ("89", "84", "production_systems"))
        self.assertEqual((rows[0]["platform"], rows[0]["job_id"], rows[0]["applied"]), ("linkedin", "li-1", ""))
        self.assertEqual(len(list((root / "data/work").glob("*/score.out"))), 1)

    def test_claude_code_signs_in_on_its_own(self):
        runner = Runner(_output({"subtype": "success", "is_error": False, "structured_output": {"jobs": []}}))
        self.score(runner)
        self.assertNotIn("ANTHROPIC_API_KEY", runner.kwargs["env"])
        self.assertNotIn("ANTHROPIC_WORKSPACE_ID", runner.kwargs["env"])
        self.assertIn("Read", runner.args[runner.args.index("--allowedTools") + 1])
        self.assertNotIn("Bash", runner.args[runner.args.index("--allowedTools") + 1])

    def test_a_key_from_the_shell_is_left_alone(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-exported-in-shell"
        runner = Runner(_output({"subtype": "success", "is_error": False, "structured_output": {"jobs": []}}))
        self.score(runner)
        self.assertEqual(runner.kwargs["env"]["ANTHROPIC_API_KEY"], "sk-ant-exported-in-shell")

    def test_rescoring_updates_the_row_and_keeps_applied(self):
        root = _root()
        result = {"subtype": "success", "is_error": False, "structured_output": {"jobs": [{"company": "x", "role": "y", "base_score": 70, "penalties": "", "final_score": 70, "url": JOB["url"]}]}}
        self.score(Runner(_output(result)), root)
        rows = read_csv(root / "data/evaluated-jobs.csv")
        rows[0]["applied"] = "Yes"
        from hunt.evaluated import write_csv

        write_csv(root / "data/evaluated-jobs.csv", rows)
        result["structured_output"]["jobs"][0]["final_score"] = 78
        self.score(Runner(_output(result)), root)
        rows = read_csv(root / "data/evaluated-jobs.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["final_score"], rows[0]["applied"]), ("78", "Yes"))

    def test_an_error_result_writes_nothing_and_says_why(self):
        runner = Runner(_output({"subtype": "error_max_turns", "is_error": True, "result": ""}), code=1)
        root, scored = self.score(runner)
        self.assertEqual(scored.code, 1)
        self.assertIn("error_max_turns", scored.message)
        self.assertEqual(read_csv(root / "data/evaluated-jobs.csv"), [])

    def test_unreadable_output_is_an_error(self):
        root, scored = self.score(Runner("Not logged in · Please run /login", code=1))
        self.assertEqual(scored.code, 1)
        self.assertIn("Not logged in", scored.message)

    def test_without_the_cli_or_a_key_it_is_still_127(self):
        root = _root()
        with patch("hunt.claude_cli.shutil.which", return_value=None), patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            scored = score_one(root, JOB, runner=Runner(""))
        self.assertEqual(scored.code, 127)

    def test_without_the_cli_a_key_scores_through_the_api(self):
        from contextlib import contextmanager
        from types import SimpleNamespace as NS

        from hunt.spend import spent

        root = _root()
        payload = {"jobs": [{"company": JOB["company"], "role": JOB["title"], "base_score": 60, "penalties": "", "final_score": 60, "url": JOB["url"]}]}
        usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        message = NS(content=[NS(type="text", text=json.dumps(payload))], stop_reason="end_turn", model="claude-opus-5-5", usage=usage)

        @contextmanager
        def stream(**kwargs):
            yield NS(get_final_message=lambda: message)

        with patch("hunt.claude_cli.shutil.which", return_value=None), patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}):
            scored = score_one(root, JOB, runner=Runner(""), client=NS(messages=NS(stream=stream)))
        self.assertEqual((scored.code, scored.final_score), (0, 60.0))
        self.assertEqual(read_csv(root / "data/evaluated-jobs.csv")[0]["final_score"], "60")
        self.assertGreater(spent(root, "score"), 0)


class ParallelScoreTests(unittest.TestCase):
    def test_roles_score_together_each_with_its_own_work_files(self):
        import threading
        import time

        from hunt.cli import score_many

        root = _root()
        jobs = [dict(JOB, url=f"https://www.linkedin.com/jobs/view/{n}", job_id=f"li-{n}", title=f"Engineer {n}") for n in range(3)]
        prompts = []
        lock = threading.Lock()

        def runner(args, **kwargs):
            prompt = args[args.index("-p") + 1]
            with lock:
                prompts.append(prompt)
            packet = next(word for word in prompt.split() if word.startswith("data/work/") and word.endswith("job.json"))
            url = json.loads((root / packet).read_text())["url"]
            time.sleep(0.3)
            stdout = _output({"subtype": "success", "is_error": False, "structured_output": {"jobs": [{"company": "x", "role": "y", "base_score": 60, "penalties": "", "final_score": 60, "url": url}]}})

            class Done:
                returncode = 0
                stderr = ""

            Done.stdout = stdout
            return Done()

        start = time.time()
        with patch("hunt.claude_cli.shutil.which", return_value="/usr/local/bin/claude"):
            results = score_many(root, jobs, runner=runner)
        self.assertLess(time.time() - start, 0.8)
        self.assertEqual([item.code for item in results], [0, 0, 0])
        self.assertEqual(len({prompt for prompt in prompts}), 3)
        self.assertEqual(sorted(row["url"] for row in read_csv(root / "data/evaluated-jobs.csv")), sorted(job["url"] for job in jobs))
