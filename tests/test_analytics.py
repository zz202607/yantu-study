"""Dashboard analytics: countdown, streak, categories, coaching, baseline."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import analytics  # noqa: E402
from app.analytics import case_bank_counts  # noqa: E402
from app.curriculum import category_key  # noqa: E402


def attempt(qid, family, status, times, created, last, next_at,
            last_result=None, last_review_at=None, id=None):
    return {
        "id": id or 1,
        "qid": qid,
        "family": family,
        "status": status,
        "times": times,
        "created_at": created,
        "last_at": last,
        "next_at": next_at,
        "last_result": last_result,
        "last_review_at": last_review_at,
    }


def log(qid, at, result, before=None, after=None, last_before=None):
    return {
        "qid": qid,
        "at": at,
        "result": result,
        "status_before": before,
        "status_after": after,
        "last_result_before": last_before,
    }


class AnalyticsTest(unittest.TestCase):
    NOW = datetime(2026, 11, 2, 20, 0)

    def setUp(self):
        self.attempts = [
            attempt("C3-11", "C", "active", 1, "2026-11-01T09:00:00",
                    "2026-11-01T09:00:00", "2026-11-03T09:00:00", id=1),
            attempt("C3-12", "C", "active", 3, "2026-10-30T09:00:00",
                    "2026-11-02T10:00:00", "2026-11-02T10:00:00",
                    last_result="wrong", last_review_at="2026-11-02T10:00:00", id=2),
            attempt("C8-1", "C", "mastered", 3, "2026-10-20T09:00:00",
                    "2026-10-20T09:00:00", "2026-12-01T09:00:00",
                    last_result="correct", last_review_at="2026-11-01T09:00:00", id=3),
            attempt("A2025-A-1", "A", "active", 1, "2026-11-01T09:00:00",
                    "2026-11-01T09:00:00", "2026-11-01T09:00:00", id=4),
            attempt("K2020-A-1", "K", "active", 2, "2026-10-01T09:00:00",
                    "2026-10-01T09:00:00", "2026-10-02T09:00:00", id=5),
            attempt("Z1-1", "Z", "active", 1, "2026-10-05T09:00:00",
                    "2026-10-05T09:00:00", "2026-10-06T09:00:00", id=6),
        ]
        self.tags = {1: ["calc"], 2: ["concept", "standard"], 3: [],
                     4: ["misread"], 5: [], 6: []}
        self.logs = [
            log("C3-11", "2026-11-02T09:00:00", "correct", "active", "active", "wrong"),
            log("C3-12", "2026-11-02T10:00:00", "wrong", "active", "active", "wrong"),
            log("C8-1", "2026-11-01T09:00:00", "correct", "active", "mastered", "wrong"),
            log("C3-12", "2026-10-20T09:00:00", "wrong", "active", "active", None),
            log("A2025-A-1", "2026-10-15T09:00:00", "correct", "active", "active", None),
            log("K2020-A-1", "2026-10-10T09:00:00", "wrong", "active", "active", None),
            log("Z1-1", "2026-10-05T09:00:00", "correct", "active", "active", None),
        ]
        self.snapshot = {
            "attempts": self.attempts,
            "tags": self.tags,
            "logs": self.logs,
            "sessions": [],
        }
        self.exam_tags = {
            "A2025-A-1": {"category": "勘察"},
        }
        self.lookup = {
            "chapter": {"C3-11": {}, "C3-12": {}, "C3-13": {}, "C8-1": {}},
            "exam": {"A2025-A-1": {}},
            "mock": {},
        }
        self.settings = {
            "exam_date": "2027-11-01",
            "exam_label": "2027 注册岩土 · 专业考试",
            "plan_start": "2026-11-01",
            "daily_target": 20,
            "baseline_due": "2026-11-04",
        }

    def home(self):
        return analytics.build_home(
            self.snapshot, settings=self.settings, lookup=self.lookup,
            exam_tags=self.exam_tags, now=self.NOW,
        )

    def test_countdown_uses_the_exam_date(self):
        result = analytics.countdown(self.settings, self.NOW)
        self.assertEqual(result["days_left"], 364)
        self.assertEqual(result["phase"], "基础期")
        self.assertGreater(result["progress"], 0)

    def test_streak_ends_today_and_counts_back(self):
        summary = analytics.summarize(
            self.snapshot, lookup=self.lookup, exam_tags=self.exam_tags, now=self.NOW
        )
        self.assertTrue(summary["streak"]["active_today"])
        self.assertEqual(summary["streak"]["days"], 2)

    def test_today_accuracy_and_recovery(self):
        home = self.home()
        self.assertEqual(home["today"]["reviews"], 2)
        self.assertEqual(home["today"]["correct"], 1)
        self.assertEqual(home["today"]["accuracy"], 0.5)
        self.assertEqual(home["today"]["recovered"], 1)

    def test_categories_map_case_and_2025_exam_questions(self):
        home = self.home()
        rows = {row["key"]: row for row in home["categories"]}
        self.assertEqual(rows["C3"]["wrong_total"], 2)
        self.assertEqual(rows["C3"]["reviews"], 3)
        self.assertAlmostEqual(rows["C3"]["accuracy"], 1 / 3, places=4)
        self.assertEqual(rows["C2"]["wrong_total"], 1)  # A2025 勘察 -> C2
        self.assertEqual(rows["C8"]["mastered"], 1)
        # K and Z are not in the 2025 tag map: they stay in the unmapped bucket.
        self.assertEqual(home["unmapped"]["wrong_total"], 2)

    def test_encouragement_always_carries_evidence(self):
        home = self.home()
        self.assertIn("今日复习 2 题", home["encouragement"]["evidence"])
        self.assertTrue(home["encouragement"]["text"])

    def test_weak_category_becomes_an_action(self):
        home = self.home()
        titles = [row["title"] for row in home["next_actions"]]
        self.assertTrue(any("浅基础" in title for title in titles), titles)
        weak = next(row for row in home["next_actions"] if row["kind"] == "weak")
        self.assertEqual(weak["category"], "C3")

    def test_drill_action_counts_slow_and_no_time_only(self):
        baseline = {
            "available": True,
            "complete": True,
            "score": 60,
            "quadrants": {"items": [
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "skipped", "triage": "no_time"},
                {"quadrant": "skipped", "triage": "no_idea"},
                {"quadrant": "wrong"},
            ]},
        }
        home = analytics.build_home(
            self.snapshot, settings=self.settings, lookup=self.lookup,
            exam_tags=self.exam_tags, baseline=baseline, now=self.NOW,
        )
        action = next(row for row in home["next_actions"]
                      if row["kind"] == "drill")
        self.assertEqual(action["title"], "限时拆解 6 题")
        self.assertEqual(action["route"], "drill")
        self.assertIn("8 分钟", action["detail"])

    def test_milestone_picks_the_closest_unfinished_target(self):
        home = self.home()
        self.assertEqual(home["milestone"]["label"], "错题入本")
        self.assertEqual(home["milestone"]["current"], 6)
        self.assertEqual(home["milestone"]["target"], 10)

    def test_bank_counts_only_use_case_chapters(self):
        counts = case_bank_counts(self.lookup)
        self.assertEqual(counts, {"C3": 3, "C8": 1})

    def test_sections_split_a_chapter_by_question_range(self):
        chapters = {
            "C3": {
                "title": "第三篇 浅基础",
                "sections": [
                    {"first": "C3-1", "last": "C3-20", "name": "第一节 承载力"},
                    {"first": "C3-21", "last": "C3-40", "name": "第二节 沉降"},
                ],
            }
        }
        data = analytics.build_stats(
            self.snapshot, settings=self.settings, lookup=self.lookup,
            exam_tags=self.exam_tags, chapters=chapters, now=self.NOW,
        )
        sections = {row["name"]: row for row in data["sections"]}
        self.assertEqual(sections["第一节 承载力"]["wrong_total"], 2)
        self.assertEqual(sections["第一节 承载力"]["reviews"], 3)
        self.assertEqual(sections["第二节 沉降"]["wrong_total"], 0)
        self.assertEqual(sections["第二节 沉降"]["bank"], 20)

    def test_category_key_rejects_unmapped_families(self):
        self.assertEqual(category_key("C3-11"), "C3")
        self.assertEqual(category_key("A2025-A-1", self.exam_tags), "C2")
        self.assertIsNone(category_key("K2020-A-1", self.exam_tags))
        self.assertIsNone(category_key("Z1-1", self.exam_tags))


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="baseline-")
        self.path = os.path.join(self.tmp, "baseline-2025.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_is_not_an_error(self):
        result = analytics.read_baseline(self.path)
        self.assertFalse(result["available"])

    def test_four_numbers_are_aggregated_across_halves(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(
                "# 2025 案例基线\n\n"
                "案例是主观题：做对 = 过程与结果均正确。\n\n"
                "## 2026-11-04 · 上午卷\n\n"
                "- 作答 **25** / 25 · 做对 **15** · 放弃 **0** · 超时 **1**\n"
                "- 得分 **30 / 50**（每题 2 分；合格线 60/100）\n"
                "- 记录方式：人工按过程自评，不按选项字母判分。\n\n"
                "## 2026-11-05 · 下午卷\n\n"
                "- 作答 **23** / 25 · 做对 **14** · 放弃 **2** · 超时 **0**\n"
                "- 得分 **28 / 50**（每题 2 分；合格线 60/100）\n"
            )
        result = analytics.read_baseline(self.path)
        self.assertTrue(result["available"])
        self.assertTrue(result["process_scored"])
        self.assertEqual(result["score"], 58)
        self.assertEqual(result["max_score"], 100)
        self.assertEqual(result["answered"], 48)
        self.assertEqual(result["correct"], 29)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["overtime"], 1)
        self.assertAlmostEqual(result["accuracy"], 29 / 48, places=4)
        self.assertEqual(result["by_category"], {})

    def test_baseline_attaches_to_curriculum_rows(self):
        categories = [{"key": "C2", "baseline": None}]
        analytics.attach_baseline(
            categories,
            {"available": True, "by_category": {"勘察": {"accuracy": 0.75}}},
        )
        self.assertEqual(categories[0]["baseline"]["accuracy"], 0.75)


if __name__ == "__main__":
    unittest.main()
