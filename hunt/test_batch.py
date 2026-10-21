import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import shutil
from contextlib import contextmanager
from unittest.mock import patch

from hunt.batch import BatchError, _context, failures, BudgetSpent, open_batches, pending_urls, poll, run_now, submit, submit_scores
from hunt.spend import spent
from hunt.evaluated import read_csv
from hunt.tailor import tailored_folder

MASTER = "# Ada\n\n- Shipped 4 multi-agent systems for 50+ customers.\n- Built the billing service.\n"


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "profile").mkdir()
    (root / "profile/og-resume.md").write_text(MASTER, encoding="utf-8")
    (root / "profile/rubric.json").write_text(json.dumps({"screenout_cap": 75, "natural_hire_at": 80}), encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Job hunt\n\n## Rubric\n\nScore 0-100.\n\n## Voice\n\nNo em dashes.\n\n## Failure\n\nWrite it down.\n", encoding="utf-8")
    return root


def _job(n: int) -> dict:
    return {
        "company": "Acme",
        "title": f"AI Engineer {n}",
        "url": f"https://jobs.example/{n}",
        "platform": "greenhouse",
        "job_id": str(n),
        "source": "greenhouse",
        "location": "Pune, India",
        "description": "Ship agents into production.",
    }


def _message(payload: dict, stop: str = "end_turn"):
    return NS(content=[NS(type="text", text=json.dumps(payload))], stop_reason=stop)


class FakeBatches:
    def __init__(self) -> None:
        self.created = []
        self.status = {}
        self.outputs = {}

    def create(self, requests):
        batch_id = f"msgbatch_{len(self.created) + 1}"
        self.created.append((batch_id, list(requests)))
        self.status[batch_id] = "in_progress"
        return NS(id=batch_id, processing_status="in_progress")

    def retrieve(self, batch_id):
        return NS(id=batch_id, processing_status=self.status[batch_id])

    def results(self, batch_id):
        yield from self.outputs[batch_id]

    def finish(self, batch_id, outputs):
        self.status[batch_id] = "ended"
        self.outputs[batch_id] = outputs


class FakeClient:
    def __init__(self) -> None:
        self.messages = NS(batches=FakeBatches())


def _ok(custom_id: str, payload: dict):
    return NS(custom_id=custom_id, result=NS(type="succeeded", message=_message(payload)))


def _score(job: dict, final: int) -> dict:
    return {"jobs": [{"company": job["company"], "role": job["title"], "base_score": final, "penalties": "", "final_score": final, "url": job["url"]}]}


class BatchTests(unittest.TestCase):
    def setUp(self):
        no_tex = patch("hunt.pdf.find_tex", return_value=None)
        no_tex.start()
        self.addCleanup(no_tex.stop)
        self.root = _root()
        self.client = FakeClient()
        self.batches = self.client.messages.batches

    def test_one_batch_scores_every_role_with_a_cached_shared_prefix(self):
        jobs = [_job(n) for n in range(3)]
        batch_id = submit_scores(self.root, jobs, client=self.client)
        self.assertEqual(batch_id, "msgbatch_1")
        requests = self.batches.created[0][1]
        self.assertEqual(len(requests), 3)
        for request in requests:
            self.assertRegex(request["custom_id"], r"^[a-zA-Z0-9_-]{1,64}$")
            params = request["params"]
            self.assertEqual(params["system"][-1]["cache_control"], {"type": "ephemeral"})
            self.assertIn("Ada", params["system"][-1]["text"])
            self.assertIn("Score 0-100", params["system"][-1]["text"])
            self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(len({request["params"]["system"][-1]["text"] for request in requests}), 1)
        self.assertEqual(pending_urls(self.root), {job["url"] for job in jobs})
        self.assertEqual(len(open_batches(self.root)), 1)

    def test_an_unfinished_batch_changes_nothing(self):
        submit_scores(self.root, [_job(1)], client=self.client)
        self.assertEqual(poll(self.root, client=self.client), [])
        self.assertEqual(read_csv(self.root / "data/evaluated-jobs.csv"), [])

    def test_scores_are_written_then_roles_above_the_cutoff_are_tailored(self):
        jobs = [_job(n) for n in range(3)]
        batch_id = submit_scores(self.root, jobs, client=self.client)
        ids = [request["custom_id"] for request in self.batches.created[0][1]]
        self.batches.finish(batch_id, [_ok(ids[0], _score(jobs[0], 88)), _ok(ids[1], _score(jobs[1], 70)), _ok(ids[2], _score(jobs[2], 76))])
        notes = poll(self.root, client=self.client)
        rows = {row["url"]: row["final_score"] for row in read_csv(self.root / "data/evaluated-jobs.csv")}
        self.assertEqual(rows, {jobs[0]["url"]: "88", jobs[1]["url"]: "70", jobs[2]["url"]: "76"})
        self.assertTrue(any("2 above 75" in note for note in notes))
        tailor_id, tailor_requests = self.batches.created[1]
        self.assertEqual(len(tailor_requests), 2)
        self.assertEqual(pending_urls(self.root), {jobs[0]["url"], jobs[2]["url"]})

        tailored = {
            "candidate_level": "mid",
            "header": {"name": "Ada", "email": "", "phone": "", "location": "", "links": []},
            "summary": "",
            "experience": [{"company": "Acme", "title": "Engineer", "location": "", "dates": "", "bullets": [{"text": "Shipped 4 multi-agent systems.", "priority": 1}]}],
            "projects": [],
            "education": [],
            "skills": [],
            "jd_keywords": [],
            "changes": ["Moved agents up."],
            "gaps": ["Banking"],
        }
        self.batches.finish(tailor_id, [_ok(request["custom_id"], tailored) for request in tailor_requests])
        notes = poll(self.root, client=self.client)
        self.assertTrue((tailored_folder(self.root, jobs[0]) / "resume.json").exists())
        self.assertIn("Banking", (tailored_folder(self.root, jobs[2]) / "notes.md").read_text())
        self.assertFalse(tailored_folder(self.root, jobs[1]).exists())
        self.assertTrue(any("2 tailored" in note for note in notes))
        self.assertEqual(open_batches(self.root), [])
        self.assertEqual(pending_urls(self.root), set())

    def test_errored_and_refused_results_are_reported_not_written(self):
        jobs = [_job(1), _job(2)]
        batch_id = submit_scores(self.root, jobs, client=self.client)
        ids = [request["custom_id"] for request in self.batches.created[0][1]]
        errored = NS(custom_id=ids[0], result=NS(type="errored", error=NS(type="overloaded_error")))
        refused = NS(custom_id=ids[1], result=NS(type="succeeded", message=_message({}, stop="refusal")))
        self.batches.finish(batch_id, [errored, refused])
        notes = poll(self.root, client=self.client)
        self.assertEqual(read_csv(self.root / "data/evaluated-jobs.csv"), [])
        self.assertTrue(any("2 failed" in note for note in notes))
        self.assertEqual(len(self.batches.created), 1)

    def test_custom_ids_stay_valid_for_long_titles(self):
        job = dict(_job(1), title="Senior Staff Forward Deployed Engineer, Financial Services and Insurance (NYC / Remote)")
        submit_scores(self.root, [job], client=self.client)
        custom_id = self.batches.created[0][1][0]["custom_id"]
        self.assertTrue(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", custom_id))


def _budget(root: Path, score: float) -> None:
    budget = {"classify_usd_per_day": 0.5, "score_usd_per_day": score}
    (root / "profile/preferences.json").write_text(json.dumps({"budget": budget}), encoding="utf-8")


def _billed_message(payload: dict, model: str = "claude-opus-5-5"):
    usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(content=[NS(type="text", text=json.dumps(payload))], stop_reason="end_turn", model=model, usage=usage)


def _billed(custom_id: str, payload: dict):
    return NS(custom_id=custom_id, result=NS(type="succeeded", message=_billed_message(payload)))


class RefusingBatches(FakeBatches):
    def create(self, requests):
        raise RuntimeError("overloaded")


class StreamingClient:
    def __init__(self, message) -> None:
        @contextmanager
        def stream(**kwargs):
            yield NS(get_final_message=lambda: message)

        self.messages = NS(stream=stream)


class SpendBatchTests(unittest.TestCase):
    def setUp(self):
        self.root = _root()
        self.client = FakeClient()
        self.batches = self.client.messages.batches

    def test_submit_reserves_and_poll_settles_at_billed_usage(self):
        jobs = [_job(1), _job(2)]
        batch_id, count = submit(self.root, "score", jobs, client=self.client)
        self.assertEqual(count, 2)
        self.assertGreater(spent(self.root, "score"), 0)
        requests = self.batches.created[0][1]
        self.batches.finish(batch_id, [_billed(request["custom_id"], _score(job, 60)) for request, job in zip(requests, jobs)])
        poll(self.root, client=self.client)
        self.assertAlmostEqual(spent(self.root, "score"), 0.008)

    def test_nothing_fits_raises_and_sends_nothing(self):
        _budget(self.root, 0.0001)
        with self.assertRaises(BudgetSpent):
            submit(self.root, "score", [_job(1)], client=self.client)
        self.assertEqual(self.batches.created, [])
        self.assertEqual(spent(self.root, "score"), 0.0)

    def test_enforce_false_sends_every_role(self):
        _budget(self.root, 0.0001)
        self.assertEqual(submit(self.root, "score", [_job(1), _job(2)], client=self.client, enforce=False)[1], 2)

    def test_a_refused_create_releases_the_reservation(self):
        client = NS(messages=NS(batches=RefusingBatches()))
        with self.assertRaises(BatchError):
            submit(self.root, "score", [_job(1)], client=client)
        self.assertEqual(spent(self.root, "score"), 0.0)

    def test_an_expired_batch_settles_at_zero_and_the_roles_stay_unscored(self):
        batch_id, _ = submit(self.root, "score", [_job(1)], client=self.client)
        custom_id = self.batches.created[0][1][0]["custom_id"]
        self.batches.finish(batch_id, [NS(custom_id=custom_id, result=NS(type="expired"))])
        notes = poll(self.root, client=self.client)
        self.assertIn("1 failed", notes[0])
        self.assertEqual(spent(self.root, "score"), 0.0)
        self.assertEqual(read_csv(self.root / "data/evaluated-jobs.csv"), [])
        self.assertEqual(pending_urls(self.root), set())

    def test_failed_roles_are_remembered_per_kind(self):
        jobs = [_job(1), _job(2)]
        batch_id, _ = submit(self.root, "score", jobs, client=self.client)
        ids = [request["custom_id"] for request in self.batches.created[0][1]]
        expired = NS(custom_id=ids[0], result=NS(type="expired"))
        no_row = _billed(ids[1], {"jobs": []})
        self.batches.finish(batch_id, [expired, no_row])
        poll(self.root, client=self.client)
        self.assertEqual(failures(self.root, "score"), {jobs[0]["url"]: 1, jobs[1]["url"]: 1})
        self.assertEqual(failures(self.root, "tailor"), {})

    def test_a_role_already_in_a_batch_is_not_sent_again(self):
        submit(self.root, "score", [_job(1)], client=self.client)
        _batch_id, count = submit(self.root, "score", [_job(1), _job(2)], client=self.client)
        self.assertEqual(count, 1)
        self.assertEqual([request["custom_id"] for request in self.batches.created[1][1]], [self.batches.created[1][1][0]["custom_id"]])
        self.assertNotIn(_job(1)["url"], [job["url"] for job in open_batches(self.root)[1]["jobs"].values()])
        with self.assertRaises(BatchError):
            submit(self.root, "score", [_job(2)], client=self.client)

    def test_two_pollers_apply_a_batch_once(self):
        import threading
        import time

        job = _job(1)
        batch_id, _ = submit(self.root, "score", [job], client=self.client)
        custom_id = self.batches.created[0][1][0]["custom_id"]
        self.batches.finish(batch_id, [_billed(custom_id, _score(job, 90))])
        slow = self.batches.results

        def results(batch):
            time.sleep(0.2)
            return slow(batch)

        self.batches.results = results
        notes: list[str] = []
        threads = [threading.Thread(target=lambda: notes.extend(poll(self.root, client=self.client))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len([note for note in notes if note.startswith("Batch scored")]), 1)
        self.assertEqual(len(self.batches.created), 2)

    def test_roles_already_tailored_are_not_tailored_again(self):
        job = _job(1)
        folder = tailored_folder(self.root, job)
        folder.mkdir(parents=True)
        (folder / "resume.md").write_text("# Ada\n", encoding="utf-8")
        batch_id, _ = submit(self.root, "score", [job], client=self.client)
        custom_id = self.batches.created[0][1][0]["custom_id"]
        self.batches.finish(batch_id, [_billed(custom_id, _score(job, 90))])
        poll(self.root, client=self.client)
        self.assertEqual(len(self.batches.created), 1)

    def test_tailoring_waits_when_scoring_spent_the_cap(self):
        _budget(self.root, 0.0045)
        job = _job(1)
        batch_id, _ = submit(self.root, "score", [job], client=self.client, enforce=False)
        custom_id = self.batches.created[0][1][0]["custom_id"]
        self.batches.finish(batch_id, [_billed(custom_id, _score(job, 90))])
        notes = poll(self.root, client=self.client)
        self.assertIn("Tailoring waits for tomorrow's budget.", notes[0])
        self.assertEqual(len(self.batches.created), 1)

    def test_run_now_records_full_price(self):
        payload = {"resume": "x"}
        result = run_now(self.root, "tailor", _job(1), client=StreamingClient(_billed_message(payload)))
        self.assertEqual(result, payload)
        self.assertAlmostEqual(spent(self.root, "tailor"), 0.008)


class HumanizerContextTests(unittest.TestCase):
    def test_only_tailor_requests_carry_the_humanizer(self):
        root = _root()
        shutil.copytree(Path(__file__).resolve().parent.parent / "prompts", root / "prompts")
        self.assertIn("humanizer skill, MIT", _context(root, "tailor"))
        self.assertNotIn("humanizer", _context(root, "score"))
