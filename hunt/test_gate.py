import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunt.gate import (
    active_classifier,
    HAIKU_SCHEMA,
    GateError,
    ask_clef,
    attach,
    classifier,
    decide,
    evaluate,
    evaluate_many,
    haiku_predictor,
    make_predictor,
    parse_result,
    redo_wrong_years,
    set_classifier,
)
from hunt.rewrite_api import load_steps
from hunt.spend import spent
from hunt.test_rewrite_api import FakeClient
from hunt.trim import shorten, years_required, years_stated


class TrimTests(unittest.TestCase):
    def test_boilerplate_is_removed_and_the_role_stays(self):
        raw = (
            "About the company. We are a public benefit corporation headquartered in San Francisco. "
            "We offer competitive compensation and benefits and generous vacation. "
            "About the role. You will ship production systems with customer teams. "
            "We encourage you to apply even if you do not meet every qualification."
        )
        short = shorten(raw)
        self.assertIn("ship production systems", short)
        self.assertNotIn("public benefit corporation", short)
        self.assertNotIn("generous vacation", short)

    def test_years_stay_in_code(self):
        self.assertEqual(years_stated("We need 10+ years of experience."), 10)
        self.assertIsNone(years_stated("No number here."))

    def test_years_come_from_requirement_sentences(self):
        self.assertEqual(years_required("Experience: 4-9 years."), (4, "Experience: 4-9 years."))
        self.assertEqual(years_stated("4-9 years of experience in Python"), 4)
        self.assertEqual(years_stated("We need 10+ years of experience."), 10)
        self.assertEqual(years_stated("NVIDIA has 25 years of leadership in GPUs. You have 5+ years of experience building systems."), 5)
        self.assertIsNone(years_stated("Founded 10 years ago, we have 10 years of company history."))
        self.assertIsNone(years_stated("20+ years of experience required."))
        self.assertEqual(years_stated("Minimum 3 years in backend development."), 3)
        self.assertEqual(years_stated("You have 2+ years with React and 4+ years of software experience."), 4)
        self.assertEqual(years_stated("3 to 5 years of experience"), 3)
        self.assertEqual(years_stated("5+ yrs exp in Java"), 5)
        self.assertIsNone(years_stated("No number here."))
        self.assertEqual(years_required("No number here."), (None, ""))
        self.assertEqual(years_stated("3 or more years of experience"), 3)
        self.assertIsNone(years_stated("Must be 18 years or older."))


class GateTests(unittest.TestCase):
    def test_keep_needs_a_high_yes_and_a_real_fit(self):
        self.assertFalse(decide(0.4, 4))
        self.assertFalse(decide(0.9, 1))
        self.assertTrue(decide(0.8, 2.4))

    def test_evaluate_uses_the_supplied_predictor(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "data").mkdir()
        (root / "profile/search.json").write_text(
            json.dumps({"keywords": ["engineer"], "exclude_title": [], "location": {"mode": "any"}, "sources": []}),
            encoding="utf-8",
        )
        (root / "profile/rubric.json").write_text(json.dumps({"max_years_without_cap": 7}), encoding="utf-8")
        (root / "profile/og-resume.md").write_text("Ships production systems.\n", encoding="utf-8")
        job = {
            "company": "Acme",
            "title": "Engineer",
            "url": "https://example.com/1",
            "description": "About the role. Build the product. 10 years required. We offer generous vacation.",
        }

        def predictor(state, questions):
            self.assertIn("Build the product", state)
            self.assertNotIn("generous vacation", state)
            self.assertIn("worth_reading", questions)
            return {"answers": {"worth_reading": {"noul": 0.9}, "fit": {"score": 3.2}}}

        record = evaluate(root, job, predictor=predictor)
        self.assertTrue(record["keep"])
        self.assertTrue(record["over_years"])
        self.assertEqual(parse_result({"answers": {"worth_reading": {"noul": 0.2}, "fit": {"score": 0.2}}}, job)["keep"], False)
        cached = json.loads((root / "data/clef-cache.json").read_text(encoding="utf-8"))
        self.assertIn("https://example.com/1", cached)
        self.assertTrue(cached["https://example.com/1"]["described"])

    def test_a_title_only_decision_is_redone_once_the_posting_text_arrives(self):
        url = "https://www.linkedin.com/jobs/view/1"
        cache = {url: {"url": url, "keep": False, "fit": 1, "described": False}}
        bare = {"url": url, "title": "Engineer", "description": ""}
        full = {"url": url, "title": "Engineer", "description": "Build agents with customers."}
        old = {"url": "https://example.com/2", "keep": True, "fit": 3}
        legacy = {"url": "https://example.com/2", "description": "Ship it."}
        attach([bare, full, legacy], {**cache, legacy["url"]: old})
        self.assertFalse(bare["jev_keep"])
        self.assertNotIn("jev_keep", full)
        self.assertTrue(legacy["jev_keep"])

        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        (root / "data/clef-cache.json").write_text(json.dumps(cache), encoding="utf-8")
        seen = []

        def evaluate_one(_root, job, predictor=None, by=None):
            seen.append(job["description"])
            return {}

        with patch("hunt.gate.evaluate", side_effect=evaluate_one):
            evaluate_many(root, [bare, full], limit=5)
        self.assertEqual(seen, ["Build agents with customers."])


