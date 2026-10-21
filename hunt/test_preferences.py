import json
import tempfile
import unittest
from pathlib import Path

from hunt.preferences import (
    PREFERENCES_SYSTEM,
    compile_preferences,
    is_confirmed,
    job_type_ok,
    load,
    migrate,
    normalize,
    pay_ok,
    places_ok,
    propose,
    save,
    seniority_ok,
    seniority_of,
    size_ok,
    work_mode_of,
)
from hunt.test_rewrite_api import RESUME, FakeClient

RULE = {"mode": "places", "cities": ["Bangalore", "Gurgaon"], "countries": ["India"], "work_modes": ["onsite", "hybrid", "remote"]}


def _root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "profile").mkdir()
    (root / "data").mkdir()
    (root / "profile/og-resume.md").write_text(RESUME, encoding="utf-8")
    (root / "profile/search.json").write_text(
        json.dumps(
            {
                "keywords": ["software engineer", "freelance"],
                "exclude_title": ["intern"],
                "location": {"mode": "any"},
                "sources": ["greenhouse", "jobspy"],
                "source_options": {"jobspy": {"sites": ["naukri"], "locations": ["Pune"], "hours_old": 48}},
            }
        ),
        encoding="utf-8",
    )
    (root / "profile/rubric.json").write_text(
        json.dumps({"natural_hire_at": 80, "max_years_without_cap": 5, "comp": {"min": None, "max": None, "currency": None}}),
        encoding="utf-8",
    )
    (root / "data/target-positions.json").write_text(
        json.dumps({"confirmed": True, "positions": ["Forward Deployed Engineer", "Software Engineer"]}), encoding="utf-8"
    )
    return root


class PreferenceFileTests(unittest.TestCase):
    def test_migration_builds_an_unconfirmed_file_from_todays_settings(self):
        root = _root()
        prefs = migrate(root)
        self.assertFalse(prefs["confirmed"])
        self.assertEqual(prefs["roles"], ["software engineer", "freelance"])
        self.assertEqual(prefs["cities"], ["Pune"])
        self.assertEqual(prefs["max_years"], 5)
        self.assertEqual(prefs["exclude_titles"], ["intern"])
        self.assertEqual(prefs["work_modes"], ["onsite", "hybrid", "remote"])
        self.assertFalse(is_confirmed(root))
        self.assertEqual(migrate(root), load(root))

    def test_normalize_drops_unknown_values(self):
        prefs = normalize({"seniority": ["senior", "wizard"], "work_modes": ["remote", "space"], "company_size": "huge", "max_years": "7"})
        self.assertEqual(prefs["seniority"], ["senior"])
        self.assertEqual(prefs["work_modes"], ["remote"])
        self.assertEqual(prefs["company_size"], "any")
        self.assertEqual(prefs["max_years"], 7)

    def test_save_confirms_and_writes_only_the_owned_fields(self):
        root = _root()
        prefs = migrate(root)
        prefs.update(
            roles=["AI Engineer", "Forward Deployed Engineer"],
            cities=["Delhi"],
            work_modes=["onsite", "remote"],
            max_years=7,
            pay_floor={"amount": 2500000, "currency": "INR", "period": "year"},
            exclude_titles=["intern", "recruiter"],
        )
        save(root, prefs)
        self.assertTrue(is_confirmed(root))
        search = json.loads((root / "profile/search.json").read_text())
        self.assertEqual(search["keywords"], ["AI Engineer", "Forward Deployed Engineer"])
        self.assertEqual(search["exclude_title"], ["intern", "recruiter"])
        self.assertEqual(search["location"], {"mode": "places", "cities": ["Delhi"], "countries": [], "remote_only_countries": [], "work_modes": ["onsite", "remote"]})
        self.assertEqual(search["source_options"]["jobspy"], {"sites": ["naukri"], "locations": ["Delhi"], "hours_old": 48, "remote_countries": []})
        self.assertEqual(search["sources"], ["greenhouse", "jobspy"])
        rubric = json.loads((root / "profile/rubric.json").read_text())
        self.assertEqual(rubric["max_years_without_cap"], 7)
        self.assertEqual(rubric["comp"], {"min": 2500000, "max": None, "currency": "INR"})
        self.assertEqual(rubric["natural_hire_at"], 80)

    def test_remote_only_clears_the_jobspy_cities(self):
        root = _root()
        prefs = migrate(root)
        prefs.update(work_modes=["remote"])
        compile_preferences(root, prefs)
        search = json.loads((root / "profile/search.json").read_text())
        self.assertEqual(search["source_options"]["jobspy"]["locations"], [])


