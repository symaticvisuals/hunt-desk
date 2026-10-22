import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from hunt.prune import prune
from hunt.store import load_inbox, load_pruned, merge_into_inbox

TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
FIELDS = ["date", "company", "role", "base_score", "penalties", "final_score", "applied", "reached_interview", "status", "platform", "job_id", "url", "source", "location"]


def _url(number: int) -> str:
    return f"https://example.com/{number}"


def _row(number: int, posted_at: str = "2026-10-20", **extra) -> dict:
    row = {
        "company": "Acme",
        "title": "AI Engineer",
        "url": _url(number),
        "apply_url": _url(number),
        "platform": "greenhouse",
        "job_id": str(number),
        "location": "Bengaluru, India",
        "description": "Build agents with customers.",
        "source": "greenhouse",
        "posted_at": posted_at,
        "first_seen": "2026-10-20T10:00:00+00:00",
        "last_seen": "2026-10-20T10:00:00+00:00",
    }
    row.update(extra)
    return row


def _root(rows: list[dict], cache: dict | None = None, batches: list | None = None, applied: list[str] = ()) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    place = {"mode": "places", "countries": ["India"], "work_modes": ["onsite", "hybrid", "remote"]}
    (root / "profile/search.json").write_text(json.dumps({"keywords": ["AI Engineer"], "exclude_title": [], "location": place, "sources": []}), encoding="utf-8")
    (root / "profile/rubric.json").write_text(json.dumps({"max_years_without_cap": 5, "screenout_cap": 75}), encoding="utf-8")
    (root / "profile/preferences.json").write_text(json.dumps({"confirmed": True, "roles": ["AI Engineer"], "seniority": [], "exclude_titles": []}), encoding="utf-8")
    (root / "profile/worth.json").write_text(json.dumps({"max_posted_days": 2}), encoding="utf-8")
    (root / "data/inbox.json").write_text(json.dumps(rows), encoding="utf-8")
    (root / "data/clef-cache.json").write_text(json.dumps(cache or {}), encoding="utf-8")
    (root / "data/batches.json").write_text(json.dumps(batches or []), encoding="utf-8")
    with (root / "data/evaluated-jobs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for url in applied:
            writer.writerow({"date": "2026-10-01", "company": "Acme", "role": "AI Engineer", "final_score": "85", "applied": "Yes", "url": url})
    return root


class PruneTests(unittest.TestCase):
    def test_old_and_closed_roles_leave_the_inbox(self):
        rows = [
            _row(1),
            _row(2, posted_at="2026-10-10"),
            _row(3, posted_at="", first_seen="2026-10-10T10:00:00+00:00", last_seen="2026-10-21T09:00:00+00:00"),
            _row(4, live="closed"),
            _row(5, posted_at="", first_seen="2026-09-01T10:00:00+00:00", last_seen="2026-09-10T10:00:00+00:00"),
            _row(6, posted_at="2026-10-01"),
        ]
        root = _root(rows, applied=[_url(6)])

        counts = prune(root, TODAY)

        self.assertEqual(counts["roles"], 4)
        self.assertEqual({row["url"] for row in load_inbox(root)}, {_url(1), _url(6)})
        self.assertEqual(set(load_pruned(root)), {_url(2), _url(3), _url(4), _url(5)})
        self.assertEqual(prune(root, TODAY)["roles"], 0)

    def test_a_role_scored_above_the_cutoff_stays_until_it_closes(self):
        root = _root([_row(1, posted_at="2026-10-01"), _row(2, posted_at="2026-10-01"), _row(3, posted_at="2026-10-01", live="closed")])
        with (root / "data/evaluated-jobs.csv").open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            for number, score in ((1, "82"), (2, "75"), (3, "90")):
                writer.writerow({"company": "Acme", "role": "AI Engineer", "final_score": score, "url": _url(number)})

        prune(root, TODAY)

        self.assertEqual({row["url"] for row in load_inbox(root)}, {_url(1)})
        self.assertEqual(set(load_pruned(root)), {_url(2), _url(3)})

    def test_a_pruned_role_listed_again_is_not_added_back(self):
        root = _root([_row(1), _row(3, posted_at="", first_seen="2026-10-10T10:00:00+00:00", also_at=["https://board.example/3"])])
        prune(root, TODAY)

        added = merge_into_inbox(root, [_row(3, posted_at="", first_seen=""), _row(7, url="https://board.example/3")], NOW)

        self.assertEqual(added, [])
        self.assertEqual([row["url"] for row in load_inbox(root)], [_url(1)])

    def test_decisions_are_kept_only_for_roles_in_the_inbox_or_the_last_search(self):
        decision = {"keep": True, "fit": 3, "worth": 0.9}
        cache = {_url(1): decision, _url(2): decision, _url(50): decision, _url(99): decision}
        root = _root([_row(1), _row(2, posted_at="2026-10-01")], cache=cache)
        (root / "data/last-search.json").write_text(json.dumps({"jobs": [{"url": _url(50)}]}), encoding="utf-8")

        counts = prune(root, TODAY)

        self.assertEqual(counts["decisions"], 2)
        self.assertEqual(set(json.loads((root / "data/clef-cache.json").read_text())), {_url(1), _url(50)})

    def test_finished_batches_keep_their_failures_and_drop_the_postings(self):
        jobs = {"job-1": {"url": _url(1), "description": "long text"}}
        batches = [
            {"id": "a", "kind": "score", "status": "done", "created": "x", "summary": "s", "jobs": jobs, "failed_urls": [_url(1)]},
            {"id": "b", "kind": "score", "status": "open", "created": "y", "jobs": jobs},
        ]
        root = _root([_row(1)], batches=batches)

        counts = prune(root, TODAY)

        done, open_ = json.loads((root / "data/batches.json").read_text())
        self.assertEqual(counts["batches"], 1)
        self.assertNotIn("jobs", done)
        self.assertEqual(done["failed_urls"], [_url(1)])
        self.assertEqual(done["summary"], "s")
        self.assertEqual(open_["jobs"], jobs)


if __name__ == "__main__":
    unittest.main()
