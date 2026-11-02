"""Learning analytics for the dashboard and the data screen.

The module is read-only: it consumes a `Store.learning_snapshot()` plus the
static curriculum and returns plain JSON-ready dicts.  Every number the UI
shows for encouragement comes from here, so praise can always name its
evidence.

Units:
    - "wrong" means an attempt at all (active or mastered); "active" excludes
      mastered questions.
    - accuracy is always correct / (correct + wrong) over the review log in
      the selected window, never a guess from a single last result.
"""

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from app.curriculum import (
    CASE_CATEGORIES,
    CASE_WEIGHT_TOTAL,
    category_key,
    load_exam_tags,
)
from app.store import TAGS


# ---------------------------------------------------------------- utilities

def _as_datetime(value):
    """ISO string or datetime -> naive local datetime (China has no DST)."""
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _day(value):
    return _as_datetime(value).date() if value else None


def _rate(correct, total):
    return round(correct / total, 4) if total else None


def case_bank_counts(lookup):
    """How many indexed case questions each `C*` chapter holds."""
    counts = Counter()
    for qid in (lookup or {}).get("chapter", {}):
        match = re.match(r"^C(\d{1,2})-", qid)
        if match:
            counts["C%d" % int(match.group(1))] += 1
    return dict(counts)


def countdown(settings, now=None):
    now_dt = _as_datetime(now or datetime.now().astimezone())
    today = now_dt.date()
    exam = date.fromisoformat(settings["exam_date"])
    start = date.fromisoformat(settings["plan_start"])
    days_left = (exam - today).days
    total_days = max(1, (exam - start).days)
    elapsed = min(total_days, max(0, (today - start).days))
    if days_left < 0:
        phase = "已考试"
    elif days_left <= 45:
        phase = "冲刺期"
    elif days_left <= 120:
        phase = "套卷期"
    elif days_left <= 270:
        phase = "强化期"
    else:
        phase = "基础期"
    return {
        "exam_date": settings["exam_date"],
        "exam_label": settings["exam_label"],
        "days_left": days_left,
        "weeks_left": max(0, -(-days_left // 7)),
        "phase": phase,
        "plan_start": settings["plan_start"],
        "progress": round(elapsed / total_days, 4),
        "passed": days_left < 0,
    }


# ---------------------------------------------------------------- snapshot

def _activity(attempts, logs, now, days):
    today = _as_datetime(now).date() if now else datetime.now().date()
    buckets = {}
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        buckets[day] = {"date": day.isoformat(), "reviews": 0, "wrong": 0,
                        "new_errors": 0}
    for log in logs:
        day = _day(log["at"])
        if day in buckets:
            buckets[day]["reviews"] += 1
            if log["result"] == "wrong":
                buckets[day]["wrong"] += 1
    for attempt in attempts:
        day = _day(attempt.get("created_at"))
        if day in buckets:
            buckets[day]["new_errors"] += 1
    rows = list(buckets.values())
    for row in rows:
        row["label"] = row["date"][5:]
    return rows


def _streak(attempts, logs, now):
    today = _as_datetime(now).date() if now else datetime.now().date()
    active = set()
    for log in logs:
        day = _day(log["at"])
        if day:
            active.add(day)
    for attempt in attempts:
        for key in ("created_at", "last_at"):
            day = _day(attempt.get(key))
            if day:
                active.add(day)
    active_today = today in active
    cursor = today if active_today else today - timedelta(days=1)
    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return {"days": streak, "active_today": active_today,
            "active_days_30": len([d for d in active
                                   if d >= today - timedelta(days=29)])}


def _today_rows(logs, attempts, now):
    today = _as_datetime(now).date() if now else datetime.now().date()
    today_logs = [log for log in logs if _day(log["at"]) == today]
    correct = sum(1 for log in today_logs if log["result"] == "correct")
    wrong = sum(1 for log in today_logs if log["result"] == "wrong")
    recovered = sum(
        1 for log in today_logs
        if log["result"] == "correct" and log["last_result_before"] == "wrong"
    )
    mastered = sum(
        1 for log in today_logs
        if log["status_after"] == "mastered"
        and log["status_before"] != "mastered"
    )
    new_errors = sum(1 for attempt in attempts
                     if _day(attempt.get("created_at")) == today)
    return {
        "day": today.isoformat(),
        "reviews": len(today_logs),
        "correct": correct,
        "wrong": wrong,
        "accuracy": _rate(correct, len(today_logs)),
        "recovered": recovered,
        "mastered": mastered,
        "new_errors": new_errors,
        "minutes": len(today_logs) * 2,
    }


def _totals(attempts, logs, now, days):
    now_dt = _as_datetime(now) if now else datetime.now()
    cutoff = now_dt - timedelta(days=days)
    period = [log for log in logs if _as_datetime(log["at"]) >= cutoff]
    correct = sum(1 for log in period if log["result"] == "correct")
    wrong = sum(1 for log in period if log["result"] == "wrong")
    stamp = now_dt.isoformat(timespec="seconds")
    active = [a for a in attempts if a.get("status") == "active"]
    due = 0
    for a in active:
        raw = a.get("next_at") or a.get("last_at")
        if raw and _as_datetime(raw) <= now_dt:
            due += 1
    stuck = sum(1 for a in active if (a.get("times") or 0) >= 3)
    reviewed = [a for a in attempts if a.get("last_result") in ("correct", "wrong")]
    ever_correct = sum(1 for a in reviewed if a["last_result"] == "correct")
    return {
        "total": len(attempts),
        "active": len(active),
        "mastered": len(attempts) - len(active),
        "due": due,
        "stuck": stuck,
        "reviews": len(period),
        "correct": correct,
        "wrong": wrong,
        "accuracy": _rate(correct, len(period)),
        "accuracy_ever": _rate(ever_correct, len(reviewed)),
        "range_days": days,
        "stamp": stamp,
    }


def _categories(attempts, tags, logs, exam_tags, bank_counts, now, days):
    now_dt = _as_datetime(now) if now else datetime.now()
    cutoff = now_dt - timedelta(days=days)
    rows = {}
    for row in CASE_CATEGORIES:
        rows[row["key"]] = {
            "key": row["key"],
            "name": row["name"],
            "short": row["short"],
            "weight": row["weight"],
            "weight_share": round(row["weight"] / CASE_WEIGHT_TOTAL, 4),
            "bank": int((bank_counts or {}).get(row["key"], 0)),
            "wrong_total": 0,
            "active": 0,
            "mastered": 0,
            "reviews": 0,
            "correct": 0,
            "wrong_reviews": 0,
            "accuracy": None,
            "last_review_at": None,
            "tags": {key: 0 for key in TAGS},
            "baseline": None,
        }
    unmapped = {"wrong_total": 0, "active": 0, "reviews": 0, "correct": 0,
                "wrong_reviews": 0, "accuracy": None, "qids": Counter()}
    mapped_total = 0
    for attempt in attempts:
        key = category_key(attempt["qid"], exam_tags)
        if key:
            bucket = rows[key]
            bucket["wrong_total"] += 1
            mapped_total += 1
            if attempt.get("status") == "mastered":
                bucket["mastered"] += 1
            else:
                bucket["active"] += 1
            for tag in tags.get(attempt["id"], []):
                if tag in bucket["tags"]:
                    bucket["tags"][tag] += 1
        else:
            unmapped["wrong_total"] += 1
            unmapped["qids"][attempt["family"]] += 1
            if attempt.get("status") != "mastered":
                unmapped["active"] += 1
    for log in logs:
        key = category_key(log["qid"], exam_tags)
        bucket = rows.get(key) if key else None
        if not bucket:
            if _as_datetime(log["at"]) >= cutoff:
                unmapped["reviews"] += 1
                if log["result"] == "correct":
                    unmapped["correct"] += 1
                else:
                    unmapped["wrong_reviews"] += 1
            continue
        at = _as_datetime(log["at"])
        if at < cutoff:
            continue
        bucket["reviews"] += 1
        if log["result"] == "correct":
            bucket["correct"] += 1
        else:
            bucket["wrong_reviews"] += 1
        if not bucket["last_review_at"] or at > _as_datetime(bucket["last_review_at"]):
            bucket["last_review_at"] = at.isoformat(timespec="seconds")
    for bucket in rows.values():
        bucket["accuracy"] = _rate(bucket["correct"],
                                   bucket["correct"] + bucket["wrong_reviews"])
        bucket["share_of_wrong"] = (
            round(bucket["wrong_total"] / mapped_total, 4) if mapped_total else 0
        )
    unmapped["accuracy"] = _rate(unmapped["correct"],
                                  unmapped["correct"] + unmapped["wrong_reviews"])
    unmapped["qids"] = dict(unmapped["qids"])
    return list(rows.values()), unmapped, mapped_total


def _section_rows(chapters):
    rows = []
    for key, chapter in (chapters or {}).items():
        for section in chapter.get("sections", []):
            try:
                first = int(str(section["first"]).split("-")[1])
                last = int(str(section["last"]).split("-")[1])
            except (KeyError, ValueError):
                continue
            rows.append({
                "chapter": key,
                "chapter_title": chapter.get("title", key),
                "name": section.get("name", ""),
                "first": first,
                "last": last,
                "bank": int(section.get("count") or (last - first + 1)),
            })
    return rows


def _sections(attempts, tags, logs, exam_tags, chapters, now, days):
    """Per-section (知识点) counts; only C-family qids carry a section."""
    section_rows = _section_rows(chapters)
    if not section_rows:
        return []
    now_dt = _as_datetime(now) if now else datetime.now()
    cutoff = now_dt - timedelta(days=days)
    buckets = {}
    for section in section_rows:
        bucket = {
            "chapter": section["chapter"],
            "chapter_title": section["chapter_title"],
            "name": section["name"],
            "first": section["first"],
            "last": section["last"],
            "bank": section["bank"],
            "wrong_total": 0,
            "active": 0,
            "mastered": 0,
            "reviews": 0,
            "correct": 0,
            "wrong_reviews": 0,
            "accuracy": None,
        }
        buckets[(section["chapter"], section["first"])] = bucket

    def find(qid):
        match = re.match(r"^C(\d{1,2})-(\d{1,3})$", qid or "")
        if not match:
            return None
        key = "C%d" % int(match.group(1))
        number = int(match.group(2))
        chapter_rows = [s for s in section_rows if s["chapter"] == key]
        if not chapter_rows:
            return None
        chosen = None
        for section in chapter_rows:
            if section["first"] <= number <= section["last"]:
                chosen = section
                break
        if chosen is None:
            # A few questions sit in the gaps between OCR-derived section
            # ranges; the nearest preceding section is the least wrong home.
            preceding = [s for s in chapter_rows if s["first"] <= number]
            chosen = max(preceding, key=lambda s: s["first"]) if preceding else chapter_rows[0]
        return buckets[(key, chosen["first"])]

    for attempt in attempts:
        bucket = find(attempt["qid"])
        if not bucket:
            continue
        bucket["wrong_total"] += 1
        if attempt.get("status") == "mastered":
            bucket["mastered"] += 1
        else:
            bucket["active"] += 1
    for log in logs:
        bucket = find(log["qid"])
        if not bucket or _as_datetime(log["at"]) < cutoff:
            continue
        bucket["reviews"] += 1
        if log["result"] == "correct":
            bucket["correct"] += 1
        else:
            bucket["wrong_reviews"] += 1
    rows = list(buckets.values())
    for bucket in rows:
        bucket["accuracy"] = _rate(
            bucket["correct"], bucket["correct"] + bucket["wrong_reviews"]
        )
    rows.sort(key=lambda row: (-row["wrong_total"], row["accuracy"] or 0))
    return rows


def _families(attempts):
    rows = {}
    for attempt in attempts:
        family = attempt.get("family") or "?"
        bucket = rows.setdefault(family, {"family": family, "total": 0,
                                          "active": 0, "mastered": 0})
        bucket["total"] += 1
        if attempt.get("status") == "mastered":
            bucket["mastered"] += 1
        else:
            bucket["active"] += 1
    return sorted(rows.values(), key=lambda row: (-row["total"], row["family"]))


def _tag_breakdown(attempts, tags):
    counts = {key: 0 for key in TAGS}
    for attempt in attempts:
        for tag in tags.get(attempt["id"], []):
            if tag in counts:
                counts[tag] += 1
    return {"labels": TAGS, "counts": counts}


def summarize(snapshot, *, lookup=None, exam_tags=None, now=None, days=30):
    attempts = snapshot.get("attempts", [])
    tags = snapshot.get("tags", {})
    logs = snapshot.get("logs", [])
    rows, unmapped, mapped_total = _categories(
        attempts, tags, logs, exam_tags or {}, case_bank_counts(lookup), now, days
    )
    totals = _totals(attempts, logs, now, days)
    totals["mapped_wrong"] = mapped_total
    return {
        "day": (_as_datetime(now).date() if now else datetime.now().date()).isoformat(),
        "totals": totals,
        "today": _today_rows(logs, attempts, now),
        "streak": _streak(attempts, logs, now),
        "categories": rows,
        "unmapped": unmapped,
        "families": _families(attempts),
        "tags": _tag_breakdown(attempts, tags),
        "activity": _activity(attempts, logs, now, max(days, 14)),
    }


# ---------------------------------------------------------------- baseline

_BASELINE_ROW = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|"
)
_BASELINE_HALF = re.compile(r"^##\s+(\S+)\s+·\s+(上午卷|下午卷)")
_BASELINE_SCORE = re.compile(r"得分\s*\*\*(\d+)\s*/\s*(\d+)\*\*")
_BASELINE_COUNTS = re.compile(
    r"作答\s*\*\*(\d+)\*\*\s*/\s*(\d+)\s*·\s*做对\s*\*\*(\d+)\*\*"
    r"\s*·\s*放弃\s*\*\*(\d+)\*\*\s*·\s*超时\s*\*\*(\d+)\*\*"
)


def read_baseline(path):
    """Parse docs/coach/baseline-2025.md when it exists.

    The file is written by `tools/score_baseline.py --write`; before that the
    dashboard says "not submitted" instead of inventing a score.
    """
    path = Path(path)
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"available": False, "path": str(path), "error": str(exc)}
    halves = []
    by_category = {}
    current = None
    for line in text.splitlines():
        match = _BASELINE_HALF.match(line.strip())
        if match:
            current = {"day": match.group(1), "half": match.group(2),
                       "score": None, "max_score": None,
                       "answered": 0, "correct": 0,
                       "skipped": 0, "overtime": 0,
                       "counts_line": False}
            halves.append(current)
            continue
        counts = _BASELINE_COUNTS.search(line)
        if counts and current is not None:
            current["answered"] = int(counts.group(1))
            current["total_questions"] = int(counts.group(2))
            current["correct"] = int(counts.group(3))
            current["skipped"] = int(counts.group(4))
            current["overtime"] = int(counts.group(5))
            current["counts_line"] = True
            continue
        score = _BASELINE_SCORE.search(line)
        if score and current is not None:
            current["score"] = int(score.group(1))
            current["max_score"] = int(score.group(2))
            continue
        row = _BASELINE_ROW.match(line.strip())
        if not row or not current:
            continue
        name = row.group(1).strip()
        if name in ("类别", "---") or set(name) <= {"-"}:
            continue
        try:
            n, answered, correct = int(row.group(2)), int(row.group(3)), int(row.group(4))
        except ValueError:
            continue
        bucket = by_category.setdefault(
            name, {"n": 0, "answered": 0, "correct": 0}
        )
        bucket["n"] += n
        bucket["answered"] += answered
        bucket["correct"] += correct
        if not current["counts_line"]:
            current["answered"] += answered
            current["correct"] += correct
    for bucket in by_category.values():
        bucket["accuracy"] = _rate(bucket["correct"], bucket["answered"])
    total_answered = sum(row["answered"] for row in halves)
    total_correct = sum(row["correct"] for row in halves)
    return {
        "available": bool(halves),
        "path": str(path),
        "halves": halves,
        "by_category": by_category,
        "process_scored": any(row["counts_line"] for row in halves),
        "answered": total_answered,
        "correct": total_correct,
        "skipped": sum(row.get("skipped", 0) for row in halves),
        "overtime": sum(row.get("overtime", 0) for row in halves),
        "accuracy": _rate(total_correct, total_answered),
        "score": sum(row["score"] or 0 for row in halves),
        "max_score": sum(row["max_score"] or 0 for row in halves),
    }


