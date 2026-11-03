"""Curriculum-to-standards matching and handbook indexing/search."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymupdf  # noqa: E402

from app import standards  # noqa: E402
from app.curriculum import CASE_CATEGORIES, CASE_WEIGHT_TOTAL, category_key  # noqa: E402


class CurriculumTest(unittest.TestCase):
    def test_ten_rows_and_nine_exam_categories(self):
        self.assertEqual(len(CASE_CATEGORIES), 10)
        weights = [row["weight"] for row in CASE_CATEGORIES]
        self.assertEqual(weights[0], 0)  # 土力学 is not its own exam question
        self.assertEqual(sum(weights), 50)
        self.assertEqual(CASE_WEIGHT_TOTAL, 50)

    def test_every_exam_category_has_a_core_standard(self):
        for row in CASE_CATEGORIES[1:]:
            self.assertTrue(row["core"], row["key"])
            self.assertTrue(row["handbook"], row["key"])

    def test_category_mapping(self):
        self.assertEqual(category_key("C3-11"), "C3")
        self.assertEqual(category_key("C1-1"), "C1")
        self.assertIsNone(category_key("Z3-1"))


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="standards-")
        root = Path(self.tmp)
        for name in (
            "1.GB 50021-2001(2009年版) 岩土工程勘察规范.pdf",
            "5.GBT50123-2019土工试验方法标准.pdf",
            "18.JGJ 94-2008 建筑桩基技术规范.pdf",
            "35.地质灾害危险性评估规范GB∕T 40112-2021(国标委).pdf",
        ):
            (root / name).write_bytes(b"%PDF-1.4\n")
        (root / "notes.txt").write_text("ignore", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_library_decodes_codes_and_titles(self):
        data = standards.load_library(self.tmp, force=True)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["count"], 4)  # txt is ignored
        rows = {row["code"]: row for row in data["files"]}
        self.assertIn("GB 50021-2001(2009年版)", rows)
        self.assertEqual(rows["GB 50021-2001(2009年版)"]["title"], "岩土工程勘察规范")
        self.assertEqual(
            rows["GB∕T 40112-2021"]["title"], "地质灾害危险性评估规范"
        )

    def test_code_key_normalises_slashes_and_years(self):
        self.assertEqual(standards.code_key("GB/T 50942-2014"), "GBT50942-2014")
        self.assertEqual(standards.code_key("GB 50021-2001(2009年版)"), "GB50021-2001")
        self.assertEqual(standards.code_key("JGJT 87-2012"), "JGJT87-2012")

    def test_header_safe_drops_chinese_from_custom_headers(self):
        # Starlette encodes header values as latin-1; a Chinese code used to
        # raise UnicodeEncodeError and 500 the standards page.
        self.assertEqual(
            standards.header_safe("GB 50021-2001(2009年版)"),
            "GB 50021-2001(2009)",
        )
        self.assertEqual(standards.header_safe(None), "")
        self.assertEqual(standards.header_safe("GB50007-2011"), "GB50007-2011")

    def test_match_by_code_beats_title(self):
        files = standards.load_library(self.tmp)["files"]
        match = standards.match_standard("岩土工程勘察规范", "GB 50021-2001", files)
        self.assertEqual(match["name"], "1.GB 50021-2001(2009年版) 岩土工程勘察规范.pdf")
        self.assertIn("/api/standards/file?name=", match["url"])

    def test_match_by_title_when_code_is_missing(self):
        files = standards.load_library(self.tmp)["files"]
        match = standards.match_standard("建筑桩基技术规范", "", files)
        self.assertEqual(match["code"], "JGJ 94-2008")

    def test_unknown_citation_returns_none(self):
        files = standards.load_library(self.tmp)["files"]
        self.assertIsNone(standards.match_standard("不存在的规范", "XX 1-1999", files))

    def test_file_path_blocks_traversal_and_unknown_files(self):
        self.assertIsNone(standards.file_path("../secret.pdf", self.tmp))
        self.assertIsNone(standards.file_path("notes.txt", self.tmp))
        self.assertIsNone(standards.file_path("missing.pdf", self.tmp))
        found = standards.file_path(
            "18.JGJ 94-2008 建筑桩基技术规范.pdf", self.tmp
        )
        self.assertIsNotNone(found)

    def test_standard_map_enriches_the_core_standard(self):
        data = standards.standard_map(self.tmp)
        shallow = next(row for row in data["categories"] if row["key"] == "C3")
        self.assertEqual(shallow["core"][0]["code"], "GB 50007-2011")
        # No GB 50007 PDF in the fixture, so the link stays absent.
        self.assertIsNone(shallow["core"][0]["file"])


class HandbookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="handbook-")
        self.pdf = os.path.join(self.tmp, "handbook.pdf")
        self.index = os.path.join(self.tmp, "handbook_index.json")
        doc = pymupdf.open()
        texts = [
            "alpha liquefaction site class",
            "beta settlement calculation",
            "gamma slope stability",
        ]
        for text in texts:
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), text, fontsize=14)
        doc.set_toc([[1, "Alpha chapter", 1], [2, "Beta section", 2]])
        doc.save(self.pdf)
        doc.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_extracts_text_and_bookmarks(self):
        data = standards.build_index(self.pdf, self.index)
        self.assertEqual(data["page_count"], 3)
        self.assertIn("liquefaction", data["pages"][0])
        self.assertEqual(data["toc"][0]["title"], "Alpha chapter")
        self.assertTrue(os.path.exists(self.index))

    def test_search_matches_every_token(self):
        result = standards.search("settlement", handbook=self.pdf,
                                  index_path=self.index)
        self.assertTrue(result["index_ready"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["page"], 2)
        self.assertEqual(result["results"][0]["bookmark"]["title"], "Beta section")

    def test_search_ranks_bookmark_matches_first(self):
        result = standards.search("chapter", handbook=self.pdf,
                                  index_path=self.index)
        self.assertGreaterEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["kind"], "toc")
        self.assertEqual(result["results"][0]["page"], 1)

    def test_empty_query_is_cheap(self):
        result = standards.search("", handbook=self.pdf, index_path=self.index)
        self.assertFalse(result["index_ready"])
        self.assertEqual(result["results"], [])

    def test_page_image_renders_and_rejects_out_of_range(self):
        path, page_count = standards.page_image(
            1, dpi=72, handbook=self.pdf, cache_dir=os.path.join(self.tmp, "pages")
        )
        self.assertEqual(page_count, 3)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(Path(path).suffix, ".png")
        with self.assertRaises(ValueError):
            standards.page_image(99, handbook=self.pdf,
                                 cache_dir=os.path.join(self.tmp, "pages"))

    def test_meta_reports_the_book(self):
        info = standards.meta(self.pdf, self.index)
        self.assertTrue(info["exists"])
        self.assertEqual(info["page_count"], 3)
        self.assertEqual(info["toc_count"], 2)


if __name__ == "__main__":
    unittest.main()


class StandardIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="std-index-")
        self.index = os.path.join(self.tmp, "standards_index.json")
        self.text = os.path.join(self.tmp, "text")
        os.makedirs(self.text)
        self.library = os.path.join(self.tmp, "library")
        os.makedirs(self.library)
        self.pdf = os.path.join(self.library, "GB 50007-2011 test.pdf")
        doc = pymupdf.open()
        for _ in range(4):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), "alpha beta", fontsize=14)
        doc.save(self.pdf)
        doc.close()
        with open(os.path.join(self.text, "GB50007-2011.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"pages": [
                "第一章 总则 alpha",
                "第二章 术语 beta",
                "5.2 承载力计算 gamma",
                "5.2.4 具体条文 delta",
            ]}, fh, ensure_ascii=False)
        with open(self.index, "w", encoding="utf-8") as fh:
            json.dump({"built_at": "2026-11-02", "folder": self.library,
                       "standards": [{
                           "name": os.path.basename(self.pdf),
                           "code": "GB 50007-2011",
                           "code_key": "GB50007-2011",
                           "title": "建筑地基基础设计规范",
                           "page_count": 4,
                           "source": "ocr_toc",
                           "confidence": "high",
                           "offset": 0,
                           "has_text": True,
                           "toc": [
                               {"level": 1, "number": "5", "title": "地基计算",
                                "printed_page": 18, "pdf_page": 3},
                               {"level": 2, "number": "5.2", "title": "承载力计算",
                                "printed_page": 21, "pdf_page": 4, "estimated": True},
                           ],
                       }]}, fh, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_summaries_and_toc(self):
        rows = standards.standard_summaries(self.index)
        self.assertEqual(rows[0]["code"], "GB 50007-2011")
        toc = standards.standard_toc("GB 50007-2011", self.index)
        self.assertEqual(toc["toc"][0]["pdf_page"], 3)
        self.assertTrue(toc["has_text"])

    def test_clause_resolves_to_longest_prefix(self):
        found = standards.locate_clause("GB 50007-2011", "第5.2.4条", self.index)
        self.assertEqual(found["matched"], "5.2")
        self.assertEqual(found["pdf_page"], 4)
        self.assertTrue(found["estimated"])
        exact = standards.locate_clause("GB50007-2011", "5", self.index)
        self.assertEqual(exact["matched"], "5")

    def test_clause_normalisation(self):
        self.assertEqual(standards.normalize_clause("第5.2.4条"), "5.2.4")
        self.assertEqual(standards.normalize_clause("附录A.0.1"), "附录A")

    def test_text_search(self):
        result = standards.search_standard(
            "GB 50007-2011", "gamma", index_path=self.index,
            text_cache=self.text,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["page"], 3)

    def test_page_renders_and_rejects_out_of_range(self):
        path, count, row = standards.standard_page(
            "GB 50007-2011", 1, dpi=72, index_path=self.index,
            library_dir=self.library, cache_dir=os.path.join(self.tmp, "pages"),
        )
        self.assertTrue(os.path.exists(path))
        self.assertEqual(count, 4)
        self.assertEqual(row["code"], "GB 50007-2011")
        with self.assertRaises(ValueError):
            standards.standard_page(
                "GB 50007-2011", 99, index_path=self.index,
                library_dir=self.library, cache_dir=os.path.join(self.tmp, "pages"),
            )
