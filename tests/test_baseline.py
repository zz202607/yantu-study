"""Case baseline: four manual numbers, no option-letter scoring."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from score_baseline import (  # noqa: E402
    append_report,
    format_report,
    validate_counts,
)


class ValidateCountsTest(unittest.TestCase):
    def test_default_skipped_is_the_rest_of_the_paper(self):
        counts = validate_counts(23, 12)
        self.assertEqual(counts["skipped"], 2)
        self.assertEqual(counts["overtime"], 0)
        self.assertEqual(counts["score"], 24)
        self.assertEqual(counts["max_score"], 50)

    def test_explicit_skipped_must_complete_25(self):
        counts = validate_counts(20, 10, skipped=5, overtime=1)
        self.assertEqual(counts["skipped"], 5)
        self.assertEqual(counts["overtime"], 1)
        with self.assertRaisesRegex(ValueError, "应等于 25"):
            validate_counts(20, 10, skipped=4)

    def test_correct_cannot_exceed_answered(self):
        with self.assertRaisesRegex(ValueError, "做对数"):
            validate_counts(20, 21)

    def test_overtime_cannot_exceed_answered(self):
        with self.assertRaisesRegex(ValueError, "超时题数"):
            validate_counts(20, 10, skipped=5, overtime=21)

    def test_out_of_range_answered_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "作答题数"):
            validate_counts(26, 1)
        with self.assertRaisesRegex(ValueError, "作答题数"):
            validate_counts(-1, 0)


class ReportTest(unittest.TestCase):
    def test_report_has_four_numbers_and_the_process_rule(self):
        counts = validate_counts(23, 12, skipped=2, overtime=1)
        text = format_report("A", counts, "2026-11-04")
        self.assertIn("作答 **23** / 25", text)
        self.assertIn("做对 **12**", text)
        self.assertIn("放弃 **2**", text)
        self.assertIn("超时 **1**", text)
        self.assertIn("得分 **24 / 50**", text)
        self.assertIn("过程评分", text)
        self.assertIn("不按选项字母判分", text)

    def test_report_never_mentions_option_letters_as_answers(self):
        text = format_report("P", validate_counts(25, 10), "2026-11-04")
        self.assertNotIn("答案串", text)
        self.assertNotIn("A/B/C/D", text)

    def test_append_refuses_a_duplicate_heading(self):
        section = format_report("A", validate_counts(25, 10), "2026-11-04")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "baseline.md")
            append_report(section, path)
            append_report(section.replace("2026-11-04", "2026-11-05"), path)
            with self.assertRaisesRegex(ValueError, "已有同名小节"):
                append_report(section, path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertEqual(text.count("2026-11-04"), 1)
        self.assertIn("2026-11-05", text)
        self.assertIn("不按选项字母判分", text)


if __name__ == "__main__":
    unittest.main()
