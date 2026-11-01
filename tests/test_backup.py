"""Backup and restore: one zip carries the database and the photos."""

import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from app.store import Store  # noqa: E402


def jpeg(color=(90, 60, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (200, 150), color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="backup-")
        self.store = Store(os.path.join(self.tmp, "data"))
        photo = self.store.save_photo(jpeg())
        self.store.add_attempts(
            [
                {"qid": "C3-11", "tags": ["calc"], "note": "算错单位",
                 "source": "single", "photo_sha": photo["sha256"]},
                {"qid": "Z1-5", "tags": [], "source": "batch"},
            ]
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_contains_db_photos_and_manifest(self):
        path = self.store.backup()
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            manifest = zf.read("manifest.json").decode("utf-8")
        self.assertIn("wrongbook.db", names)
        self.assertTrue(any(n.startswith("attachments/orig/") for n in names))
        self.assertTrue(any(n.startswith("attachments/thumb/") for n in names))
        self.assertIn('"attempts": 2', manifest)

    def test_restore_round_trip_keeps_rows_notes_and_photos(self):
        path = self.store.backup()
        target = os.path.join(self.tmp, "restored")
        report = Store.restore_from_zip(path, target)
        self.assertEqual(report["attempts"], 2)
        self.assertIsNone(report["safety_backup"])  # nothing to replace yet
        restored = Store(target)
        item = restored.get_attempt_by_qid("C3-11")
        self.assertEqual(item["tags"], ["calc"])
        self.assertEqual(item["note"], "算错单位")
        self.assertIsNotNone(item["photo"])
        self.assertTrue(
            restored.photo_file(item["photo"]["sha256"], "orig").exists()
        )

    def test_restore_snapshots_the_previous_data_first(self):
        path = self.store.backup()
        self.store.add_attempts([{"qid": "C4-1", "source": "batch"}])
        report = Store.restore_from_zip(path, os.path.join(self.tmp, "data"))
        self.assertIsNotNone(report["safety_backup"])
        self.assertTrue(os.path.exists(report["safety_backup"]))
        reopened = Store(os.path.join(self.tmp, "data"))
        self.assertIsNone(reopened.get_attempt_by_qid("C4-1"))

    def test_zip_without_a_database_is_rejected(self):
        bad = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", "nope")
        with self.assertRaises(ValueError):
            Store.restore_from_zip(bad, os.path.join(self.tmp, "x"))


if __name__ == "__main__":
    unittest.main()
