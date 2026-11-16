"""Guided standards reading: route integrity, annotated pages, progress."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import reading, standards  # noqa: E402
from app.store import Store  # noqa: E402


class GuideDataTest(unittest.TestCase):
    def setUp(self):
        self.data = reading.load_guides()
        self.lookup = json.loads(
            (reading.ROOT / "index" / "lookup.json").read_text(encoding="utf-8")
        )["chapter"]

    def test_nine_guides_and_unique_stop_ids(self):
        guides = self.data["standards"]
        self.assertEqual(len(guides), 9)
        seen = set()
        for guide in guides:
            self.assertTrue(guide["code_key"])
            self.assertTrue(guide["name"])
            self.assertGreaterEqual(len(guide["stops"]), 6)
            for stop in guide["stops"]:
                self.assertNotIn(stop["id"], seen)
                seen.add(stop["id"])
        self.assertEqual(len(seen), 67)

    def test_every_stop_has_a_page_and_a_short_session(self):
        for guide in self.data["standards"]:
            for stop in guide["stops"]:
                self.assertGreater(stop["page"], 0, stop["id"])
                self.assertGreaterEqual(stop["minutes"], 5, stop["id"])
                self.assertLessEqual(stop["minutes"], 20, stop["id"])
                self.assertTrue(stop["why"], stop["id"])
                self.assertTrue(stop["pitfall"], stop["id"])
                self.assertTrue(stop["check"], stop["id"])
                self.assertTrue(stop["look_for"], stop["id"])

    def test_sections_exist_in_the_index_when_the_toc_has_them(self):
        index = json.loads(
            (reading.ROOT / "index" / "standards_index.json").read_text(encoding="utf-8")
        )
        by_code = {row["code_key"]: row for row in index["standards"]}
        # These books have no section-level TOC in the index; pages were read
        # from the printed 目次 by hand.
        manual = {"JGJ120-2012", "GB50025-2018", "GB50330-2013"}
        for guide in self.data["standards"]:
            row = by_code[guide["code_key"]]
            numbers = {entry.get("number") for entry in row.get("toc") or []}
            for stop in guide["stops"]:
                if guide["code_key"] in manual:
                    continue
                root = stop["section"].split("/")[0]
                if root.startswith("附录"):
                    continue
                found = any(
                    number == root or str(number or "").startswith(root + ".")
                    for number in numbers
                )
                self.assertTrue(
                    found, "%s %s" % (guide["code_key"], stop["section"])
                )

    def test_practice_ranges_exist(self):
        for guide in self.data["standards"]:
            for stop in guide["stops"]:
                practice = stop.get("practice")
                if not practice:
                    continue
                self.assertIn(practice["qid"], self.lookup, stop["id"])
                self.assertIn(practice["qid_end"], self.lookup, stop["id"])
                self.assertLessEqual(
                    int(practice["qid"].split("-")[1]),
                    int(practice["qid_end"].split("-")[1]),
                )

    def test_clean_pdf_page_lookup(self):
        guide = reading.guide("GB50007-2011")
        stop = next(row for row in guide["stops"] if row["section"] == "5.2")
        self.assertEqual(reading._stop_pdf_page(guide, stop), 30)
        stop = next(row for row in guide["stops"] if row["section"] == "附录K")
        self.assertGreater(reading._stop_pdf_page(guide, stop), 130)


class AnnotatedIndexTest(unittest.TestCase):
    def test_built_index_is_monotonic_and_covers_gb50007_stops(self):
        index = reading.load_annotated()
        entry = (index.get("files") or {}).get("GB50007-2011")
        if not entry:
            self.skipTest("annotated index not built on this machine")
        self.assertEqual(entry["file"].lower().endswith(".pdf"), True)
        self.assertGreaterEqual(entry["content_start"], 1)
        anchors = entry["anchors"]
        self.assertGreaterEqual(len(anchors), 4)
        for (page_a, printed_a), (page_b, printed_b) in zip(anchors, anchors[1:]):
            self.assertLess(page_a, page_b)
            self.assertLessEqual(printed_a, printed_b)
        stops = entry["stops"]
        for stop in reading.guide("GB50007-2011")["stops"]:
            self.assertTrue(stops.get(stop["id"]), stop["id"])
        self.assertEqual(stops["gb50007-3"], 22)
        self.assertEqual(stops["gb50007-4-1"], 28)
        self.assertEqual(stops["gb50007-5-2"], 37)

    def test_every_guide_has_a_page_for_every_stop(self):
        index = reading.load_annotated()
        files = index.get("files") or {}
        if not files:
            self.skipTest("annotated index not built on this machine")
        for guide in reading.load_guides()["standards"]:
            entry = files.get(guide["code_key"])
            self.assertIsNotNone(entry, guide["code_key"])
            self.assertTrue(entry.get("file"), guide["code_key"])
            self.assertGreaterEqual(len(entry.get("anchors") or []), 2, guide["code_key"])
            for stop in guide["stops"]:
                page = (entry.get("stops") or {}).get(stop["id"])
                self.assertTrue(page, "%s %s" % (guide["code_key"], stop["id"]))
                self.assertLessEqual(page, entry["page_count"])


class AnnotatedPageCropTest(unittest.TestCase):
    """The annotated scans are 2-up; half= crops one copy at full size."""

    def setUp(self):
        import pymupdf as fitz
        self.tmp = tempfile.mkdtemp(prefix="reading-crop-")
        folder = Path(self.tmp)
        pdf = folder / "two-up.pdf"
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        page.insert_text((40, 100), "LEFT COPY", fontsize=30)
        page.insert_text((440, 100), "RIGHT COPY", fontsize=30)
        doc.save(pdf)
        doc.close()
        single = folder / "single.pdf"
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 100), "SINGLE PAGE", fontsize=30)
        doc.save(single)
        doc.close()
        (folder / "annotated_index.json").write_text(json.dumps({
            "built_at": "2026-11-16",
            "folder": str(folder),
            "files": {
                "TEST-1": {
                    "file": "two-up.pdf",
                    "page_count": 1,
                    "stops": {"t-1": 1},
                },
                "TEST-2": {
                    "file": "single.pdf",
                    "page_count": 1,
                    "stops": {"t-2": 1},
                },
            },
        }), encoding="utf-8")
        self.folder = folder
        self.saved = (
            reading.ANNOTATED_INDEX_PATH,
            reading.ANNOTATED_DIR,
            reading.PAGE_CACHE,
        )
        reading.ANNOTATED_INDEX_PATH = folder / "annotated_index.json"
        reading.ANNOTATED_DIR = folder
        reading.PAGE_CACHE = folder / "cache"
        reading._ANNOTATED_CACHE.update(mtime=None, data=None)
        reading._LAYOUT_CACHE.clear()

    def tearDown(self):
        (reading.ANNOTATED_INDEX_PATH,
         reading.ANNOTATED_DIR,
         reading.PAGE_CACHE) = self.saved
        reading._ANNOTATED_CACHE.update(mtime=None, data=None)
        reading._LAYOUT_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def image_size(path):
        from PIL import Image
        with Image.open(path) as image:
            return image.size

    def test_half_crop_is_half_the_width_of_the_full_page(self):
        whole = reading.annotated_page_at("TEST-1", 1, dpi=72)
        left = reading.annotated_page_at("TEST-1", 1, dpi=72, half="left")
        right = reading.annotated_page_at("TEST-1", 1, dpi=72, half="right")
        full_w, full_h = self.image_size(whole[0])
        left_w, left_h = self.image_size(left[0])
        right_w, right_h = self.image_size(right[0])
        self.assertLessEqual(abs(left_w * 2 - full_w), 2)
        self.assertLessEqual(abs(right_w * 2 - full_w), 2)
        self.assertLessEqual(abs(left_h - full_h), 1)
        self.assertNotEqual(left[0], whole[0])
        self.assertNotEqual(left[0], right[0])
        self.assertTrue(left[0].exists())
        self.assertTrue(right[0].exists())

    def test_unknown_half_uses_the_full_page_cache(self):
        whole = reading.annotated_page_at("TEST-1", 1, dpi=72)
        weird = reading.annotated_page_at("TEST-1", 1, dpi=72, half="../bad")
        self.assertEqual(whole[0], weird[0])
        self.assertEqual(weird[0].parent, reading.PAGE_CACHE / "TEST-1")

    def test_stop_page_accepts_half(self):
        rendered = reading.annotated_page("TEST-1", "t-1", dpi=72, half="left")
        self.assertIsNotNone(rendered)
        self.assertIn("_l.png", rendered[0].name)

    def test_portrait_book_ignores_half_and_keeps_the_full_page(self):
        full = reading.annotated_page_at("TEST-2", 1, dpi=72)
        half = reading.annotated_page_at("TEST-2", 1, dpi=72, half="left")
        self.assertEqual(full[0], half[0])
        width, height = self.image_size(half[0])
        self.assertEqual((width, height), (600, 800))
        self.assertEqual(reading._LAYOUT_CACHE[
            (str(self.folder / "single.pdf"),
             reading._mtime(self.folder / "single.pdf"))
        ], False)

    def test_entry_layout_flag_matches_the_pdf(self):
        self.assertTrue(reading._entry_two_up("TEST-1", {"file": "two-up.pdf"}))
        self.assertFalse(reading._entry_two_up("TEST-2", {"file": "single.pdf"}))
        self.assertFalse(reading._entry_two_up("TEST-3", None))


class ReadingProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reading-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_done_stop_counts_once_and_accumulates_seconds(self):
        self.store.set_reading_progress(
            "GB50007-2011", "gb50007-3", "done", seconds=300, note="先记组合"
        )
        self.store.set_reading_progress(
            "GB50007-2011", "gb50007-3", "done", seconds=120
        )
        progress = self.store.reading_progress()
        row = progress[("GB50007-2011", "gb50007-3")]
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["seconds"], 420)
        self.assertEqual(row["note"], "")  # the second visit clears the note
        overview = reading.overview(self.store)
        self.assertEqual(overview["stats"]["stops_done"], 1)
        self.assertEqual(overview["stats"]["stops_total"], 67)
        self.assertEqual(len(self.store.reading_log()), 2)

    def test_recommendation_continues_the_last_standard(self):
        reading.record(self.store, "GB50007-2011", "gb50007-3", "done", seconds=60)
        overview = reading.overview(self.store)
        self.assertEqual(overview["recommendation"]["code_key"], "GB50007-2011")
        self.assertEqual(overview["recommendation"]["stop_id"], "gb50007-4-1")

    def test_review_status_wins_the_next_stop(self):
        reading.record(self.store, "GB50011-2010", "gb50011-3-1", "done", seconds=60)
        reading.record(self.store, "GB50011-2010", "gb50011-4-1", "review", seconds=90)
        overview = reading.overview(self.store)
        self.assertEqual(overview["recommendation"]["stop_id"], "gb50011-4-1")
        detail = reading.detail("GB50011-2010", self.store.reading_progress())
        stop = next(row for row in detail["stops"] if row["id"] == "gb50011-4-1")
        self.assertEqual(stop["status"], "review")
        self.assertEqual(stop["seconds"], 90)

    def test_rejects_unknown_stop_and_status(self):
        with self.assertRaises(ValueError):
            reading.record(self.store, "GB50007-2011", "nope", "done")
        with self.assertRaises(ValueError):
            self.store.set_reading_progress("GB50007-2011", "gb50007-3", "later")

    def test_overview_has_all_guides_in_exam_order(self):
        overview = reading.overview(self.store)
        order = [row["order"] for row in overview["standards"]]
        self.assertEqual(order, sorted(order))
        self.assertEqual(overview["standards"][0]["code_key"], "GB50011-2010")


if __name__ == "__main__":
    unittest.main()
