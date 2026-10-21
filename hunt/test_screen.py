import unittest
from datetime import date, datetime, timezone

from hunt.screen import looks_closed, newest_first, posted_on, prepare, worth_custom_resume, years_blocks
from hunt.sources import Job


class ScreenTests(unittest.TestCase):
    def test_dates_and_relative_posting_words(self):
        today = date(2026, 10, 15)
        self.assertEqual(posted_on("2026-10-02T18:00:00Z", today), "2026-10-02")
        self.assertEqual(
            posted_on(1_760_000_000_000, today),
            datetime.fromtimestamp(1_760_000_000, timezone.utc).date().isoformat(),
        )
        self.assertEqual(posted_on("Posted Yesterday", today), "2026-10-14")
        self.assertEqual(posted_on("Posted 3 Days Ago", today), "2026-10-12")

    def test_big_org_years_drop_and_newest_first(self):
        jobs = [
            Job("Startup", "Engineer", "https://s.example/1", "x", "1", "", "5 years in Python", "feed", posted_at="2026-10-01"),
            Job("Stripe", "Engineer", "https://b.example/1", "greenhouse", "2", "", "8+ years of experience", "greenhouse", posted_at="2026-10-10", big=True),
            Job("Stripe", "Engineer", "https://b.example/2", "greenhouse", "3", "", "This position has been filled", "greenhouse", posted_at="2026-10-12", big=True),
            Job("OpenAI", "Engineer", "https://b.example/3", "ashby", "4", "", "Build agents", "ashby", posted_at="2026-10-11", big=True, apply_url="https://b.example/apply"),
            Job("Nowhere", "Engineer", "", "feed", "5", "", "Build agents", "feed"),
        ]
        kept, notes = prepare(jobs, 7)
        self.assertEqual([job.url for job in kept], ["https://b.example/3", "https://s.example/1"])
        self.assertTrue(any(line.startswith("big-org years") for line in notes))
        self.assertTrue(any(line.startswith("closed") for line in notes))
        self.assertTrue(years_blocks(jobs[1], 7))
        self.assertFalse(years_blocks(jobs[0], 7))
        self.assertTrue(looks_closed(jobs[2].description))

    def test_big_org_years_use_the_low_end(self):
        self.assertFalse(years_blocks({"big": True, "description": "Experience: 4-9 years."}, 5))
        self.assertTrue(years_blocks({"big": True, "description": "8+ years of experience required."}, 5))

    def test_newest_first_breaks_a_same_day_tie_by_the_later_find(self):
        morning = {"url": "a", "posted_at": "2026-10-17", "first_seen": "2026-10-17T10:47:00+05:30"}
        night = {"url": "b", "posted_at": "2026-10-17", "first_seen": "2026-10-17T15:36:00+00:00"}
        yesterday = {"url": "c", "posted_at": "2026-10-16", "first_seen": "2026-10-17T22:00:00+05:30"}
        undated = {"url": "d", "posted_at": "", "first_seen": "2026-10-16T09:00:00+05:30"}
        old_undated = {"url": "e", "posted_at": "", "first_seen": "2026-10-02T09:00:00+05:30"}
        jobs = sorted([old_undated, morning, undated, yesterday, night], key=newest_first, reverse=True)
        self.assertEqual([job["url"] for job in jobs], ["b", "a", "c", "d", "e"])

    def test_claude_waits_for_a_plausible_match(self):
        plausible = {"apply_url": "https://example.com/a", "url": "https://example.com/a", "keep": True, "fit": 3, "description": "Build it"}
        stretch = dict(plausible, fit=2)
        self.assertTrue(worth_custom_resume(plausible, 7)[0])
        self.assertEqual(worth_custom_resume(stretch, 7)[1], "stretch")
        self.assertEqual(worth_custom_resume(dict(plausible, apply_url="", url=""), 7)[1], "no apply link")


