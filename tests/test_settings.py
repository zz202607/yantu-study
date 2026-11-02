"""Study settings: defaults, validation and atomic persistence."""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.settings import DEFAULTS, Settings  # noqa: E402


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="settings-")
        self.settings = Settings(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_when_no_file(self):
        self.assertEqual(self.settings.load(), DEFAULTS)

    def test_update_merges_and_persists(self):
        data = self.settings.update({"exam_date": "2027-11-06", "daily_target": 12})
        self.assertEqual(data["exam_date"], "2027-11-06")
        self.assertEqual(data["daily_target"], 12)
        self.assertEqual(self.settings.load()["exam_date"], "2027-11-06")

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self.settings.update({"exam_date": "2027/11/06"})

    def test_exam_must_follow_plan_start(self):
        with self.assertRaises(ValueError):
            self.settings.update({"exam_date": "2026-01-01"})

    def test_daily_target_range_is_enforced(self):
        for value in (0, 61, "many"):
            with self.assertRaises(ValueError):
                self.settings.update({"daily_target": value})

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.settings.update({"theme": "dark"})

    def test_corrupt_file_falls_back_without_crashing(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(self.settings.load(), DEFAULTS)

    def test_partial_file_is_merged_with_defaults(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"daily_target": 8}, fh)
        data = self.settings.load()
        self.assertEqual(data["daily_target"], 8)
        self.assertEqual(data["exam_date"], DEFAULTS["exam_date"])

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        self.settings.update({"daily_target": 9})
        leftovers = [name for name in os.listdir(self.tmp)
                     if name.startswith(".settings-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
