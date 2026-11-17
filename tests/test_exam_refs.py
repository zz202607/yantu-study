"""Past-exam markers: index loading, sorting, clause -> stop placement."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app import exam_refs, reading  # noqa: E402


def _question(year, session, num, refs, evidence="answer"):
    return {
        "done": True,
        "year": year,
        "session": session,
        "num": num,
        "evidence": evidence,
        "refs": refs,
    }


def _ref(code_key, clauses, evidence="answer"):
    return {
        "code_key": code_key,
        "code": "GB 50007-2011" if code_key == "GB50007-2011" else "JGJ 94-2008",
        "title": "建筑地基基础设计规范" if code_key == "GB50007-2011" else "建筑桩基技术规范",
        "clauses": clauses,
        "evidence": evidence,
    }


PAYLOAD = {
    "version": 1,
    "built_at": "2026-11-17T10:00:00",
    "coverage": {"total": 4, "done": 4, "with_refs": 4, "standards": 2},
    "questions": {
        "A2024-A-6": _question(2024, "A", 6, [_ref("GB50007-2011", ["5.2.7"])]),
        "A2023-P-9": _question(2023, "P", 9, [_ref("GB50007-2011", ["5.3.2"])]),
        "A2022-A-1": _question(2022, "A", 1, [_ref("GB50007-2011", [])]),
        "A2021-A-2": _question(2021, "A", 2, [_ref("JGJ94-2008", ["5.3.6"])]),
    },
}


class ExamRefsIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exam-refs-")
        self.path = Path(self.tmp) / "exam_refs.json"
        self.path.write_text(json.dumps(PAYLOAD, ensure_ascii=False), encoding="utf-8")
        self.saved = exam_refs.INDEX_PATH
        exam_refs.INDEX_PATH = self.path
        exam_refs._CACHE.update(mtime=None, data=None)

    def tearDown(self):
        exam_refs.INDEX_PATH = self.saved
        exam_refs._CACHE.update(mtime=None, data=None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_index_is_empty_not_an_error(self):
        missing = Path(self.tmp) / "nope.json"
        self.assertEqual(exam_refs.for_standard("GB50007-2011", path=missing), [])
        self.assertEqual(exam_refs.summary(path=missing)["total"], 0)

    def test_refs_are_newest_first_and_labelled(self):
        refs = exam_refs.for_standard("GB50007-2011")
        self.assertEqual([row["qid"] for row in refs],
                         ["A2024-A-6", "A2023-P-9", "A2022-A-1"])
        self.assertEqual(refs[0]["label"], "2024上午6")
        self.assertEqual(refs[0]["short_label"], "24上6")
        self.assertEqual(refs[1]["label"], "2023下午9")
        self.assertEqual(refs[1]["clauses"], ["5.3.2"])

    def test_counts_by_standard(self):
        counts = exam_refs.counts_by_standard()
        self.assertEqual(counts["GB50007-2011"], 3)
        self.assertEqual(counts["JGJ94-2008"], 1)

    def test_clause_places_a_ref_on_its_stop(self):
        stops = reading.guide("GB50007-2011")["stops"]
        by_stop = exam_refs.for_stops(stops, "GB50007-2011")
        self.assertEqual([row["qid"] for row in by_stop["gb50007-5-2"]],
                         ["A2024-A-6"])
        self.assertEqual([row["qid"] for row in by_stop["gb50007-5-3"]],
                         ["A2023-P-9"])
        # A clause-less citation stays at standard level instead of guessing.
        placed = {row["qid"] for rows in by_stop.values() for row in rows}
        self.assertNotIn("A2022-A-1", placed)

    def test_appendix_clause_matches_the_appendix_stop(self):
        stop = {"id": "x", "section": "附录K", "clauses": ["附录K"]}
        self.assertTrue(exam_refs._stop_matches(stop, "附录K.0.1"))
        self.assertTrue(exam_refs._stop_matches(stop, "附录K"))
        self.assertFalse(exam_refs._stop_matches(stop, "5.3.2"))

    def test_detail_carries_markers_for_stops_and_standard(self):
        detail = reading.detail("GB50007-2011", {})
        self.assertEqual(detail["exam"]["count"], 3)
        self.assertEqual(detail["exam"]["coverage"]["done"], 4)
        stop = next(row for row in detail["stops"] if row["id"] == "gb50007-5-2")
        self.assertEqual(stop["exam_count"], 1)
        self.assertEqual(stop["exam_refs"][0]["qid"], "A2024-A-6")


class CitationParsingTest(unittest.TestCase):
    def test_standard_code_and_clause_become_a_ref(self):
        from build_exam_refs import refs_from_text

        refs = refs_from_text(
            "按《建筑地基基础设计规范》GB 50007-2011 第5.2.4条计算", "answer"
        )
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["code_key"], "GB50007-2011")
        self.assertEqual(refs[0]["clauses"], ["5.2.4"])

    def test_bare_title_is_kept_when_it_resolves(self):
        from build_exam_refs import refs_from_text

        refs = refs_from_text("按照《建筑地基基础设计规范》验算抗倾覆。", "text")
        self.assertEqual([row["code_key"] for row in refs], ["GB50007-2011"])

    def test_unknown_title_is_dropped(self):
        from build_exam_refs import refs_from_text

        self.assertEqual(refs_from_text("见《某本不存在的规范》第1.1条。", "answer"), [])


if __name__ == "__main__":
    unittest.main()
