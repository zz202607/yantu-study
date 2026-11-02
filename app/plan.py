"""Study plan and weekly control.

The app cannot infer how many questions were practised from the wrong book:
it only stores errors.  The plan therefore reads explicit `practice_sessions`
(four-number records) plus manual progress marks, and turns them into
"planned vs actual" with fixed correction rules.

Phases are anchored to the 2027 exam in `DEFAULT_CONFIG`; the final phase end
is replaced by the user's exam date from settings.  All dates are local.
"""

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from app.store import PRACTICE_SOURCES

DEFAULT_WEEKLY_HOURS = 11

DEFAULT_PHASES = [
    {"key": "baseline", "name": "基线准备", "start": "2026-11-02",
     "end": "2026-11-08", "weekly_questions": 0,
     "focus": "交 2025 上午/下午基线；贴核心规范书签"},
    {"key": "foundation", "name": "基础期", "start": "2026-11-09",
     "end": "2027-02-28", "weekly_questions": 20,
     "focus": "土力学 9 章 + 必拿原型 + 七本核心规范"},
    {"key": "strengthen", "name": "强化期", "start": "2027-03-01",
     "end": "2027-06-30", "weekly_questions": 32,
     "focus": "A/B 原型 + 2012–2018 真题按类别"},
    {"key": "papers", "name": "套卷期", "start": "2027-07-01",
     "end": "2027-09-30", "weekly_questions": 38,
     "focus": "2019–2025 真题 + 每周 1–2 套限时"},
    {"key": "sprint", "name": "冲刺期", "start": "2027-10-01",
     "end": "2027-11-01", "weekly_questions": 55,
     "focus": "错题二刷 + 每周 2 套 + one-pager"},
]

# A1..A17 + B1..B8 are the 25 core prototypes; C/D are the next-priority 7.
PROTOTYPES = [
    ("A1", "浅基础-承载力与深宽修正", "A", True),
    ("A2", "浅基础-基底压力/持力层", "A", True),
    ("A3", "浅基础-软弱下卧层", "A", True),
    ("A4", "浅基础-变形与沉降", "A", True),
    ("A5", "地基处理-换填垫层", "A", True),
    ("A6", "地基处理-预压/固结度", "A", True),
    ("A7", "地基处理-复合地基（单一桩型）", "A", True),
    ("A8", "地基处理-多桩型/复合地基沉降", "A", True),
    ("A9", "特殊土-湿陷性黄土", "A", True),
    ("A10", "特殊土-膨胀土", "A", True),
    ("A11", "特殊土-盐渍土", "A", True),
    ("A12", "特殊土-冻土", "A", True),
    ("A13", "特殊土-滑坡与崩塌", "A", True),
    ("A14", "特殊土-泥石流", "A", True),
    ("A15", "地震-场地类别与波速", "A", True),
    ("A16", "地震-液化判别与液化指数", "A", True),
    ("A17", "地震-地震影响系数/反应谱", "A", True),
    ("B1", "勘察-载荷试验", "B", True),
    ("B2", "勘察-土工试验与三相换算", "B", True),
    ("B3", "勘察-岩体分级", "B", True),
    ("B4", "深基础-桩基承载力", "B", True),
    ("B5", "深基础-桩基水平承载力", "B", True),
    ("B6", "深基础-桩基沉降", "B", True),
    ("B7", "检测-基桩检测", "B", True),
    ("B8", "检测-地基/复合地基载荷试验", "B", True),
    ("C1", "边坡-朗肯/库仑土压力", "C", False),
    ("C4", "边坡-稳定性（圆弧/直线/折线）", "C", False),
    ("C5", "边坡-重力式挡墙", "C", False),
    ("C6", "边坡-锚杆与板桩", "C", False),
    ("D1", "基坑-水土压力/水平荷载", "D", False),
    ("D3", "基坑-支挡稳定性/锚杆", "D", False),
    ("D6", "基坑-地下水控制", "D", False),
]

SOIL_CHAPTERS = [
    (1, "物理性质与工程分类"),
    (2, "渗透性与渗流"),
    (3, "应力计算（有效应力）"),
    (4, "变形与地基沉降"),
    (5, "抗剪强度"),
    (6, "挡土结构物上的土压力"),
    (7, "边坡稳定分析"),
    (8, "地基承载力"),
    (9, "土的动力特性（只读）"),
]