class FitLevelTests(unittest.TestCase):
    def test_the_label_rounds_half_up(self):
        from hunt.gate import fit_level

        job = {"url": "https://example.com/f", "description": ""}
        record = parse_result({"answers": {"worth_reading": {"noul": 0.9}, "fit": {"score": 2.5}}}, job)
        self.assertEqual(record["fit_label"], "A plausible match")
        self.assertEqual(fit_level(2.49), 2)
        self.assertEqual(fit_level(9), 4)
        self.assertEqual(fit_level(-1), 0)
        self.assertIsNone(fit_level(None))


class YearsSentenceTests(unittest.TestCase):
    def test_the_years_sentence_travels_to_the_desk(self):
        from hunt.desk import _years_note

        job = {"url": "https://example.com/y", "description": "About the role. Experience: 4-9 years. Build agents."}
        record = parse_result({"answers": {"worth_reading": {"noul": 0.9}, "fit": {"score": 3}}}, job)
        self.assertEqual(record["years_stated"], 4)
        self.assertEqual(record["years_sentence"], "Experience: 4-9 years.")
        attach([job], {job["url"]: record})
        self.assertEqual(job["jev_years_sentence"], "Experience: 4-9 years.")
        self.assertEqual(_years_note(job), 'Asks 4 years: "Experience: 4-9 years."')
        self.assertEqual(_years_note({"description": "Build agents."}), "")


