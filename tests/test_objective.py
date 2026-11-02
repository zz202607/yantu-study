"""Objective knowledge scoring: single and exact-match multiple choice."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.objective import normalize_answer, score_objective  # noqa: E402

KEY = {
    "paper": "K-test",
    "questions": [
        {"qid": "K1", "type": "single", "answer": "B", "points": 1, "category": "勘察"},
        {"qid": "K2", "type": "multiple", "answer": ["A", "D"], "points": 2, "category": "勘察"},
        {"qid": "K3", "type": "single", "answer": "C", "points": 1, "category": "浅基础"},
    ],
}


class NormalizeTest(unittest.TestCase):
    def test_letters_are_uppercased_and_sorted(self):
        self.assertEqual(normalize_answer("d, a"), "AD")
        self.assertEqual(normalize_answer(["b", "A"]), "AB")

    def test_blank_is_empty(self):
        self.assertEqual(normalize_answer(None), "")
        self.assertEqual(normalize_answer(" - "), "")


class ScoreTest(unittest.TestCase):
    def test_single_and_exact_multiple(self):
        result = score_objective(KEY, {"K1": "B", "K2": "DA", "K3": "A"})
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["answered"], 3)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["score"], 3)
        self.assertEqual(result["max_score"], 4)
        self.assertAlmostEqual(result["accuracy"], 2 / 3, places=4)
        self.assertTrue(result["questions"][1]["correct"])
        self.assertFalse(result["questions"][2]["correct"])

    def test_partial_multiple_choice_scores_zero(self):
        result = score_objective(KEY, {"K1": "B", "K2": "A", "K3": "C"})
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["score"], 2)
        row = next(q for q in result["questions"] if q["qid"] == "K2")
        self.assertFalse(row["correct"])

    def test_extra_multiple_choice_option_scores_zero(self):
        result = score_objective(KEY, {"K1": "B", "K2": "ABD", "K3": "C"})
        self.assertEqual(result["score"], 2)

    def test_blank_is_unanswered_and_wrong(self):
        result = score_objective(KEY, {"K1": "B"})
        self.assertEqual(result["answered"], 1)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["score"], 1)
        blank = next(q for q in result["questions"] if q["qid"] == "K2")
        self.assertEqual(blank["gave"], "")
        self.assertFalse(blank["correct"])

    def test_two_letters_on_single_choice_is_invalid(self):
        result = score_objective(KEY, {"K1": "AB", "K2": "AD", "K3": "C"})
        row = next(q for q in result["questions"] if q["qid"] == "K1")
        self.assertFalse(row["valid"])
        self.assertFalse(row["correct"])

    def test_category_totals(self):
        result = score_objective(KEY, {"K1": "B", "K2": "AD", "K3": "C"})
        cat = result["by_category"]["勘察"]
        self.assertEqual(cat["n"], 2)
        self.assertEqual(cat["correct"], 2)
        self.assertEqual(cat["points"], 3)
        self.assertEqual(cat["max_points"], 3)

    def test_list_answers_are_matched_in_key_order(self):
        result = score_objective(KEY, ["B", "AD", "C"])
        self.assertEqual(result["score"], 4)

    def test_key_without_answers_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺少答案"):
            score_objective({"questions": [{"qid": "K1", "answer": ""}]}, {})


if __name__ == "__main__":
    unittest.main()
