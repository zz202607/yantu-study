"""Per-question clause hints: prototype, keyword and cached-citation paths."""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import answer_ocr, clauses  # noqa: E402


class ClauseHintsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="clauses-")
        self.empty = os.path.join(self.tmp, "empty")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, qid, citations):
        path = answer_ocr.cache_file(self.tmp, qid)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "version": answer_ocr.CACHE_VERSION,
                "source": {},
                "result": {"citations": citations, "candidates": []},
            }, fh, ensure_ascii=False)

    def test_prototype_maps_to_curated_stop(self):
        data = clauses.hints("C3-11", cache_dir=self.empty)
        self.assertEqual(data["category_key"], "C3")
        first = data["hints"][0]
        self.assertEqual(first["source"], "prototype")
        self.assertEqual(first["code"], "GB 50007-2011")
        self.assertEqual(first["section"], "5.2")
        self.assertIn("5.2.4", first["clauses"])
        self.assertGreater(first["pdf_page"], 0)
        self.assertGreater(first["page_count"], 0)
        self.assertIn("A1", first["reason"])

    def test_chapter_section_keyword_maps_to_stop(self):
        data = clauses.hints("C3-200", cache_dir=self.empty)
        self.assertTrue(data["hints"])
        first = data["hints"][0]
        self.assertEqual(first["source"], "keyword")
        self.assertEqual(first["code"], "GB 50007-2011")
        self.assertEqual(first["section"], "8.1")
        self.assertIn("冲切", first["reason"])

    def test_question_without_guide_returns_no_hints(self):
        data = clauses.hints("C8-130", cache_dir=self.empty)  # 盐渍土, no guide
        self.assertEqual(data["hints"], [])
        self.assertEqual(data["category_key"], "C8")

    def test_cached_citation_wins_and_merges_with_prototype(self):
        self._cache("C3-100", [{
            "title": "建筑地基基础设计规范",
            "code": "GB50007-2011",
            "clauses": ["5.2.4"],
        }])
        data = clauses.hints("C3-100", cache_dir=self.tmp)
        first = data["hints"][0]
        self.assertEqual(first["source"], "answer")
        self.assertIn("5.2.4", first["clauses"])
        self.assertIn("5.2.1", first["clauses"])  # merged from the prototype row
        same_section = [
            row for row in data["hints"]
            if row["code"] == "GB 50007-2011" and row["section"] == "5.2"
        ]
        self.assertEqual(len(same_section), 1)

    def test_unresolved_citation_is_reported_not_guessed(self):
        self._cache("A2025-A-1", [{
            "title": "水运工程岩土工程勘察规范",
            "code": "",
            "clauses": ["14.7.4.5"],
        }])
        data = clauses.hints("A2025-A-1", cache_dir=self.tmp)
        self.assertEqual(data["hints"], [])
        self.assertEqual(len(data["unresolved"]), 1)
        self.assertEqual(data["unresolved"][0]["clause"], "14.7.4.5")

    def test_every_hint_has_an_openable_page(self):
        for qid in ("C4-12", "C5-20", "C6-180", "C7-50", "C9-40"):
            data = clauses.hints(qid, cache_dir=self.empty)
            self.assertTrue(data["hints"], qid)
            for row in data["hints"]:
                self.assertGreater(row["pdf_page"], 0, qid)
                self.assertTrue(row["code"], qid)
                self.assertTrue(row["clauses"], qid)


if __name__ == "__main__":
    unittest.main()