class RedoYearsTests(unittest.TestCase):
    def test_decisions_made_with_wrong_years_are_redone(self):
        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        cache = {
            "https://example.com/a": {"url": "https://example.com/a", "keep": True, "fit": 2, "years_stated": 9},
            "https://example.com/b": {"url": "https://example.com/b", "keep": True, "fit": 3, "years_stated": 10},
        }
        (root / "data/clef-cache.json").write_text(json.dumps(cache), encoding="utf-8")
        jobs = [
            {"url": "https://example.com/a", "description": "Experience: 4-9 years."},
            {"url": "https://example.com/b", "description": "10+ years of experience."},
        ]
        self.assertEqual(redo_wrong_years(root, jobs), 1)
        left = json.loads((root / "data/clef-cache.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(left), ["https://example.com/b"])

    def test_the_classify_command_redoes_wrong_years_first(self):
        import contextlib
        import io

        from hunt.cli import cmd_clef

        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        url = "https://example.com/a"
        row = {"company": "Acme", "title": "AI Engineer", "url": url, "description": "Experience: 4-9 years."}
        (root / "data/inbox.json").write_text(json.dumps([row]), encoding="utf-8")
        (root / "data/clef-cache.json").write_text(json.dumps({url: {"url": url, "keep": True, "years_stated": 9}}), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_clef(root, ["--redo-years", "--limit", "0"])
        self.assertEqual(code, 0)
        self.assertIn("1 decisions used the wrong years. They are queued for the classifier.", out.getvalue())
        self.assertEqual(json.loads((root / "data/clef-cache.json").read_text(encoding="utf-8")), {})


class ParallelClefTests(unittest.TestCase):
    def test_roles_are_classified_together_and_no_decision_is_lost(self):
        import time

        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "data").mkdir()
        (root / "profile/search.json").write_text(json.dumps({"keywords": ["engineer"], "sources": []}), encoding="utf-8")
        (root / "profile/rubric.json").write_text(json.dumps({"max_years_without_cap": 7}), encoding="utf-8")
        (root / "profile/og-resume.md").write_text("Ships production systems.\n", encoding="utf-8")
        jobs = [{"company": "Acme", "title": f"Engineer {n}", "url": f"https://example.com/{n}", "description": "Build it."} for n in range(6)]

        def predictor(state, questions):
            time.sleep(0.2)
            return {"answers": {"worth_reading": {"noul": 0.9}, "fit": {"score": 3.2}}}

        start = time.time()
        with patch.dict("os.environ", {"CLEF_PARALLEL": "3"}):
            found = evaluate_many(root, jobs, limit=10, predictor=predictor)
        self.assertLess(time.time() - start, 0.75)
        self.assertEqual(len(found), 6)
        cached = json.loads((root / "data/clef-cache.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(cached), sorted(job["url"] for job in jobs))


def _haiku_root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    (root / "profile/search.json").write_text(json.dumps({"keywords": ["engineer"], "sources": []}), encoding="utf-8")
    (root / "profile/rubric.json").write_text(json.dumps({"max_years_without_cap": 7}), encoding="utf-8")
    (root / "profile/og-resume.md").write_text("Ships production systems.\n", encoding="utf-8")
    (root / "profile/models.json").write_text(json.dumps({"classifier": "haiku", "steps": {}}), encoding="utf-8")
    return root


class HaikuTests(unittest.TestCase):
    def test_haiku_keeps_a_fit_and_the_years_cap_still_applies(self):
        root = _haiku_root()
        client = FakeClient({"You screen one job posting": json.dumps({"worth_reading": True, "fit": 3})})
        job = {
            "company": "Acme",
            "title": "Engineer",
            "url": "https://example.com/h1",
            "description": "About the role. Build the product. 10 years required.",
        }
        record = evaluate(root, job, predictor=haiku_predictor(root, client=client))
        self.assertTrue(record["keep"])
        self.assertTrue(record["over_years"])
        self.assertEqual(record["by"], "haiku")
        self.assertEqual(record["fit_label"], "A plausible match")
        sent = client.messages.kwargs[0]
        self.assertEqual(sent["model"], "claude-haiku-5-5")
        self.assertEqual(sent["output_config"]["effort"], "low")
        self.assertEqual(sent["output_config"]["format"]["schema"], HAIKU_SCHEMA)
        self.assertIn("Build the product", sent["messages"][0]["content"])
        self.assertIn("4: A natural hire if the resume is true", sent["system"][0]["text"])
        cached = json.loads((root / "data/clef-cache.json").read_text(encoding="utf-8"))
        self.assertEqual(cached["https://example.com/h1"]["by"], "haiku")

    def test_a_posting_haiku_calls_not_worth_reading_is_skipped(self):
        root = _haiku_root()
        client = FakeClient({"You screen one job posting": json.dumps({"worth_reading": False, "fit": 4})})
        job = {"company": "Acme", "title": "Engineer", "url": "https://example.com/h2", "description": "Sell it."}
        record = evaluate(root, job, predictor=haiku_predictor(root, client=client))
        self.assertFalse(record["keep"])

    def test_the_switch_picks_the_predictor(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(classifier(root), "clef")
        self.assertIs(make_predictor(root), ask_clef)
        (root / "profile").mkdir()
        (root / "profile/models.json").write_text(json.dumps({"classifier": "gpt", "steps": {}}), encoding="utf-8")
        with self.assertRaises(GateError):
            make_predictor(root)
        set_classifier(root, "haiku")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            self.assertTrue(callable(make_predictor(root)))
        with self.assertRaises(GateError):
            set_classifier(root, "gpt")

    def test_set_classifier_keeps_the_steps(self):
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        steps = {"score": {"model": "claude-opus-5-5", "effort": "high", "max_tokens": 9000}}
        (root / "profile/models.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
        set_classifier(root, "haiku")
        saved = json.loads((root / "profile/models.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"steps": steps, "classifier": "haiku"})
        self.assertEqual(load_steps(root)["classify"]["model"], "claude-haiku-5-5")
        self.assertEqual(load_steps(root)["score"]["max_tokens"], 9000)

        fresh = Path(tempfile.mkdtemp())
        set_classifier(fresh, "haiku")
        self.assertEqual(classifier(fresh), "haiku")
        self.assertEqual(load_steps(fresh)["classify"]["effort"], "low")

    def test_a_missing_key_fails_only_when_a_role_is_pending(self):
        root = _haiku_root()
        url = "https://example.com/h3"
        job = {"company": "Acme", "title": "Engineer", "url": url, "description": "Build it."}
        (root / "data/clef-cache.json").write_text(json.dumps({url: {"url": url, "keep": True}}), encoding="utf-8")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            self.assertEqual(evaluate_many(root, [job], limit=5), [])
            fresh = dict(job, url="https://example.com/h4")
            with self.assertRaises(GateError):
                evaluate_many(root, [fresh], limit=5)

    def test_the_role_line_names_the_classifier(self):
        from hunt.desk import _jev_line

        haiku = {"url": "https://example.com/h5", "description": "Build it."}
        clef = {"url": "https://example.com/h6", "description": "Build it."}
        attach(
            [haiku, clef],
            {
                haiku["url"]: {"keep": True, "worth": 1.0, "fit": 3.0, "fit_label": "A plausible match", "by": "haiku"},
                clef["url"]: {"keep": False, "worth": 0.4, "fit": 1.0, "fit_label": "Same field, wrong role"},
            },
        )
        self.assertEqual(haiku["jev_by"], "haiku")
        self.assertEqual(clef["jev_by"], "clef")
        self.assertEqual(_jev_line(haiku), "Haiku keep · A plausible match")
        self.assertEqual(_jev_line(clef), "Clef-flash 0.40 skip · Same field, wrong role")
        self.assertEqual(_jev_line({}), "Not classified yet")


class BilledHaikuClient:
    """Answers every classify call with a plausible match and a fixed usage."""

    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.calls = []
        usage = type("Usage", (), {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
        message = type("Message", (), {})()
        message.content = [type("Block", (), {"type": "text", "text": json.dumps({"worth_reading": True, "fit": 3})})()]
        message.model = "claude-haiku-5-5"
        message.stop_reason = stop_reason
        message.usage = usage()
        outer = self

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get_final_message(self):
                return message

        class Messages:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                return Stream()

        self.messages = Messages()


def _with_budget(root: Path, classify: float) -> Path:
    (root / "profile/preferences.json").write_text(
        json.dumps({"budget": {"classify_usd_per_day": classify, "score_usd_per_day": 3}}), encoding="utf-8"
    )
    return root


class HaikuBudgetTests(unittest.TestCase):
    JOB = {"company": "Acme", "title": "Engineer", "url": "https://example.com/b1", "description": "Build the product."}

    def test_haiku_stops_when_the_classify_cap_is_spent(self):
        root = _with_budget(_haiku_root(), 0)
        client = BilledHaikuClient()
        with self.assertRaisesRegex(GateError, "classify budget is spent"):
            evaluate(root, self.JOB, predictor=haiku_predictor(root, client=client))
        self.assertEqual(client.calls, [])

    def test_each_haiku_call_is_billed(self):
        root = _with_budget(_haiku_root(), 0.5)
        evaluate(root, self.JOB, predictor=haiku_predictor(root, client=BilledHaikuClient()))
        self.assertAlmostEqual(spent(root, "classify"), 0.00015)

    def test_a_billed_but_unusable_reply_still_counts(self):
        root = _with_budget(_haiku_root(), 0.5)
        with self.assertRaises(GateError):
            evaluate(root, self.JOB, predictor=haiku_predictor(root, client=BilledHaikuClient(stop_reason="max_tokens")))
        self.assertAlmostEqual(spent(root, "classify"), 0.00015)

    def test_clef_falls_back_to_haiku_only_with_a_key_and_no_ollama(self):
        root = _haiku_root()
        (root / "profile/models.json").write_text(json.dumps({"classifier": "clef", "steps": {}}), encoding="utf-8")
        with patch("hunt.gate.clef_reachable", return_value=False):
            self.assertEqual(active_classifier(root, {"ANTHROPIC_API_KEY": "sk-ant-x"}), "haiku")
            self.assertEqual(active_classifier(root, {}), "clef")
            self.assertEqual(active_classifier(root), "clef")
        with patch("hunt.gate.clef_reachable", return_value=True):
            self.assertEqual(active_classifier(root, {"ANTHROPIC_API_KEY": "sk-ant-x"}), "clef")
