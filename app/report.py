"""Weekly and monthly one-page study reports.

A report is a projection of what already happened: the practice ledger (four
numbers per sitting), the review log, the wrong book, the 2025 baseline and
the plan.  It never estimates activity that was not recorded — an empty week
says so, and the next actions say what to do about it.

Periods are calendar based: weeks run Monday..Sunday (ISO), months are
calendar months.  `key` is stable (`week-2026-W45`, `month-2026-11`), so
saving the same period twice replaces the same two files (`.md` and `.json`).
The Markdown file is the frozen page; the JSON keeps the numbers that made it.
"""

import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from app import analytics, plan
from app.curriculum import CASE_CATEGORIES, category_key
from app.store import PRACTICE_SOURCES, STUCK_TIMES, now_iso

PERIODS = ("week", "month")
PERIOD_NAMES = {"week": "周报", "month": "月报"}
STATUS_LABELS = {
    "ahead": "超额",
    "on_track": "按计划",
    "behind": "落后",
    "baseline": "基线周",
    "upcoming": "未开始",
}
_WEEKDAY_CN = "一二三四五六日"
_KEY_RE = re.compile(r"^(week-\d{4}-W\d{2}|month-\d{4}-\d{2})$")
_EXTRA_NAMES = {"knowledge": "知识题", "mixed": "综合/基线", "unmapped": "未归类"}


# ---------------------------------------------------------------- calendar

