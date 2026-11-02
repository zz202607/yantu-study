"""Standards index builder: TOC parsing, dedupe and page sanitising."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_standards_index as B  # noqa: E402


class TocParseTest(unittest.TestCase):
    def test_split_chinese_toc_lines_are_joined(self):
        lines = [
            "目次",
            "1", "总则", "1",
            "2.1", "术语", "…… 2",
            "3. 2", "地震影响", "6",
        ]
        rows = B.parse_toc_lines(lines)
        numbers = {row["number"]: row for row in rows}
        self.assertEqual(numbers["1"]["title"], "总则")
        self.assertEqual(numbers["2.1"]["printed_page"], 2)
        self.assertEqual(numbers["3.2"]["title"], "地震影响")

    def test_chinese_title_wins_over_english(self):
        rows = B.parse_toc_lines([
            "5.1 General 31",
            "5.1 一般规定 31",
        ])
        self.assertEqual(rows[0]["title"], "一般规定")

    def test_noise_chapter_numbers_are_dropped(self):
        rows = B.parse_toc_lines([
            "101 李峰 16",
            "4 7.3 水泥土搅拌桩 10",
            "5 地基计算 18",
        ])
        self.assertEqual([row["number"] for row in rows], ["5"])

    def test_out_of_order_page_is_clamped(self):
        rows = B.sanitize_entries([
            {"level": 2, "number": "6.2", "title": "土岩组合", "printed_page": 35},
            {"level": 2, "number": "6.4", "title": "滑坡", "printed_page": 68},
            {"level": 2, "number": "6.7", "title": "挡墙", "printed_page": 45},
        ])
        row = next(item for item in rows if item["number"] == "6.4")
        self.assertEqual(row["printed_page"], 45)
        self.assertTrue(row["estimated"])


if __name__ == "__main__":
    unittest.main()
