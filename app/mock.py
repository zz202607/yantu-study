"""Full-paper mock: 25 questions, 180 minutes, per-question clock and marks.

Why this exists: the 2025 baseline showed 13 correct-but-overtime answers and
an average of 10.6 minutes per question against a 7.2-minute budget.  The
paper does not punish what you cannot do; it punishes what you keep doing
after the clock says stop.  So the mock records three things per question:

    mark    A (know it, fast) / B (know it, needs work) / C (偏 or hard)
    result  correct / wrong / skipped, entered after the paper
    seconds the wall clock actually spent on it

The review then answers the only three questions that matter:

    Did the A marks hold up?          -> mark accuracy
    Which C questions ate > 8 min?    -> missed skips, with the time cost
    Were the year's "偏" questions     -> recognition rate against the
    recognised before being solved?      publisher's 考点分布 table (2026)

Question rows are created empty at start so the running page never guesses a
sequence; a run can be closed and resumed the same day.
"""

import json
from datetime import datetime
from pathlib import Path

TOTAL_QUESTIONS = 25
PER_QUESTION = 2
HALF_SCORE = TOTAL_QUESTIONS * PER_QUESTION  # 50
PASS_SCORE = 60                             # both halves, 100 total
DEFAULT_MINUTES = 180
BUDGET_SECONDS = 360 * 60 / (TOTAL_QUESTIONS * 2)  # 7.2 min, the real average
HARD_STOP_SECONDS = 8 * 60                  # the per-question stop rule
MIN_YEAR = 2018                             # 25 questions per half from 2018
MAX_YEAR = 2026

MARKS = {"A": "会且快", "B": "会但要算", "C": "偏 / 难"}
RESULTS = {"correct": "对", "wrong": "错", "skipped": "弃"}
HALF_NAMES = {"A": "上午卷", "P": "下午卷"}

ROOT = Path(__file__).resolve().parent.parent
LEVELS_PATH = ROOT / "index" / "exam_levels.json"
PAPER_DIR = ROOT / "print_samples"