CORE_STANDARDS = [
    ("C2", "GB 50021-2001", "岩土工程勘察规范"),
    ("C3", "GB 50007-2011", "建筑地基基础设计规范"),
    ("C4", "JGJ 94-2008", "建筑桩基技术规范"),
    ("C5", "JGJ 79-2012", "建筑地基处理技术规范"),
    ("C6", "GB 50330-2013", "建筑边坡工程技术规范"),
    ("C7", "JGJ 120-2012", "建筑基坑支护技术规程"),
    ("C9", "GB 50011-2010", "建筑抗震设计规范"),
]

MUST_HAVE_CATEGORIES = {"C3", "C5", "C8", "C9"}
TARGET_QUESTIONS = 1200
TARGET_REAL_EXAM = 751

# Question ranges from docs/coach/prototypes.md.  Used only to select a
# printable drill; the plan never pretends these were already practised.
PROTOTYPE_RANGES = {
    "A1": ("C3-8", "C3-39"),
    "A2": ("C3-40", "C3-89"),
    "A3": ("C3-90", "C3-114"),
    "A4": ("C3-132", "C3-178"),
    "A5": ("C5-11", "C5-26"),
    "A6": ("C5-33", "C5-95"),
    "A7": ("C5-96", "C5-210"),
    "A8": ("C5-211", "C5-234"),
    "A9": ("C8-4", "C8-67"),
    "A10": ("C8-68", "C8-119"),
    "A11": ("C8-120", "C8-154"),
    "A12": ("C8-155", "C8-188"),
    "A13": ("C8-215", "C8-227"),
    "A14": ("C8-228", "C8-247"),
    "A15": ("C9-3", "C9-35"),
    "A16": ("C9-36", "C9-79"),
    "A17": ("C9-80", "C9-118"),
    "B1": ("C2-51", "C2-82"),
    "B2": ("C2-124", "C2-181"),
    "B3": ("C2-210", "C2-258"),
    "B4": ("C4-6", "C4-140"),
    "B5": ("C4-104", "C4-123"),
    "B6": ("C4-141", "C4-169"),
    "B7": ("C10-4", "C10-62"),
    "B8": ("C10-63", "C10-103"),
    "C1": ("C6-7", "C6-30"),
    "C4": ("C6-90", "C6-166"),
    "C5": ("C6-167", "C6-200"),
    "C6": ("C6-201", "C6-224"),
    "D1": ("C7-4", "C7-28"),
    "D3": ("C7-38", "C7-78"),
    "D6": ("C7-106", "C7-119"),
}


def _today(now=None):
    if now is None:
        return datetime.now().date()
    if isinstance(now, datetime):
        return now.date()
    if isinstance(now, date):
        return now
    return datetime.fromisoformat(str(now)).date()


def _parse_day(value):
    return date.fromisoformat(str(value)[:10])


def _phase_list(settings=None):
    phases = [dict(row) for row in DEFAULT_PHASES]
    if settings and settings.get("exam_date"):
        try:
            exam = date.fromisoformat(settings["exam_date"])
            if exam > date.fromisoformat(phases[-1]["start"]):
                phases[-1]["end"] = exam.isoformat()
        except ValueError:
            pass
    return phases


def load_config(data_dir):
    path = Path(data_dir) / "plan.json"
    config = {"weekly_hours": DEFAULT_WEEKLY_HOURS}
    try:
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            hours = stored.get("weekly_hours", DEFAULT_WEEKLY_HOURS)
            hours = int(hours)
            if 4 <= hours <= 30:
                config["weekly_hours"] = hours
    except (OSError, ValueError, TypeError):
        pass
    return config


def save_config(data_dir, weekly_hours):
    try:
        hours = int(weekly_hours)
    except (TypeError, ValueError):
        raise ValueError("每周小时数必须是整数")
    if not 4 <= hours <= 30:
        raise ValueError("每周可投入小时数应在 4–30 之间")
    path = Path(data_dir) / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"weekly_hours": hours}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)
    return {"weekly_hours": hours}


