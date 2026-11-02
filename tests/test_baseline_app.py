"""In-app 2025 baseline: four numbers, optional per-question states, ledger."""

import os
import shutil
import sqlite3
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
        self.assertEqual(sessions[0]["baseline_half"], "2025-A")
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

    def test_deleting_a_half_clears_stale_triage(self):
        self.save("A", items=items("A", ["skipped"] * 25))
        self.store.set_baseline_triage(2025, "A", "A2025-A-1", "no_time")
        self.assertEqual(self.store.baseline_triage(2025)["A"],
                         {"A2025-A-1": "no_time"})
        self.store.delete_baseline("A", 2025)
        self.assertEqual(self.store.baseline_triage(2025)["A"], {})

    def test_a_2024_retest_does_not_overwrite_the_2025_ledger(self):
        self.save("A")
        result = baseline.save(
            self.store, "A",
            {"day": "2026-12-15", "minutes": 180, "answered": 20,
             "correct": 15, "skipped": 5, "overtime": 3, "items": []},
            {}, year=2024, require_paper=False,
        )
        self.assertEqual(result["year"], 2024)
        self.assertTrue(result["half_detail"]["A"]["recorded"])
        self.assertEqual(result["half_detail"]["A"]["items_done"], 0)
        self.assertEqual(result["score"], 30)
        sessions = self.store.list_practice_sessions()
        self.assertEqual(len(sessions), 2)
        self.assertEqual({row["baseline_half"] for row in sessions},
                         {"2025-A", "2024-A"})
        self.assertTrue(self.store.delete_baseline("A", 2024))
        remaining = self.store.list_practice_sessions()
        self.assertEqual([row["baseline_half"] for row in remaining], ["2025-A"])

    def test_retest_rejects_per_question_items(self):
        with self.assertRaisesRegex(ValueError, "只记四个数"):
            baseline.save(
                self.store, "A",
                {"day": "2026-12-15", "answered": 20, "correct": 15,
                 "skipped": 5, "overtime": 0,
                 "items": [{"qid": "A2024-A-1", "result": "correct"}]},
                {}, year=2024, require_paper=False,
            )

    def test_legacy_bare_ledger_key_migrates_to_2025(self):
        self.save("A")
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                "UPDATE practice_sessions SET baseline_half = 'A'"
                " WHERE baseline_half = '2025-A'"
            )
        Store(self.tmp)  # init runs the v8 migration
        sessions = self.store.list_practice_sessions()
        self.assertEqual(sessions[0]["baseline_half"], "2025-A")

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

    # -- four quadrants + triage -----------------------------------------

    def test_quadrants_split_correct_answers_by_overtime(self):
        results = ["skipped"] * 25
        results[0] = "correct"
        results[1] = "correct"
        results[2] = "wrong"
        rows = items("A", results)
        rows[1]["overtime"] = 1
        summary = self.save("A", items=rows)
        quadrants = summary["quadrants"]
        self.assertTrue(quadrants["available"])
        self.assertEqual(quadrants["counts"],
                         {"fast": 1, "slow": 1, "wrong": 1, "skipped": 22})
        self.assertEqual(quadrants["coverage"], {"done": 25, "total": 25})
        by_qid = {row["qid"]: row for row in quadrants["items"]}
        self.assertEqual(by_qid["A2025-A-1"]["quadrant"], "fast")
        self.assertEqual(by_qid["A2025-A-2"]["quadrant"], "slow")
        self.assertEqual(by_qid["A2025-A-3"]["quadrant"], "wrong")
        self.assertEqual(by_qid["A2025-A-4"]["quadrant"], "skipped")

    def test_skipped_triage_is_saved_and_survives_a_resave(self):
        results = ["skipped"] * 25
        self.save("A", items=items("A", results))
        row = baseline.set_triage(
            self.store, "A", "A2025-A-3", "no_idea"
        )
        self.assertEqual(row["triage"], "no_idea")
        summary = baseline.summary(self.store, self.tags)
        skipped = [
            item for item in summary["quadrants"]["items"]
            if item["qid"] == "A2025-A-3"
        ][0]
        self.assertEqual(skipped["triage"], "no_idea")
        # Re-saving the half must not wipe the triage table.
        summary = self.save("A", items=items("A", results))
        skipped = [
            item for item in summary["quadrants"]["items"]
            if item["qid"] == "A2025-A-3"
        ][0]
        self.assertEqual(skipped["triage"], "no_idea")
        # Clearing works too.
        baseline.set_triage(self.store, "A", "A2025-A-3", "")
        summary = baseline.summary(self.store, self.tags)
        skipped = [
            item for item in summary["quadrants"]["items"]
            if item["qid"] == "A2025-A-3"
        ][0]
        self.assertIsNone(skipped["triage"])

    def test_triage_only_accepts_skipped_questions(self):
        results = ["correct"] + ["skipped"] * 24
        self.save("A", answered=24, correct=1, skipped=1,
                  items=items("A", results)[:2])
        with self.assertRaisesRegex(ValueError, "只有放弃题"):
            baseline.set_triage(self.store, "A", "A2025-A-1", "no_time")
        with self.assertRaisesRegex(ValueError, "分流只能是"):
            baseline.set_triage(self.store, "A", "A2025-A-2", "maybe")
        with self.assertRaisesRegex(ValueError, "还没有逐题录入"):
            baseline.set_triage(self.store, "A", "A2025-A-25", "no_idea")

    def test_import_lists_split_wrong_skipped_and_overtime(self):
        results = ["skipped"] * 25
        results[0] = "correct"   # fast
        results[1] = "correct"   # overtime
        results[2] = "wrong"
        results[3] = "skipped"   # no_idea
        results[4] = "skipped"   # no_time
        rows = items("A", results)
        rows[1]["overtime"] = 1
        self.save("A", items=rows)
        baseline.set_triage(self.store, "A", "A2025-A-4", "no_idea")
        baseline.set_triage(self.store, "A", "A2025-A-5", "no_time")
        self.assertEqual(
            baseline.qids_for_import(self.store, "A", "wrong"),
            ["A2025-A-3"],
        )
        self.assertEqual(
            baseline.qids_for_import(self.store, "A", "overtime"),
            ["A2025-A-2"],
        )
        self.assertEqual(
            baseline.qids_for_import(self.store, "A", "skipped",
                                     triage="no_idea"),
            ["A2025-A-4"],
        )
        self.assertEqual(
            baseline.qids_for_import(self.store, "A", "skipped",
                                     triage="no_time"),
            ["A2025-A-5"],
        )
        self.assertEqual(
            baseline.qids_for_import(self.store, "A", "skipped"),
            ["A2025-A-%d" % number for number in range(4, 26)],
        )
        with self.assertRaisesRegex(ValueError, "导入类型"):
            baseline.qids_for_import(self.store, "A", "everything")

    # -- must-have categories + exam simulation --------------------------

    def test_must_have_accuracy_uses_paper_category_keys(self):
        rows = items("A", ["correct"] * 25)
        must_numbers = [2, 4, 5, 8, 11, 13, 14, 17, 20, 22, 23]
        for number in must_numbers:
            rows[number - 1]["result"] = "wrong"
        self.save("A", items=rows)
        must = baseline.summary(self.store, self.tags)["must"]
        self.assertTrue(must["available"])
        self.assertEqual(must["attempted"], 11)
        self.assertEqual(must["correct"], 0)
        self.assertEqual(must["accuracy"], 0.0)
        self.assertEqual(
            [row["key"] for row in must["rows"]],
            ["C3", "C5", "C8", "C9"],
        )
        self.assertEqual(
            {row["key"]: row["n"] for row in must["rows"]},
            {"C3": 3, "C5": 3, "C8": 3, "C9": 2},
        )

    def test_simulation_turns_pace_into_exam_day_gaps(self):
        self.save("A", answered=17, correct=10, skipped=8, overtime=0)
        summary = self.save("P", answered=17, correct=9, skipped=8,
                            overtime=0)
        sim = summary["simulation"]
        self.assertTrue(sim["available"])
        self.assertEqual(sim["minutes"], 360)
        self.assertEqual(sim["per_question_minutes"], 10.59)
        self.assertEqual(sim["capacity_per_half"], 17)
        self.assertEqual(sim["target_per_half"], 20)
        self.assertEqual(sim["target_minutes_per_question"], 9)
        self.assertEqual(sim["gaps"]["questions_per_half"], 3)
        self.assertEqual(sim["gaps"]["minutes_per_question"], 1.59)
        self.assertEqual(sim["gaps"]["correct"], 11)
        self.assertEqual(sim["gaps"]["score"], 22)

    def test_plan_advice_reads_baseline_must_accuracy(self):
        from app import plan
        rows = items("A", ["wrong"] * 25)
        self.save("A", items=rows)
        rows = items("P", ["wrong"] * 25)
        self.save("P", items=rows)
        summary = baseline.summary(self.store, self.tags)
        data = plan.overview(
            self.store, self.tmp, {"exam_date": "2027-11-01"},
            now="2026-11-10", baseline=summary,
        )
        self.assertEqual(data["baseline_must_accuracy"], 0.0)
        self.assertTrue(any("基线必拿四类" in line for line in data["advice"]))

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
