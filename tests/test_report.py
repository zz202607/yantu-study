"""Weekly/monthly reports: calendar math, aggregation, saving and backup."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import report  # noqa: E402
from app.store import Store  # noqa: E402

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
SETTINGS = {
    "exam_date": "2027-11-01",
    "exam_label": "2027 注册岩土 · 专业考试",
    "plan_start": "2026-11-01",
    "daily_target": 20,
    "baseline_due": "2026-11-04",
}
NOW = datetime(2026, 12, 2, 20, 0)


def iso(moment):
    return moment.isoformat(timespec="seconds")


class PeriodTest(unittest.TestCase):
    def test_week_is_monday_to_sunday(self):
        info = report.period_info("week", "2026-12-02",
                                  today=date(2026, 12, 2))
        self.assertEqual(info["start"], "2026-11-30")
        self.assertEqual(info["end"], "2026-12-06")
        self.assertEqual(info["days"], 7)
        self.assertEqual(info["key"], "week-2026-W49")
        self.assertEqual(info["prev_anchor"], "2026-11-29")
        self.assertEqual(info["next_anchor"], "2026-12-07")
        self.assertTrue(info["current"])

    def test_week_across_the_year_uses_the_iso_year(self):
        info = report.period_info("week", "2025-12-31",
                                  today=date(2026, 1, 10))
        self.assertEqual(info["start"], "2025-12-29")
        self.assertEqual(info["end"], "2026-01-04")
        self.assertEqual(info["key"], "week-2026-W01")
        self.assertFalse(info["current"])

    def test_month_bounds_and_leap_year(self):
        info = report.period_info("month", "2028-02-15",
                                  today=date(2028, 2, 15))
        self.assertEqual(info["start"], "2028-02-01")
        self.assertEqual(info["end"], "2028-02-29")
        self.assertEqual(info["key"], "month-2028-02")
        self.assertEqual(info["prev_anchor"], "2028-01-31")
        self.assertEqual(info["next_anchor"], "2028-03-01")

    def test_month_december_points_at_next_january(self):
        info = report.period_info("month", "2026-12-10",
                                  today=date(2026, 12, 10))
        self.assertEqual(info["end"], "2026-12-31")
        self.assertEqual(info["next_anchor"], "2027-01-01")

    def test_bad_period_and_date_are_rejected(self):
        with self.assertRaises(ValueError):
            report.period_info("quarter", "2026-12-02")
        with self.assertRaises(ValueError):
            report.period_info("week", "not-a-date")


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="report-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_session(self, day, **kwargs):
        defaults = {
            "source": "exercise", "category": "C3", "attempted": 10,
            "correct": 7, "abandoned": 0, "overtime": 0, "minutes": 90,
        }
        defaults.update(kwargs)
        return self.store.add_practice_session(day=day, **defaults)

    def add_attempts(self, qids, at=BASE):
        result = self.store.add_attempts([
            {"qid": qid, "at": iso(at), "source": "batch"}
            for qid in qids
        ])
        self.assertFalse(result["errors"])

    def build(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return report.build(self.store, self.tmp, SETTINGS, **kwargs)

    def test_empty_week_is_reported_as_empty(self):
        data = self.build(period="week", anchor="2026-11-02")
        self.assertEqual(data["practice"]["attempted"], 0)
        self.assertEqual(data["review"]["reviews"], 0)
        self.assertIsNone(data["practice"]["accuracy"])
        self.assertIn("没有", data["encouragement"]["text"])
        self.assertIn("没有活动记录", data["markdown"])
        self.assertTrue(any(action["kind"] == "baseline"
                            for action in data["next_actions"]))
        self.assertFalse(data["baseline"]["available"])

    def test_week_aggregates_only_sessions_inside_the_week(self):
        # The previous period comparison uses only its first three days.
        self.add_session("2026-11-25", category="C5", attempted=10,
                         correct=8, minutes=100)
        self.add_session("2026-11-29", category="C3", attempted=9,
                         correct=9, minutes=80)  # outside the compare slice
        self.add_session("2026-11-30", category="C3", attempted=10,
                         correct=7, minutes=90)
        self.add_session("2026-12-06", category="C5", attempted=6,
                         correct=3, minutes=60, abandoned=1, overtime=1)
        self.add_session("2026-12-07", category="C3", attempted=9,
                         correct=9, minutes=80)  # next week
        data = self.build(period="week", anchor="2026-12-02")
        practice = data["practice"]
        self.assertEqual(practice["sessions"], 2)
        self.assertEqual(practice["attempted"], 16)
        self.assertEqual(practice["correct"], 10)
        self.assertEqual(practice["accuracy"], 0.625)
        self.assertEqual(practice["abandoned"], 1)
        self.assertEqual(practice["overtime"], 1)
        self.assertEqual(practice["target"], 20)
        # Wednesday: three days of the 20/week pace are due (8.57 -> 9).
        self.assertEqual(practice["target_to_date"], 9)
        self.assertEqual(practice["behind"], 7)
        self.assertEqual(practice["status"], "ahead")
        self.assertEqual(practice["behind_basis"], "to_date")
        cats = {row["key"]: row for row in data["categories"]}
        self.assertEqual(cats["C3"]["attempted"], 10)
        self.assertEqual(cats["C3"]["accuracy"], 0.7)
        self.assertEqual(cats["C5"]["attempted"], 6)
        self.assertEqual(cats["C5"]["accuracy"], 0.5)
        self.assertEqual(data["previous"]["practice"]["attempted"], 10)
        self.assertEqual(data["previous"]["delta"]["attempted"], 6)
        self.assertEqual(data["previous"]["delta"]["accuracy"], -17.5)
        self.assertEqual(data["previous"]["compare"]["label"], "上期同期")
        self.assertEqual(data["previous"]["compare"]["days"], 3)
        self.assertIn("截至今日目标 9", data["markdown"])
        self.assertIn("超额 7", data["markdown"])
        self.assertFalse(any(action["kind"] == "behind"
                             for action in data["next_actions"]))

    def test_month_target_prorates_across_a_phase_boundary(self):
        # November 2026: 1..8 are the baseline phase (target 0), 9..30 are
        # the foundation phase (20/week). 22 * 20 / 7 = 62.86 -> 63.
        data = self.build(period="month", anchor="2026-11-15")
        self.assertEqual(data["key"], "month-2026-11")
        self.assertEqual(data["practice"]["target"], 63)
        self.assertEqual(data["practice"]["target_to_date"], 63)
        self.assertEqual(data["practice"]["behind_basis"], "full")
        self.assertEqual(data["phase"]["name"], "基础期")
        self.assertEqual(data["previous"]["compare"]["label"], "上期")

    def test_in_progress_month_uses_the_elapsed_target(self):
        # On 11-02 only the baseline phase (target 0) has elapsed, so the
        # month-to-date target is zero and cannot call 42 questions behind.
        self.add_session("2026-11-02", attempted=42, correct=28, minutes=300)
        data = self.build(period="month", anchor="2026-11-02",
                          now=datetime(2026, 11, 2, 20, 0))
        practice = data["practice"]
        self.assertEqual(practice["target"], 63)
        self.assertEqual(practice["target_to_date"], 0)
        self.assertEqual(practice["behind"], 42)
        self.assertEqual(practice["status"], "ahead")
        self.assertEqual(practice["behind_basis"], "to_date")
        self.assertEqual(data["previous"]["compare"]["label"], "上期同期")
        self.assertEqual(data["previous"]["compare"]["days"], 2)
        self.assertIn("截至今日目标 0", data["markdown"])

    def test_upcoming_week_is_not_called_behind(self):
        self.add_attempts(["C1-1"], at=BASE)
        data = self.build(period="week", anchor="2026-11-09",
                          now=datetime(2026, 11, 4, 20, 0))
        practice = data["practice"]
        self.assertEqual(practice["status"], "upcoming")
        self.assertEqual(practice["behind"], 0)
        self.assertEqual(practice["behind_basis"], "none")
        self.assertIn("还没开始", data["encouragement"]["text"])
        self.assertFalse(any("没有练习记录" in action["title"]
                             for action in data["next_actions"]))
        self.assertFalse(data["previous"]["compare"]["comparable"])
        self.assertIn("未开始", data["markdown"])

    def test_review_logs_are_counted_in_the_period(self):
        self.add_attempts(["C3-11", "C5-7"])
        session = self.store.start_session(now=BASE + timedelta(days=2))
        self.store.grade(session["id"], "C3-11", "correct",
                         now=BASE + timedelta(days=2))
        self.store.grade(session["id"], "C5-7", "wrong",
                         now=BASE + timedelta(days=2))
        data = self.build(period="week", anchor="2026-11-03",
                          now=datetime(2026, 11, 3, 20, 0))
        review = data["review"]
        self.assertEqual(review["reviews"], 2)
        self.assertEqual(review["correct"], 1)
        self.assertEqual(review["wrong"], 1)
        self.assertEqual(review["accuracy"], 0.5)
        self.assertEqual(review["recovered"], 1)
        self.assertEqual(review["sessions"], 1)
        cats = {row["key"]: row for row in data["categories"]}
        self.assertEqual(cats["C3"]["reviews"], 1)
        self.assertEqual(cats["C3"]["review_accuracy"], 1.0)
        self.assertEqual(cats["C5"]["review_accuracy"], 0.0)
        # The attempts were recorded on 11-01, outside this week.
        self.assertEqual(data["book"]["new_errors"], 0)

    def test_mastery_transition_is_in_the_next_week(self):
        self.add_attempts(["C1-1"])
        first = self.store.start_session(now=BASE + timedelta(days=2))
        self.store.grade(first["id"], "C1-1", "correct",
                         now=BASE + timedelta(days=2))
        second_day = BASE + timedelta(days=9)
        second = self.store.start_session(now=second_day)
        self.store.grade(second["id"], "C1-1", "correct", now=second_day)
        data = self.build(period="week", anchor="2026-11-10",
                          now=datetime(2026, 11, 10, 20, 0))
        self.assertEqual(data["review"]["mastered"], 1)
        self.assertEqual(data["book"]["mastered"], 1)
        self.assertEqual(data["book"]["active"], 0)

    def test_baseline_categories_and_status_are_attached(self):
        baseline = {
            "available": True,
            "complete": True,
            "score": 66,
            "max_score": 100,
            "pass_line": 60,
            "passed": True,
            "answered": 44,
            "correct": 33,
            "accuracy": 0.75,
            "halves": [
                {"half": "A", "label": "上午卷", "recorded": True,
                 "day": "2026-11-03", "answered": 22, "correct": 17,
                 "skipped": 3, "overtime": 2, "score": 34,
                 "max_score": 50, "accuracy": 0.77},
                {"half": "P", "label": "下午卷", "recorded": True,
                 "day": "2026-11-04", "answered": 22, "correct": 16,
                 "skipped": 3, "overtime": 1, "score": 32,
                 "max_score": 50, "accuracy": 0.73},
            ],
            "by_category": {
                "浅基础": {"n": 9, "answered": 8, "correct": 6,
                          "accuracy": 0.75},
            },
        }
        data = self.build(period="week", anchor="2026-11-03",
                          now=datetime(2026, 11, 4, 20, 0),
                          baseline=baseline)
        self.assertTrue(data["baseline"]["complete"])
        self.assertTrue(data["baseline"]["recorded_in_period"])
        cats = {row["key"]: row for row in data["categories"]}
        self.assertEqual(cats["C3"]["baseline_accuracy"], 0.75)
        self.assertFalse(any(action["kind"] == "baseline"
                             for action in data["next_actions"]))
        self.assertIn("已过线", data["markdown"])

    def test_missing_baseline_asks_for_it(self):
        data = self.build(period="week", anchor="2026-11-03",
                          now=datetime(2026, 11, 4, 20, 0))
        action = next(action for action in data["next_actions"]
                      if action["kind"] == "baseline")
        self.assertIn("基线", action["title"])
        self.assertIn("2026-11-04", action["detail"])

    def test_weak_category_produces_a_standards_action(self):
        self.add_session("2026-11-30", category="C3", attempted=4,
                         correct=1, minutes=40)
        baseline = {"available": True, "complete": True, "halves": []}
        data = self.build(period="week", anchor="2026-12-02",
                          baseline=baseline)
        weak = [action for action in data["next_actions"]
                if action["kind"] == "weak"]
        self.assertTrue(weak)
        self.assertEqual(weak[0]["category"], "C3")
        self.assertEqual(weak[0]["route"], "standards")

    def test_save_replaces_the_same_period(self):
        data = self.build(period="week", anchor="2026-12-02")
        first = report.save(self.tmp, data, now=NOW)
        second = report.save(self.tmp, data,
                             now=NOW + timedelta(minutes=5))
        self.assertEqual(first["key"], second["key"])
        folder = os.path.join(self.tmp, "reports")
        self.assertEqual(sorted(os.listdir(folder)),
                         ["week-2026-W49.json", "week-2026-W49.md"])
        saved = report.list_saved(self.tmp)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["saved_at"], "2026-12-02T20:05:00")
        loaded = report.load_saved(self.tmp, first["key"])
        self.assertIn("# 2026 年第 49 周", loaded["markdown"])
        self.assertTrue(os.path.exists(report.report_file(
            self.tmp, first["key"])))
        self.assertTrue(report.delete_saved(self.tmp, first["key"]))
        self.assertEqual(report.list_saved(self.tmp), [])
        self.assertFalse(report.delete_saved(self.tmp, first["key"]))

    def test_saved_report_key_must_be_a_period(self):
        data = self.build(period="week", anchor="2026-12-02")
        data["key"] = "../escape"
        with self.assertRaises(ValueError):
            report.save(self.tmp, data)
        with self.assertRaises(ValueError):
            report.load_saved(self.tmp, "week-2026-W49/../x")

    def test_backup_carries_saved_reports(self):
        data = self.build(period="week", anchor="2026-12-02")
        meta = report.save(self.tmp, data, now=NOW)
        path = Store(self.tmp).backup()
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            manifest = zf.read("manifest.json").decode("utf-8")
        self.assertIn("reports/%s.md" % meta["key"], names)
        self.assertIn("reports/%s.json" % meta["key"], names)
        self.assertIn('"reports": 2', manifest)
        target = os.path.join(self.tmp, "restored")
        Store.restore_from_zip(path, target)
        self.assertTrue(os.path.exists(
            os.path.join(target, "reports", meta["key"] + ".md")))


if __name__ == "__main__":
    unittest.main()