def _parse_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _day_of(value):
    """ISO timestamp -> local calendar day (China has no DST)."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.date()


def _today(now=None):
    return _parse_day(now) if now is not None else date.today()


def period_info(period="week", anchor=None, *, today=None):
    """Resolve one calendar period and its neighbours.

    `anchor` can be any day inside the period (empty means "now").  The
    previous/next anchors are dates that select the neighbouring period when
    passed back in.
    """
    period = str(period or "week").lower()
    if period not in PERIODS:
        raise ValueError("周期只能是 week 或 month")
    today_day = _today(today)
    try:
        day = _parse_day(anchor) if anchor else today_day
    except ValueError:
        raise ValueError("日期应为 YYYY-MM-DD")
    if period == "week":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
        iso = start.isocalendar()
        key = "week-%04d-W%02d" % (iso[0], iso[1])
        title = "%d 年第 %d 周 · 学习周报" % (iso[0], iso[1])
        unit = "周"
    else:
        start = day.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        key = "month-%04d-%02d" % (start.year, start.month)
        title = "%d 年 %d 月 · 学习月报" % (start.year, start.month)
        unit = "月"
    days = (end - start).days + 1
    return {
        "period": period,
        "key": key,
        "title": title,
        "unit": unit,
        "label": "%s – %s" % (start.isoformat(), end.isoformat()),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "prev_anchor": (start - timedelta(days=1)).isoformat(),
        "next_anchor": (end + timedelta(days=1)).isoformat(),
        "current": start <= today_day <= end,
        "started": today_day >= start,
        "finished": today_day > end,
    }


# ---------------------------------------------------------------- aggregates

def _rate(correct, total):
    return round(correct / total, 4) if total else None


def _pct(value):
    return "—" if value is None else "%d%%" % round(value * 100)


def _in_range(day, info):
    return bool(day) and info["start"] <= day.isoformat() <= info["end"]


def _sessions_in(store, info):
    """Practice ledger rows inside one period (max 200, plenty per period)."""
    rows = store.list_practice_sessions(since=info["start"], limit=200)
    return [row for row in rows if info["start"] <= str(row["day"])[:10] <= info["end"]]


def _session_totals(rows):
    total = {
        "sessions": len(rows),
        "attempted": sum(row["attempted"] for row in rows),
        "correct": sum(row["correct"] for row in rows),
        "abandoned": sum(row["abandoned"] for row in rows),
        "overtime": sum(row["overtime"] for row in rows),
        "minutes": sum(row["minutes"] for row in rows),
    }
    total["accuracy"] = _rate(total["correct"], total["attempted"])
    total["avg_minutes"] = (
        round(total["minutes"] / total["attempted"], 1)
        if total["attempted"] else None
    )
    return total


def _by_source(rows):
    buckets = {}
    for row in rows:
        source = row.get("source") or "other"
        bucket = buckets.setdefault(
            source,
            {"key": source, "label": PRACTICE_SOURCES.get(source, source),
             "attempted": 0, "correct": 0},
        )
        bucket["attempted"] += row["attempted"]
        bucket["correct"] += row["correct"]
    return sorted(buckets.values(), key=lambda row: -row["attempted"])


def _logs_in(logs, info):
    return [log for log in logs if _in_range(_day_of(log.get("at")), info)]


def _review_totals(rows):
    correct = sum(1 for log in rows if log["result"] == "correct")
    wrong = sum(1 for log in rows if log["result"] == "wrong")
    recovered = sum(
        1 for log in rows
        if log["result"] == "correct" and log.get("last_result_before") == "wrong"
    )
    mastered = sum(
        1 for log in rows
        if log.get("status_after") == "mastered"
        and log.get("status_before") != "mastered"
    )
    days = {_day_of(log.get("at")) for log in rows}
    return {
        "reviews": len(rows),
        "correct": correct,
        "wrong": wrong,
        "accuracy": _rate(correct, correct + wrong),
        "recovered": recovered,
        "mastered": mastered,
        "active_days": len([day for day in days if day]),
    }


def _attempts_in(attempts, info):
    return [attempt for attempt in attempts
            if _in_range(_day_of(attempt.get("created_at")), info)]


def _activity(info, logs, attempts):
    """One bucket per day: reviews, wrong reviews and newly recorded errors."""
    buckets = {}
    cursor = date.fromisoformat(info["start"])
    end = date.fromisoformat(info["end"])
    while cursor <= end:
        buckets[cursor.isoformat()] = {
            "date": cursor.isoformat(),
            "label": cursor.isoformat()[5:],
            "weekday": _WEEKDAY_CN[cursor.weekday()],
            "reviews": 0,
            "wrong": 0,
            "new_errors": 0,
        }
        cursor += timedelta(days=1)
    for log in logs:
        day = _day_of(log.get("at"))
        key = day.isoformat() if day else None
        if key in buckets:
            buckets[key]["reviews"] += 1
            if log["result"] == "wrong":
                buckets[key]["wrong"] += 1
    for attempt in attempts:
        day = _day_of(attempt.get("created_at"))
        key = day.isoformat() if day else None
        if key in buckets:
            buckets[key]["new_errors"] += 1
    return list(buckets.values())


def _bucket_activity(rows, size=7):
    """Group monthly daily rows into ~weekly chunks for the Markdown page."""
    chunks = []
    for offset in range(0, len(rows), size):
        group = rows[offset:offset + size]
        if not group:
            continue
        chunks.append({
            "label": "%s–%s" % (group[0]["label"], group[-1]["label"]),
            "reviews": sum(row["reviews"] for row in group),
            "wrong": sum(row["wrong"] for row in group),
            "new_errors": sum(row["new_errors"] for row in group),
        })
    return chunks


# ---------------------------------------------------------------- categories

def _category_shell():
    rows = {}
    for row in CASE_CATEGORIES:
        rows[row["key"]] = {
            "key": row["key"],
            "name": row["name"],
            "short": row["short"],
            "weight": row["weight"],
            "exam_category": row["exam_category"],
            "attempted": 0,
            "correct": 0,
            "minutes": 0,
            "reviews": 0,
            "review_correct": 0,
            "new_errors": 0,
            "baseline_answered": 0,
            "baseline_correct": 0,
            "baseline_accuracy": None,
        }
    return rows


def _extra_row(key):
    return {
        "key": key,
        "name": _EXTRA_NAMES[key],
        "short": _EXTRA_NAMES[key],
        "weight": None,
        "exam_category": None,
        "attempted": 0,
        "correct": 0,
        "minutes": 0,
        "reviews": 0,
        "review_correct": 0,
        "new_errors": 0,
        "baseline_answered": 0,
        "baseline_correct": 0,
        "baseline_accuracy": None,
    }


def _bucket_key(rows, key):
    """Map a ledger/log category or qid to a category row key."""
    key = str(key or "")
    if key in rows:
        return key
    lower = key.lower()
    if lower in ("knowledge", "mixed"):
        return lower
    return "unmapped"


def _baseline_categories(baseline):
    raw = (baseline or {}).get("by_category") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {
            row.get("category") or row.get("name"): row
            for row in raw if isinstance(row, dict)
        }
    return {}


def _category_rows(sessions, logs, new_errors, exam_tags, baseline):
    rows = _category_shell()
    extras = ("knowledge", "mixed", "unmapped")

    def bucket(raw_key):
        key = _bucket_key(rows, raw_key)
        if key not in rows:
            rows[key] = _extra_row(key)
        return rows[key]

    for session in sessions:
        row = bucket(session.get("category"))
        row["attempted"] += session["attempted"]
        row["correct"] += session["correct"]
        row["minutes"] += session["minutes"]
    for log in logs:
        row = bucket(category_key(log.get("qid"), exam_tags) or "unmapped")
        row["reviews"] += 1
        if log["result"] == "correct":
            row["review_correct"] += 1
    for attempt in new_errors:
        row = bucket(category_key(attempt.get("qid"), exam_tags) or "unmapped")
        row["new_errors"] += 1

    name_to_key = {
        row["exam_category"]: row["key"] for row in CASE_CATEGORIES
        if row["exam_category"]
    }
    for name, values in _baseline_categories(baseline).items():
        key = name_to_key.get(name)
        if not key or key not in rows:
            continue
        rows[key]["baseline_answered"] = values.get("answered") or 0
        rows[key]["baseline_correct"] = values.get("correct") or 0
        rows[key]["baseline_accuracy"] = values.get("accuracy")

    out = []
    for key, row in rows.items():
        row["accuracy"] = _rate(row["correct"], row["attempted"])
        row["review_accuracy"] = _rate(row["review_correct"], row["reviews"])
        if key in extras and not (
            row["attempted"] or row["reviews"] or row["new_errors"]
        ):
            continue
        out.append(row)
    return out


# ---------------------------------------------------------------- baseline

def _baseline_compact(baseline, settings, info):
    data = baseline or {}
    halves = []
    for row in data.get("halves") or []:
        day = row.get("day")
        halves.append({
            "half": row.get("half"),
            "label": row.get("label"),
            "recorded": bool(row.get("recorded")),
            "day": day,
            "answered": row.get("answered") or 0,
            "correct": row.get("correct") or 0,
            "skipped": row.get("skipped") or 0,
            "overtime": row.get("overtime") or 0,
            "score": row.get("score"),
            "max_score": row.get("max_score"),
            "accuracy": row.get("accuracy"),
            "recorded_in_period": bool(day and info["start"] <= str(day)[:10] <= info["end"]),
        })
    return {
        "available": bool(data.get("available")),
        "complete": bool(data.get("complete")),
        "score": data.get("score"),
        "max_score": data.get("max_score"),
        "pass_line": data.get("pass_line", 60),
        "passed": data.get("passed"),
        "answered": data.get("answered"),
        "correct": data.get("correct"),
        "accuracy": data.get("accuracy"),
        "due": settings.get("baseline_due"),
        "halves": halves,
        "recorded_in_period": any(row["recorded_in_period"] for row in halves),
    }


# ---------------------------------------------------------------- coaching

def _planned_questions(settings, info, weekly_hours):
    """Daily-prorated weekly targets; handles a month spanning two phases."""
    total = 0.0
    cursor = date.fromisoformat(info["start"])
    end = date.fromisoformat(info["end"])
    while cursor <= end:
        phase = plan.phase_on(settings, cursor)
        total += plan.weekly_target(phase, weekly_hours) / 7.0
        cursor += timedelta(days=1)
    return int(round(total))


def _encouragement(practice, review, book, previous, info):
    label = "本周" if info["period"] == "week" else "本月"
    if not info["started"]:
        return {
            "text": "这一%s还没开始；先把今天该做的复习或基线走完。" % info["unit"],
            "evidence": "依据：本期从 %s 开始；错题本共 %d 道。" % (
                info["start"], book["total"]),
        }
    if not practice["attempted"] and not review["reviews"]:
        return {
            "text": "%s还没有练习或复习记录；一次 20 分钟的复习也算开张。" % label,
            "evidence": "依据：练习 0 题、复习 0 次；错题本共 %d 道。" % book["total"],
        }
    parts = []
    if practice["attempted"]:
        parts.append("练习 %d 题（正确率 %s）" % (
            practice["attempted"], _pct(practice["accuracy"])))
    if review["reviews"]:
        parts.append("复习 %d 次（正确率 %s）" % (
            review["reviews"], _pct(review["accuracy"])))
    if review["mastered"]:
        parts.append("新掌握 %d 道" % review["mastered"])
    text = "%s完成：%s。" % (label, "，".join(parts))
    delta = previous["delta"].get("accuracy")
    if delta is not None and delta >= 5:
        text += "正确率比上期高 %.0f 个百分点。" % delta
    evidence = "依据：练习 %d 题、做对 %d；复习 %d 次、做对 %d；新增错题 %d 道。" % (
        practice["attempted"], practice["correct"], review["reviews"],
        review["correct"], book["new_errors"],
    )
    return {"text": text, "evidence": evidence}


def _baseline_action(settings, baseline, today):
    if (baseline or {}).get("complete"):
        return None
    try:
        due = date.fromisoformat(settings["baseline_due"])
        gap = (due - today).days
        when = "计划 %s" % settings["baseline_due"]
        when += " · 还剩 %d 天" % gap if gap >= 0 else " · 已过 %d 天" % -gap
    except (KeyError, TypeError, ValueError):
        when = "计划日期未设定"
    done = [row.get("half") for row in (baseline or {}).get("halves") or []
            if row.get("recorded")]
    if len(done) == 1:
        title = "交 2025 基线%s" % ("下午卷" if done[0] == "A" else "上午卷")
    else:
        title = "交 2025 基线卷"
    return {
        "kind": "baseline",
        "title": title,
        "detail": when + "；两半各限时 3 小时，按过程自评四个数（不按选项判案例）。",
        "route": "baseline",
    }


def _next_actions(practice, review, book, categories, baseline, settings, due,
                  info, today):
    actions = []
    base = _baseline_action(settings, baseline, today)
    if base:
        actions.append(base)
    if due:
        actions.append({
            "kind": "review",
            "title": "今日复习 %d 题" % due,
            "detail": "预计 %d 分钟；按到期 → 新错 → 顽固排好，照着做就行。" % max(1, due * 2),
            "route": "review",
        })
    weak = []
    for row in categories:
        total = row["attempted"] + row["reviews"]
        correct = row["correct"] + row["review_correct"]
        if row["weight"] and total >= 3:
            accuracy = correct / total
            if accuracy < 0.6:
                weak.append((row["weight"] * total, accuracy, row))
    if weak:
        _, accuracy, row = max(weak, key=lambda item: item[0])
        actions.append({
            "kind": "weak",
            "title": "弱项：%s" % row["short"],
            "detail": "练习+复习 %d 次，正确率 %s；先回规范地图，再做 3 题同类题。" % (
                row["attempted"] + row["reviews"], _pct(accuracy)),
            "route": "standards",
            "category": row["key"],
        })
    if practice["status"] == "behind":
        actions.append({
            "kind": "behind",
            "title": "还差 %d 题" % -practice["behind"],
            "detail": "先补差额，不要开新资料；周日复盘时把这页存下来。",
            "route": "plan",
        })
    if info["started"] and not practice["attempted"]:
        actions.append({
            "kind": "steady",
            "title": "这一%s没有练习记录" % info["unit"],
            "detail": "做完一次先记四个数，否则计划无法判断；复习记录也会自动进来。",
            "route": "plan",
        })
    if practice["attempted"] >= 10 and practice["overtime"] / practice["attempted"] > 0.15:
        actions.append({
            "kind": "overtime",
            "title": "超时 %d 题" % practice["overtime"],
            "detail": "平均 %.1f 分钟/题；先做限时拆解，不增加知识量。" % (
                practice["avg_minutes"] or 0),
            "route": "plan",
        })
    untouched = [row for row in categories
                 if row["new_errors"] and not row["reviews"]]
    if untouched:
        row = max(untouched, key=lambda item: item["new_errors"])
        actions.append({
            "kind": "untouched",
            "title": "%s 新增 %d 道没复习" % (row["short"], row["new_errors"]),
            "detail": "错题记下了但还没回看；点开错题本先过一遍题面。",
            "route": "book",
            "category": row["key"],
        })
    if not actions:
        actions.append({
            "kind": "steady",
            "title": "节奏正常",
            "detail": "按今日复习队列走；周日把这页存下来，下周对比。",
            "route": "review",
        })
    return actions[:3]


# ---------------------------------------------------------------- markdown

def _plan_line(practice, info):
    if practice["status"] == "baseline":
        return "- 计划：基线周 · 练习记录 %d 题" % practice["attempted"]
    if practice["status"] == "upcoming":
        return "- 计划：全期目标 %d 题（未开始）" % practice["target"]
    behind = practice["behind"]
    if behind > 0:
        note = "超额 %d" % behind
    elif behind < 0:
        note = "还差 %d" % -behind
    else:
        note = "正好"
    if practice["behind_basis"] == "to_date":
        return "- 计划：%d / %d 题（全期目标）；截至今日目标 %d，%s" % (
            practice["attempted"], practice["target"],
            practice["target_to_date"], note)
    return "- 计划：%d / %d 题（%s，%s）" % (
        practice["attempted"], practice["target"],
        STATUS_LABELS.get(practice["status"], practice["status"]), note)


def to_markdown(data):
    info = data["range"]
    practice = data["practice"]
    review = data["review"]
    book = data["book"]
    baseline = data["baseline"]
    countdown = data.get("countdown") or {}
    lines = ["# " + data["title"], ""]
    head = [info["label"]]
    if data.get("phase"):
        head.append(data["phase"]["name"])
    if countdown.get("days_left") is not None:
        if countdown["days_left"] >= 0:
            head.append("距 %s 还有 %d 天" % (
                countdown.get("exam_date", "考试"), countdown["days_left"]))
        else:
            head.append("已过 %s" % countdown.get("exam_date", "考试"))
    lines.append(" · ".join(head))
    lines.append("")
    lines.append("> " + data["encouragement"]["text"])
    lines.append("> " + data["encouragement"]["evidence"])
    lines.append("")
    lines.append("## 关键数字")
    lines.append(
        "- 练习：%d 题 · 做对 %d（%s）· 放弃 %d · 超时 %d · 用时 %.1f h"
        % (practice["attempted"], practice["correct"], _pct(practice["accuracy"]),
           practice["abandoned"], practice["overtime"], practice["minutes"] / 60))
    lines.append(_plan_line(practice, info))
    lines.append("- 复习：%d 次 · 做对 %d（%s）· 新掌握 %d · 顽固题 %d"
                 % (review["reviews"], review["correct"],
                    _pct(review["accuracy"]), review["mastered"], book["stuck"]))
    lines.append("- 错题本（现状）：共 %d · 未掌握 %d · 已掌握 %d · 本期新增 %d"
                 % (book["total"], book["active"], book["mastered"],
                    book["new_errors"]))
    previous = data.get("previous") or {}
    if (previous.get("compare") or {}).get("comparable"):
        delta = previous.get("delta") or {}
        bits = []
        if delta.get("attempted") is not None:
            bits.append("练习 %+d 题" % delta["attempted"])
        if delta.get("reviews") is not None:
            bits.append("复习 %+d 次" % delta["reviews"])
        if delta.get("accuracy") is not None:
            bits.append("正确率 %+g 个百分点" % delta["accuracy"])
        if bits:
            lines.append("- 对比%s：%s" % (
                (previous.get("compare") or {}).get("label", "上期"),
                " · ".join(bits)))
    lines.append("")
    lines.append("## 九大类分项")
    lines.append("| 类别 | 练习 | 练习正确率 | 复习 | 复习正确率 | 新增错题 | 基线 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in data["categories"]:
        lines.append("| %s | %d | %s | %d | %s | %d | %s |" % (
            row["short"], row["attempted"], _pct(row["accuracy"]),
            row["reviews"], _pct(row["review_accuracy"]),
            row["new_errors"], _pct(row["baseline_accuracy"])))
    lines.append("")
    lines.append("## 节奏")
    if info["period"] == "week":
        rows = data["activity"]
    else:
        rows = _bucket_activity(data["activity"])
    active = [row for row in rows if row["reviews"] or row["new_errors"]]
    if not active:
        lines.append("- 没有活动记录。")
    else:
        for row in rows:
            if info["period"] == "week":
                label = "%s %s" % (row["label"], row["weekday"])
            else:
                label = row["label"]
            lines.append("- %s · 复习 %d · 新错 %d" % (
                label, row["reviews"], row["new_errors"]))
    lines.append("")
    lines.append("## 2025 基线")
    if not baseline["available"]:
        lines.append("- 尚未记录；计划 %s。" % (baseline.get("due") or "日期未定"))
    else:
        for half in baseline["halves"]:
            if not half["recorded"]:
                lines.append("- %s：未记录" % half["label"])
                continue
            lines.append("- %s：%s · %d 题 · %d 对 · %s/%s 分" % (
                half["label"], half["day"], half["answered"], half["correct"],
                half["score"], half["max_score"]))
        total = "合计 %s/%s · 通过线 %s" % (
            baseline["score"], baseline["max_score"], baseline["pass_line"])
        if baseline["complete"]:
            total += " · " + ("已过线" if baseline["passed"] else "未过线")
        else:
            total += " · 两半未齐"
        lines.append("- " + total)
    lines.append("")
    lines.append("## 下一步")
    for index, action in enumerate(data["next_actions"], start=1):
        lines.append("%d. **%s** — %s" % (
            index, action["title"], action["detail"]))
    lines.append("")
    lines.append("---")
    lines.append("生成于 %s · 数据来自本机 data/ · 保存后这一页不再自动变化"
                 % data["generated_at"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- build

def build(store, data_dir, settings, *, exam_tags=None, baseline=None,
          period="week", anchor=None, now=None):
    """Assemble one report; nothing is written until `save()` is called."""
    info = period_info(period, anchor, today=_today(now))
    config = plan.load_config(data_dir)
    hours = config["weekly_hours"]
    today = _today(now)

    sessions = _sessions_in(store, info)
    prev_info = period_info(info["period"], info["prev_anchor"], today=today)
    start_day = date.fromisoformat(info["start"])
    end_day = date.fromisoformat(info["end"])
    elapsed_days = (
        (min(today, end_day) - start_day).days + 1
        if today >= start_day else 0
    )
    # Mid-period, compare with the same slice of the previous period; a full
    # previous week against two days of this week is not a fair delta.
    compare_days = min(info["days"], elapsed_days) if info["started"] else 0
    if 0 < compare_days < info["days"]:
        prev_end = (date.fromisoformat(prev_info["start"])
                    + timedelta(days=compare_days - 1))
        prev_info = dict(
            prev_info,
            end=prev_end.isoformat(),
            days=compare_days,
            label="%s – %s" % (prev_info["start"], prev_end.isoformat()),
        )
    prev_sessions = _sessions_in(store, prev_info)
    snapshot = store.learning_snapshot()
    attempts = snapshot.get("attempts", [])
    logs = snapshot.get("logs", [])
    period_logs = _logs_in(logs, info)
    prev_logs = _logs_in(logs, prev_info)
    new_errors = _attempts_in(attempts, info)
    prev_errors = _attempts_in(attempts, prev_info)

    practice = _session_totals(sessions)
    previous_practice = _session_totals(prev_sessions)
    review = _review_totals(period_logs)
    previous_review = _review_totals(prev_logs)
    review["sessions"] = sum(
        1 for row in snapshot.get("sessions", [])
        if _in_range(_day_of(row.get("started_at")), info)
    )
    phase = plan.phase_on(settings, info["end"])
    full_target = _planned_questions(settings, info, hours)
    if not info["started"]:
        to_date_target = 0
    elif today >= end_day:
        to_date_target = full_target
    else:
        to_date_target = _planned_questions(
            settings, dict(info, end=today.isoformat()), hours
        )
    practice["target"] = full_target
    practice["target_to_date"] = to_date_target
    if phase.get("key") == "baseline":
        practice["behind"] = 0
        practice["status"] = "baseline"
        practice["behind_basis"] = "none"
    elif not info["started"]:
        practice["behind"] = 0
        practice["status"] = "upcoming"
        practice["behind_basis"] = "none"
    else:
        compared = full_target if today >= end_day else to_date_target
        practice["behind"] = practice["attempted"] - compared
        practice["status"] = (
            "ahead" if practice["behind"] >= 2
            else "behind" if practice["behind"] <= -2 else "on_track"
        )
        practice["behind_basis"] = "full" if today >= end_day else "to_date"
    practice["by_source"] = _by_source(sessions)
    practice["target_basis"] = "按每周 %.0f 小时折算" % hours

    active = [row for row in attempts if row.get("status") != "mastered"]
    book = {
        "total": len(attempts),
        "active": len(active),
        "mastered": len(attempts) - len(active),
        "stuck": sum(1 for row in active if (row.get("times") or 0) >= STUCK_TIMES),
        "new_errors": len(new_errors),
    }

    categories = _category_rows(sessions, period_logs, new_errors, exam_tags,
                                baseline)
    baseline_view = _baseline_compact(baseline, settings, info)
    # The countdown is read "now" (same as the dashboard); the phase above is
    # the phase of the reported period, so a past report still names its phase.
    countdown = analytics.countdown(settings, now=now)
    due = 0
    try:
        due = store.review_overview(now=now)["count"]
    except Exception:  # noqa: BLE001 - a report must still render offline
        due = 0

    previous = {
        "practice": {
            key: previous_practice[key]
            for key in ("attempted", "correct", "accuracy", "minutes")
        },
        "review": {
            "reviews": previous_review["reviews"],
            "accuracy": previous_review["accuracy"],
        },
        "new_errors": len(prev_errors),
        "compare": {
            "label": "上期同期" if 0 < compare_days < info["days"] else "上期",
            "days": compare_days or info["days"],
            "comparable": bool(info["started"]),
        },
        "delta": {
            "attempted": practice["attempted"] - previous_practice["attempted"],
            "reviews": review["reviews"] - previous_review["reviews"],
            "new_errors": len(new_errors) - len(prev_errors),
            "accuracy": (
                round((practice["accuracy"] - previous_practice["accuracy"]) * 100, 1)
                if practice["accuracy"] is not None
                and previous_practice["accuracy"] is not None else None
            ),
        },
    }

    data = {
        "period": info["period"],
        "key": info["key"],
        "title": info["title"],
        "short": info["unit"],
        "range": info,
        "generated_at": now_iso(now),
        "phase": {
            key: phase.get(key)
            for key in ("key", "name", "focus", "start", "end", "week",
                        "weeks", "weekly_questions")
        },
        "countdown": countdown,
        "practice": practice,
        "previous": previous,
        "review": review,
        "book": book,
        "categories": categories,
        "activity": _activity(info, period_logs, new_errors),
        "baseline": baseline_view,
        "encouragement": _encouragement(practice, review, book, previous, info),
        "next_actions": _next_actions(
            practice, review, book, categories, baseline, settings, due,
            info, today,
        ),
    }
    data["markdown"] = to_markdown(data)
    return data


# ---------------------------------------------------------------- saved pages

def _reports_dir(data_dir):
    return Path(data_dir) / "reports"


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".report-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _check_key(key):
    key = str(key or "")
    if not _KEY_RE.match(key):
        raise ValueError("报告标识不合法")
    return key


def save(data_dir, data, now=None):
    """Freeze the page as `<key>.md` plus `<key>.json`; same period replaces."""
    key = _check_key(data.get("key"))
    saved_at = now_iso(now)
    payload = dict(data)
    payload["saved_at"] = saved_at
    markdown = payload.get("markdown") or to_markdown(payload)
    payload["markdown"] = markdown
    folder = _reports_dir(data_dir)
    _atomic_write(folder / (key + ".json"),
                  json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(folder / (key + ".md"), markdown)
    return {
        "key": key,
        "period": payload.get("period"),
        "title": payload.get("title"),
        "start": (payload.get("range") or {}).get("start"),
        "end": (payload.get("range") or {}).get("end"),
        "saved_at": saved_at,
        "generated_at": payload.get("generated_at"),
        "attempts": (payload.get("practice") or {}).get("attempted"),
        "reviews": (payload.get("review") or {}).get("reviews"),
        "file": key + ".md",
    }


def list_saved(data_dir, limit=30):
    folder = _reports_dir(data_dir)
    items = []
    if not folder.exists():
        return items
    for path in folder.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = str(payload.get("key") or path.stem)
        if not _KEY_RE.match(key):
            continue
        items.append({
            "key": key,
            "period": payload.get("period"),
            "title": payload.get("title"),
            "start": (payload.get("range") or {}).get("start"),
            "end": (payload.get("range") or {}).get("end"),
            "saved_at": payload.get("saved_at"),
            "attempts": (payload.get("practice") or {}).get("attempted"),
            "reviews": (payload.get("review") or {}).get("reviews"),
            "file": key + ".md",
            "size": path.stat().st_size,
        })
    items.sort(key=lambda row: (row.get("saved_at") or "", row["key"]),
               reverse=True)
    return items[:limit]


def load_saved(data_dir, key):
    key = _check_key(key)
    path = _reports_dir(data_dir) / (key + ".json")
    if not path.exists():
        raise KeyError(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("报告文件损坏：%s" % key)
    md_path = path.with_suffix(".md")
    markdown = (
        md_path.read_text(encoding="utf-8")
        if md_path.exists() else payload.get("markdown") or ""
    )
    return {
        "key": key,
        "title": payload.get("title"),
        "saved_at": payload.get("saved_at"),
        "markdown": markdown,
        "data": payload,
    }


def report_file(data_dir, key):
    key = _check_key(key)
    path = _reports_dir(data_dir) / (key + ".md")
    if not path.exists():
        raise KeyError(key)
    return path


def delete_saved(data_dir, key):
    key = _check_key(key)
    folder = _reports_dir(data_dir)
    removed = False
    for suffix in (".md", ".json"):
        path = folder / (key + suffix)
        if path.exists():
            path.unlink()
            removed = True
    return removed
