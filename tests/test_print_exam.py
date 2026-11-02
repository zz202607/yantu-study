"""Past-paper printing: qids come from the index, in order, without phantoms."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import print_exam  # noqa: E402


def lookup(numbers, year=2024, half="A", extra=None):
    exam = {"A%d-%s-%d" % (year, half, n): {} for n in numbers}
    exam.update(extra or {})
    return {"exam": exam}


def test_2024_half_is_ordered_and_ignores_other_halves():
    rows = lookup(range(1, 26), extra={"A2024-P-1": {}, "A2023-A-1": {}})
    qids = print_exam.half_qids(rows, 2024, "A")
    assert qids[0] == "A2024-A-1"
    assert qids[-1] == "A2024-A-25"
    assert len(qids) == 25


def test_phantom_zero_is_dropped_but_does_not_break_order():
    rows = lookup(range(1, 26))
    rows["exam"]["A2024-A-0"] = {}
    assert print_exam.half_qids(rows, 2024, "A")[0] == "A2024-A-1"


def test_missing_question_is_rejected():
    numbers = [n for n in range(1, 26) if n != 7]
    with pytest.raises(SystemExit, match="不连续"):
        print_exam.half_qids(lookup(numbers), 2024, "A")


def test_wrong_count_is_rejected():
    with pytest.raises(SystemExit, match="应有 25 题"):
        print_exam.half_qids(lookup(range(1, 24)), 2024, "A")
