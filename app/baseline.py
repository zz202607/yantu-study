"""2025 case baseline: four process-scored numbers, entered inside the app.

Case questions are subjective.  A right final number without the written
process scores zero, so the paper is graded by hand and the app only records
counts.  The publisher's A/B/C/D letters are never read here (see
`docs/objective-scoring.md` for where option-letter scoring is allowed).

A half is 25 questions x 2 points = 50; both halves make 100, with the usual
60-point pass line.  The four numbers are:

    作答 = correct + wrong        (questions actually attempted)
    做对 = process and result both right
    放弃 = 25 - 作答
    超时 = answered questions over 10 minutes

Per-question states are optional.  Entering all 25 lets the four numbers be
derived and gives an exact nine-category breakdown; entering none keeps the
manual counts and leaves category stats empty.  Partial items never override
the manual counts — they only contribute to category stats that are labelled
with their coverage.

The legacy CLI (`tools/score_baseline.py`) remains for backfill; the database
is the source of truth once a half is saved here.
"""

import re
from collections import Counter
from datetime import date

from app.curriculum import CASE_BY_KEY, CASE_CATEGORIES, MUST_HAVE_KEYS, category_key
from app.store import PROCESS_LABELS

YEAR = 2025
TOTAL_QUESTIONS = 25
PER_QUESTION = 2
PASS_SCORE = 60
HALF_NAMES = {"A": "上午卷", "P": "下午卷"}
RESULT_LABELS = {"correct": "对", "wrong": "错", "skipped": "弃"}
RESULTS = tuple(RESULT_LABELS)

# Baseline triage: a blank question is either "knew it, ran out of time" or
# "did not know it".  The first needs a clock, the second needs a prototype.
TRIAGE_LABELS = {"no_time": "没时间", "no_idea": "不会"}
TRIAGE_KEYS = tuple(TRIAGE_LABELS)

# The 2025 baseline showed the real bottleneck: correct answers were slow.
# The six-week target is 20 answered questions per half at 9 minutes each
# (exactly 180 minutes) and 75% accuracy (15 correct = 30 points per half).
QUADRANT_LABELS = {
    "fast": "会且快",
    "slow": "会但慢",
    "wrong": "做错",
    "skipped": "放弃",
}
QUADRANT_ACTIONS = {
    "fast": "维持：不用重做",
    "slow": "限时：8 分钟重做",
    "wrong": "补原型：入错题本",
    "skipped": "分流：没时间 / 不会",
}
TARGET_PER_HALF = 20
TARGET_MINUTES_PER_QUESTION = 9
TARGET_ACCURACY = 0.75

# Exam tags use the `exam_category` name (勘察), not the curriculum display
# name (岩土工程勘察), so order by the former and fall back for unknowns.
_ORDER = {
    row["exam_category"]: index
    for index, row in enumerate(CASE_CATEGORIES)
    if row.get("exam_category")
}


def validate_counts(answered, correct, skipped=None, overtime=0):
    """Validate the four numbers; return a normalized dict.

    Same contract as the CLI so a report written on paper can be entered in
    either place without changing what counts as a valid record.
    """
    try:
        answered = int(answered)
        correct = int(correct)
        overtime = int(overtime or 0)
        skipped = TOTAL_QUESTIONS - answered if skipped is None else int(skipped)
    except (TypeError, ValueError):
        raise ValueError("四个数都必须是整数")
    if not 0 <= answered <= TOTAL_QUESTIONS:
        raise ValueError("作答题数应在 0–%d 之间" % TOTAL_QUESTIONS)
    if not 0 <= correct <= answered:
        raise ValueError("做对数应在 0–作答题数之间")
    if not 0 <= skipped <= TOTAL_QUESTIONS:
        raise ValueError("放弃题数应在 0–%d 之间" % TOTAL_QUESTIONS)
    if answered + skipped != TOTAL_QUESTIONS:
        raise ValueError(
            "作答题数 %d + 放弃题数 %d 应等于 %d"
            % (answered, skipped, TOTAL_QUESTIONS)
        )
    if not 0 <= overtime <= answered:
        raise ValueError("超时题数应在 0–作答题数之间")
    return {
        "answered": answered,
        "correct": correct,
        "skipped": skipped,
        "overtime": overtime,
        "score": correct * PER_QUESTION,
        "max_score": TOTAL_QUESTIONS * PER_QUESTION,
    }