def _load_levels():
    """Per-question labels from the 2026 考点分布 table, or None."""
    try:
        payload = json.loads(LEVELS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    years = payload.get("years")
    return years if isinstance(years, dict) else None


LEVELS = _load_levels()


def qid_for(year, half, seq):
    return "A%d-%s-%d" % (int(year), half, int(seq))


def qids_for(year, half):
    return [qid_for(year, half, seq) for seq in range(1, TOTAL_QUESTIONS + 1)]


def _validate_year(year):
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise ValueError("年份必须是整数")
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError("整卷模拟只支持 %d–%d 年" % (MIN_YEAR, MAX_YEAR))
    return year


def _validate_half(half):
    key = str(half or "").strip().upper()
    if key not in HALF_NAMES:
        raise ValueError("整卷只有上午卷 A 或下午卷 P")
    return key


def level_for(year, half, seq):
    """Publisher's label for one question, when that year has one."""
    if not LEVELS:
        return None
    row = (LEVELS.get(str(year)) or {}).get(half, {}).get(str(seq))
    return row if isinstance(row, dict) else None


def start(store, payload, day=None):
    """Create a run plus 25 empty rows; the clock starts at started_at."""
    year = _validate_year(payload.get("year"))
    half = _validate_half(payload.get("half"))
    try:
        minutes = int(payload.get("minutes") or DEFAULT_MINUTES)
    except (TypeError, ValueError):
        raise ValueError("考试时长必须是分钟")
    if not 60 <= minutes <= 360:
        raise ValueError("考试时长应在 60–360 分钟之间")
    day = str(payload.get("day") or day
              or datetime.now().astimezone().strftime("%Y-%m-%d"))
    return store.start_mock_run(year, half, day, qids_for(year, half),
                                minutes=minutes)


def record(store, run_id, payload):
    """One in-paper tap: turn the current question in, or skip it."""
    run = store.mock_run(run_id)
    if run is None:
        raise ValueError("模拟记录不存在")
    try:
        seq = int(payload.get("seq"))
    except (TypeError, ValueError):
        raise ValueError("题号不对")
    action = payload.get("action")
    if action not in ("done", "skip"):
        raise ValueError("动作只能是 done 或 skip")
    fields = {"seconds": payload.get("seconds")}
    mark = payload.get("mark")
    if mark in MARKS:
        fields["mark"] = mark
    if action == "skip":
        fields["result"] = "skipped"
        fields["mark"] = "C"
    return store.update_mock_item(run_id, seq, fields)


def save_results(store, run_id, payload):
    """Post-paper bulk entry: result plus corrections to mark and seconds."""
    run = store.mock_run(run_id)
    if run is None:
        raise ValueError("模拟记录不存在")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("没有要保存的题目")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("题目格式不对")
        try:
            seq = int(item.get("seq"))
        except (TypeError, ValueError):
            raise ValueError("题号不对")
        fields = {}
        if "result" in item:
            fields["result"] = item.get("result")
        if "mark" in item:
            fields["mark"] = item.get("mark")
        if "seconds" in item:
            fields["seconds"] = item.get("seconds")
        run = store.update_mock_item(run_id, seq, fields)
    return run


def _seconds(item):
    value = item.get("seconds")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def review(run):
    """The three answers: A reliability, missed skips, 偏 recognition."""
    items = run.get("items") or []
    answered = [i for i in items if i.get("result") in ("correct", "wrong")]
    correct = [i for i in answered if i["result"] == "correct"]
    skipped = [i for i in items if i.get("result") == "skipped"]
    graded = answered + skipped
    timed = [i for i in items if _seconds(i) is not None]
    total_seconds = sum(_seconds(i) for i in timed)
    avg_seconds = total_seconds / len(timed) if timed else None
    over_budget = [i for i in timed if _seconds(i) > BUDGET_SECONDS + 1]
    over_stop = [i for i in timed if _seconds(i) > HARD_STOP_SECONDS]

    quadrants = {"fast": [], "slow": [], "wrong": [], "skipped": []}
    for item in items:
        seconds = _seconds(item)
        result = item.get("result")
        if result == "correct":
            bucket = "slow" if seconds is not None and seconds > HARD_STOP_SECONDS \
                else "fast"
            quadrants[bucket].append(item["seq"])
        elif result == "wrong":
            quadrants["wrong"].append(item["seq"])
        elif result == "skipped":
            quadrants["skipped"].append(item["seq"])

    marks = {}
    for mark, label in MARKS.items():
        group = [i for i in items if i.get("mark") == mark]
        group_answered = [i for i in group if i.get("result") in ("correct", "wrong")]
        group_correct = [i for i in group_answered if i["result"] == "correct"]
        group_timed = [i for i in group if _seconds(i) is not None]
        marks[mark] = {
            "label": label,
            "count": len(group),
            "answered": len(group_answered),
            "correct": len(group_correct),
            "accuracy": (len(group_correct) / len(group_answered)
                         if group_answered else None),
            "avg_seconds": (sum(_seconds(i) for i in group_timed)
                            / len(group_timed)) if group_timed else None,
        }

    # "该跳没跳": marked C, did not skip it, and still burned the stop budget.
    missed_skips = [
        {"seq": i["seq"], "seconds": _seconds(i), "result": i.get("result")}
        for i in items
        if i.get("mark") == "C" and _seconds(i) is not None
        and _seconds(i) > HARD_STOP_SECONDS and i.get("result") != "skipped"
    ]
    # "误跳": skipped a question you had marked A or B.
    wrong_skips = [
        {"seq": i["seq"], "mark": i.get("mark")}
        for i in items
        if i.get("result") == "skipped" and i.get("mark") in ("A", "B")
    ]

    # Recognise the publisher's 偏 questions: was it marked C or skipped?
    # Questions the paper never reached stay out of the denominator -- an
    # untouched row is a missing entry, not a recognition failure.
    bias = {"total": 0, "recognized": 0, "untouched": 0, "items": []}
    for item in items:
        level = level_for(run["year"], run["half"], item["seq"])
        if not level or level.get("freq") != "偏":
            continue
        if item.get("result") not in RESULTS:
            bias["untouched"] += 1
            continue
        bias["total"] += 1
        recognized = item.get("mark") == "C" or item.get("result") == "skipped"
        if recognized:
            bias["recognized"] += 1
        bias["items"].append({
            "seq": item["seq"],
            "point": level.get("point", ""),
            "level": level.get("level", ""),
            "mark": item.get("mark"),
            "result": item.get("result"),
            "recognized": recognized,
        })
    bias["rate"] = (bias["recognized"] / bias["total"]) if bias["total"] else None

    score = len(correct) * PER_QUESTION
    return {
        "run_id": run["id"],
        "year": run["year"],
        "half": run["half"],
        "half_name": HALF_NAMES.get(run["half"], run["half"]),
        "day": run["day"],
        "status": run["status"],
        "graded": len(graded),
        "answered": len(answered),
        "correct": len(correct),
        "wrong": len(answered) - len(correct),
        "skipped": len(skipped),
        "score": score,
        "half_score": HALF_SCORE,
        "accuracy": (len(correct) / len(answered)) if answered else None,
        "total_seconds": total_seconds,
        "avg_seconds": avg_seconds,
        "budget_seconds": BUDGET_SECONDS,
        "hard_stop_seconds": HARD_STOP_SECONDS,
        "over_budget": [i["seq"] for i in over_budget],
        "over_stop": [i["seq"] for i in over_stop],
        "quadrants": quadrants,
        "marks": marks,
        "missed_skips": missed_skips,
        "wrong_skips": wrong_skips,
        "bias": bias,
        "levels": bool(LEVELS and LEVELS.get(str(run["year"]))),
        "advice": advice(run, score, avg_seconds, marks, missed_skips,
                         wrong_skips, bias, over_budget, len(graded)),
    }


def advice(run, score, avg_seconds, marks, missed_skips, wrong_skips, bias,
           over_budget, graded):
    """At most three lines, each tied to a number from this paper."""
    lines = []
    if graded < TOTAL_QUESTIONS:
        lines.append("还差 %d 题没录结果；补齐后才好判断是速度问题还是原型问题。"
                     % (TOTAL_QUESTIONS - graded))
    if avg_seconds is not None and avg_seconds > HARD_STOP_SECONDS:
        lines.append("平均 %.1f 分钟/题，比 7.2 分钟预算多 %.1f 分钟；"
                     "下一场先按 7 分钟扫一遍，只做标 A 的。"
                     % (avg_seconds / 60, avg_seconds / 60 - 7.2))
    if missed_skips:
        lost = sum(i["seconds"] for i in missed_skips) / 60
        lines.append("有 %d 道标 C 的题投入超过 8 分钟，共 %.0f 分钟；"
                     "这些时间够做 %.1f 道常规题。到点必跳。"
                     % (len(missed_skips), lost, lost / 7.2))
    a = marks.get("A") or {}
    if a.get("accuracy") is not None and a["accuracy"] < 0.8:
        lines.append("标 A 的题做对率 %.0f%%，A 的判断偏乐观："
                     "读题 30 秒说不出规范章节就降到 B。"
                     % (a["accuracy"] * 100))
    if bias.get("rate") is not None and bias["rate"] < 0.6:
        lines.append("今年标「偏」的 %d 题里只识别出 %d 题；"
                     "开卷先扫答题依据，陌生规范名先标 C。"
                     % (bias["total"], bias["recognized"]))
    if not lines:
        if score >= HALF_SCORE * 0.6:
            lines.append("本半 %d/%d，节奏和判断都在线；下一场只盯变量："
                         "把标 C 的题压到 8 分钟内结束。" % (score, HALF_SCORE))
        else:
            lines.append("本半 %d/%d；先补标 A 的错题原型，再谈偏题识别。"
                         % (score, HALF_SCORE))
    return lines[:3]


def overview(store, limit=10):
    """Plan-page payload: recent runs plus the 2026 labels for the UI."""
    runs = store.recent_mock_runs(limit=limit)
    out = []
    for run in runs:
        classified = run
        full = store.mock_run(run["id"])
        if full:
            view = review(full)
            classified = dict(run)
            classified.update({
                "graded": view["graded"],
                "correct": view["correct"],
                "skipped": view["skipped"],
                "score": view["score"],
                "avg_seconds": view["avg_seconds"],
            })
        out.append(classified)
    papers = {}
    for year in range(MIN_YEAR, MAX_YEAR + 1):
        papers[str(year)] = {
            half: (PAPER_DIR / ("exam_%d_%s.pdf" % (year, half))).exists()
            for half in ("A", "P")
        }
    return {
        "runs": out,
        "years": list(range(MAX_YEAR, MIN_YEAR - 1, -1)),
        "papers": papers,
        "levels": LEVELS or {},
        "marks": MARKS,
        "results": RESULTS,
        "budget_seconds": BUDGET_SECONDS,
        "hard_stop_seconds": HARD_STOP_SECONDS,
        "default_minutes": DEFAULT_MINUTES,
        "pass_score": PASS_SCORE,
    }
