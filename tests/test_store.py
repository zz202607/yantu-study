"""Wrong-book store: batches, tags, filters, photos, duplicate marking."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from app.store import Store, TAG_KEYS, now_iso  # noqa: E402


def jpeg(color=(180, 40, 40), size=(320, 240)):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wrongbook-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, *qids, **kwargs):
        rows = [
            {"qid": qid, "family": qid[0], "source": "batch", **kwargs}
            for qid in qids
        ]
        return self.store.add_attempts(rows)

    def test_batch_creates_one_row_per_question_with_tags(self):
        result = self.add("C3-11", "C3-12", tags=["calc", "concept"])
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(result["errors"], [])
        item = self.store.get_attempt_by_qid("C3-11")
        self.assertEqual(item["tags"], ["concept", "calc"])
        self.assertEqual(item["times"], 1)
        self.assertEqual(self.store.stats()["total"], 2)

    def test_bad_tag_rejects_only_that_question(self):
        result = self.store.add_attempts(
            [
                {"qid": "C3-11", "tags": ["nope"]},
                {"qid": "C3-12", "tags": ["calc"]},
            ]
        )
        self.assertEqual([e["qid"] for e in result["errors"]], ["C3-11"])
        self.assertEqual(len(result["created"]), 1)
        self.assertIsNone(self.store.get_attempt_by_qid("C3-11"))

    def test_marking_again_bumps_times_and_replaces_tags(self):
        self.add("C3-11", tags=["concept"], note="第一次")
        first = self.store.get_attempt_by_qid("C3-11")
        result = self.add("C3-11", tags=["calc"])
        self.assertEqual(result["updated"][0]["qid"], "C3-11")
        again = self.store.get_attempt_by_qid("C3-11")
        self.assertEqual(again["times"], 2)
        self.assertEqual(again["tags"], ["calc"])
        # A blank note from the marking sheet must not wipe an earlier note.
        self.assertEqual(again["note"], "第一次")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(self.store.stats()["total"], 1)

    def test_skip_existing_is_idempotent(self):
        self.add("C3-11")
        result = self.store.add_attempts(
            [{"qid": "C3-11", "source": "import"}], skip_existing=True
        )
        self.assertEqual(result["created"], [])
        self.assertEqual(result["skipped"], ["C3-11"])
        self.assertEqual(self.store.get_attempt_by_qid("C3-11")["times"], 1)

    def test_filters_by_family_tag_chapter_status_and_qid(self):
        self.add("C3-11", tags=["calc"])
        self.add("C4-12", tags=["concept"])
        self.add("Z1-5", tags=["calc"])
        self.assertEqual(self.store.list_attempts(family="C")["total"], 2)
        self.assertEqual(self.store.list_attempts(chapter="C3")["total"], 1)
        self.assertEqual(self.store.list_attempts(tag="calc")["total"], 2)
        self.assertEqual(self.store.list_attempts(qid="Z1-5")["total"], 1)
        item = self.store.get_attempt_by_qid("C3-11")
        self.store.update_attempt(item["id"], status="mastered")
        self.assertEqual(self.store.list_attempts(status="mastered")["total"], 1)
        self.assertEqual(self.store.list_attempts(status="active")["total"], 2)

    def test_time_filter_uses_the_last_wrong_date(self):
        old = now_iso(datetime.now().astimezone() - timedelta(days=40))
        self.store.add_attempts([{"qid": "C3-11", "at": old, "source": "import"}])
        self.add("C3-12")
        self.assertEqual(self.store.list_attempts(days=7)["total"], 1)
        self.assertEqual(self.store.list_attempts()["total"], 2)

    def test_pagination_reports_total_and_more(self):
        self.add(*["C3-%d" % i for i in range(1, 8)])
        page = self.store.list_attempts(limit=5, offset=0)
        self.assertEqual(len(page["items"]), 5)
        self.assertEqual(page["total"], 7)
        self.assertTrue(page["has_more"])
        rest = self.store.list_attempts(limit=5, offset=5)
        self.assertFalse(rest["has_more"])

    def test_update_can_clear_tags_and_note(self):
        self.add("C3-11", tags=["calc"], note="x")
        item = self.store.get_attempt_by_qid("C3-11")
        updated = self.store.update_attempt(item["id"], tags=[], note="")
        self.assertEqual(updated["tags"], [])
        self.assertEqual(updated["note"], "")

    def test_delete_removes_tags(self):
        self.add("C3-11", tags=["calc"])
        item = self.store.get_attempt_by_qid("C3-11")
        self.assertTrue(self.store.delete_attempt(item["id"]))
        self.assertIsNone(self.store.get_attempt(item["id"]))
        self.assertEqual(self.store.stats()["total"], 0)
        self.assertEqual(self.store.stats()["by_tag"]["calc"], 0)

    def test_photo_dedupes_by_sha_and_writes_files(self):
        data = jpeg()
        first = self.store.save_photo(data)
        second = self.store.save_photo(data)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertTrue(self.store.photo_file(first["sha256"], "orig").exists())
        self.assertTrue(self.store.photo_file(first["sha256"], "thumb").exists())
        self.assertIsNone(self.store.photo_file("../../etc/passwd", "orig"))

    def test_attempt_links_a_photo_and_unknown_photo_is_an_error(self):
        photo = self.store.save_photo(jpeg())
        result = self.store.add_attempts(
            [{"qid": "C3-11", "photo_sha": photo["sha256"], "source": "single"}]
        )
        self.assertEqual(len(result["created"]), 1)
        item = self.store.get_attempt_by_qid("C3-11")
        self.assertEqual(item["photo"]["sha256"], photo["sha256"])
        bad = self.store.add_attempts([{"qid": "C3-12", "photo_sha": "0" * 64}])
        self.assertEqual(len(bad["errors"]), 1)

    def test_orphan_photos_are_pruned(self):
        self.store.save_photo(jpeg())
        self.assertEqual(self.store.prune_orphan_photos(), 1)
        self.assertEqual(self.store.stats()["total"], 0)

    def test_stats_count_tags_and_families(self):
        self.add("C3-11", tags=["calc"])
        self.add("C4-12", tags=["calc", "concept"])
        stats = self.store.stats()
        self.assertEqual(stats["by_tag"]["calc"], 2)
        self.assertEqual(stats["by_tag"]["concept"], 1)
        self.assertEqual(stats["by_family"]["C"], 2)
        self.assertEqual(stats["active"], 2)

    def test_note_length_is_capped(self):
        result = self.store.add_attempts([{"qid": "C3-11", "note": "x" * 3000}])
        self.assertEqual(len(result["errors"]), 1)

    def test_tag_keys_are_the_four_agreed_labels(self):
        self.assertEqual(TAG_KEYS, ("concept", "calc", "standard", "misread"))


if __name__ == "__main__":
    unittest.main()
