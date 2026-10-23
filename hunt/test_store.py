import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hunt.store import (
    Busy,
    data_lock,
    dismiss,
    is_dismissed,
    load_dismissed,
    load_inbox,
    merge,
    merge_into_inbox,
    role_key,
    run_lock,
    save_inbox,
    seed_inbox,
    write_json,
)

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)


def role(url, company="Acme", title="Forward Deployed Engineer", **extra):
    base = {
        "company": company,
        "title": title,
        "url": url,
        "apply_url": url,
        "platform": "linkedin",
        "description": "",
        "posted_at": "2026-10-14",
    }
    base.update(extra)
    return base


class DescriptionTryTests(unittest.TestCase):
    def test_listed_rows_without_a_detail_fetch_are_not_counted(self):
        greenhouse = role("https://gh.example/1", platform="greenhouse")
        jobspy = role("https://jobs.example/2", title="AI Engineer", platform="linkedin")
        rows, _ = merge([], [greenhouse, jobspy], NOW)
        rows, _ = merge(rows, [dict(greenhouse), dict(jobspy)], NOW + timedelta(hours=1))
        by_url = {row["url"]: row for row in rows}
        self.assertNotIn("description_tries", by_url["https://gh.example/1"])
        self.assertEqual(by_url["https://jobs.example/2"]["description_tries"], 1)
        rows, _ = merge(rows, [dict(jobspy, description="Build agents.")], NOW + timedelta(hours=2))
        self.assertEqual({row["url"]: row for row in rows}["https://jobs.example/2"]["description"], "Build agents.")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()

    def test_write_json_replaces_the_file_whole(self):
        path = self.root / "data/x.json"
        write_json(path, {"a": 1})
        write_json(path, {"a": 2})
        self.assertEqual(json.loads(path.read_text()), {"a": 2})
        self.assertEqual([p.name for p in (self.root / "data").iterdir()], ["x.json"])

    def test_write_json_retries_a_replace_windows_refuses(self):
        real = os.replace
        failures = iter([PermissionError(13, "in use"), PermissionError(13, "in use")])

        def flaky(src, dst):
            error = next(failures, None)
            if error:
                raise error
            real(src, dst)

        with patch("hunt.store.os.replace", side_effect=flaky):
            write_json(self.root / "data/x.json", {"a": 1})
        self.assertEqual(json.loads((self.root / "data/x.json").read_text(encoding="utf-8")), {"a": 1})

    def test_write_json_gives_up_and_leaves_no_temp_file(self):
        with patch("hunt.store.os.replace", side_effect=PermissionError(13, "in use")), patch("hunt.store.time.sleep"):
            with self.assertRaises(PermissionError):
                write_json(self.root / "data/y.json", {"a": 1})
        self.assertEqual(list((self.root / "data").glob(".y.json.*")), [])

    def test_a_second_run_is_refused_while_one_holds_the_lock(self):
        with run_lock(self.root):
            with self.assertRaises(Busy):
                with run_lock(self.root):
                    pass
        with run_lock(self.root):
            pass

    def test_data_lock_is_reentrant_in_one_thread_and_exclusive_across_threads(self):
        blocked = []

        def other():
            try:
                with data_lock(self.root, timeout=0.2):
                    blocked.append(False)
            except Busy:
                blocked.append(True)

        with data_lock(self.root):
            with data_lock(self.root):
                worker = threading.Thread(target=other)
                worker.start()
                worker.join()
        self.assertEqual(blocked, [True])

    def test_roles_accumulate_and_a_repeat_only_updates_last_seen(self):
        rows, new = merge([], [role("https://l/1")], NOW)
        later = NOW + timedelta(hours=4)
        rows, new_again = merge(rows, [role("https://l/1", description="Build agents"), role("https://l/2", title="AI Engineer")], later)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["url"] for r in new_again], ["https://l/2"])
        first = rows[0]
        self.assertEqual(first["first_seen"], NOW.isoformat())
        self.assertEqual(first["last_seen"], later.isoformat())
        self.assertEqual(first["description"], "Build agents")

    def test_the_same_role_on_two_boards_keeps_the_ats_url(self):
        board = role("https://www.linkedin.com/jobs/view/9", company="Cisco Systems Inc.")
        ats = role("https://boards.greenhouse.io/cisco/jobs/9", company="Cisco Systems", platform="greenhouse")
        rows, new = merge([], [board], NOW)
        rows, new = merge(rows, [ats], NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(new, [])
        self.assertEqual(rows[0]["url"], "https://boards.greenhouse.io/cisco/jobs/9")
        self.assertEqual(rows[0]["also_at"], ["https://www.linkedin.com/jobs/view/9"])
        rows, new = merge(rows, [role("https://www.linkedin.com/jobs/view/9", company="Cisco")], NOW)
        self.assertEqual(len(rows), 1)

    def test_a_direct_apply_link_beats_a_bare_board_link(self):
        bare = role("https://www.naukri.com/job-1", company="Epam Systems")
        direct = role("https://www.linkedin.com/jobs/view/3", company="EPAM Systems Pvt Ltd", apply_url="https://epam.com/apply/3")
        rows, _ = merge([], [bare, direct], NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://www.linkedin.com/jobs/view/3")

    def test_a_trailing_city_does_not_split_one_role(self):
        self.assertEqual(
            role_key({"company": "Acme", "title": "AI Engineer (Bengaluru)"}),
            role_key({"company": "Acme", "title": "AI Engineer - Remote"}),
        )
        self.assertNotEqual(
            role_key({"company": "Acme", "title": "AI Engineer (Generative AI)"}),
            role_key({"company": "Acme", "title": "AI Engineer"}),
        )

    def test_the_inbox_only_grows(self):
        long_gone = dict(role("https://l/gone", posted_at="2025-01-01"), first_seen="2025-01-02T00:00:00+00:00", last_seen="2025-01-03T00:00:00+00:00")
        save_inbox(self.root, [long_gone])
        merge_into_inbox(self.root, [role("https://l/new", title="AI Engineer")], NOW + timedelta(days=400))
        self.assertEqual(sorted(r["url"] for r in load_inbox(self.root)), ["https://l/gone", "https://l/new"])

    def test_a_live_ats_role_with_an_old_posting_date_survives_the_merge(self):
        old = role("https://gh/1", posted_at="2026-05-01", platform="greenhouse")
        merge_into_inbox(self.root, [old], NOW)
        merge_into_inbox(self.root, [old], NOW + timedelta(hours=1))
        self.assertEqual([r["url"] for r in load_inbox(self.root)], ["https://gh/1"])

    def test_a_dismissed_role_stays_hidden_when_it_is_posted_again(self):
        dismiss(self.root, role("https://l/1", company="Acme Technologies"))
        gone = load_dismissed(self.root)
        self.assertTrue(is_dismissed(role("https://l/1"), gone))
        self.assertTrue(is_dismissed(role("https://n/77", company="Acme"), gone))
        self.assertFalse(is_dismissed(role("https://n/78", title="Backend Engineer"), gone))

    def test_merge_into_inbox_saves_and_reports_new_roles(self):
        new = merge_into_inbox(self.root, [role("https://l/1")], NOW)
        self.assertEqual(len(new), 1)
        self.assertEqual(len(load_inbox(self.root)), 1)
        self.assertEqual(merge_into_inbox(self.root, [role("https://l/1")], NOW), [])

    def test_the_last_search_seeds_an_empty_inbox_once(self):
        write_json(self.root / "data/last-search.json", {"jobs": [role("https://l/1"), role("https://l/2", title="AI Engineer")]})
        self.assertEqual(seed_inbox(self.root, NOW), 2)
        self.assertEqual(seed_inbox(self.root, NOW), 0)
        self.assertEqual(len(load_inbox(self.root)), 2)

    def test_the_first_merge_seeds_from_the_last_search_before_adding(self):
        write_json(self.root / "data/last-search.json", {"jobs": [role("https://l/old", title="AI Engineer")]})
        new = merge_into_inbox(self.root, [role("https://l/new")], NOW)
        self.assertEqual([r["url"] for r in new], ["https://l/new"])
        self.assertEqual(sorted(r["url"] for r in load_inbox(self.root)), ["https://l/new", "https://l/old"])

    @unittest.skipIf(sys.platform == "win32", "Windows has no POSIX file modes")
    def test_write_json_keeps_the_file_mode(self):
        path = self.root / "data/mode.json"
        write_json(path, {"a": 1})
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        path.chmod(0o640)
        write_json(path, {"a": 2})
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