class ProposalTests(unittest.TestCase):
    def test_claude_proposes_and_empty_fields_keep_the_current_value(self):
        root = _root()
        migrate(root)
        reply = json.dumps(
            {
                "roles": ["Software Engineer", "Backend Engineer"],
                "seniority": ["mid", "senior"],
                "max_years": 8,
                "cities": [],
                "countries": [],
                "work_modes": ["onsite", "hybrid", "remote"],
                "job_types": ["fulltime"],
                "exclude_titles": ["intern"],
                "pay_floor_amount": None,
                "pay_currency": "",
                "company_size": "any",
            }
        )
        client = FakeClient({PREFERENCES_SYSTEM[:40]: reply})
        prefs, note = propose(root, client=client)
        self.assertEqual(prefs["roles"], ["Software Engineer", "Backend Engineer"])
        self.assertEqual(prefs["max_years"], 8)
        self.assertEqual(prefs["cities"], ["Pune"])
        self.assertIn("Claude", note)
        self.assertEqual(client.messages.kwargs[0]["model"], "claude-haiku-5-5")

    def test_a_failed_call_falls_back_to_the_resume_alone(self):
        root = _root()
        migrate(root)
        client = FakeClient({PREFERENCES_SYSTEM[:40]: RuntimeError("down")})
        prefs, note = propose(root, client=client)
        self.assertIn("software engineer", [role.lower() for role in prefs["roles"]])
        self.assertIn("without Claude", note)


class MatchTests(unittest.TestCase):
    def test_seniority_words_in_titles(self):
        self.assertEqual(seniority_of("Senior / Staff Fullstack Engineer"), {"senior", "staff"})
        self.assertEqual(seniority_of("Regional Director, Forward Deployed Engineering"), {"leadership"})
        self.assertEqual(seniority_of("Forward Deployed Engineer"), {"mid"})
        self.assertEqual(seniority_of("Sr. AI Engineer"), {"senior"})
        self.assertTrue(seniority_ok("Senior / Staff Engineer", ["senior"]))
        self.assertFalse(seniority_ok("Head of AI", ["mid", "senior"]))
        self.assertTrue(seniority_ok("Head of AI", []))

    def test_work_mode_comes_from_the_board_or_the_location(self):
        self.assertEqual(work_mode_of("Pune, India", "Hybrid"), "hybrid")
        self.assertEqual(work_mode_of("Delhi; Remote", ""), "remote")
        self.assertEqual(work_mode_of("Delhi", "Work from office"), "onsite")

    def test_city_aliases_and_modes(self):
        self.assertTrue(places_ok("Bengaluru, Karnataka, India", "", RULE))
        self.assertTrue(places_ok("Gurugram, Haryana", "Hybrid", RULE))
        self.assertFalse(places_ok("Mumbai, India", "", RULE))
        self.assertFalse(places_ok("Bengaluru", "Hybrid", dict(RULE, work_modes=["onsite"])))

    def test_remote_roles_and_countries(self):
        self.assertTrue(places_ok("Remote", "", RULE))
        self.assertTrue(places_ok("Remote - Anywhere", "", RULE))
        self.assertTrue(places_ok("India; Remote", "", RULE))
        self.assertFalse(places_ok("Berlin, Germany; Remote", "", RULE))
        self.assertFalse(places_ok("Remote", "", dict(RULE, work_modes=["onsite"])))

    def test_no_cities_falls_back_to_countries_then_anywhere(self):
        self.assertTrue(places_ok("Noida, India", "", dict(RULE, cities=[])))
        self.assertFalse(places_ok("London, UK", "", dict(RULE, cities=[])))
        self.assertTrue(places_ok("London, UK", "", dict(RULE, cities=[], countries=[])))

    def test_pay_floor_compares_annual_pay_in_the_same_currency(self):
        floor = {"pay_floor": {"amount": 2500000, "currency": "INR", "period": "year"}}
        self.assertFalse(pay_ok({"pay_max": 1800000, "pay_currency": "INR", "pay_interval": "yearly"}, floor))
        self.assertTrue(pay_ok({"pay_max": 250000, "pay_currency": "INR", "pay_interval": "monthly"}, floor))
        self.assertTrue(pay_ok({"pay_max": 100000, "pay_currency": "USD", "pay_interval": "yearly"}, floor))
        self.assertTrue(pay_ok({"pay_max": None}, floor))
        self.assertTrue(pay_ok({"pay_max": 10, "pay_currency": "INR"}, {"pay_floor": {"amount": None}}))

    def test_job_type_and_size_apply_only_when_known(self):
        self.assertFalse(job_type_ok({"job_type": "contract"}, {"job_types": ["fulltime"]}))
        self.assertTrue(job_type_ok({"job_type": "fulltime, contract"}, {"job_types": ["fulltime"]}))
        self.assertTrue(job_type_ok({"job_type": ""}, {"job_types": ["fulltime"]}))
        self.assertFalse(size_ok({"big": True, "size_known": True}, {"company_size": "startup"}))
        self.assertTrue(size_ok({"big": True, "size_known": False}, {"company_size": "startup"}))
        self.assertFalse(size_ok({"big": False, "size_known": True}, {"company_size": "big"}))


