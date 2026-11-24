"""Full-paper mock: per-question clock, A/B/C marks and the skip review.

Pinned here: 25 rows per half, skip forcing C, the 7.2-minute budget and
8-minute hard stop, the "missed skip" rule, and the 2026 偏-question
recognition rate.  The mock must never count an untouched row as a
recognition failure -- that is a missing entry, not a decision.
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import mock  # noqa: E402
from app.store import Store  # noqa: E402


class MockStoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class StartTest(MockStoreCase):
    def test_start_creates_25_empty_rows(self):
        run = mock.start(self.store, {"year": 2026, "half": "A"})
        self.assertEqual(len(run["items"]), 25)
        self.assertEqual(run["items"][0]["qid"], "A2026-A-1")
        self.assertEqual(run["items"][-1]["qid"], "A2026-A-25")
        self.assertIsNone(run["items"][0]["result"])
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["minutes"], 180)

    def test_start_rejects_bad_year_and_half(self):
        for payload in (
            {"year": 2017, "half": "A"},
            {"year": 2027, "half": "A"},
            {"year": "x", "half": "A"},
            {"year": 2026, "half": "B"},
        ):
            with self.assertRaises(ValueError):
                mock.start(self.store, payload)

    def test_custom_day_and_minutes(self):
        run = mock.start(self.store, {"year": 2018, "half": "P",
                                      "day": "2026-12-15", "minutes": 90})
        self.assertEqual(run["day"], "2026-12-15")
        self.assertEqual(run["minutes"], 90)


class RecordTest(MockStoreCase):
    def setUp(self):
        super().setUp()
        self.run = mock.start(self.store, {"year": 2026, "half": "A"})

    def item(self, seq):
        return next(i for i in self.store.mock_run(self.run["id"])["items"]
                    if i["seq"] == seq)

    def test_done_records_seconds_and_mark(self):
        mock.record(self.store, self.run["id"],
                    {"seq": 1, "action": "done", "seconds": 300, "mark": "A"})
        row = self.item(1)
        self.assertEqual(row["seconds"], 300)
        self.assertEqual(row["mark"], "A")
        self.assertIsNone(row["result"])

    def test_skip_forces_c_and_skipped(self):
        mock.record(self.store, self.run["id"],
                    {"seq": 2, "action": "skip", "seconds": 95, "mark": "A"})
        row = self.item(2)
        self.assertEqual(row["mark"], "C")
        self.assertEqual(row["result"], "skipped")
        self.assertEqual(row["seconds"], 95)

    def test_invalid_action_and_seq(self):
        with self.assertRaises(ValueError):
            mock.record(self.store, self.run["id"], {"seq": 1, "action": "x"})
        with self.assertRaises(ValueError):
            mock.record(self.store, self.run["id"], {"seq": 26, "action": "done"})
        with self.assertRaises(ValueError):
            mock.record(self.store, 9999, {"seq": 1, "action": "done"})

    def test_item_field_validation(self):
        with self.assertRaises(ValueError):
            self.store.update_mock_item(self.run["id"], 1, {"mark": "D"})
        with self.assertRaises(ValueError):
            self.store.update_mock_item(self.run["id"], 1, {"result": "maybe"})
        with self.assertRaises(ValueError):
            self.store.update_mock_item(self.run["id"], 1, {"seconds": "x"})
        with self.assertRaises(ValueError):
            self.store.update_mock_item(self.run["id"], 1, {"bogus": 1})

    def test_seconds_are_clamped(self):
        self.store.update_mock_item(self.run["id"], 1, {"seconds": -5})
        self.assertEqual(self.item(1)["seconds"], 0)
        self.store.update_mock_item(self.run["id"], 1, {"seconds": 99999})
        self.assertEqual(self.item(1)["seconds"], 7200)


class ReviewTest(MockStoreCase):
    def setUp(self):
        super().setUp()
        self.run = mock.start(self.store, {"year": 2026, "half": "A"})

    def finish(self, items, marks=None):
        marks = marks or {}
        for seq, (seconds, result) in items.items():
            fields = {"seconds": seconds, "result": result}
            if seq in marks:
                fields["mark"] = marks[seq]
            self.store.update_mock_item(self.run["id"], seq, fields)
        return mock.review(self.store.mock_run(self.run["id"]))

    def test_score_and_quadrants(self):
        view = self.finish({
            1: (300, "correct"),    # fast
            2: (600, "correct"),    # slow: over the 8-minute stop
            3: (200, "wrong"),
            4: (60, "skipped"),
        })
        self.assertEqual(view["score"], 4)
        self.assertEqual(view["correct"], 2)
        self.assertEqual(view["wrong"], 1)
        self.assertEqual(view["skipped"], 1)
        self.assertEqual(view["quadrants"]["fast"], [1])
        self.assertEqual(view["quadrants"]["slow"], [2])
        self.assertEqual(view["quadrants"]["wrong"], [3])
        self.assertEqual(view["quadrants"]["skipped"], [4])
        self.assertEqual(view["avg_seconds"], (300 + 600 + 200 + 60) / 4)

    def test_budget_and_stop_lists(self):
        view = self.finish({
            1: (432, "correct"),    # exactly the budget: not over
            2: (434, "correct"),    # one-second rounding grace, then over
            3: (481, "correct"),
        })
        self.assertEqual(view["over_budget"], [2, 3])
        self.assertEqual(view["over_stop"], [3])

    def test_missed_skip_rule(self):
        view = self.finish({
            1: (900, "correct"),    # C + 15 min + not skipped -> missed skip
            2: (900, "skipped"),    # C + over stop + skipped -> fine
            3: (300, "wrong"),      # C but under the stop -> not counted
        }, marks={1: "C", 2: "C", 3: "C"})
        self.assertEqual([i["seq"] for i in view["missed_skips"]], [1])

    def test_wrong_skips(self):
        view = self.finish({1: (60, "skipped"), 2: (60, "skipped")},
                           marks={1: "A", 2: "B"})
        self.assertEqual([i["seq"] for i in view["wrong_skips"]], [1, 2])

    def test_mark_accuracy(self):
        view = self.finish({
            1: (100, "correct"), 2: (100, "correct"), 3: (100, "wrong"),
        }, marks={1: "A", 2: "A", 3: "A"})
        self.assertEqual(view["marks"]["A"]["count"], 3)
        self.assertAlmostEqual(view["marks"]["A"]["accuracy"], 2 / 3)

    def test_bias_skips_untouched_questions(self):
        # 2026 A has 9 questions the publisher labels 偏; only seq 1 is graded.
        view = self.finish({1: (100, "wrong")}, marks={1: "C"})
        self.assertEqual(view["bias"]["total"], 1)
        self.assertEqual(view["bias"]["recognized"], 1)
        self.assertEqual(view["bias"]["untouched"], 8)
        self.assertEqual(view["bias"]["rate"], 1.0)

    def test_bias_counts_skip_as_recognition(self):
        view = self.finish({1: (100, "skipped")})
        self.assertEqual(view["bias"]["recognized"], 1)

    def test_partial_paper_advice_mentions_missing_rows(self):
        view = self.finish({1: (100, "correct")})
        self.assertTrue(any("还差" in line for line in view["advice"]))
        self.assertEqual(view["graded"], 1)


class LifecycleTest(MockStoreCase):
    def test_finish_and_delete(self):
        run = mock.start(self.store, {"year": 2024, "half": "P"})
        finished = self.store.finish_mock_run(run["id"], note="第一场")
        self.assertEqual(finished["status"], "done")
        self.assertEqual(finished["note"], "第一场")
        self.assertIsNotNone(finished["finished_at"])
        self.assertTrue(self.store.delete_mock_run(run["id"]))
        self.assertIsNone(self.store.mock_run(run["id"]))
        self.assertFalse(self.store.delete_mock_run(run["id"]))

    def test_overview_lists_runs_and_levels(self):
        run = mock.start(self.store, {"year": 2026, "half": "A"})
        self.store.update_mock_item(run["id"], 1, {"result": "correct",
                                                   "seconds": 100})
        view = mock.overview(self.store)
        self.assertEqual(len(view["runs"]), 1)
        self.assertEqual(view["runs"][0]["correct"], 1)
        self.assertIn("2026", view["levels"])
        self.assertIn("2026", [str(y) for y in view["years"]])

    def test_save_results_bulk(self):
        run = mock.start(self.store, {"year": 2023, "half": "A"})
        mock.save_results(self.store, run["id"], {"items": [
            {"seq": 1, "result": "correct", "mark": "A"},
            {"seq": 2, "result": "wrong"},
        ]})
        items = self.store.mock_run(run["id"])["items"]
        self.assertEqual(items[0]["result"], "correct")
        self.assertEqual(items[0]["mark"], "A")
        self.assertEqual(items[1]["result"], "wrong")

    def test_save_results_rejects_empty(self):
        run = mock.start(self.store, {"year": 2023, "half": "A"})
        with self.assertRaises(ValueError):
            mock.save_results(self.store, run["id"], {"items": []})


if __name__ == "__main__":
    unittest.main()
