"""Legacy photo import: filename parsing, preview statuses, idempotency."""

import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from app.catalog import Catalog  # noqa: E402
from app.photo_import import import_photos, parse_qids, scan_photos  # noqa: E402
from app.store import Store  # noqa: E402


def write_jpeg(path, color):
    Image.new("RGB", (240, 180), color).save(path, "JPEG", quality=90)


class ParseTest(unittest.TestCase):
    def test_single_question(self):
        self.assertEqual(parse_qids("3-11.jpg"), ["C3-11"])

    def test_two_questions_and_a_suffix(self):
        self.assertEqual(parse_qids("4-12 4-13错.jpg"), ["C4-12", "C4-13"])

    def test_repeated_number_is_kept_once(self):
        self.assertEqual(parse_qids("4-12 4-12错.jpg"), ["C4-12"])

    def test_name_without_question_is_empty(self):
        self.assertEqual(parse_qids("IMG_2026.jpg"), [])

    def test_family_is_configurable(self):
        self.assertEqual(parse_qids("1-58.jpg", family="Z"), ["Z1-58"])


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="import-")
        self.photos = os.path.join(self.tmp, "photos")
        os.makedirs(self.photos)
        self.store = Store(os.path.join(self.tmp, "data"))
        self.catalog = Catalog(os.path.join(ROOT, "index"))
        write_jpeg(os.path.join(self.photos, "3-11.jpg"), (200, 30, 30))
        write_jpeg(os.path.join(self.photos, "4-12 4-13错.jpg"), (30, 120, 200))
        write_jpeg(os.path.join(self.photos, "99-99.jpg"), (30, 200, 30))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_preview_reports_new_duplicate_and_unknown(self):
        items = scan_photos(self.store, self.catalog, self.photos)
        status = {q["qid"]: q["status"] for item in items for q in item["qids"]}
        self.assertEqual(status["C3-11"], "new")
        self.assertEqual(status["C4-12"], "new")
        self.assertEqual(status["C4-13"], "new")
        self.assertEqual(status["C99-99"], "unknown")
        self.assertEqual(self.store.stats()["total"], 0)  # preview writes no attempts

    def test_import_creates_only_new_known_questions(self):
        result = import_photos(self.store, self.catalog, self.photos)
        self.assertEqual(sorted(result["created"]), ["C3-11", "C4-12", "C4-13"])
        self.assertEqual(self.store.stats()["total"], 3)
        self.assertEqual(self.store.get_attempt_by_qid("C4-13")["source"], "import")
        self.assertIsNotNone(self.store.get_attempt_by_qid("C4-13")["photo"])

    def test_reimport_is_a_no_op(self):
        import_photos(self.store, self.catalog, self.photos)
        again = import_photos(self.store, self.catalog, self.photos)
        self.assertEqual(again["created"], [])
        self.assertEqual(self.store.stats()["total"], 3)
        self.assertEqual(self.store.get_attempt_by_qid("C3-11")["times"], 1)

    def test_selecting_by_sha_imports_a_subset(self):
        items = scan_photos(self.store, self.catalog, self.photos)
        target = next(i for i in items if i["file"].endswith("3-11.jpg"))
        result = import_photos(
            self.store, self.catalog, self.photos, shas=[target["sha256"]]
        )
        self.assertEqual(result["created"], ["C3-11"])

    def test_dry_run_plans_without_writing(self):
        result = import_photos(self.store, self.catalog, self.photos, dry_run=True)
        self.assertEqual(sorted(result["planned"]), ["C3-11", "C4-12", "C4-13"])
        self.assertEqual(self.store.stats()["total"], 0)


if __name__ == "__main__":
    unittest.main()
