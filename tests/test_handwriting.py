"""Handwriting upload targets: today's review order first, then fresh errors."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.store import Store  # noqa: E402


class HandwritingTargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="handwriting-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, *qids):
        rows = [{"qid": qid, "family": qid[0], "source": "batch"} for qid in qids]
        result = self.store.add_attempts(rows)
        self.assertEqual(result["errors"], [])

    def test_fresh_errors_are_upload_targets(self):
        self.add("C3-11", "C3-12")
        targets = self.store.handwriting_targets()
        self.assertEqual({item["qid"] for item in targets}, {"C3-11", "C3-12"})

    def test_review_queue_comes_first_then_out_of_queue_new_attempts(self):
        self.add(*["C3-%d" % i for i in range(1, 22)])
        queue = self.store.review_queue()
        targets = self.store.handwriting_targets()
        self.assertEqual(len(queue), 20)
        self.assertEqual(len(targets), 21)
        self.assertEqual(
            [item["id"] for item in targets[:20]],
            [item["id"] for item in queue],
        )

    def test_checked_sheet_stays_available_for_photos(self):
        # Checking reschedules review items out of today's queue and leaves
        # correct new answers without a wrong-book row.  The sheet just done
        # must still list its wrong-book questions: review items plus wrongs.
        self.add("C3-11", "C3-9")
        pack = self.store.save_day_pack([
            {"qid": "C3-11", "role": "review"},
            {"qid": "C3-9", "role": "new"},
            {"qid": "C3-12", "role": "new"},
        ])
        self.store.close_day_pack(
            pack["id"], results=[{"qid": "C3-9", "result": "wrong"}]
        )
        with mock.patch.object(self.store, "review_queue", return_value=[]), \
                mock.patch.object(self.store, "today_attempts", return_value=[]):
            targets = self.store.handwriting_targets()
        self.assertEqual([item["qid"] for item in targets], ["C3-11", "C3-9"])

    def test_superseded_sheet_is_not_a_photo_source(self):
        self.add("C3-11")
        self.store.save_day_pack([{"qid": "C3-11", "role": "new"}])
        self.store.save_day_pack([{"qid": "C3-12", "role": "new"}])
        with mock.patch.object(self.store, "review_queue", return_value=[]), \
                mock.patch.object(self.store, "today_attempts", return_value=[]):
            targets = self.store.handwriting_targets()
        self.assertEqual(targets, [])

    def test_uploaded_photo_is_reported_on_the_target(self):
        self.add("C3-11")
        item = self.store.get_attempt_by_qid("C3-11")
        photo = self.store.save_photo(_jpeg())
        self.store.update_attempt(item["id"], process_photo_sha=photo["sha256"])
        target = self.store.handwriting_targets()[0]
        self.assertIsNotNone(target["process_photo"])


def _jpeg(color=(180, 40, 40), size=(320, 240)):
    import io

    from PIL import Image

    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
