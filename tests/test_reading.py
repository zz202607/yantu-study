"""Guided standards reading: route integrity, annotated pages, progress."""

import json
import os
import shutil
import sys
import tempfile
import unittest

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