def paper_questions(tags, year=YEAR):
    """Return `{"A": [...], "P": [...]}` ordered by question number.

    Only question number and exam category are exposed.  The source tags also
    carry option letters; they stay out of this module on purpose.
    """
    pattern = re.compile(r"^A%d-([AP])-(\d{1,3})$" % int(year))
    out = {"A": [], "P": []}
    for qid, info in (tags or {}).items():
        match = pattern.match(str(qid))
        if not match:
            continue
        half, number = match.group(1), int(match.group(2))
        if not 1 <= number <= TOTAL_QUESTIONS:
            continue
        out[half].append({
            "qid": qid,
            "seq": number,
            "category": (info or {}).get("category") or "",
            "category_key": category_key(qid, tags),
        })
    for rows in out.values():
        rows.sort(key=lambda row: row["seq"])
    return out


def _category_order(name):
    return (_ORDER.get(name, len(_ORDER)), name)


def _by_category(items, questions):
    """Category stats over recorded items, with the exam's full denominator."""
    totals = {}
    for question in questions:
        bucket = totals.setdefault(
            question["category"],
            {"category": question["category"], "n": 0, "answered": 0,
             "correct": 0, "recorded": 0},
        )
        bucket["n"] += 1
    by_qid = {question["qid"]: question for question in questions}
    for item in items:
        question = by_qid.get(item["qid"])
        if not question:
            continue
        bucket = totals[question["category"]]
        bucket["recorded"] += 1
        if item["result"] == "skipped":
            continue
        bucket["answered"] += 1
        if item["result"] == "correct":
            bucket["correct"] += 1
    rows = []
    for bucket in totals.values():
        bucket["accuracy"] = (
            round(bucket["correct"] / bucket["answered"], 4)
            if bucket["answered"] else None
        )
        rows.append(bucket)
    rows.sort(key=lambda row: (_category_order(row["category"]), row["category"]))
    return rows


def _merge_category(merged, rows):
    for row in rows:
        bucket = merged.setdefault(
            row["category"],
            {"category": row["category"], "n": 0, "answered": 0,
             "correct": 0, "recorded": 0},
        )
        bucket["n"] += row["n"]
        bucket["answered"] += row["answered"]
        bucket["correct"] += row["correct"]
        bucket["recorded"] += row["recorded"]


def _finish_category(merged):
    for bucket in merged.values():
        bucket["accuracy"] = (
            round(bucket["correct"] / bucket["answered"], 4)
            if bucket["answered"] else None
        )
    return {
        name: bucket
        for name, bucket in sorted(
            merged.items(), key=lambda pair: _category_order(pair[0])
        )
    }


def _process_tally(items):
    return dict(Counter(
        item["process_status"] for item in items if item.get("process_status")
    ))


def quadrant_of(result, overtime):
    if result == "skipped":
        return "skipped"
    if result == "wrong":
        return "wrong"
    return "slow" if overtime else "fast"


def _quadrant_rows(items, questions, triage, half):
    """Per-question rows with the quadrant and the skip triage attached."""
    by_qid = {question["qid"]: question for question in questions}
    rows = []
    for item in items:
        question = by_qid.get(item["qid"])
        if not question:
            continue
        quadrant = quadrant_of(item["result"], item["overtime"])
        rows.append({
            "qid": item["qid"],
            "half": half,
            "seq": item["seq"],
            "category": item["category"],
            "category_key": question["category_key"],
            "result": item["result"],
            "overtime": bool(item["overtime"]),
            "quadrant": quadrant,
            "triage": (triage or {}).get(item["qid"]) if quadrant == "skipped" else None,
        })
    return rows


def _quadrant_counts(rows):
    counts = {key: 0 for key in QUADRANT_LABELS}
    for row in rows:
        counts[row["quadrant"]] += 1
    return counts