def phase_for(phases, today):
    if today < _parse_day(phases[0]["start"]):
        phase = dict(phases[0])
        week = 1
        total = 1
        start = _parse_day(phase["start"])
    else:
        phase = dict(phases[-1])
        for row in phases:
            if _parse_day(row["start"]) <= today <= _parse_day(row["end"]):
                phase = dict(row)
                break
        start = _parse_day(phase["start"])
        end = _parse_day(phase["end"])
        week = max(1, (today - start).days // 7 + 1)
        total = max(1, math.ceil(((end - start).days + 1) / 7))
    start = _parse_day(phase["start"])
    end = _parse_day(phase["end"])
    week = max(1, (today - start).days // 7 + 1) if today >= start else 1
    total = max(1, math.ceil(((end - start).days + 1) / 7))
    week_start = start + timedelta(days=(week - 1) * 7)
    week_end = min(end, week_start + timedelta(days=6))
    phase["week"] = week
    phase["weeks"] = total
    phase["week_start"] = week_start.isoformat()
    phase["week_end"] = week_end.isoformat()
    phase["days_left"] = (end - today).days
    return phase


def _in_range(day, start, end):
    return str(start) <= str(day)[:10] <= str(end)


def _sum_sessions(sessions):
    total = {
        "sessions": len(sessions),
        "attempted": sum(row["attempted"] for row in sessions),
        "correct": sum(row["correct"] for row in sessions),
        "abandoned": sum(row["abandoned"] for row in sessions),
        "overtime": sum(row["overtime"] for row in sessions),
        "minutes": sum(row["minutes"] for row in sessions),
    }
    total["accuracy"] = (
        round(total["correct"] / total["attempted"], 4)
        if total["attempted"] else None
    )
    total["avg_minutes"] = (
        round(total["minutes"] / total["attempted"], 1)
        if total["attempted"] else None
    )
    return total


def _by_category(sessions):
    rows = {}
    for row in sessions:
        bucket = rows.setdefault(
            row["category"],
            {"category": row["category"], "attempted": 0, "correct": 0,
             "minutes": 0},
        )
        bucket["attempted"] += row["attempted"]
        bucket["correct"] += row["correct"]
        bucket["minutes"] += row["minutes"]
    for bucket in rows.values():
        bucket["accuracy"] = (
            round(bucket["correct"] / bucket["attempted"], 4)
            if bucket["attempted"] else None
        )
    return sorted(rows.values(), key=lambda row: -row["attempted"])


def _progress_items(progress):
    return {key: value for key, value in progress.items()}


def _recorded_halves(baseline):
    """Normalize DB and legacy-markdown baseline shapes to {"A", "P"}."""
    recorded = set()
    for row in (baseline or {}).get("halves") or []:
        if row.get("recorded") is False:
            continue
        if baseline.get("source") == "file" and not row.get("counts_line"):
            continue
        half = row.get("half")
        if half not in ("A", "P"):
            half = {"上午卷": "A", "下午卷": "P"}.get(row.get("label") or half)
        if half in ("A", "P"):
            recorded.add(half)
    return recorded


def _task_list(phase, week_target, week_actual, prototypes, chapters, standards,
               baseline=None):
    tasks = []
    remaining = max(0, week_target - week_actual["attempted"])
    if phase["key"] == "baseline":
        recorded = _recorded_halves(baseline)
        tasks.append({"key": "baseline:A", "label": "交 2025 案例上午基线",
                      "detail": "限时 3 小时；四个数在 App 里记",
                      "done": "A" in recorded})
        tasks.append({"key": "baseline:P", "label": "交 2025 案例下午基线",
                      "detail": "限时 3 小时；两半都做完再对答案",
                      "done": "P" in recorded})
        tasks.append({"key": "standards:tags", "label": "贴七本核心规范书签/标签",
                      "detail": "按章节目录贴，和 App 索引一致",
                      "done": False})
        return tasks
    if remaining:
        tasks.append({"key": "week:remaining", "label": "本周还差 %d 题" % remaining,
                      "detail": "按当前阶段目标；不要用看视频代替动笔"})
    for row in prototypes:
        if not row["done"]:
            tasks.append({"key": "prototype:%s" % row["key"],
                          "label": "原型 %s · %s" % (row["key"], row["label"]),
                          "detail": "本轮 10–15 题；做完打勾"})
            if len(tasks) >= 4:
                break
    for row in chapters:
        if not row["done"]:
            tasks.append({"key": "soil:%d" % row["chapter"],
                          "label": "土力学第 %d 章 · %s" % (row["chapter"], row["label"]),
                          "detail": "教材 + 选做 10–15 题"})
            break
    for row in standards:
        if not row["done"]:
            tasks.append({"key": "standard:%s" % row["key"],
                          "label": "核心规范 %s %s" % (row["code"], row["title"]),
                          "detail": "精读 + 贴章节标签"})
            break
    tasks.append({"key": "weekly:report", "label": "周日：录本周练习 + 看周报",
                  "detail": "四个数 + 下周任务，30 分钟"})
    return tasks[:5]


def _advice(week, phase, must_accuracy, review_due, phase_summary, baseline=None):
    if phase["key"] == "baseline":
        recorded = _recorded_halves(baseline)
        if len(recorded) == 2:
            score = baseline.get("score") or 0
            return ["两半基线都已记录（合计 %d/100）；按分项正确率定基础期的优先级。"
                    % score]
        if recorded:
            missing = "下午卷" if "A" in recorded else "上午卷"
            return ["已完成%s；还差%s，3 小时做完后在同一页记四个数。"
                    % ("上午卷" if "A" in recorded else "下午卷", missing)]
        if week["attempted"]:
            return ["基线已记录 %d 题；两半都做完再对答案，然后进入基础期。"
                    % week["attempted"]]
        return ["本周先交 2025 上午/下午基线；两半各 3 小时，四个数在 App 里记。"]
    advice = []
    if week["attempted"] == 0:
        advice.append("本周还没有练习记录；做完一次先记四个数，否则计划无法判断。")
    elif week["behind"] <= -2:
        advice.append("本周还差 %d 题；先补差额，不要开新资料。" % -week["behind"])
    elif week["behind"] >= 2:
        advice.append("本周超额 %d 题；保持，不要一次加太多。" % week["behind"])
    if week["avg_minutes"] and week["avg_minutes"] > 10:
        advice.append("平均 %.1f 分钟/题，超过 10 分钟；先做限时拆解，不增加知识量。"
                      % week["avg_minutes"])
    if must_accuracy is not None and week["attempted"] >= 10 and must_accuracy < 0.85:
        advice.append("必拿类正确率 %d%%，低于 85%%；停新题，回原型卡和规范。"
                      % round(must_accuracy * 100))
    if review_due >= 15:
        advice.append("今日复习到期 %d 题（上限 20）；先清复习，再开新题。" % review_due)
    if phase["weeks"] >= 3 and phase_summary["expected"]:
        ratio = phase_summary["attempted"] / phase_summary["expected"]
        if ratio < 0.8:
            advice.append("本阶段进度 %d%%，低于 80%%；砍低频习题，保 A/B 和真题。"
                          % round(ratio * 100))
    if not advice:
        advice.append("节奏正常；按本周任务走，周日复盘一次。")
    return advice[:4]


def overview(store, data_dir, settings=None, now=None, baseline=None):
    today = _today(now)
    config = load_config(data_dir)
    hours = config["weekly_hours"]
    phases = _phase_list(settings)
    phase = phase_for(phases, today)
    scale = hours / DEFAULT_WEEKLY_HOURS
    week_target = int(round(phase["weekly_questions"] * scale))
    phase_start = phase["start"]
    week_start = phase["week_start"]
    week_end = phase["week_end"]
    all_sessions = store.list_practice_sessions(limit=500)
    week_sessions = [row for row in all_sessions
                     if _in_range(row["day"], week_start, week_end)]
    phase_sessions = [row for row in all_sessions
                      if _in_range(row["day"], phase_start, phase["end"])]
    week = _sum_sessions(week_sessions)
    week["target"] = week_target
    if phase["key"] == "baseline":
        week["behind"] = 0
        week["status"] = "baseline"
    else:
        week["behind"] = week["attempted"] - week_target
        week["status"] = ("ahead" if week["behind"] >= 2
                          else "behind" if week["behind"] <= -2 else "on_track")
    week["by_category"] = _by_category(week_sessions)
    phase_sum = _sum_sessions(phase_sessions)
    phase_sum["expected"] = week_target * phase["week"]
    phase_sum["progress"] = (
        round(phase_sum["attempted"] / phase_sum["expected"], 4)
        if phase_sum["expected"] else 0
    )
    must_attempted = sum(
        row["attempted"] for row in phase_sessions
        if row["category"] in MUST_HAVE_CATEGORIES
    )
    must_correct = sum(
        row["correct"] for row in phase_sessions
        if row["category"] in MUST_HAVE_CATEGORIES
    )
    must_accuracy = (
        round(must_correct / must_attempted, 4) if must_attempted else None
    )
    try:
        review_due = store.review_overview(now=now)["count"]
    except Exception:  # noqa: BLE001 - plan must still render
        review_due = 0

    progress = store.plan_progress()
    prototypes = []
    for key, label, tier, core in PROTOTYPES:
        row = progress.get("prototype:%s" % key, {})
        prototypes.append({
            "key": key, "label": label, "tier": tier, "core": core,
            "done": row.get("status") == "done",
            "status": row.get("status", "todo"),
            "accuracy": row.get("accuracy"),
        })
    chapters = []
    for number, label in SOIL_CHAPTERS:
        row = progress.get("soil:%d" % number, {})
        chapters.append({
            "chapter": number, "label": label,
            "done": row.get("status") == "done",
            "status": row.get("status", "todo"),
        })
    standards = []
    for key, code, title in CORE_STANDARDS:
        row = progress.get("standard:%s" % key, {})
        standards.append({
            "key": key, "code": code, "title": title,
            "done": row.get("status") == "done",
            "status": row.get("status", "todo"),
        })

    real_exam = sum(
        row["attempted"] for row in all_sessions if row["source"] == "real_exam"
    )
    total_questions = sum(row["attempted"] for row in all_sessions)
    targets = [
        {
            "key": "prototype", "label": "原型卡",
            "done": sum(1 for row in prototypes if row["core"] and row["done"]),
            "total": sum(1 for row in prototypes if row["core"]),
        },
        {
            "key": "soil", "label": "土力学章节",
            "done": sum(1 for row in chapters if row["done"]),
            "total": len(chapters),
        },
        {
            "key": "standard", "label": "核心规范",
            "done": sum(1 for row in standards if row["done"]),
            "total": len(standards),
        },
        {
            "key": "real_exam", "label": "案例真题",
            "done": real_exam, "total": TARGET_REAL_EXAM,
        },
        {
            "key": "questions", "label": "总题量",
            "done": total_questions, "total": TARGET_QUESTIONS,
        },
    ]
    for row in targets:
        row["progress"] = round(min(1, row["done"] / row["total"]), 4) if row["total"] else 0
    tasks = _task_list(phase, week_target, week, prototypes, chapters, standards,
                       baseline)
    return {
        "day": today.isoformat(),
        "baseline": baseline or {"available": False},
        "config": {
            "weekly_hours": hours,
            "weekly_hours_default": DEFAULT_WEEKLY_HOURS,
        },
        "phase": phase,
        "week": week,
        "phase_summary": phase_sum,
        "must_accuracy": must_accuracy,
        "review_due": review_due,
        "targets": targets,
        "prototypes": prototypes,
        "soil_chapters": chapters,
        "core_standards": standards,
        "tasks": tasks,
        "advice": _advice(week, phase, must_accuracy, review_due, phase_sum,
                          baseline),
        "recent_sessions": store.list_practice_sessions(limit=8),
        "sources": PRACTICE_SOURCES,
    }


def valid_progress_key(key):
    if key.startswith("prototype:"):
        return key.split(":", 1)[1] in {row[0] for row in PROTOTYPES}
    if key.startswith("soil:"):
        try:
            return 1 <= int(key.split(":", 1)[1]) <= len(SOIL_CHAPTERS)
        except ValueError:
            return False
    if key.startswith("standard:"):
        return key.split(":", 1)[1] in {row[0] for row in CORE_STANDARDS}
    return False


def qids_in_range(start, end):
    prefix = start.split("-")[0]
    first, last = int(start.split("-")[1]), int(end.split("-")[1])
    return ["%s-%d" % (prefix, number) for number in range(first, last + 1)]


def week_drill(store, data_dir, settings=None, per_prototype=5, limit=30):
    """Select a printable drill for the current unfinished prototypes.

    New questions are preferred; if a prototype already has enough wrong-book
    entries, the remaining slots use active ones so the sheet is never empty.
    No per-question practice log exists, so this deliberately does not claim
    "not done before"; the user sees the qids on the cover.
    """
    data = overview(store, data_dir, settings)
    progress = store.plan_progress()
    snapshot = store.learning_snapshot()
    attempts = {row["qid"]: row for row in snapshot.get("attempts", [])}
    chosen = []
    tasks = []
    for key, label, tier, core in PROTOTYPES:
        if progress.get("prototype:%s" % key, {}).get("status") == "done":
            continue
        if key not in PROTOTYPE_RANGES:
            continue
        start, end = PROTOTYPE_RANGES[key]
        qids = qids_in_range(start, end)
        new = [qid for qid in qids if qid not in attempts]
        active = [
            qid for qid in qids
            if qid in attempts and attempts[qid].get("status") != "mastered"
        ]
        picked = (new or active)[:per_prototype]
        if not picked:
            continue
        chosen.extend(picked)
        tasks.append({"key": key, "label": label, "qids": picked})
        if len(chosen) >= limit:
            break
    chosen = list(dict.fromkeys(chosen))[:limit]
    return {
        "qids": chosen,
        "tasks": tasks,
        "phase": data["phase"],
        "week": data["week"],
        "count": len(chosen),
    }
