"""Dates on a page must never turn into question keys.

A handwritten or printed `2024-11-14` is read by OCR as digits joined by a
dash.  The loose channel used to take the tail of the year and the month
(`4-11`) and offer it as a real question, so a photo of a dated page showed a
phantom candidate.  This test pins the date guard while keeping ordinary
labels intact.

Run:
    py -3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from recognize_qid import qid_candidates  # noqa: E402

VALID = {"3-11", "4-11", "5-11", "10-14"}


def qids(text):
    return [qid for qid, _, _ in qid_candidates(text, VALID)]


class DateGuardTest(unittest.TestCase):
    def test_full_date_with_dashes_is_not_a_qid(self):
        # `2024-11` used to resolve to the valid key `4-11`.
        self.assertEqual(qids("2024-11-14"), [])

    def test_full_date_with_dots_is_not_a_qid(self):
        self.assertEqual(qids("2024.11.14"), [])

    def test_full_date_with_slashes_is_not_a_qid(self):
        self.assertEqual(qids("2024/11/14"), [])

    def test_chinese_date_is_not_a_qid(self):
        self.assertEqual(qids("2024年11月14日"), [])

    def test_year_month_without_day_is_not_a_qid(self):
        self.assertEqual(qids("2024-11"), [])

    def test_two_digit_year_is_not_a_qid(self):
        self.assertEqual(qids("24-11-14"), [])

    def test_spaces_inside_the_date_do_not_help_it(self):
        self.assertEqual(qids("2024 - 11 - 14"), [])

    def test_ocr_dash_variants_are_still_a_date(self):
        self.assertEqual(qids("2024—11—14"), [])
        self.assertEqual(qids("2024_11_14"), [])
        self.assertEqual(qids("2024一11一14"), [])

    def test_printable_qid_still_wins(self):
        self.assertEqual(set(qids("3-11 某土层")), {"3-11"})

    def test_qid_next_to_another_number_is_unchanged(self):
        self.assertIn("4-11", qids("4-11  某基础底面"))

    def test_non_date_number_run_still_uses_the_suffix_split(self):
        # A three-digit run that is not a date (e.g. a glued page number)
        # still resolves to the printed label through the existing split.
        self.assertIn("3-11", qids("203-11 某题"))


if __name__ == "__main__":
    unittest.main()
