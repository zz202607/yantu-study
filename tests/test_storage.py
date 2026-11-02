"""Photo storage: compression cap, usage stats and process-photo retention."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from app.store import PHOTO_MAX_SIDE, Store  # noqa: E402

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)


def jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (3200, 2400), color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="storage-")
        self.store = Store(self.tmp)
        self.store.add_attempts([{"qid": "C1-1", "source": "single"}])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def grade_with_photo(self, day, color, status="process_right_result_right"):
        photo = self.store.save_photo(jpeg(color))
        moment = BASE + timedelta(days=day)
        session = self.store.start_session(now=moment)
        self.assertIn("C1-1", session["queue"])
        result = self.store.grade(
            session["id"], "C1-1", None,
            process_status=status,
            process_photo_sha=photo["sha256"], now=moment,
        )
        return result["attempt"]

    def test_new_photos_are_downscaled(self):
        photo = self.store.save_photo(jpeg((200, 30, 30)))
        self.assertLessEqual(photo["width"], PHOTO_MAX_SIDE)
        self.assertLessEqual(photo["height"], PHOTO_MAX_SIDE)

    def test_stats_and_prune_keep_latest_process_photos(self):
        self.grade_with_photo(2, (200, 30, 30))
        self.grade_with_photo(9, (30, 120, 200),
                              status="process_right_result_wrong")
        last = self.grade_with_photo(12, (30, 200, 30))
        before = self.store.storage_stats()
        self.assertGreaterEqual(before["photos"], 3)
        self.assertGreater(before["attachments_bytes"], 0)

        report = self.store.prune_process_photos(keep=2)
        self.assertGreaterEqual(report["removed_photos"], 1)
        self.assertIsNotNone(report["backup"])
        self.assertTrue(os.path.exists(report["backup"]))
        self.assertGreater(report["freed_bytes"], 0)
        after = self.store.storage_stats()
        self.assertLess(after["attachments_bytes"], before["attachments_bytes"])

        item = self.store.get_attempt_by_qid("C1-1")
        self.assertIsNotNone(item["process_photo"])
        self.assertEqual(item["process_photo"]["sha256"], last["process_photo"]["sha256"])

    def test_prune_rejects_bad_keep(self):
        with self.assertRaisesRegex(ValueError, "保留张数"):
            self.store.prune_process_photos(keep=9)


if __name__ == "__main__":
    unittest.main()