class HiddenReasonTests(unittest.TestCase):
    def ctx(self, **prefs):
        from hunt.preferences import normalize
        from hunt.screen import Context

        base = {
            "roles": ["Forward Deployed Engineer", "AI Engineer"],
            "seniority": ["mid", "senior"],
            "cities": ["Bangalore"],
            "countries": ["India"],
            "work_modes": ["onsite", "hybrid", "remote"],
            "job_types": ["fulltime"],
            "pay_floor": {"amount": 2000000, "currency": "INR", "period": "year"},
            "company_size": "any",
            "exclude_titles": ["intern"],
            "max_years": 5,
        }
        base.update(prefs)
        normalized = normalize(base)
        search = {
            "keywords": normalized["roles"],
            "exclude_title": normalized["exclude_titles"],
            "location": {"mode": "places", "cities": normalized["cities"], "countries": normalized["countries"], "work_modes": normalized["work_modes"]},
        }
        return Context(prefs=normalized, search=search, max_years=5, screenout=75, dismissed={"urls": set(), "keys": set()}, today=date(2026, 10, 15))

    def job(self, **extra):
        base = {
            "company": "Acme",
            "title": "Forward Deployed Engineer",
            "url": "https://acme.example/1",
            "apply_url": "https://acme.example/1",
            "location": "Bengaluru, India",
            "description": "Build agents with customers.",
            "posted_at": "2026-10-14",
            "jev_keep": True,
            "jev_fit": 3.4,
        }
        base.update(extra)
        return base

    def reason(self, ctx=None, **extra):
        from hunt.screen import hidden_reason

        return hidden_reason(self.job(**extra), ctx or self.ctx())

    def test_the_plausible_line_comes_from_the_rubric(self):
        import json
        import tempfile
        from dataclasses import replace
        from pathlib import Path

        from hunt.screen import context

        self.assertEqual(self.reason(jev_fit=2.52), "")
        self.assertEqual(self.reason(jev_fit=2.49), "stretch")
        self.assertEqual(self.reason(replace(self.ctx(), plausible_at=3.0), jev_fit=2.52), "stretch")
        root = Path(tempfile.mkdtemp())
        (root / "profile").mkdir()
        (root / "profile/rubric.json").write_text(json.dumps({"plausible_at": 2.5}), encoding="utf-8")
        self.assertEqual(context(root).plausible_at, 2.5)

    def test_worth_runs_before_the_score(self):
        self.assertEqual(self.reason(company="Talentgigs", final_score="90"), "staffing")
        self.assertEqual(self.reason(final_score="90"), "")

    def test_a_skip_row_is_never_classified(self):
        self.assertEqual(self.reason(company="Talentgigs", jev_keep=None, jev_fit=None), "staffing")
        self.assertEqual(self.reason(jev_keep=None, jev_fit=None), "not classified yet")

    def test_a_skip_row_never_has_its_text_fetched(self):
        from hunt.screen import needs_text

        self.assertTrue(needs_text(self.job(description="", jev_keep=None, jev_fit=None), self.ctx()))
        self.assertFalse(needs_text(self.job(company="Talentgigs", description="", jev_keep=None, jev_fit=None), self.ctx()))
        self.assertFalse(needs_text(self.job(description="", description_tries=2), self.ctx()))
        self.assertTrue(needs_text(self.job(description="JR2028008", jev_keep=None, jev_fit=None), self.ctx()))

    def test_a_kept_plausible_match_is_visible(self):
        self.assertEqual(self.reason(), "")

    def test_each_rule_has_its_reason(self):
        ctx = self.ctx()
        ctx.dismissed["urls"].add("https://acme.example/gone")
        self.assertEqual(self.reason(ctx, url="https://acme.example/gone"), "dismissed")
        self.assertEqual(self.reason(applied="Yes"), "applied")
        self.assertEqual(self.reason(posted_at="2026-09-20"), "stale")
        self.assertEqual(self.reason(title="Account Executive"), "not a target role")
        self.assertEqual(self.reason(title="Forward Deployed Engineer Intern"), "not a target role")
        self.assertEqual(self.reason(title="Director, Forward Deployed Engineering"), "seniority")
        self.assertEqual(self.reason(title="Head of Forward Deployed Engineer team"), "seniority")
        self.assertEqual(self.reason(location="Mumbai, India"), "location")
        self.assertEqual(self.reason(job_type="contract"), "job type")
        self.assertEqual(self.reason(pay_max=1500000, pay_currency="INR", pay_interval="yearly"), "below pay floor")
        self.assertEqual(self.reason(self.ctx(company_size="startup"), big=True, size_known=True), "company size")
        self.assertEqual(self.reason(live="closed"), "closed")
        self.assertEqual(self.reason(apply_url="", url=""), "closed")
        self.assertEqual(self.reason(big=True, description="10+ years required"), "years")
        self.assertEqual(self.reason(final_score="70"), "scored low")
        self.assertEqual(self.reason(jev_keep=None, jev_fit=None), "not classified yet")
        self.assertEqual(self.reason(jev_keep=False), "not a match")
        self.assertEqual(self.reason(jev_fit=2.2), "stretch")

    def test_a_score_above_the_cutoff_replaces_clef(self):
        self.assertEqual(self.reason(final_score="78", jev_keep=False), "")
        self.assertEqual(self.reason(final_score="75", jev_keep=True), "scored low")

    def test_first_seen_counts_when_the_board_gives_no_date(self):
        self.assertEqual(self.reason(posted_at="", first_seen="2026-09-01T00:00:00+00:00"), "stale")
        self.assertEqual(self.reason(posted_at="", first_seen="2026-10-14T00:00:00+00:00"), "")

    def test_the_claude_gate_ignores_the_score_cutoff(self):
        from hunt.screen import worth_custom_resume

        self.assertEqual(worth_custom_resume(self.job(), 5, self.ctx()), (True, ""))
        self.assertEqual(worth_custom_resume(self.job(jev_fit=2), 5, self.ctx()), (False, "stretch"))

    def test_max_posted_days_hides_older_postings_even_when_still_listed(self):
        from hunt.screen import needs_text

        ctx = self.ctx()
        ctx.worth_rules["max_posted_days"] = 1
        self.assertEqual(self.reason(ctx, posted_at="2026-10-14"), "")
        self.assertEqual(self.reason(ctx, posted_at="2026-10-13", last_seen="2026-10-15T09:00:00+05:30"), "posted too long ago")
        self.assertEqual(self.reason(ctx, posted_at="", first_seen="2026-10-12T00:00:00+00:00"), "posted too long ago")
        self.assertFalse(needs_text(self.job(posted_at="2026-10-10", description="", jev_keep=None, jev_fit=None), ctx))
        self.assertEqual(self.reason(posted_at="2026-10-08"), "")

    def test_an_old_posting_the_board_still_lists_is_not_stale(self):
        self.assertEqual(self.reason(posted_at="2026-09-10", last_seen="2026-10-15T09:00:00+05:30"), "")
        self.assertEqual(self.reason(posted_at="2026-09-10", last_seen="2026-10-10T09:00:00+05:30"), "stale")
        self.assertEqual(self.reason(posted_at="2026-10-08", last_seen="2026-10-08T09:00:00+05:30"), "")