def attach_baseline(categories, baseline):
    """Put the baseline accuracy on the matching curriculum row."""
    if not baseline or not baseline.get("available"):
        return
    lookup = {
        row["exam_category"]: row for row in CASE_CATEGORIES
        if row["exam_category"]
    }
    for name, result in (baseline.get("by_category") or {}).items():
        row = lookup.get(name)
        if row:
            for category in categories:
                if category["key"] == row["key"]:
                    category["baseline"] = result


# ---------------------------------------------------------------- coach

def _encouragement(summary, due, settings):
    today = summary["today"]
    streak = summary["streak"]
    totals = summary["totals"]
    if today["reviews"]:
        if today["mastered"]:
            text = "今天有 %d 道题达到掌握标准——连续两次做对、间隔 ≥7 天，这是能划掉的题。" % today["mastered"]
        elif today["recovered"]:
            text = "今天有 %d 道旧错题第一次做对，回看在起作用。" % today["recovered"]
        elif (today["accuracy"] or 0) >= 0.8:
            text = "今天做了 %d 题，做对 %d 题——把会做的稳住，本身就是涨分。" % (
                today["reviews"], today["correct"])
        else:
            text = "今天完成 %d 题；错的 %d 道已经排进后面几天，不会漏。" % (
                today["reviews"], today["wrong"])
        evidence = "依据：今日复习 %d 题，正确 %d 题（%s）；连续学习 %d 天。" % (
            today["reviews"], today["correct"],
            "%d%%" % round(today["accuracy"] * 100) if today["accuracy"] is not None else "—",
            streak["days"])
        return {"text": text, "evidence": evidence}
    if due:
        return {
            "text": "今天有 %d 题到期；先做 5 题也算推进，排期已经替你决定好了。" % due,
            "evidence": "依据：到期队列 %d 题；错题本共 %d 题，已掌握 %d 题。" % (
                due, totals["total"], totals["mastered"]),
        }
    if totals["total"]:
        return {
            "text": "今天没有到期题。翻一眼弱项规范，或记下今天新错的一道题，都算数。",
            "evidence": "依据：错题本 %d 题，未掌握 %d 题，顽固题 %d 道。" % (
                totals["total"], totals["active"], totals["stuck"]),
        }
    return {
        "text": "从一张错题照片开始：题号、解析、错因和复习日期会自动接上。",
        "evidence": "依据：错题本还是空的。",
    }


