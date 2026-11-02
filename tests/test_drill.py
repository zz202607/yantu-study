"""M9.2 timed drills and recognition cards.

The selection rules, the first-step requirement for abandoning, the overlap
between B4/B5 ranges, and the rule that only timed drills reach the weekly
practice ledger are all pinned here.
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import drill  # noqa: E402
from app.store import Store  # noqa: E402

CATEGORY = "浅基础"
EXAM_TAGS = {}


def add_tag(qid, category=CATEGORY):
    EXAM_TAGS[qid] = {"category": category}


def baseline_stub():
    """A -> 3 slow, 2 skipped no_time, 2 wrong, 1 untriaged; P -> 2 + 1."""
    items = []

    def add(half, seq, quadrant, triage=None, category=CATEGORY,
            cat_key="C3"):
        qid = "A2025-%s-%d" % (half, seq)
        add_tag(qid, category)
        items.append({
            "qid": qid,
            "half": half,
            "seq": seq,
            "category": category,
            "category_key": cat_key,
            "result": "correct" if quadrant in ("slow", "fast") else
                      "wrong" if quadrant == "wrong" else "skipped",
            "overtime": quadrant == "slow",
            "quadrant": quadrant,
            "triage": triage,
        })

    add("A", 1, "slow", category="浅基础")
    add("A", 2, "slow", category="浅基础")
    add("A", 3, "slow", category="地基处理", cat_key="C5")
    add("A", 4, "skipped", triage="no_time", category="特殊土", cat_key="C8")
    add("A", 5, "skipped", triage="no_time", category="地震", cat_key="C9")
    add("A", 6, "skipped", triage="no_idea", category="边坡", cat_key="C6")
    add("A", 7, "skipped", category="勘察", cat_key="C2")
    add("A", 8, "wrong", category="浅基础")
    add("A", 9, "wrong", category="浅基础")
    add("P", 1, "slow", category="浅基础")
    add("P", 2, "slow", category="地基处理", cat_key="C5")
    add("P", 3, "skipped", triage="no_time", category="特殊土", cat_key="C8")
    return {
        "quadrants": {"items": items},
        "must": {
            "rows": [
                {"key": "C3", "accuracy": 0.42},
                {"key": "C5", "accuracy": 0.6},
                {"key": "C8", "accuracy": 0.8},
                {"key": "C9", "accuracy": 0.73},
            ]
        },
    }


class PrototypeTruthTest(unittest.TestCase):
    def test_simple_range_and_standard(self):
        info = drill.prototype_for("C3-8")
        self.assertEqual(info["key"], "A1")
        self.assertEqual(info["category"], "C3")
        self.assertEqual(info["standard"]["code"], "GB 50007-2011")

    def test_overlapping_b4_b5_accepts_both_but_shows_b5(self):
        info = drill.prototype_for("C4-104")
        self.assertEqual(info["key"], "B5")
        self.assertIn("B4", info["keys"])
        self.assertIn("B5", info["keys"])

    def test_special_soil_core_standards_are_explicit(self):
        self.assertEqual(drill.prototype_for("C8-4")["standard"]["code"],
                         "GB 50025-2018")
        self.assertEqual(drill.prototype_for("C8-68")["standard"]["code"],
                         "GB 50112-2013")
        self.assertEqual(drill.prototype_for("C8-120")["standard"]["code"],
                         "GB/T 50942-2014")

    def test_frozen_ground_and_debris_flow_have_no_fake_standard(self):
        self.assertIsNone(drill.prototype_for("C8-155")["standard"])
        self.assertIsNone(drill.prototype_for("C8-215")["standard"])
        self.assertIsNone(drill.prototype_for("C8-228")["standard"])

    def test_unknown_question_returns_none(self):
        self.assertIsNone(drill.prototype_for("C3-1"))  # before A1's range


class TimedSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drill-timed-")
        self.store = Store(self.tmp)
        self.baseline = baseline_stub()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mixed_puts_slow_first_then_no_time_then_wrong(self):
        pick = drill.timed_queue("mixed", count=10, baseline=self.baseline,
                                 store=self.store)
        qids = pick["qids"]
        self.assertEqual(len(qids), 10)
        self.assertEqual(qids[:5], ["A2025-A-1", "A2025-A-2", "A2025-A-3",
                                    "A2025-P-1", "A2025-P-2"])
        self.assertEqual(qids[5:8], ["A2025-A-4", "A2025-A-5", "A2025-P-3"])
        self.assertEqual(qids[8:], ["A2025-A-8", "A2025-A-9"])
        self.assertNotIn("A2025-A-6", qids)  # no_idea belongs to the wrong book
        self.assertNotIn("A2025-A-7", qids)  # untriaged must be classified

    def test_skipped_only_uses_no_time_and_reports_untriaged(self):
        pick = drill.timed_queue("skipped", count=5, baseline=self.baseline,
                                 store=self.store)
        self.assertEqual(pick["qids"], ["A2025-A-4", "A2025-A-5", "A2025-P-3"])
        self.assertIn("未分流", pick["hint"])

    def test_empty_source_raises_actionable_error(self):
        baseline = {"quadrants": {"items": []}}
        with self.assertRaisesRegex(ValueError, "先录入 2025 基线"):
            drill.timed_queue("slow", baseline=baseline, store=self.store)

    def test_prototype_drill_prefers_unseen_questions(self):
        pick = drill.timed_queue("prototype", source_key="A1", count=5,
                                 baseline=self.baseline, store=self.store,
                                 exists=lambda qid: True)
        self.assertEqual(pick["qids"][0], "C3-8")
        self.assertEqual(len(pick["qids"]), 5)


class TimedSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drill-session-")
        self.store = Store(self.tmp)
        self.baseline = baseline_stub()
        self.state = drill.start(
            self.store, {"mode": "timed", "source": "mixed", "count": 5},
            self.baseline, exists=lambda qid: True,
        )
        self.session_id = self.state["session"]["id"]
        self.qids = self.state["session"]["queue"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, qid, **payload):
        body = {"qid": qid, "seconds": 100}
        body.update(payload)
        return drill.record(self.store, self.session_id, body, EXAM_TAGS)

    def test_abandon_without_first_step_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "放弃也要写首步"):
            self.record(self.qids[0], result="abandoned", first_step="  ")

    def test_last_item_auto_finishes_and_writes_one_ledger_row(self):
        results = [
            ("correct", "", 100),
            ("wrong", "先查 GB 50007 承载力修正", 200),
            ("overtime", "先算基底压力", 480),
            ("abandoned", "先列已知量，再选公式", 60),
            ("correct", "", 60),
        ]
        state = None
        for qid, (result, first_step, seconds) in zip(self.qids, results):
            state = self.record(qid, result=result, first_step=first_step,
                                seconds=seconds)
        self.assertTrue(state["finished"])
        summary = state["summary"]
        self.assertEqual(summary["counts"]["correct"], 2)
        self.assertEqual(summary["counts"]["wrong"], 1)
        self.assertEqual(summary["counts"]["overtime"], 1)
        self.assertEqual(summary["counts"]["abandoned"], 1)
        self.assertEqual(summary["attempted"], 4)
        self.assertEqual(summary["first_step"], 3)
        self.assertEqual(summary["abandoned_with_step"], 1)

        sessions = self.store.list_practice_sessions(limit=10)
        self.assertEqual(len(sessions), 1)
        ledger = sessions[0]
        self.assertEqual(ledger["source"], "drill")
        self.assertEqual(ledger["attempted"], 4)
        self.assertEqual(ledger["correct"], 2)
        self.assertEqual(ledger["overtime"], 1)
        self.assertEqual(ledger["abandoned"], 1)
        self.assertEqual(ledger["minutes"], 15)
        self.assertEqual(ledger["category"], "mixed")
        self.assertEqual(ledger["drill_session_id"], self.session_id)

    def test_finish_twice_does_not_duplicate_ledger(self):
        self.record(self.qids[0], result="correct")
        drill.finish(self.store, self.session_id, EXAM_TAGS)
        drill.finish(self.store, self.session_id, EXAM_TAGS)
        self.assertEqual(len(self.store.list_practice_sessions(limit=10)), 1)

    def test_delete_ledger_is_blocked_from_the_wrong_direction(self):
        self.record(self.qids[0], result="correct")
        state = drill.finish(self.store, self.session_id, EXAM_TAGS)
        ledger_id = state["session"]["ledger_session_id"]
        with self.assertRaisesRegex(ValueError, "限时拆解记录"):
            self.store.delete_practice_session(ledger_id)


class RecognitionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drill-recog-")
        self.store = Store(self.tmp)
        self.baseline = baseline_stub()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, source="prototype", source_key="A1", count=5):
        return drill.start(
            self.store,
            {"mode": "recognize", "source": source, "source_key": source_key,
             "count": count},
            self.baseline, exists=lambda qid: True,
        )

    def test_round_robin_covers_different_prototypes(self):
        state = self.start(source="core", count=5)
        qids = state["session"]["queue"]
        keys = [drill.prototype_for(qid)["key"] for qid in qids]
        self.assertEqual(keys, ["A1", "A2", "A3", "A4", "A5"])

    def test_second_card_rotates_to_new_questions(self):
        first = self.start(source="core", count=5)["session"]["queue"]
        for qid in first:
            info = drill.prototype_for(qid)
            drill.record(self.store, 1, {
                "qid": qid, "seconds": 12,
                "guess_prototype": info["key"],
                "guess_standard": (info["standard"] or {}).get("code", ""),
            })
        second = self.start(source="core", count=5)["session"]["queue"]
        self.assertFalse(set(first) & set(second))

    def test_scoring_accepts_overlap_and_unknown_standard(self):
        state = self.start(source="prototype", source_key="B5", count=5)
        qid = state["session"]["queue"][0]
        self.assertEqual(qid, "C4-104")
        after = drill.record(self.store, state["session"]["id"], {
            "qid": qid, "seconds": 9,
            "guess_prototype": "B4",
            "guess_standard": "JGJ 94-2008",
        })
        item = after["item"]
        self.assertTrue(item["prototype_ok"])
        self.assertTrue(item["standard_ok"])

        frozen = self.start(source="prototype", source_key="A12", count=5)
        qid = frozen["session"]["queue"][0]
        after = drill.record(self.store, frozen["session"]["id"], {
            "qid": qid, "seconds": 10,
            "guess_prototype": "A12",
            "guess_standard": "",
        })
        item = after["item"]
        self.assertTrue(item["prototype_ok"])
        self.assertIsNone(item["standard_ok"])
        self.assertTrue(item["identify_ok"])

    def test_cards_do_not_enter_the_question_ledger(self):
        state = self.start(count=5)
        for qid in state["session"]["queue"]:
            info = drill.prototype_for(qid)
            state = drill.record(self.store, state["session"]["id"], {
                "qid": qid, "seconds": 12,
                "guess_prototype": info["key"],
                "guess_standard": (info["standard"] or {}).get("code", ""),
            })
        self.assertTrue(state["finished"])
        self.assertEqual(state["summary"]["prototype_ok"], 5)
        self.assertEqual(state["summary"]["standard_ok"], 5)
        self.assertEqual(len(self.store.list_practice_sessions(limit=10)), 0)

    def test_wrong_standard_is_counted_but_prototype_still_credits(self):
        state = self.start(count=5)
        qid = state["session"]["queue"][0]
        info = drill.prototype_for(qid)
        after = drill.record(self.store, state["session"]["id"], {
            "qid": qid, "seconds": 10,
            "guess_prototype": info["key"],
            "guess_standard": "GB 50011-2010(2016 年版)",
        })
        item = after["item"]
        self.assertTrue(item["prototype_ok"])
        self.assertFalse(item["standard_ok"])
        self.assertFalse(item["identify_ok"])


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drill-overview-")
        self.store = Store(self.tmp)
        self.baseline = baseline_stub()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_counts_and_weak_categories(self):
        view = drill.overview(self.store, self.baseline,
                              exists=lambda qid: True)
        self.assertEqual(view["timed"]["counts"]["slow"], 5)
        self.assertEqual(view["timed"]["counts"]["skipped"], 3)
        self.assertEqual(view["timed"]["counts"]["wrong"], 2)
        self.assertEqual(view["timed"]["counts"]["untriaged"], 1)
        self.assertEqual(view["recognize"]["weak_categories"],
                         ["C3", "C5", "C8", "C9"])
        self.assertGreater(view["recognize"]["weak_count"], 0)
        self.assertEqual(view["recognize"]["core_count"], 25)
        self.assertEqual(view["recognize"]["all_count"], 32)
        self.assertIsNone(view["timed"]["last"])
        self.assertTrue(any(row["code"] == "GB 50007-2011"
                            for row in view["standards"]))


if __name__ == "__main__":
    unittest.main()
