"""Study plan: phases, weekly control, practice sessions and progress marks."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import plan  # noqa: E402
from app.store import Store  # noqa: E402

SETTINGS = {"exam_date": "2027-11-01"}
NOW = datetime(2026, 12, 1, 20, 0)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, day, **kwargs):
        defaults = {
            "source": "exercise", "category": "C3", "attempted": 10,
            "correct": 7, "abandoned": 0, "overtime": 0, "minutes": 90,
            "note": "",
        }
        defaults.update(kwargs)
        return self.store.add_practice_session(day=day, **defaults)

    def test_phase_and_week_are_computed_from_the_phase_start(self):
        phases = plan._phase_list(SETTINGS)
        phase = plan.phase_for(phases, NOW.date())
        self.assertEqual(phase["key"], "foundation")
        self.assertEqual(phase["week"], 4)  # 2026-11-09 + 3 weeks
        self.assertGreaterEqual(phase["weeks"], 10)

    def test_weekly_actuals_and_target(self):
        self.add("2026-12-01", source="real_exam", category="C3",
                 attempted=20, correct=16, abandoned=1, overtime=1, minutes=200)
        self.add("2026-11-15", source="exercise", category="C1",
                 attempted=20, correct=15, minutes=180)
        data = plan.overview(self.store, self.tmp, SETTINGS, now=NOW)
        self.assertEqual(data["week"]["target"], 20)
        self.assertEqual(data["week"]["attempted"], 20)
        self.assertEqual(data["week"]["correct"], 16)
        self.assertEqual(data["week"]["accuracy"], 0.8)
        self.assertEqual(data["week"]["behind"], 0)
        self.assertEqual(data["week"]["status"], "on_track")
        self.assertEqual(data["week"]["avg_minutes"], 10.0)
        self.assertEqual(data["phase_summary"]["attempted"], 40)

    def test_targets_count_all_time_progress(self):
        self.add("2026-12-01", source="real_exam", attempted=20)
        self.add("2026-11-15", source="exercise", attempted=20)
        self.store.set_plan_progress("prototype:A1", "done", accuracy=0.9)
        self.store.set_plan_progress("soil:1", "done")
        self.store.set_plan_progress("standard:C3", "done")
        data = plan.overview(self.store, self.tmp, SETTINGS, now=NOW)
        targets = {row["key"]: row for row in data["targets"]}
        self.assertEqual(targets["real_exam"]["done"], 20)
        self.assertEqual(targets["questions"]["done"], 40)
        self.assertEqual(targets["prototype"]["done"], 1)
        self.assertEqual(targets["soil"]["done"], 1)
        self.assertEqual(targets["standard"]["done"], 1)

    def test_must_have_accuracy_and_advice(self):
        self.add("2026-12-01", attempted=20, correct=16)  # C3 = must-have
        data = plan.overview(self.store, self.tmp, SETTINGS, now=NOW)
        self.assertEqual(data["must_accuracy"], 0.8)
        self.assertTrue(any("85%" in line for line in data["advice"]))

    def test_behind_advice(self):
        self.add("2026-12-01", attempted=5, correct=4, minutes=80)
        data = plan.overview(self.store, self.tmp, SETTINGS, now=NOW)
        self.assertEqual(data["week"]["behind"], -15)
        self.assertTrue(any("还差 15 题" in line for line in data["advice"]))

    def test_weekly_hours_scale_the_target(self):
        self.assertEqual(plan.save_config(self.tmp, 10)["weekly_hours"], 10)
        data = plan.overview(self.store, self.tmp, SETTINGS, now=NOW)
        self.assertEqual(data["week"]["target"], 18)  # 20 * 10/11

    def test_baseline_phase_has_baseline_tasks(self):
        data = plan.overview(
            self.store, self.tmp, SETTINGS,
            now=datetime(2026, 11, 3, 9, 0),
        )
        self.assertEqual(data["phase"]["key"], "baseline")
        self.assertTrue(any("基线" in task["label"] for task in data["tasks"]))
        self.assertEqual(data["week"]["target"], 0)

    def test_progress_keys_are_limited(self):
        self.assertTrue(plan.valid_progress_key("prototype:A1"))
        self.assertTrue(plan.valid_progress_key("soil:9"))
        self.assertTrue(plan.valid_progress_key("standard:C3"))
        self.assertFalse(plan.valid_progress_key("prototype:X1"))
        self.assertFalse(plan.valid_progress_key("soil:10"))
        self.assertFalse(plan.valid_progress_key("misc:1"))

    def test_session_validation(self):
        with self.assertRaisesRegex(ValueError, "作答题数"):
            self.add("2026-12-01", attempted=0)
        with self.assertRaisesRegex(ValueError, "做对数"):
            self.add("2026-12-01", attempted=10, correct=11)
        with self.assertRaisesRegex(ValueError, "未知来源"):
            self.add("2026-12-01", source="video")
        with self.assertRaisesRegex(ValueError, "未知类别"):
            self.add("2026-12-01", category="C99")

    def test_recent_sessions_are_listed(self):
        self.add("2026-12-01", note="一套")
        data = plan.overview(self.store, self.tmp, SETTINGS, now=NOW)
        self.assertEqual(len(data["recent_sessions"]), 1)
        self.assertEqual(data["recent_sessions"][0]["note"], "一套")


class DrillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drill-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_range_and_week_drill(self):
        self.assertEqual(
            plan.qids_in_range("C3-8", "C3-10"),
            ["C3-8", "C3-9", "C3-10"],
        )
        data = plan.week_drill(
            self.store, self.tmp, SETTINGS, per_prototype=3, limit=9,
        )
        self.assertEqual(data["count"], 9)
        self.assertEqual(len(data["tasks"]), 3)
        self.assertTrue(all(qid.startswith(("C3-", "C5-", "C8-", "C9-"))
                            for qid in data["qids"]))


if __name__ == "__main__":
    unittest.main()