def _milestone(summary, logs_total):
    streak = summary["streak"]["days"]
    totals = summary["totals"]
    candidates = [
        ("错题入本", totals["total"], 10),
        ("连续学习", streak, 7),
        ("复习次数", logs_total, 100),
        ("掌握题数", totals["mastered"], 10),
    ]
    pending = [
        {"label": label, "current": current, "target": target,
         "progress": round(min(1, current / target), 4),
         "remaining": max(0, target - current)}
        for label, current, target in candidates if current < target
    ]
    if pending:
        return max(pending, key=lambda row: row["progress"])
    label, current, target = candidates[-1]
    return {"label": label, "current": current, "target": target,
            "progress": 1.0, "remaining": 0}


def _actions(summary, baseline, settings, now):
    today = _as_datetime(now).date() if now else datetime.now().date()
    totals = summary["totals"]
    actions = []
    if not baseline.get("complete", False):
        try:
            due_day = date.fromisoformat(settings["baseline_due"])
            gap = (due_day - today).days
            when = ("计划 %s" % settings["baseline_due"]
                    + (" · 还剩 %d 天" % gap if gap >= 0 else " · 已过 %d 天" % -gap))
        except ValueError:
            when = "计划日期未设定"
        detail = baseline.get("half_detail") or {}
        done = [half for half in ("A", "P")
                if (detail.get(half) or {}).get("recorded")]
        if len(done) == 1:
            title = "交 2025 基线%s" % ("下午卷" if done[0] == "A" else "上午卷")
        else:
            title = "交 2025 基线卷"
        actions.append({
            "kind": "baseline",
            "title": title,
            "detail": when + "；两半各限时 3 小时，按过程自评四个数（不按选项判案例）。",
            "route": "baseline",
        })
    if totals["due"]:
        actions.append({
            "kind": "review",
            "title": "今日复习 %d 题" % totals["due"],
            "detail": "预计 %d 分钟；按到期 → 新错 → 顽固排好，照着做就行。" % (
                max(1, totals["due"] * 2)),
            "route": "review",
        })
    weak = [
        row for row in summary["categories"]
        if row["reviews"] >= 3 and (row["accuracy"] or 0) < 0.6
    ]
    if weak:
        row = max(weak, key=lambda item: (item["weight"], item["wrong_total"]))
        actions.append({
            "kind": "weak",
            "title": "弱项：%s" % row["short"],
            "detail": "正确率 %d%%（%d 次）；先回规范地图，再做 3 题同类题。" % (
                round((row["accuracy"] or 0) * 100), row["reviews"]),
            "route": "standards",
            "category": row["key"],
        })
    untouched = [
        row for row in summary["categories"]
        if row["wrong_total"] and not row["reviews"]
    ]
    if untouched:
        row = max(untouched, key=lambda item: item["wrong_total"])
        actions.append({
            "kind": "untouched",
            "title": "%s 还有 %d 道没复习过" % (row["short"], row["wrong_total"]),
            "detail": "错题记下了但还没回看；点开错题本先过一遍题面。",
            "route": "book",
            "category": row["key"],
        })
    if totals["stuck"]:
        actions.append({
            "kind": "stuck",
            "title": "顽固题 %d 道" % totals["stuck"],
            "detail": "错 ≥3 次；先回书/规范看概念，不再机械刷。",
            "route": "book",
        })
    if not actions:
        actions.append({
            "kind": "steady",
            "title": "今天没有到期题",
            "detail": "可以记 1–2 道新错题，或翻一眼弱项规范，保持节奏。",
            "route": "lookup",
        })
    return actions[:3]


