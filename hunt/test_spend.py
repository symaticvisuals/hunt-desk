import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path

from hunt.spend import (
    DEFAULT_PRICES,
    LEDGER,
    attach,
    cost,
    estimate,
    prices,
    record,
    release,
    reserve,
    room,
    settle,
    spent,
    summary,
)


def _root(classify: float = 0.01, score: float = 1.0) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    budget = {"classify_usd_per_day": classify, "score_usd_per_day": score}
    (root / "profile/preferences.json").write_text(json.dumps({"budget": budget}), encoding="utf-8")
    return root


def _ledger(root: Path) -> list[dict]:
    return json.loads((root / LEDGER).read_text(encoding="utf-8"))["entries"]


def _in_threads(count: int, work) -> list[int]:
    results: list[int] = []
    lock = threading.Lock()

    def one() -> None:
        value = work()
        with lock:
            results.append(value)

    threads = [threading.Thread(target=one) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


class PriceTests(unittest.TestCase):
    def test_cost_applies_the_batch_discount(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        self.assertAlmostEqual(cost(DEFAULT_PRICES, "claude-haiku-5-5", usage, batch=True), 0.30)

    def test_cache_reads_and_writes_have_their_own_rates(self):
        usage = {"cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(cost(DEFAULT_PRICES, "claude-opus-5-5", usage, batch=False), 5.20)

    def test_an_estimate_is_an_upper_bound(self):
        self.assertAlmostEqual(estimate(DEFAULT_PRICES, "claude-haiku-5-5", 6000, 2000, batch=True), 0.00057505, places=8)

    def test_an_unknown_model_is_priced_as_the_dearest(self):
        self.assertAlmostEqual(cost(DEFAULT_PRICES, "claude-new", {"output_tokens": 1_000_000}, batch=False), 20.0)

    def test_models_json_prices_override_the_table(self):
        root = _root()
        (root / "profile/models.json").write_text(
            json.dumps({"prices": {"checked": "2026-10-06", "claude-haiku-5-5": {"input": 1, "output": 1}}}), encoding="utf-8"
        )
        table = prices(root)
        self.assertEqual(table["claude-haiku-5-5"]["input"], 1)
        self.assertNotIn("checked", table)
        self.assertEqual(table["claude-opus-5-5"], DEFAULT_PRICES["claude-opus-5-5"])


class LedgerTests(unittest.TestCase):
    def test_reserve_takes_the_longest_prefix_that_fits(self):
        root = _root()
        token, count = reserve(root, "classify", [0.004, 0.004, 0.004])
        self.assertTrue(token)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(spent(root, "classify"), 0.008)
        self.assertAlmostEqual(room(root, "classify"), 0.002)

    def test_settle_replaces_the_reservation(self):
        root = _root()
        token, _ = reserve(root, "classify", [0.004])
        attach(root, token, "msgbatch_x")
        settle(root, "msgbatch_x", 0.001)
        self.assertAlmostEqual(spent(root, "classify"), 0.001)
        self.assertEqual(reserve(root, "classify", [0.02]), ("", 0))

    def test_release_drops_the_reservation(self):
        root = _root()
        token, _ = reserve(root, "score", [0.5])
        release(root, token)
        self.assertEqual(spent(root, "score"), 0.0)

    def test_score_and_tailor_share_one_cap(self):
        root = _root()
        record(root, "tailor", 0.5)
        self.assertAlmostEqual(spent(root, "score"), 0.5)
        self.assertEqual(summary(root), "classify $0.00/$0.01 · score $0.50/$1.00")

    def test_enforce_false_ignores_the_cap(self):
        root = _root()
        self.assertEqual(reserve(root, "score", [5.0], enforce=False)[1], 1)

    def test_a_zero_cap_reserves_nothing(self):
        root = _root(classify=0)
        self.assertEqual(reserve(root, "classify", [0.0001]), ("", 0))

    def test_two_writers_never_overshoot(self):
        root = _root(classify=0.1)
        counts = _in_threads(4, lambda: reserve(root, "classify", [0.03] * 10)[1])
        self.assertEqual(sum(counts), 3)
        self.assertLessEqual(spent(root, "classify"), 0.1)

    def test_entries_older_than_sixty_days_are_dropped_on_write(self):
        root = _root()
        old_day = (date.today() - timedelta(days=61)).isoformat()
        entry = {"id": "sync-old", "day": old_day, "step": "classify", "reserved": 0.0, "actual": 0.01}
        (root / LEDGER).write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
        record(root, "classify", 0.0)
        self.assertNotIn(old_day, [item["day"] for item in _ledger(root)])


if __name__ == "__main__":
    unittest.main()
