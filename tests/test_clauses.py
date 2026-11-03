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


class ClauseIndexTest(unittest.TestCase):
    """M12: the wrong book read back through its cited clauses."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="clause-index-")
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

    def _attempts(self):
        return [
            {"id": 1, "qid": "C3-100", "status": "active", "times": 1},
            {"id": 2, "qid": "C3-11", "status": "active", "times": 2},
            {"id": 9, "qid": "C8-130", "status": "active", "times": 1},
        ]

    def test_exact_rows_merge_questions_from_the_same_section(self):
        self._cache("C3-100", [{
            "title": "建筑地基基础设计规范",
            "code": "GB50007-2011",
            "clauses": ["5.2.4"],
        }])
        data = clauses.reverse(self._attempts(), cache_dir=self.tmp)
        self.assertEqual(len(data["exact"]), 1)
        row = data["exact"][0]
        self.assertEqual(row["section"], "5.2")
        self.assertEqual(row["source"], "answer")
        self.assertEqual(row["source_label"], "解析依据")
        self.assertEqual(row["active_count"], 2)
        self.assertGreater(row["pdf_page"], 0)
        sources = {q["qid"]: q["source"] for q in row["questions"]}
        self.assertEqual(sources, {"C3-100": "answer", "C3-11": "prototype"})
        self.assertEqual(row["sources"], {"answer": 1, "prototype": 1, "keyword": 0})
        self.assertEqual(
            [q["qid"] for q in row["questions"]], ["C3-100", "C3-11"]
        )
        # The prototype-only section stays in the suggestion bucket; the
        # exactly-cited question is not repeated there for the same standard.
        suggested = {r["section"]: r for r in data["suggested"]}
        self.assertIn("4.1", suggested)
        self.assertEqual(
            [q["qid"] for q in suggested["4.1"]["questions"]], ["C3-11"]
        )
        self.assertEqual(data["summary"]["exact_rows"], 1)
        self.assertEqual(data["summary"]["indexed"], 2)

    def test_section_span_stays_one_suggestion_row(self):
        data = clauses.reverse(
            [{"id": 1, "qid": "C4-13", "status": "active"}],
            cache_dir=self.empty,
        )
        sections = [row["section"] for row in data["suggested"]]
        self.assertEqual(sections, ["5.2/5.3", "5.4"])
        span = data["suggested"][0]
        self.assertIn("5.2.2", span["clauses"])
        self.assertIn("5.3.5", span["clauses"])
        self.assertEqual(span["active_count"], 1)
        for row in data["suggested"]:
            self.assertEqual(row["source"], "prototype")
            self.assertEqual(row["source_label"], "原型建议")

    def test_mastered_questions_are_hidden_by_default(self):
        attempts = [
            {"id": 1, "qid": "C3-11", "status": "active", "times": 2},
            {"id": 2, "qid": "C3-13", "status": "mastered", "times": 4},
        ]
        default = clauses.reverse(attempts, cache_dir=self.empty)
        self.assertEqual(default["summary"]["included"], 1)
        self.assertEqual(default["summary"]["mastered_hidden"], 1)
        qids = [
            q["qid"] for row in default["exact"] + default["suggested"]
            for q in row["questions"]
        ]
        self.assertEqual(qids, ["C3-11", "C3-11"])  # 5.2 + 4.1, one question each
        full = clauses.reverse(attempts, include_mastered=True, cache_dir=self.empty)
        rows = [
            row for row in full["exact"] + full["suggested"]
            if any(q["qid"] == "C3-13" for q in row["questions"])
        ]
        self.assertTrue(rows)
        self.assertTrue(all(row["mastered_count"] == 1 for row in rows))
        self.assertTrue(all(row["active_count"] == 1 for row in rows))

    def test_question_without_any_hint_is_not_silently_dropped(self):
        data = clauses.reverse(self._attempts(), cache_dir=self.empty)
        self.assertEqual([row["qid"] for row in data["unmatched"]], ["C8-130"])
        self.assertEqual(data["summary"]["unmatched"], 1)
        self.assertEqual(data["summary"]["indexed"], 2)

    def test_every_index_row_is_openable(self):
        data = clauses.reverse(self._attempts(), cache_dir=self.empty)
        rows = data["exact"] + data["suggested"]
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["code"], row["section"])
            self.assertGreater(row["pdf_page"], 0, row["section"])
            self.assertTrue(row["clauses"], row["section"])
            self.assertTrue(row["questions"], row["section"])
            self.assertIn(row["source"], ("answer", "prototype", "keyword"))

    def test_unresolved_citation_keeps_its_qid(self):
        self._cache("A2025-A-1", [{
            "title": "水运工程岩土工程勘察规范",
            "code": "",
            "clauses": ["14.7.4.5"],
        }])
        data = clauses.reverse(
            [{"id": 3, "qid": "A2025-A-1", "status": "active"}],
            cache_dir=self.tmp,
        )
        self.assertEqual(data["unmatched"][0]["qid"], "A2025-A-1")
        self.assertEqual(data["unresolved"][0]["qid"], "A2025-A-1")
        self.assertEqual(data["summary"]["unresolved"], 1)


if __name__ == "__main__":
    unittest.main()