def build_home(snapshot, *, settings, lookup=None, exam_tags=None, baseline=None,
               now=None):
    summary = summarize(snapshot, lookup=lookup, exam_tags=exam_tags,
                        now=now, days=30)
    baseline = baseline or {"available": False}
    attach_baseline(summary["categories"], baseline)
    streak = summary["streak"]
    totals = summary["totals"]
    activity = summary["activity"][-14:]
    week_reviews = sum(row["reviews"] for row in activity[-7:])
    target = int(settings["daily_target"])
    return {
        "countdown": countdown(settings, now),
        "day": summary["day"],
        "today": summary["today"],
        "streak": streak,
        "totals": totals,
        "categories": summary["categories"],
        "unmapped": summary["unmapped"],
        "tags": summary["tags"],
        "activity": activity,
        "week": {
            "reviews": week_reviews,
            "target": target * 7,
            "progress": round(min(1, week_reviews / (target * 7)), 4),
        },
        "encouragement": _encouragement(summary, totals["due"], settings),
        "next_actions": _actions(summary, baseline, settings, now),
        "milestone": _milestone(summary, len(snapshot.get("logs", []))),
        "baseline": {
            "available": baseline.get("available", False),
            "complete": baseline.get("complete", False),
            "score": baseline.get("score"),
            "max_score": baseline.get("max_score"),
            "accuracy": baseline.get("accuracy"),
            "passed": baseline.get("passed"),
            "pass_line": baseline.get("pass_line", 60),
            "halves": baseline.get("halves") or [],
            "due": settings.get("baseline_due"),
        },
        "settings": {
            "exam_date": settings["exam_date"],
            "exam_label": settings["exam_label"],
            "daily_target": target,
            "baseline_due": settings["baseline_due"],
        },
    }


