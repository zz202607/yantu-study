"""Catalog display data: families, chapters, sections, short book names."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import Catalog, normalize_qid  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_bare_qid_gets_the_current_family(self):
        self.assertEqual(normalize_qid("3-11", "C"), "C3-11")
        self.assertEqual(normalize_qid("3-11", "Z"), "Z3-11")

    def test_full_keys_survive(self):
        self.assertEqual(normalize_qid("m2-13"), "M2-13")
        self.assertEqual(normalize_qid("A2016-P-19"), "A2016-P-19")

    def test_dashes_and_spaces_are_normalised(self):
        self.assertEqual(normalize_qid(" 3—11 "), "C3-11")

    def test_junk_is_rejected(self):
        self.assertIsNone(normalize_qid("hello"))
        self.assertIsNone(normalize_qid(""))


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = Catalog(os.path.join(ROOT, "index"))

    def test_case_question_maps_to_volume_and_section(self):
        info = self.cat.describe("C3-8")
        self.assertEqual(info["family"], "C")
        self.assertEqual(info["chapter"], "第三篇 浅基础")
        self.assertEqual(info["section"], "第一节 地基承载力计算与修正")
        self.assertTrue(info["book"])

    def test_knowledge_question_has_chapter_number(self):
        info = self.cat.describe("Z1-5")
        self.assertEqual(info["chapter"], "第1章")
        self.assertEqual(info["family"], "Z")

    def test_exam_question_names_year_and_session(self):
        info = self.cat.describe("A2016-P-19")
        self.assertEqual(info["chapter"], "2016 案例真题 · 下午卷")
        self.assertTrue(info["book"])

    def test_mock_question_names_the_set(self):
        info = self.cat.describe("M2-13")
        self.assertEqual(info["chapter"], "模拟第2套")
        self.assertEqual(info["family"], "M")

    def test_unknown_qid_is_blank_and_not_an_error(self):
        self.assertFalse(self.cat.exists("C99-1"))
        info = self.cat.describe("C99-1")
        self.assertEqual(info["chapter"], "")

    def test_chapter_keys_cover_the_case_volumes(self):
        keys = [row["key"] for row in self.cat.chapter_keys()]
        self.assertEqual(keys, ["C%d" % i for i in range(1, 11)])

    def test_exists_accepts_every_family(self):
        self.assertTrue(self.cat.exists("C3-8"))
        self.assertTrue(self.cat.exists("Z1-5"))
        self.assertTrue(self.cat.exists("A2016-P-19"))
        self.assertTrue(self.cat.exists("K2016-P-1"))
        self.assertTrue(self.cat.exists("M2-13"))


if __name__ == "__main__":
    unittest.main()
