import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hunt.rewrite_api import DEFAULT_STEPS
from hunt.seed import seed

REPO = Path(__file__).resolve().parent.parent


def _root_with_defaults() -> Path:
    root = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "profile/defaults", root / "profile/defaults")
    shutil.copytree(REPO / "data/defaults", root / "data/defaults")
    return root


class SeedTests(unittest.TestCase):
    def test_copies_every_default_once_and_never_overwrites(self):
        root = _root_with_defaults()
        self.assertEqual(
            seed(root),
            [
                "profile/og-resume.md",
                "profile/rubric.json",
                "profile/search.json",
                "profile/models.json",
                "profile/worth.json",
                "data/watchlist.json",
            ],
        )
        (root / "profile/rubric.json").write_text("{}", encoding="utf-8")
        self.assertEqual(seed(root), [])
        self.assertEqual((root / "profile/rubric.json").read_text(encoding="utf-8"), "{}")
        self.assertEqual((root / "profile/og-resume.md").read_text(encoding="utf-8").strip(), "PLACEHOLDER_RESUME")

    def test_a_missing_default_is_skipped(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(seed(root), [])

    def test_shipped_defaults_are_neutral(self):
        rubric = json.loads((REPO / "profile/defaults/rubric.json").read_text(encoding="utf-8"))
        self.assertEqual((rubric["screenout_cap"], rubric["bonuses"], rubric["max_years_without_cap"]), (75, [], None))
        search = json.loads((REPO / "profile/defaults/search.json").read_text(encoding="utf-8"))
        self.assertEqual((search["keywords"], search["source_options"]["jobspy"]["hours_old"]), ([], 48))
        models = json.loads((REPO / "profile/defaults/models.json").read_text(encoding="utf-8"))
        self.assertEqual((models["classifier"], models["steps"]), ("haiku", DEFAULT_STEPS))


if __name__ == "__main__":
    unittest.main()
