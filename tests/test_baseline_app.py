"""In-app 2025 baseline: four numbers, optional per-question states, ledger."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import analytics, baseline  # noqa: E402
from app.store import Store  # noqa: E402

CATEGORIES = ["勘察", "浅基础", "深基础", "地基处理", "特殊土",
              "边坡", "基坑", "地震", "检测"]


def make_tags():
    tags = {}
    for half in ("A", "P"):
        for number in range(1, baseline.TOTAL_QUESTIONS + 1):
            tags["A2025-%s-%d" % (half, number)] = {
                "category": CATEGORIES[(number - 1) % len(CATEGORIES)],
                "answer": "C",
                "text": "instruction %d" % number,
            }
    return tags


def items(half, results):
    """Build complete items from result strings."""
    return [
        {
            "qid": "A2025-%s-%d" % (half, number),
            "result": result,
            "overtime": 0,
        }
        for number, result in enumerate(results, start=1)
    ]


class BaselineAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="baseline-app-")
        self.store = Store(self.tmp)
        self.tags = make_tags()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, half="A", **payload):
        defaults = {
            "day": "2026-11-04",
            "minutes": 180,
            "answered": 23,
            "correct": 15,
            "skipped": 2,
            "overtime": 3,
            "note": "",
            "items": [],
        }
        defaults.update(payload)
        return baseline.save(self.store, half, defaults, self.tags)

    # -- paper -----------------------------------------------------------

    def test_paper_is_ordered_and_never_exposes_the_option_key(self):
        paper = baseline.paper_questions(self.tags)
        self.assertEqual(len(paper["A"]), 25)
        self.assertEqual(len(paper["P"]), 25)
        self.assertEqual(paper["A"][0]["qid"], "A2025-A-1")
        self.assertEqual(paper["A"][-1]["seq"], 25)
        self.assertEqual(paper["A"][0]["category"], "勘察")
        self.assertEqual(paper["A"][0]["category_key"], "C2")
        self.assertNotIn("answer", paper["A"][0])
        self.assertNotIn("text", paper["A"][0])

    # -- full items derive the four numbers ------------------------------

    def test_complete_items_derive_counts_and_category_breakdown(self):
        results = ["correct"] * 15 + ["wrong"] * 7 + ["skipped"] * 3
        summary = self.save(
            "A",
            answered=0,
            correct=0,
            skipped=0,
            overtime=2,
            items=[
                dict(row, overtime=1) if number < 2 else row
                for number, row in enumerate(items("A", results))
            ],
        )
        detail = summary["half_detail"]["A"]
        self.assertTrue(summary["available"])
        self.assertEqual(detail["answered"], 22)
        self.assertEqual(detail["correct"], 15)
        self.assertEqual(detail["skipped"], 3)
        self.assertEqual(detail["overtime"], 2)
        self.assertEqual(detail["score"], 30)
        self.assertEqual(detail["items_done"], 25)
        self.assertEqual(summary["score"], 30)
        self.assertEqual(summary["max_score"], 50)
        self.assertAlmostEqual(summary["accuracy"], 15 / 22, places=4)
        self.assertEqual(sum(row["n"] for row in detail["by_category"]), 25)
        self.assertEqual(
            sum(row["answered"] for row in detail["by_category"]), 22
        )
        self.assertEqual(detail["process"]["process_right_result_right"], 15)
        self.assertEqual(
            [row["category"] for row in detail["by_category"]],
            ["勘察", "浅基础", "深基础", "地基处理", "边坡",
             "基坑", "特殊土", "地震", "检测"],
        )

    def test_wrong_process_status_is_kept_but_correct_is_process_right(self):
        result = items("P", ["correct", "wrong"])
        result[0]["process_status"] = "process_wrong"  # overridden by correct
        result[1]["process_status"] = "no_idea"
        summary = self.save(
            "P", answered=2, correct=1, skipped=23, overtime=0,
            items=result + items("P", ["skipped"] * 23)[2:],
        )
        stored = summary["half_detail"]["P"]["items"]
        by_number = {row["seq"]: row for row in stored}
        self.assertEqual(by_number[1]["process_status"],
                         "process_right_result_right")
        self.assertEqual(by_number[2]["process_status"], "no_idea")
        self.assertIsNone(by_number[3]["process_status"])

    # -- partial / manual counts -----------------------------------------

    def test_partial_items_never_override_manual_counts(self):
        summary = self.save(
            "A", answered=20, correct=12, skipped=5, overtime=0,
            items=items("A", ["correct"] * 3),
        )
        detail = summary["half_detail"]["A"]
        self.assertEqual(detail["answered"], 20)
        self.assertEqual(detail["correct"], 12)
        self.assertEqual(detail["items_done"], 3)
        self.assertEqual(
            sum(row["answered"] for row in detail["by_category"]), 3
        )

    def test_partial_items_cannot_exceed_the_manual_counts(self):
        with self.assertRaisesRegex(ValueError, "超过四个数的做对数"):
            self.save("A", answered=20, correct=2, skipped=5,
                      items=items("A", ["correct"] * 3))
        with self.assertRaisesRegex(ValueError, "放弃数"):
            self.save("A", answered=24, correct=10, skipped=1,
                      items=items("A", ["skipped", "skipped"]))

    def test_four_numbers_must_account_for_all_25(self):
        with self.assertRaisesRegex(ValueError, "应等于 25"):
            self.save("A", answered=20, correct=10, skipped=4)

    def test_correct_and_overtime_cannot_exceed_answered(self):
        with self.assertRaisesRegex(ValueError, "做对数"):
            self.save("A", answered=20, correct=21, skipped=5)
        with self.assertRaisesRegex(ValueError, "超时题数"):
            self.save("A", answered=20, correct=10, skipped=5, overtime=21)

    # -- validation ------------------------------------------------------

    def test_unknown_and_duplicate_questions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不是 2025 这一半的题"):
            self.save("A", items=[{"qid": "A2025-P-1", "result": "correct"}])
        row = {"qid": "A2025-A-1", "result": "correct"}
        with self.assertRaisesRegex(ValueError, "题号重复"):
            self.save("A", items=[row, dict(row)])

    def test_result_and_process_status_are_validated(self):
        with self.assertRaisesRegex(ValueError, "逐题状态不合法"):
            self.save("A", items=[{"qid": "A2025-A-1", "result": "maybe"}])
        with self.assertRaisesRegex(ValueError, "过程状态不合法"):
            self.save("A", items=[{
                "qid": "A2025-A-1", "result": "wrong",
                "process_status": "guessing",
            }])
        with self.assertRaisesRegex(ValueError, "不能选「过程对·结果对」"):
            self.save("A", items=[{
                "qid": "A2025-A-1", "result": "wrong",
                "process_status": "process_right_result_right",
            }])

    def test_half_and_date_are_validated(self):
        with self.assertRaisesRegex(ValueError, "上午卷 A 或下午卷 P"):
            baseline.save(self.store, "X", {"answered": 25, "correct": 10},
                          self.tags)
        with self.assertRaisesRegex(ValueError, "日期"):
            self.save("A", day="11/04/2026")

    def test_incomplete_paper_tags_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "题目标签不完整"):
            baseline.save(self.store, "A", {"answered": 25, "correct": 10},
                          {})

    def test_zero_minutes_is_kept_not_defaulted(self):
        summary = self.save("A", minutes=0)
        self.assertEqual(summary["half_detail"]["A"]["minutes"], 0)

    # -- summary ---------------------------------------------------------

    def test_both_halves_make_a_100_point_score(self):
        self.save("A", answered=25, correct=18, skipped=0, overtime=2)
        summary = self.save("P", answered=25, correct=16, skipped=0, overtime=1)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["score"], 68)
        self.assertEqual(summary["max_score"], 100)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["answered"], 50)
        self.assertEqual(summary["correct"], 34)

    def test_category_accuracy_attaches_to_the_curriculum(self):
        results = ["wrong"] * 25  # every category wrong in this half
        self.save("A", items=items("A", results))
        summary = baseline.summary(self.store, self.tags)
        rows = [{"key": "C2", "baseline": None}]
        analytics.attach_baseline(rows, summary)
        self.assertEqual(rows[0]["baseline"]["accuracy"], 0.0)
        self.assertEqual(rows[0]["baseline"]["n"], 3)  # 勘察 in one half

    # -- ledger ----------------------------------------------------------

    def test_saving_a_half_upserts_one_ledger_row(self):
        self.save("A", answered=25, correct=15, skipped=0, overtime=1)
        sessions = self.store.list_practice_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["source"], "real_exam")
        self.assertEqual(sessions[0]["baseline_half"], "A")
        self.assertEqual(sessions[0]["attempted"], 25)
        self.assertEqual(sessions[0]["correct"], 15)
        self.assertEqual(sessions[0]["abandoned"], 0)
        self.assertEqual(sessions[0]["overtime"], 1)

        self.save("A", answered=23, correct=14, skipped=2, overtime=0,
                  day="2026-11-05")
        sessions = self.store.list_practice_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["attempted"], 23)
        self.assertEqual(sessions[0]["day"], "2026-11-05")

    def test_baseline_ledger_cannot_be_deleted_like_a_plain_session(self):
        self.save("A")
        session = self.store.list_practice_sessions()[0]
        with self.assertRaisesRegex(ValueError, "基线页"):
            self.store.delete_practice_session(session["id"])

    def test_deleting_a_half_removes_its_ledger_row(self):
        self.save("A")
        self.save("P")
        self.assertTrue(self.store.delete_baseline("A", 2025))
        self.assertEqual(len(self.store.list_practice_sessions()), 1)
        self.assertFalse(self.store.delete_baseline("A", 2025))
        summary = baseline.summary(self.store, self.tags)
        self.assertFalse(summary["half_detail"]["A"]["recorded"])
        self.assertTrue(summary["half_detail"]["P"]["recorded"])

    def test_wrong_qids_are_in_paper_order(self):
        results = ["skipped"] * 25
        results[4] = "wrong"   # A2025-A-5
        results[1] = "wrong"   # A2025-A-2
        self.save("A", items=items("A", results))
        self.assertEqual(
            baseline.wrong_qids(self.store, "A"),
            ["A2025-A-2", "A2025-A-5"],
        )
        self.assertEqual(baseline.wrong_qids(self.store, "P"), [])

    def test_plan_counts_the_baseline_as_real_exam_work(self):
        from app import plan
        self.save("A", answered=25, correct=18, skipped=0, overtime=2)
        data = plan.overview(
            self.store, self.tmp, {"exam_date": "2027-11-01"},
            now="2026-11-04",
            baseline=baseline.summary(self.store, self.tags),
        )
        self.assertEqual(data["week"]["attempted"], 25)
        self.assertEqual(data["week"]["correct"], 18)
        self.assertTrue(any(row["key"] == "baseline:A" and row["done"]
                            for row in data["tasks"]))


if __name__ == "__main__":
    unittest.main()
