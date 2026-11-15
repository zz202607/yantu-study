"""The daily task sheet behind the app's first-open popup.

Every "done" flag is read from a log the app already writes (review_log,
practice_sessions, drill_sessions, reading_log), so the checklist cannot drift
from what actually happened.  The module is read-only and cheap enough to
serve on every open.
"""

from datetime import datetime

from app import analytics
from app.pacing import fit_counts

WEEKDAYS = "一二三四五六日"

# Shown in the popup and in the full manual sheet.  Keep each step short: this
# is read on a phone, standing at a desk, not studied like a document.
MANUAL = [
    {
        "title": "每天怎么走",
        "steps": [
            "打开 App，首页第一张卡「今天这么走」就是顺序，跟着「现在做」往下点。",
            "① 打印今天的卷：一份 PDF 装今天要复习的错题 + 今天的新题（按每周 "
            "11 小时摊到每天，复习先占时间）；没时间就点卡片底下的 30/60 分钟短卷，"
            "先保复习，剩下的顺延。",
            "② 错题复习：按到期 → 新错 → 顽固排好，做对/做错两个键；"
            "案例按过程评分。",
            "③ 上传手写：纸上做完的题，选题目 → 拍照，照片挂在这道题上。",
            "④ 记今天四个数（作答 / 做对 / 放弃 / 超时）—— 点一下直接填，"
            "周计划和纠偏都靠它。",
            "新错题：拍题号 → 核对题干/解析 → 勾错因存档；"
            "有精力再做限时拆解 / 认题卡、规范伴读。",
        ],
    },
    {
        "title": "打印怎么用",
        "steps": [
            "首页第 1 步「打印今天的卷」一键出卷；顶部「打印」按钮有更多范围。",
            "题面在前、答案单独成页；第一次先别翻答案部分。",
            "每份文件名带卷名和生成时间，下载后不会互相覆盖。",
            "封面自带结果回填格，做完可以照格子录回 App。",
        ],
    },
    {
        "title": "出差打印一周量",
        "steps": [
            "打印面板选「未来 7 天复习包」：一周复习题按天排好，末尾附本周新题。",
            "每天一页任务单，题目按天分组；答案统一封在最后。",
            "排期只是预测：今天做对/做错会把后面的题提前或推后，回来录结果即可。",
            "最多一次排 100 题（新题先占额度），超出的部分留在下一次打印。",
        ],
    },
    {
        "title": "手机与数据",
        "steps": [
            "电脑双击 start.bat，手机扫二维码进入（同一 Wi-Fi）；start.bat 可设开机自启。",
            "手机浏览器菜单「添加到主屏幕」，用起来接近原生 App。",
            "数据都在 data/；设置页有备份 zip，换电脑先恢复再继续。",
            "照片默认每题留最近 2 张，设置页可清理旧过程照片。",
        ],
    },
]


def _drillable(baseline):
    """Baseline rows that make a timed drill worthwhile right now."""
    rows = ((baseline or {}).get("quadrants") or {}).get("items") or []
    return sum(
        1 for row in rows
        if row.get("quadrant") == "slow"
        or (row.get("quadrant") == "skipped"
            and row.get("triage") == "no_time")
    )


def _review_task(overview, activity):
    queue = overview.get("count") or 0
    planned = overview.get("planned")
    target = queue if planned is None else planned
    graded = activity.get("reviews") or 0
    remaining = max(0, target - graded)
    reasons = overview.get("reasons") or {}
    parts = []
    if reasons.get("due"):
        parts.append("到期 %d" % reasons["due"])
    if reasons.get("new"):
        parts.append("新错 %d" % reasons["new"])
    if reasons.get("stuck"):
        parts.append("顽固 %d" % reasons["stuck"])
    if remaining:
        title = "错题复习 %d 题" % remaining
        detail = "今天计划 %d 题，已做 %d 题 · 约 %d 分钟" % (
            target, graded, overview.get("est_minutes") or remaining * 2)
        if parts:
            detail += " · " + " · ".join(parts)
        progress = "%d/%d" % (min(graded, target), target)
    else:
        title = "错题复习"
        detail = ("今天 %d 题已完成，排期接上了。" % graded if graded
                  else "今天没有到期题；可以加练一组，或记一道新错题。")
        progress = "完成 %d 题" % graded if graded else "0/0"
    return {
        "key": "review",
        "title": title,
        "detail": detail,
        "route": "review",
        "minutes": overview.get("est_minutes") or 0,
        "done": target == 0 or graded >= target,
        "progress": progress,
        "optional": False,
    }


def _drill_task(store, activity, baseline):
    active = store.active_drill()
    drillable = _drillable(baseline)
    if not active and drillable < 5:
        return None
    done = (activity.get("drills") or 0) > 0
    if active:
        title = "续做上次的训练"
        detail = "那一组还没做完；接着练完再开始新的。"
    else:
        title = "限时拆解 / 认题卡 1 组"
        detail = "%d 道可练 · 5 题一组，每题先写首步。" % drillable
    return {
        "key": "drill",
        "title": title,
        "detail": detail,
        "route": "drill",
        "minutes": 40,
        "done": done,
        "optional": True,
    }


