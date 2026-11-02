"""Objective-question scoring for knowledge papers (single / multiple choice).

**Boundary:** this module is for the professional *knowledge* exam (Z/K), not
for the professional *case* exam (C/A/M).  Case questions are subjective and
are graded from the written process by a human (`Store.grade` process
statuses).  Never feed a case paper's published option letters here.

Knowledge-paper rules used here:
    - single choice: exactly one letter;
    - multiple choice: exact set match; partial, extra or missing options = 0
      (the exam's stated rule: 错选/多选/少选均不得分).

The key is data-driven; no case or 2025 source-book key is bundled.
"""

import re

LETTER_RE = re.compile(r"[A-Z]")


def normalize_answer(value):
    """'A,B' / ['A','B'] / 'ab' -> 'AB'; blank -> ''."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set, frozenset)):
        chars = []
        for item in value:
            chars.extend(LETTER_RE.findall(str(item).upper()))
    else:
        chars = LETTER_RE.findall(str(value).upper())
    return "".join(sorted(set(chars)))


def _specs(key):
    if isinstance(key, dict) and "questions" in key:
        rows = key["questions"]
    elif isinstance(key, dict):
        rows = [{"qid": qid, **spec} for qid, spec in key.items()]
    elif isinstance(key, list):
        rows = key
    else:
        raise ValueError("答案键必须是 questions 列表或 qid -> 答案对象")
    for spec in rows:
        if not isinstance(spec, dict):
            raise ValueError("每道题的答案键必须是对象")
        qid = spec.get("qid") or spec.get("id")
        if not qid:
            raise ValueError("答案键里有题目缺 qid")
        yield str(qid), spec


def score_objective(key, answers, default_points=1):
    """Score one objective paper; returns per-question rows and totals.

    `answers` is a mapping qid -> letter/letters.  A list is also accepted and
    is matched in key order; use a mapping whenever questions were skipped.
    """
    if isinstance(answers, list):
        specs = list(_specs(key))
        if len(answers) != len(specs):
            raise ValueError("答案 %d 个，题目 %d 道" % (len(answers), len(specs)))
        answers = {qid: answers[index] for index, (qid, _) in enumerate(specs)}
    if answers is None:
        answers = {}
    if not isinstance(answers, dict):
        raise ValueError("答案必须是 qid -> 字母 的对象")
    rows = []
    total = max_total = 0
    answered = correct_count = 0
    by_category = {}
    for qid, spec in _specs(key):
        expected = normalize_answer(spec.get("answer"))
        if not expected:
            raise ValueError("题 %s 缺少答案" % qid)
        kind = str(spec.get("type") or ("single" if len(expected) == 1 else "multiple")).lower()
        if kind not in ("single", "multiple"):
            raise ValueError("题 %s 的题型只能是 single/multiple" % qid)
        try:
            points = int(spec.get("points", default_points))
        except (TypeError, ValueError):
            raise ValueError("题 %s 的分值不是整数" % qid)
        if points < 0:
            raise ValueError("题 %s 的分值不能为负" % qid)
        gave = normalize_answer(answers.get(qid))
        valid = bool(gave)
        if kind == "single" and len(gave) > 1:
            valid = False
        is_correct = valid and gave == expected
        max_total += points
        if gave:
            answered += 1
        if is_correct:
            correct_count += 1
            total += points
        category = spec.get("category") or "未分类"
        bucket = by_category.setdefault(
            category, {"n": 0, "answered": 0, "correct": 0, "points": 0,
                       "max_points": 0}
        )
        bucket["n"] += 1
        bucket["max_points"] += points
        if gave:
            bucket["answered"] += 1
        if is_correct:
            bucket["correct"] += 1
            bucket["points"] += points
        rows.append({
            "qid": qid,
            "type": kind,
            "expected": expected,
            "gave": gave,
            "valid": valid,
            "correct": is_correct,
            "points": points,
            "category": category,
        })
    return {
        "n": len(rows),
        "answered": answered,
        "correct": correct_count,
        "score": total,
        "max_score": max_total,
        "accuracy": round(correct_count / len(rows), 4) if rows else None,
        "by_category": by_category,
        "questions": rows,
    }