def _must_have(items, questions):
    """Accuracy inside the four must-have categories for one half."""
    rows = {}
    for question in questions:
        key = question["category_key"]
        if key not in MUST_HAVE_KEYS:
            continue
        bucket = rows.setdefault(key, {
            "key": key,
            "category": CASE_BY_KEY[key]["name"],
            "n": 0, "answered": 0, "correct": 0, "recorded": 0,
        })
        bucket["n"] += 1
    by_qid = {question["qid"]: question for question in questions}
    for item in items:
        question = by_qid.get(item["qid"])
        key = question["category_key"] if question else None
        if key not in rows:
            continue
        bucket = rows[key]
        bucket["recorded"] += 1
        if item["result"] != "skipped":
            bucket["answered"] += 1
            if item["result"] == "correct":
                bucket["correct"] += 1
    return [rows[key] for key in MUST_HAVE_KEYS if key in rows]


def _merge_must(merged, rows):
    for row in rows:
        bucket = merged.setdefault(row["key"], {
            "key": row["key"],
            "category": row["category"],
            "n": 0, "answered": 0, "correct": 0, "recorded": 0,
        })
        for field in ("n", "answered", "correct", "recorded"):
            bucket[field] += row[field]


def _finish_must(merged):
    rows = []
    for key in MUST_HAVE_KEYS:
        bucket = merged.get(key)
        if not bucket:
            continue
        bucket["accuracy"] = (
            round(bucket["correct"] / bucket["answered"], 4)
            if bucket["answered"] else None
        )
        bucket["pending"] = bucket["n"] - bucket["recorded"]
        rows.append(bucket)
    attempted = sum(row["answered"] for row in rows)
    correct = sum(row["correct"] for row in rows)
    return {
        "available": any(row["recorded"] for row in rows),
        "attempted": attempted,
        "correct": correct,
        "accuracy": round(correct / attempted, 4) if attempted else None,
        "recorded": sum(row["recorded"] for row in rows),
        "pending": sum(row["pending"] for row in rows),
        "rows": rows,
    }