def _reading_task(recommendation, activity):
    if not recommendation:
        return None
    section = recommendation.get("section") or ""
    title = "规范伴读 · %s %s" % (recommendation.get("short") or "", section)
    detail = "%s · %s 分钟 · %s" % (
        recommendation.get("title") or "下一站",
        recommendation.get("minutes") or 0,
        recommendation.get("reason") or "",
    )
    return {
        "key": "reading",
        "title": title.strip(),
        "detail": detail,
        "route": "reading",
        "code_key": recommendation.get("code_key"),
        "stop_id": recommendation.get("stop_id"),
        "minutes": recommendation.get("minutes") or 0,
        "done": (activity.get("readings") or 0) > 0,
        "optional": True,
    }


def _practice_task(activity):
    return {
        "key": "practice",
        "title": "记练习台账",
        "detail": "点「记四个数」直接填：作答 / 做对 / 放弃 / 超时。",
        "route": "plan",
        "minutes": 1,
        "done": (activity.get("practices") or 0) > 0,
        "optional": True,
    }


def daily_new_target(plan, now=None):
    """Today's share of the weekly new-question target.

    The same number drives the briefing, the home path and the one-tap day
    sheet, so the printed question count never contradicts the app.
    """
    week = (plan or {}).get("week") or {}
    try:
        target = int(week.get("target") or 0)
        attempted = int(week.get("attempted") or 0)
    except (TypeError, ValueError):
        return 0
    remaining = max(0, target - attempted)
    if not remaining:
        return 0
    now = now or datetime.now().astimezone()
    days_left = max(1, 7 - now.weekday())
    return -(-remaining // days_left)


def daily_budget(plan):
    """Minutes available for today, spread from the weekly-hours setting."""
    hours = ((plan or {}).get("config") or {}).get("weekly_hours")
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return 0
    if hours <= 0:
        return 0
    return max(1, int(round(hours * 60 / 7)))


def daily_new_quota(plan, review_count=0, now=None):
    """Today's new questions: the weekly share, never past the day's budget.

    This is the stop-gate against a Sunday catch-up turning into 20 new
    questions on top of a full review queue.  A short review queue leaves
    the weekly share intact; a long one shrinks today's new count, and the
    rest stays in the weekly plan instead of disappearing.
    """
    share = daily_new_target(plan, now)
    if not share:
        return 0
    budget = daily_budget(plan)
    if not budget:
        return share
    _, fitted = fit_counts(budget, review_count, share)
    return min(share, fitted)


def _plan_task(plan, activity, now, review_count=0):
    """New-question volume from the weekly plan, spread over the days left."""
    week = (plan or {}).get("week") or {}
    try:
        target = int(week.get("target") or 0)
        attempted = int(week.get("attempted") or 0)
    except (TypeError, ValueError):
        target, attempted = 0, 0
    if target <= 0:
        return _practice_task(activity)
    remaining = max(0, target - attempted)
    days_left = max(1, 7 - now.weekday())
    daily = daily_new_quota(plan, review_count, now)
    recorded = (activity.get("practices") or 0) > 0
    progress = "%d/%d" % (min(attempted, target), target)
    if remaining <= 0:
        title = "本周题量已完成"
        detail = "本周 %d/%d 题；可以清复习或加练一组。" % (attempted, target)
        done = True
    elif recorded:
        title = "新题练习（今天已记台账）"
        detail = "本周 %d/%d 题；今天的四个数已经进台账。" % (attempted, target)
        done = True
    else:
        title = "新题练习 今天约 %d 题" % daily
        detail = "本周 %d/%d 题，剩 %d 天；做完点「记四个数」直接填。" % (
            attempted, target, days_left)
        done = False
    return {
        "key": "plan",
        "title": title,
        "detail": detail,
        "route": "plan",
        "minutes": 0 if done else daily * 9,
        "done": done,
        "progress": progress,
        "optional": True,
    }


def build(store, settings, *, now=None, reading=None, baseline=None, plan=None):
    now = now or datetime.now().astimezone()
    day = now.strftime("%Y-%m-%d")
    activity = store.day_activity(day)
    overview = store.review_overview(now)
    recommendation = ((reading or {}).get("recommendation")
                      if isinstance(reading, dict) else None)

    tasks = [_review_task(overview, activity)]
    for candidate in (
        _plan_task(plan, activity, now, overview.get("count") or 0),
        _drill_task(store, activity, baseline),
        _reading_task(recommendation, activity),
    ):
        if candidate:
            tasks.append(candidate)

    remaining = [task for task in tasks if not task["done"]]
    minutes = sum(task.get("minutes") or 0 for task in remaining)
    if remaining:
        lead = "今天还有 %d 项没做，约 %d 分钟。" % (len(remaining), minutes)
        if activity.get("reviews"):
            lead += "已经复习 %d 题，接着来。" % activity["reviews"]
    else:
        lead = "今天的任务都完成了。可以加练一组，或者早点休息。"

    return {
        "day": day,
        "weekday": "周" + WEEKDAYS[now.weekday()],
        "countdown": analytics.countdown(settings, now),
        "activity": activity,
        "review": {
            "count": overview.get("count") or 0,
            "est_minutes": overview.get("est_minutes") or 0,
            "reasons": overview.get("reasons") or {},
        },
        "tasks": tasks,
        "done_count": sum(1 for task in tasks if task["done"]),
        "total_count": len(tasks),
        "lead": lead,
        "manual": MANUAL,
    }
