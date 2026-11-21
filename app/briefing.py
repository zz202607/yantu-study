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
            "打开 App，首页「今天的卷」只有两步：先打印，做完回来核对。",
            "① 打印今天的卷：App 自己知道哪些是复习、哪些是新题，"
            "按每周可投入时间算好张数；没时间就用 30/60 分钟短卷。",
            "② 做完核对：点「做完核对」→ 默认都算做对，只把没做对的题点成"
            "「错」并选错因；也可以拍一张卷面。",
            "保存后自动归档：错题入本、复习排期、练习总量都自动写，"
            "不用再填四个数。",
            "没打印的日子：点「直接在 App 里复习」；新错题也可以单独用「拍题」记。",
            "有精力再做限时拆解 / 认题卡、规范伴读。",
        ],
    },
    {
        "title": "系统怎么运作",
        "steps": [
            "错题要先录一次才会进复习：在 App 里复习点「没做对」会自动记；"
            "纸上做的用「拍题」把题号拍进来，勾选后一次记一页，不用一题一题录。",
            "每题只有一行账：错几次、上次对错、下次哪天复习；同一题再记只加次数。",
            "每天排队顺序：先到期，再 3 天内新错，最后最多 3 道顽固题；每天上限 20 题，"
            "超出的自动顺延。",
            "复习结果自动排下次：做对 7 → 15 → 30 天；连续两次做对且间隔 ≥7 天才算掌握；"
            "做错回到 1 天（长间隔后再错给 3 天）。",
            "新题量：阶段周目标（现在 20 题/周）÷ 剩余天数，再受每天约 94 分钟的时间预算限制；"
            "题目按未完成原型从题库里选。",
            "练习四个数不用手填：今日卷核对时按题目结果自动算；"
            "只有没打印、自己另做的练习，才在计划页手动记一次。",
        ],
    },
    {
        "title": "打印怎么用",
        "steps": [
            "首页「今天的卷」点一下就能打印；顶部「打印」按钮有更多范围。"
            "打印会记住这张卷的题号，核对时不用再找题。",
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
        "active_id": active.get("id") if active else None,
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
        "detail": "作答 / 做对 / 放弃 / 超时 · 只算新题和套卷，复习自动记。",
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


def _plan_task(plan, activity, now, review_count=0, pack=None):
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
    if (pack and pack.get("closed")
            and not pack.get("summary", {}).get("superseded")):
        return {
            "key": "plan",
            "title": "今日卷已自动归档",
            "detail": "错题、复习排期和练习总量都已写入，不用再手填四个数。",
            "route": "pack",
            "minutes": 0,
            "progress": "%d/%d" % (min(attempted, target), target),
            "done": True,
            "optional": True,
        }
    if pack and not pack.get("closed"):
        return {
            "key": "plan",
            "title": "做完今日卷再核对",
            "detail": "核对归档后，错题和练习量会自动写入。",
            "route": "pack",
            "minutes": 0,
            "progress": "%d/%d" % (min(attempted, target), target),
            "done": False,
            "optional": True,
        }
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
        detail = "本周 %d/%d 题，剩 %d 天；做完点第 4 步直接填。" % (
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


def _retest_todo(retest, now):
    """The December full-paper retest, shown until both halves are recorded."""
    if not retest:
        return None
    halves = {row.get("half"): row for row in retest.get("halves") or []}
    pending = [half for half in ("A", "P")
               if not (halves.get(half) or {}).get("recorded")]
    if not pending:
        return None
    due = now.replace(month=12, day=15, hour=0, minute=0, second=0,
                      microsecond=0)
    days = (due.date() - now.date()).days
    if days > 0:
        detail = "12 月 15 日 · 还剩 %d 天；先打印两半题面，答案封存。" % days
    elif days == 0:
        detail = "就是今天：两半各 180 分钟，做完再记四个数。"
    else:
        detail = "已过计划日 %d 天；尽快补做，2025 已做基线。" % abs(days)
    return {
        "key": "retest_2024",
        "title": "2024 整卷复测",
        "detail": detail,
        "action": "retest",
        "days_left": days,
        "pending": pending,
    }


def _backlog(store, baseline, retest, now):
    """Long-term leftovers the daily sheet should keep putting in front.

    These are not "today's tasks": they stay until the underlying state
    changes (triage row written, question in the wrong book, drill finished,
    retest half recorded), so they cannot be ticked off by opening a page.
    """
    items = (((baseline or {}).get("quadrants") or {}).get("items")) or []
    skipped = [row for row in items if row.get("quadrant") == "skipped"]
    untriaged = [row for row in skipped if not row.get("triage")]
    no_idea = [row for row in skipped if row.get("triage") == "no_idea"]
    no_idea_new = [row for row in no_idea
                   if not store.get_attempt_by_qid(row.get("qid"))]
    no_time = [row for row in skipped if row.get("triage") == "no_time"]
    slow = [row for row in items if row.get("quadrant") == "slow"]

    out = []
    if untriaged:
        out.append({
            "key": "triage_skipped",
            "title": "%d 道放弃题还没分流" % len(untriaged),
            "detail": "点「没时间」或「不会」，一题一秒；分完才知道该补哪边。",
            "action": "triage_skipped",
            "count": len(untriaged),
        })
    elif no_idea_new:
        out.append({
            "key": "import_no_idea",
            "title": "%d 道「不会」的题还没入错题本" % len(no_idea_new),
            "detail": "分流已经做完；入本后才会按 1/7/15/30 天安排复习。",
            "action": "import_no_idea",
            "count": len(no_idea_new),
        })

    pool = slow + no_time
    active = store.active_drill("timed")
    finished = store.list_drills(limit=5, mode="timed", finished=True)
    drill_done = any(row.get("source") in ("slow", "skipped")
                     for row in finished)
    if len(pool) >= 5 and not active and not drill_done:
        # Prefer the five overtime questions; fall back to the "ran out of
        # time" pile when there are not enough slow ones for a 5-question set.
        source = "slow" if len(slow) >= 5 else "skipped"
        out.append({
            "key": "drill_baseline",
            "title": "超时 / 没时间题 %d 道，练一组" % len(pool),
            "detail": "5 题 × 8 分钟硬停，每题先写首步；计时到点就停。",
            "action": "drill_baseline",
            "source": source,
            "count": min(5, len(pool)),
        })

    retest_todo = _retest_todo(retest, now)
    if retest_todo:
        out.append(retest_todo)
    return out


def build(store, settings, *, now=None, reading=None, baseline=None,
          plan=None, retest=None):
    now = now or datetime.now().astimezone()
    day = now.strftime("%Y-%m-%d")
    activity = store.day_activity(day)
    overview = store.review_overview(now)
    recommendation = ((reading or {}).get("recommendation")
                      if isinstance(reading, dict) else None)

    pack = store.latest_day_pack(day)
    tasks = [_review_task(overview, activity)]
    for candidate in (
        _plan_task(plan, activity, now, overview.get("count") or 0, pack),
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
        "backlog": _backlog(store, baseline, retest, now),
        "done_count": sum(1 for task in tasks if task["done"]),
        "total_count": len(tasks),
        "lead": lead,
        "manual": MANUAL,
    }
