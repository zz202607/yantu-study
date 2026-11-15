"""Photos of past-exam pages must yield `A2025-A-7`-style keys.

A printed wrong-book sheet mixes workbook labels (`3-11`) with self-describing
exam labels (`A2025-A-7`, `K2025-P-68`) and mock labels (`M2-13`).  The
recogniser used to accept only `<chapter>-<number>` pairs, so a photo of an
exam question produced no candidates at all.  These tests pin the shape, the
OCR damage variants, and the index validation that stops a dropped dash from
inventing a question.

Run:
    py -3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from recognize_qid import (  # noqa: E402
    auto_qid,
    load_self_describing,
    qid_candidates,
)

EXAM = {"A2025-A-1", "A2025-A-7", "A2025-A-70", "A2024-P-25", "K2025-P-68"}
MOCK = {"M1-2", "M2-13"}
VALID = {"3-11", "4-11"}


def cands(text, valid=VALID, exam=EXAM, mock=MOCK):
    return qid_candidates(text, valid, exam, mock)


def qids(text, **kwargs):
    return [qid for qid, _, _ in cands(text, **kwargs)]


def kinds(text, **kwargs):
    return {qid: kind for qid, _, kind in cands(text, **kwargs)}


class ExamLabelTest(unittest.TestCase):
    def test_label_at_line_start_is_a_start(self):
        self.assertEqual(
            kinds("A2025-A-7 · 来源：2025案例A 第 12 页"),
            {"A2025-A-7": "start"},
        )

    def test_answer_caption_is_read(self):
        self.assertEqual(qids("A2025-A-7 答案 · 来源：2025案例A 第 30 页"),
                         ["A2025-A-7"])

    def test_knowledge_exam_label_is_read(self):
        self.assertEqual(qids("K2025-P-68 · 来源：2025知识P 第 9 页"),
                         ["K2025-P-68"])

    def test_ocr_dash_variants_keep_the_key(self):
        for text in (
            "A2025—A—7",
            "A2025–A–7",
            "A2025一A一7",
            "A2025·A·7",
            "A2025.A.7",
            "A2025 A 7",
            "A2025−A−7",
        ):
            with self.subTest(text=text):
                self.assertEqual(qids(text), ["A2025-A-7"])

    def test_dropped_separators_keep_the_key(self):
        for text in ("A2025A-7", "A2025-A7", "A2025A7"):
            with self.subTest(text=text):
                self.assertEqual(qids(text), ["A2025-A-7"])

    def test_fullwidth_and_lowercase_are_normalised(self):
        self.assertEqual(qids("ａ２０２５－ａ－７ 某工程"), ["A2025-A-7"])

    def test_leading_bracket_is_tolerated(self):
        self.assertEqual(kinds("（A2025-A-7）某题"), {"A2025-A-7": "start"})

    def test_leading_zeros_are_dropped(self):
        self.assertEqual(qids("A2025-A-07"), ["A2025-A-7"])

    def test_stray_mark_before_the_label_is_a_loose_read(self):
        self.assertEqual(kinds("p=A2025-A-7"), {"A2025-A-7": "loose"})

    def test_hyphen_is_not_a_truncator(self):
        # `A2025-A-70` must not be offered as question 7.
        self.assertEqual(qids("A2025-A-70"), ["A2025-A-70"])

    def test_unknown_key_is_rejected(self):
        self.assertEqual(qids("A2025-A-99"), [])
        self.assertEqual(qids("A2023-A-7"), [])

    def test_without_a_registry_a_well_formed_read_survives(self):
        self.assertEqual(
            qids("A2025-A-7", exam=None), ["A2025-A-7"]
        )

    def test_exam_label_can_auto_open(self):
        rows = [
            {"qid": qid, "score": round(score, 3), "kind": kind}
            for qid, score, kind in cands("A2025-A-7 · 来源：…")
        ]
        self.assertEqual(auto_qid(rows), "A2025-A-7")

    def test_date_never_becomes_an_exam_label(self):
        self.assertEqual(qids("2025-A-7"), [])
        self.assertEqual(qids("2025-11-14"), [])


class MockLabelTest(unittest.TestCase):
    def test_mock_label_at_line_start(self):
        self.assertEqual(kinds("M2-13 某题"), {"M2-13": "start"})

    def test_dropped_dash_resolves_against_the_mock_index(self):
        rows = cands("M213 某题", mock={"M2-13"})
        self.assertEqual({qid: kind for qid, _, kind in rows},
                         {"M2-13": "bare"})

    def test_ambiguous_bare_read_is_dropped(self):
        self.assertEqual(qids("M213 某题", mock={"M2-13", "M21-3"}), [])

    def test_unknown_mock_key_is_rejected(self):
        self.assertEqual(qids("M9-99 某题"), [])

    def test_mock_label_does_not_spawn_a_chapter_read(self):
        # `M2-13` contains `2-13`; that run belongs to the mock label and is
        # not a question of the current workbook.
        rows = cands("M2-13 某题", valid={"2-13"}, mock={"M2-13"})
        self.assertEqual([qid for qid, _, _ in rows], ["M2-13"])

    def test_bare_mock_label_does_not_spawn_a_chapter_read(self):
        rows = cands("M213某题", valid={"2-13"}, mock={"M2-13"})
        self.assertEqual([qid for qid, _, _ in rows], ["M2-13"])


class RegistryTest(unittest.TestCase):
    def test_real_index_keys_load(self):
        exam, mock = load_self_describing()
        self.assertIn("A2025-A-7", exam)
        self.assertIn("K2025-P-68", exam)
        self.assertTrue(mock)


if __name__ == "__main__":
    unittest.main()
