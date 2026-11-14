"""Question/answer crop boundaries: OCR label noise and short label lines."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crop_question as CQ  # noqa: E402
import extract_qids as EQ  # noqa: E402


def hit(qid, raw, y, x=100):
    return {"qid": qid, "raw": raw, "box": [x, y, x + 500, y + 20]}


class LabelNoiseTest(unittest.TestCase):
    def test_regulation_citation_is_not_a_label(self):
        # JGJ 94-2008 used to be stored as `94-200` because the old regex
        # allowed a trailing digit; replaying the current rule drops it.
        self.assertIsNone(CQ.label_qid(hit("94-200", "94-2008),", 163)))

    def test_real_label_survives(self):
        self.assertEqual(CQ.label_qid(hit("4-8", "4-8（12D11", 92)), "4-8")
        self.assertEqual(CQ.label_qid(hit("4-10", "4-10(07C1", 505)), "4-10")


class NextLabelTest(unittest.TestCase):
    chapters = {4, 5, 6}

    def test_citation_is_skipped_for_the_real_next_question(self):
        hits = [
            hit("94-200", "94-2008),", 163, x=72),
            hit("4-9", "4-9（11D11", 300),
        ]
        self.assertEqual(
            CQ.next_label(hits, 0.72, 0, "C4-8", self.chapters), 300 * 0.72
        )

    def test_only_a_citation_means_no_boundary(self):
        hits = [hit("94-200", "94-2008),", 163, x=72)]
        self.assertIsNone(CQ.next_label(hits, 0.72, 0, "C4-8", self.chapters))

    def test_an_earlier_question_does_not_close_the_crop(self):
        hits = [hit("4-7", "4-7（10D5", 300)]
        self.assertIsNone(CQ.next_label(hits, 0.72, 0, "C4-8", self.chapters))

    def test_the_next_chapter_does_close_it(self):
        hits = [hit("5-1", "5-1（12D2", 300)]
        self.assertEqual(
            CQ.next_label(hits, 0.72, 0, "C4-8", self.chapters), 300 * 0.72
        )


class ShortLabelLineTest(unittest.TestCase):
    """A label printed on its own line is short but still a question line."""

    @staticmethod
    def box(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

    def test_standalone_label_is_accepted(self):
        # RapidOCR reads `4-9` alone at x=98 on an 789 px wide page.
        self.assertTrue(EQ._is_question_line(self.box(98, 78, 130, 96), 789, "4-9"))

    def test_same_fragment_inside_a_figure_is_rejected(self):
        self.assertFalse(
            EQ._is_question_line(self.box(250, 300, 282, 318), 789, "4-9")
        )


if __name__ == "__main__":
    unittest.main()
