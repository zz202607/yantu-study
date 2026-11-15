"""Checking a printed day sheet derives everything else.

The print knows the review/new split; the user only flags exceptions.  These
tests pin the derivation: review scheduling, wrong-book rows, one practice
row, photo attachment, and one-shot closing.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.daypack import apply_results  # noqa: E402
from app.store import Store, now_iso  # noqa: E402


def jpeg(color=(180, 40, 40), size=(320, 240)):
    import io

    from PIL import Image

    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class DayPackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="daypack-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self):
        self.store.add_attempts([
            {"qid": "C3-11", "family": "C", "source": "batch"},
            {"qid": "C3-12", "family": "C", "source": "batch"},
        ])
        session = self.store.start_session()
        pack = self.store.save_day_pack(
            [
                {"qid": "C3-11", "role": "review"},
                {"qid": "C3-12", "role": "review"},
                {"qid": "Z1-2", "role": "new"},
                {"qid": "Z1-3", "role": "new"},
                {"qid": "Z1-4", "role": "new"},
            ],
            session_id=session["id"],
        )
        return session, pack

    def test_review_results_reschedule_and_new_wrongs_are_filed(self):
        _, pack = self.seed()
        outcome = apply_results(
            self.store, pack,
            [
                {"qid": "C3-11", "result": "correct"},
                {"qid": "C3-12", "result": "wrong", "tags": ["calc"]},
                {"qid": "Z1-2", "result": "wrong", "tags": ["concept"]},
                {"qid": "Z1-3", "result": "skipped"},
                {"qid": "Z1-4", "result": "correct"},
            ],
        )
        summary = outcome["summary"]
        self.assertTrue(outcome["pack"]["closed"])
        self.assertEqual(summary["review"],
                         {"total": 2, "correct": 1, "wrong": 1, "skipped": 0})
        self.assertEqual(summary["new"],
                         {"total": 3, "correct": 1, "wrong": 1,
                          "skipped": 1, "overtime": 0})
        self.assertEqual(summary["wrongs"], ["Z1-2"])

        right = self.store.get_attempt_by_qid("C3-11")
        self.assertEqual(right["interval_days"], 7)
        self.assertEqual(right["streak"], 1)
        wrong = self.store.get_attempt_by_qid("C3-12")
        self.assertEqual(wrong["times"], 2)
        self.assertEqual(wrong["interval_days"], 1)
        self.assertEqual(wrong["tags"], ["calc"])

        new_wrong = self.store.get_attempt_by_qid("Z1-2")
        self.assertIsNotNone(new_wrong)
        self.assertEqual(new_wrong["source"], "paper")
        self.assertEqual(new_wrong["tags"], ["concept"])
        self.assertIsNone(self.store.get_attempt_by_qid("Z1-3"))
        self.assertIsNone(self.store.get_attempt_by_qid("Z1-4"))

        practice = self.store.list_practice_sessions()[0]
        self.assertEqual(practice["source"], "paper")
        self.assertEqual(practice["attempted"], 2)
        self.assertEqual(practice["correct"], 1)
        self.assertEqual(practice["abandoned"], 1)
        self.assertEqual(practice["overtime"], 0)

    def test_unflagged_items_default_to_correct(self):
        _, pack = self.seed()
        apply_results(
            self.store, pack,
            [
                {"qid": "C3-12", "result": "wrong", "tags": ["calc"]},
                {"qid": "Z1-2", "result": "overtime"},
            ],
        )
        closed = self.store.day_pack(pack["id"])
        self.assertEqual(
            {row["qid"]: row["result"] for row in closed["items"]},
            {"C3-11": "correct", "C3-12": "wrong", "Z1-2": "overtime",
             "Z1-3": "correct", "Z1-4": "correct"},
        )

    def test_photo_lands_on_the_pack_and_the_new_wrong(self):
        _, pack = self.seed()
        photo = self.store.save_photo(jpeg())
        outcome = apply_results(
            self.store, pack,
            [{"qid": "Z1-2", "result": "wrong", "tags": ["misread"]}],
            photo_sha=photo["sha256"],
        )
        self.assertEqual(outcome["pack"]["photo_id"], photo["id"])
        attempt = self.store.get_attempt_by_qid("Z1-2")
        self.assertIsNotNone(attempt["photo"])

    def test_a_pack_closes_only_once(self):
        _, pack = self.seed()
        apply_results(self.store, pack, [])
        with self.assertRaises(ValueError):
            apply_results(self.store, pack, [])

    def test_unknown_qid_is_rejected(self):
        _, pack = self.seed()
        with self.assertRaises(ValueError):
            apply_results(self.store, pack,
                          [{"qid": "C9-99", "result": "wrong"}])

    def test_new_print_supersedes_an_untouched_one(self):
        _, first = self.seed()
        self.store.save_day_pack(
            [{"qid": "C3-11", "role": "review"}], session_id=first["session_id"])
        old = self.store.day_pack(first["id"])
        self.assertTrue(old["closed"])
        self.assertEqual(old["summary"], {"superseded": True})


if __name__ == "__main__":
    unittest.main()