class JobSpyCountryTests(unittest.TestCase):
    def test_the_first_country_picks_the_indeed_and_glassdoor_site(self):
        root = _root()
        prefs = migrate(root)
        prefs.update(countries=["India", "Singapore"])
        compile_preferences(root, prefs)
        search = json.loads((root / "profile/search.json").read_text())
        self.assertEqual(search["source_options"]["jobspy"]["country_indeed"], "India")

    def test_an_unknown_country_leaves_the_setting_alone(self):
        root = _root()
        prefs = migrate(root)
        prefs.update(countries=["Atlantis"])
        compile_preferences(root, prefs)
        search = json.loads((root / "profile/search.json").read_text())
        self.assertNotIn("country_indeed", search["source_options"]["jobspy"])


RULE_US = {"mode": "places", "cities": ["Delhi"], "countries": ["India", "United States"], "work_modes": ["onsite", "hybrid", "remote"]}


class RemoteCountryTests(unittest.TestCase):
    def test_us_remote_roles_pass_and_us_onsite_roles_do_not(self):
        self.assertTrue(places_ok("San Francisco, CA; Remote", "", RULE_US))
        self.assertTrue(places_ok("Remote - US", "", RULE_US))
        self.assertTrue(places_ok("United States; Remote", "", RULE_US))
        self.assertTrue(places_ok("Seattle; Remote", "", RULE_US))
        self.assertFalse(places_ok("New York, NY", "", RULE_US))
        self.assertFalse(places_ok("London, UK; Remote", "", RULE_US))
        self.assertTrue(places_ok("New Delhi, Delhi, India", "", RULE_US))

    def test_usa_is_the_same_country(self):
        rule = dict(RULE_US, countries=["USA"])
        self.assertTrue(places_ok("Remote, United States", "", rule))
        self.assertTrue(places_ok("Austin, TX; Remote", "", rule))

    def test_remote_countries_are_compiled_only_when_remote_is_allowed(self):
        root = _root()
        prefs = migrate(root)
        prefs.update(countries=["India", "United States"], work_modes=["onsite", "remote"])
        compile_preferences(root, prefs)
        jobspy = json.loads((root / "profile/search.json").read_text())["source_options"]["jobspy"]
        self.assertEqual(jobspy["remote_countries"], ["India", "United States"])
        prefs.update(work_modes=["onsite"])
        compile_preferences(root, prefs)
        jobspy = json.loads((root / "profile/search.json").read_text())["source_options"]["jobspy"]
        self.assertEqual(jobspy["remote_countries"], [])


RULE_IN = {"mode": "places", "cities": [], "countries": ["India"], "remote_only_countries": ["United States"], "work_modes": ["onsite", "hybrid", "remote"]}


class RemoteOnlyCountryTests(unittest.TestCase):
    def test_any_indian_city_passes_even_without_the_word_india(self):
        for place in ("Bengaluru, Karnataka", "Pune, India", "Noida", "Mumbai(Mahim), India", "KA, India", "Gurugram, Haryana", "Hyderabad"):
            self.assertTrue(places_ok(place, "", RULE_IN), place)

    def test_us_roles_pass_only_when_remote(self):
        self.assertTrue(places_ok("San Francisco, CA; Remote", "", RULE_IN))
        self.assertTrue(places_ok("Remote - US", "", RULE_IN))
        self.assertFalse(places_ok("New York, NY", "", RULE_IN))
        self.assertFalse(places_ok("Seattle, WA", "Hybrid", RULE_IN))

    def test_other_countries_and_anywhere(self):
        self.assertFalse(places_ok("London, UK; Remote", "", RULE_IN))
        self.assertFalse(places_ok("Berlin, Germany", "", RULE_IN))
        self.assertTrue(places_ok("Remote", "", RULE_IN))

    def test_no_cities_searches_the_whole_country_plus_us_remote(self):
        root = _root()
        prefs = migrate(root)
        self.assertEqual(prefs["remote_only_countries"], [])
        prefs.update(cities=[], countries=["India"], remote_only_countries=["United States"])
        compile_preferences(root, prefs)
        search = json.loads((root / "profile/search.json").read_text())
        self.assertEqual(search["location"]["remote_only_countries"], ["United States"])
        jobspy = search["source_options"]["jobspy"]
        self.assertEqual(jobspy["locations"], ["India"])
        self.assertEqual(jobspy["remote_countries"], ["United States"])
        self.assertEqual(jobspy["country_indeed"], "India")

    def test_with_cities_the_country_also_gets_a_remote_search(self):
        root = _root()
        prefs = migrate(root)
        prefs.update(cities=["Delhi"], countries=["India"], remote_only_countries=["United States"])
        compile_preferences(root, prefs)
        jobspy = json.loads((root / "profile/search.json").read_text())["source_options"]["jobspy"]
        self.assertEqual(jobspy["locations"], ["Delhi"])
        self.assertEqual(jobspy["remote_countries"], ["India", "United States"])