def simulation(recorded_halves, minutes, answered, correct):
    """Turn the baseline into a plain exam-day estimate.

    The estimate is deliberately arithmetic, not motivational: with the
    observed pace, how many questions fit in 180 minutes, and what has to
    change to reach 20 questions x 9 minutes x 75%.
    """
    if not recorded_halves or not answered:
        return {
            "available": False,
            "target_per_half": TARGET_PER_HALF,
            "target_minutes_per_question": TARGET_MINUTES_PER_QUESTION,
            "target_accuracy": TARGET_ACCURACY,
        }
    minutes = int(minutes or 0)
    raw_per_question = minutes / answered if minutes else None
    per_question = round(raw_per_question, 2) if raw_per_question else None
    per_half_minutes = round(minutes / recorded_halves, 1) if minutes else 0
    capacity = (
        int(per_half_minutes // raw_per_question) if raw_per_question else 0
    )
    target_correct = round(TARGET_ACCURACY * TARGET_PER_HALF)  # 15
    target_correct_total = target_correct * recorded_halves
    target_score = target_correct * PER_QUESTION * recorded_halves
    score = correct * PER_QUESTION
    if per_question is None:
        headline = "基线缺用时，无法推演速度；先补上每半用时。"
    else:
        headline = (
            "按 %.1f 分钟/题，每半 3 小时只能做 %d 题；目标 %d 题 × %d 分钟"
            "刚好用满 180 分钟。"
            % (per_question, capacity, TARGET_PER_HALF,
               TARGET_MINUTES_PER_QUESTION)
        )
    return {
        "available": True,
        "recorded_halves": recorded_halves,
        "minutes": minutes,
        "answered": answered,
        "correct": correct,
        "score": score,
        "per_question_minutes": per_question,
        "per_half_minutes": per_half_minutes,
        "capacity_per_half": capacity,
        "target_per_half": TARGET_PER_HALF,
        "target_minutes_per_question": TARGET_MINUTES_PER_QUESTION,
        "target_accuracy": TARGET_ACCURACY,
        "target_correct_per_half": target_correct,
        "target_correct_total": target_correct_total,
        "target_score": target_score,
        "gaps": {
            "questions_per_half": TARGET_PER_HALF - capacity,
            "minutes_per_question": (
                round(per_question - TARGET_MINUTES_PER_QUESTION, 2)
                if per_question is not None else None
            ),
            "correct": max(0, target_correct_total - correct),
            "score": max(0, target_score - score),
        },
        "headline": headline,
    }


def summary(store, tags, year=YEAR):
    """Assemble the baseline view from the database.

    The shape is a superset of `analytics.read_baseline`, so the dashboard,
    stats screen and curriculum rows all consume the same numbers whether
    they came from this app or the legacy markdown file.
    """
    paper = paper_questions(tags, year)
    runs = {row["half"]: row for row in store.baseline_runs(year)}
    triage_map = store.baseline_triage(year)
    halves = []
    detail = {}
    merged_categories = {}
    merged_must = {}
    quadrant_rows = []
    total_minutes = 0
    process = Counter()
    for half in ("A", "P"):
        run = runs.get(half)
        questions = paper[half]
        base = {
            "half": half,
            "label": HALF_NAMES[half],
            "total_questions": TOTAL_QUESTIONS,
            "max_score": TOTAL_QUESTIONS * PER_QUESTION,
        }
        if not run:
            halves.append({
                **base,
                "day": None,
                "recorded": False,
                "answered": 0,
                "correct": 0,
                "skipped": 0,
                "overtime": 0,
                "score": None,
                "accuracy": None,
                "items_done": 0,
                "note": "",
                "photo": None,
                "by_category": [],
                "process": {},
                "quadrants": _quadrant_counts([]),
            })
            detail[half] = {**halves[-1], "items": [], "questions": questions}
            continue
        items = store.baseline_items(run["id"])
        rows = _by_category(items, questions)
        _merge_category(merged_categories, rows)
        half_quadrant_rows = _quadrant_rows(
            items, questions, triage_map.get(half, {}), half
        )
        quadrant_rows.extend(half_quadrant_rows)
        _merge_must(merged_must, _must_have(items, questions))
        tally = _process_tally(items)
        process.update(tally)
        total_minutes += int(run["minutes"] or 0)
        accuracy = (
            round(run["correct"] / run["answered"], 4)
            if run["answered"] else None
        )
        entry = {
            **base,
            "day": run["day"],
            "recorded": True,
            "answered": run["answered"],
            "correct": run["correct"],
            "skipped": run["skipped"],
            "overtime": run["overtime"],
            "score": run["correct"] * PER_QUESTION,
            "accuracy": accuracy,
            "items_done": run["items_done"],
            "note": run["note"],
            "photo": run["photo"],
            "minutes": run["minutes"],
            "updated_at": run["updated_at"],
            "by_category": rows,
            "process": tally,
            "quadrants": _quadrant_counts(half_quadrant_rows),
            "items": items,
            "questions": questions,
        }
        halves.append({
            key: entry[key]
            for key in (
                "half", "label", "day", "total_questions", "max_score",
                "recorded", "answered", "correct", "skipped", "overtime",
                "score", "accuracy", "items_done", "note", "photo",
                "by_category", "process", "quadrants",
            )
        })
        detail[half] = entry

    recorded = [row for row in halves if row["recorded"]]
    answered = sum(row["answered"] for row in recorded)
    correct = sum(row["correct"] for row in recorded)
    quadrants = {
        "available": bool(quadrant_rows),
        "labels": QUADRANT_LABELS,
        "actions": QUADRANT_ACTIONS,
        "counts": _quadrant_counts(quadrant_rows),
        "coverage": {
            "done": len(quadrant_rows),
            "total": TOTAL_QUESTIONS * len(recorded),
        },
        "items": quadrant_rows,
    }
    return {
        "available": bool(recorded),
        "source": "app",
        "year": int(year),
        "complete": len(recorded) == 2,
        "pass_line": PASS_SCORE,
        "passed": len(recorded) == 2
        and sum(row["score"] for row in recorded) >= PASS_SCORE,
        "score": sum(row["score"] for row in recorded),
        "max_score": 50 * len(recorded),
        "answered": answered,
        "correct": correct,
        "skipped": sum(row["skipped"] for row in recorded),
        "overtime": sum(row["overtime"] for row in recorded),
        "accuracy": round(correct / answered, 4) if answered else None,
        "items_done": sum(row["items_done"] for row in recorded),
        "total_questions": 50,
        "by_category": _finish_category(merged_categories),
        "process": dict(process),
        "process_scored": True,
        "score_rule": "每题 %d 分；做对 = 过程与结果均正确" % PER_QUESTION,
        "halves": halves,
        "half_detail": detail,
        "quadrants": quadrants,
        "must": _finish_must(merged_must),
        "simulation": simulation(len(recorded), total_minutes, answered, correct),
    }


def _normalize_items(raw_items, questions):
    """Validate per-question states; returns ordered store-ready dicts."""
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise ValueError("逐题状态必须是数组")
    if len(raw_items) > TOTAL_QUESTIONS:
        raise ValueError("半卷最多 %d 题" % TOTAL_QUESTIONS)
    by_qid = {row["qid"]: row for row in questions}
    items, seen = [], set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("逐题状态格式不对")
        qid = str(raw.get("qid") or "").strip().upper()
        question = by_qid.get(qid)
        if not question:
            raise ValueError("不是 2025 这一半的题：%s" % (qid or "空题号"))
        if qid in seen:
            raise ValueError("题号重复：%s" % qid)
        seen.add(qid)
        result = str(raw.get("result") or "").strip().lower()
        if result not in RESULTS:
            raise ValueError("逐题状态不合法：%s" % (result or "空"))
        process_status = raw.get("process_status") or None
        if process_status is not None and process_status not in PROCESS_LABELS:
            raise ValueError("过程状态不合法：%s" % process_status)
        overtime = 1 if raw.get("overtime") else 0
        if result == "correct":
            # Case scoring: correct means the process was right too.
            process_status = "process_right_result_right"
        elif result == "skipped":
            process_status = None
            overtime = 0
        elif process_status == "process_right_result_right":
            raise ValueError("做错的题不能选「过程对·结果对」")
        items.append({
            "qid": qid,
            "seq": question["seq"],
            "category": question["category"],
            "result": result,
            "overtime": overtime,
            "process_status": process_status,
        })
    items.sort(key=lambda row: row["seq"])
    return items


def save(store, half, payload, tags, year=YEAR):
    """Validate and persist one half; returns the refreshed summary."""
    half = str(half or "").strip().upper()
    if half not in HALF_NAMES:
        raise ValueError("基线只有上午卷 A 或下午卷 P")
    paper = paper_questions(tags, year)
    questions = paper[half]
    if len(questions) != TOTAL_QUESTIONS:
        raise ValueError("2025 %s卷题目标签不完整（%d/%d）"
                         % (HALF_NAMES[half], len(questions), TOTAL_QUESTIONS))
    items = _normalize_items(payload.get("items"), questions)

    if len(items) == TOTAL_QUESTIONS:
        counts = {
            "answered": sum(1 for row in items if row["result"] != "skipped"),
            "correct": sum(1 for row in items if row["result"] == "correct"),
            "skipped": sum(1 for row in items if row["result"] == "skipped"),
            "overtime": sum(row["overtime"] for row in items),
        }
    else:
        counts = validate_counts(
            payload.get("answered"),
            payload.get("correct"),
            payload.get("skipped"),
            payload.get("overtime") or 0,
        )
        correct_items = sum(1 for row in items if row["result"] == "correct")
        wrong_items = sum(1 for row in items if row["result"] == "wrong")
        skipped_items = sum(1 for row in items if row["result"] == "skipped")
        overtime_items = sum(row["overtime"] for row in items)
        if correct_items > counts["correct"]:
            raise ValueError("逐题「对」%d 题，超过四个数的做对数 %d"
                             % (correct_items, counts["correct"]))
        if wrong_items > counts["answered"] - counts["correct"]:
            raise ValueError("逐题「错」%d 题，超过四个数的错题数 %d"
                             % (wrong_items,
                                counts["answered"] - counts["correct"]))
        if skipped_items > counts["skipped"]:
            raise ValueError("逐题「弃」%d 题，超过四个数的放弃数 %d"
                             % (skipped_items, counts["skipped"]))
        if overtime_items > counts["overtime"]:
            raise ValueError("逐题「超时」%d 题，超过四个数的超时数 %d"
                             % (overtime_items, counts["overtime"]))

    day = str(payload.get("day") or date.today().isoformat())[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("日期应为 YYYY-MM-DD")
    raw_minutes = payload.get("minutes")
    try:
        minutes = 180 if raw_minutes in (None, "") else int(raw_minutes)
    except (TypeError, ValueError):
        raise ValueError("用时必须是整数")
    if not 0 <= minutes <= 600:
        raise ValueError("用时应在 0–600 分钟之间")
    note = str(payload.get("note") or "").strip()[:2000]
    photo_sha = payload.get("photo_sha")
    photo_sha = str(photo_sha).strip() if photo_sha else None

    store.save_baseline(
        half=half,
        year=year,
        day=day,
        minutes=minutes,
        answered=counts["answered"],
        correct=counts["correct"],
        skipped=counts["skipped"],
        overtime=counts["overtime"],
        items=items,
        note=note,
        photo_sha=photo_sha,
    )
    return summary(store, tags, year)


def set_triage(store, half, qid, triage, year=YEAR):
    """Label one abandoned baseline question as no-time or no-idea."""
    half = str(half or "").strip().upper()
    if half not in HALF_NAMES:
        raise ValueError("基线只有上午卷 A 或下午卷 P")
    qid = str(qid or "").strip().upper()
    runs = {row["half"]: row for row in store.baseline_runs(year)}
    run = runs.get(half)
    if not run:
        raise ValueError("这一半还没有基线记录")
    item = next(
        (row for row in store.baseline_items(run["id"]) if row["qid"] == qid),
        None,
    )
    if not item:
        raise ValueError("这题还没有逐题录入：%s" % (qid or "空题号"))
    if item["result"] != "skipped":
        raise ValueError("只有放弃题需要分流")
    value = str(triage or "").strip().lower()
    if value and value not in TRIAGE_KEYS:
        raise ValueError("分流只能是「没时间」或「不会」")
    store.set_baseline_triage(year, half, qid, value or None)
    return {"half": half, "qid": qid, "triage": value or None}


def qids_for_import(store, half, kind="wrong", triage=None, year=YEAR):
    """Question ids ready to re-enter a list, in paper order.

    `kind` is one of:
      wrong     - answered and scored wrong
      overtime  - answered correctly but over the ten-minute mark
      skipped   - left blank; `triage` can narrow to no_time or no_idea
    """
    half = str(half or "").strip().upper()
    if half not in HALF_NAMES:
        raise ValueError("基线只有上午卷 A 或下午卷 P")
    kind = str(kind or "wrong").strip().lower()
    if kind not in ("wrong", "overtime", "skipped"):
        raise ValueError("导入类型只能是 wrong / overtime / skipped")
    triage = str(triage or "").strip().lower() or None
    if triage and triage not in TRIAGE_KEYS:
        raise ValueError("分流只能是「没时间」或「不会」")
    runs = {row["half"]: row for row in store.baseline_runs(year)}
    run = runs.get(half)
    if not run:
        return []
    triage_map = store.baseline_triage(year).get(half, {})
    qids = []
    for item in store.baseline_items(run["id"]):
        if kind == "wrong":
            keep = item["result"] == "wrong"
        elif kind == "overtime":
            keep = item["result"] == "correct" and bool(item["overtime"])
        else:
            keep = item["result"] == "skipped"
            if keep and triage:
                keep = triage_map.get(item["qid"]) == triage
        if keep:
            qids.append(item["qid"])
    return qids


def wrong_qids(store, half, year=YEAR):
    """Questions marked wrong in one recorded half, in paper order."""
    return qids_for_import(store, half, "wrong", year=year)
