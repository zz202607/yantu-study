"""Baseline scoring: answer parsing, per-category accuracy, report append."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from score_baseline import (  # noqa: E402
    append_report,
    format_report,
    parse_answers,
    score_half,
)

ROWS = [
    ("A2025-A-1", "勘察", "C"),
    ("A2025-A-2", "勘察", "B"),
    ("A2025-A-3", "浅基础", "D"),
    ("A2025-A-4", "浅基础", "A"),
    ("A2025-A-5", "边坡", "C"),
]


class ParseAnswersTest(unittest.TestCase):
    def test_spaces_and_commas_are_ignored(self):
        self.assertEqual(
            parse_answers("c b, d,a、-", 5), ["C", "B", "D", "A", "-"]
        )

    def test_wrong_length_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "应有 5 个答案"):
            parse_answers("ABCD", 5)

    def test_unknown_character_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不认识的字符"):
            parse_answers("ABXE-", 5)


class ScoreHalfTest(unittest.TestCase):
    def test_counts_and_category_table(self):
        result = score_half(["C", "B", "A", "D", "-"], ROWS)
        self.assertEqual(result["n"], 5)
        self.assertEqual(result["answered"], 4)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["wrong"], 2)
        self.assertEqual(result["blank"], 1)
        self.assertEqual(result["score"], 4)
        self.assertEqual(result["max_score"], 10)
        self.assertEqual(result["by_category"]["勘察"]["correct"], 2)
        self.assertEqual(result["by_category"]["浅基础"]["answered"], 2)
        self.assertEqual(result["by_category"]["浅基础"]["correct"], 0)
        self.assertEqual(result["blank_qids"], ["A2025-A-5"])
        self.assertEqual(
            [(row["qid"], row["gave"], row["answer"]) for row in result["wrong_rows"]],
            [("A2025-A-3", "A", "D"), ("A2025-A-4", "D", "A")],
        )

    def test_length_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "答案 4 个，题目 5 道"):
            score_half(["A", "B", "C", "D"], ROWS)


class ReportTest(unittest.TestCase):
    def test_report_has_the_four_numbers_and_rates(self):
        result = score_half(["C", "B", "A", "D", "-"], ROWS)
        text = format_report("A", result, ["C", "B", "A", "D", "-"], 1, 1, "2026-11-04")
        self.assertIn("作答题数 **4** / 5", text)
        self.assertIn("做对 **2**", text)
        self.assertIn("放弃 **1**", text)
        self.assertIn("超时 **1**", text)
        self.assertIn("| 勘察 | 2 | 2 | 2 | 100% |", text)
        self.assertIn("| 浅基础 | 2 | 2 | 0 | 0% |", text)
        self.assertIn("A2025-A-3（浅基础，你 A / 答案 D）", text)

    def test_append_refuses_a_duplicate_heading(self):
        result = score_half(["C", "B", "A", "D", "-"], ROWS)
        section = format_report("A", result, ["C", "B", "A", "D", "-"], 1, 0, "2026-11-04")
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


if __name__ == "__main__":
    unittest.main()