def build_stats(snapshot, *, settings, lookup=None, exam_tags=None, baseline=None,
                chapters=None, now=None, days=30):
    summary = summarize(snapshot, lookup=lookup, exam_tags=exam_tags,
                        now=now, days=days)
    baseline = baseline or {"available": False}
    attach_baseline(summary["categories"], baseline)
    summary["sections"] = _sections(
        snapshot.get("attempts", []), snapshot.get("tags", {}),
        snapshot.get("logs", []), exam_tags or {}, chapters, now, days,
    )
    chapter = (lookup or {}).get("chapter", {})
    exam = (lookup or {}).get("exam", {})
    mock = (lookup or {}).get("mock", {})
    case_total = sum(case_bank_counts(lookup).values())
    covered = sum(1 for row in summary["categories"] if row["wrong_total"])
    baseline = dict(baseline)
    baseline.setdefault("due", settings["baseline_due"])
    return {
        **summary,
        "countdown": countdown(settings, now),
        "coverage": {
            "case_bank": case_total,
            "exam_bank": len(exam),
            "mock_bank": len(mock),
            "indexed_total": len(chapter) + len(exam) + len(mock),
            "categories_touched": covered,
            "categories_total": len(CASE_CATEGORIES),
        },
        "baseline": baseline,
        "encouragement": _encouragement(summary, summary["totals"]["due"], settings),
        "next_actions": _actions(summary, baseline, settings, now),
    }


def dump_json(data, path):
    """Optional helper for probes; not used by the server."""
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
