"""Daily briefing: task states come from logs, never from the checklist."""

import sqlite3
from datetime import datetime, timedelta, timezone

from app.briefing import (
    build,
    daily_budget,
    daily_new_quota,
    daily_new_target,
)
from app.settings import Settings
from app.store import Store

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 11, 4, 9, 0, tzinfo=TZ)


def iso(moment):
    return moment.isoformat(timespec="seconds")


def new_store(tmp_path):
    return Store(tmp_path)


def add(store, qids, at=BASE):
    rows = [{"qid": qid, "at": iso(at), "source": "batch"} for qid in qids]
    assert not store.add_attempts(rows)["errors"]


def force_due(store, qid, moment):
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE attempts SET next_at = ? WHERE qid = ?", (iso(moment), qid)
        )


def task(data, key):
    return next(row for row in data["tasks"] if row["key"] == key)


def test_empty_day_marks_review_done_and_keeps_the_manual(tmp_path):
    store = new_store(tmp_path)
    data = build(store, Settings(tmp_path).load(), now=BASE)

    assert data["day"] == "2026-11-04"
    assert data["weekday"] == "周三"
    assert data["tasks"][0]["key"] == "review"
    assert task(data, "review")["done"] is True
    assert task(data, "practice")["done"] is False
    assert data["manual"] and data["manual"][0]["steps"]
    assert data["lead"]


def test_review_task_counts_today_and_flips_when_finished(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    force_due(store, "C1-1", BASE - timedelta(hours=2))

    before = build(store, Settings(tmp_path).load(), now=BASE)
    review = task(before, "review")
    assert review["done"] is False
    assert review["progress"] == "0/1"
    assert before["review"]["count"] == 1

    session = store.start_session(now=BASE)
    store.grade(session["id"], "C1-1", "correct", now=BASE)

    after = build(store, Settings(tmp_path).load(), now=BASE)
    review = task(after, "review")
    assert review["done"] is True
    assert review["progress"] == "完成 1 题"
    assert after["activity"]["reviews"] == 1
    assert after["activity"]["correct"] == 1


def test_partial_review_is_not_marked_done(tmp_path):
    """Grading 3 of a 5-question session must not look like "finished" just
    because the remaining queue shrank."""
    store = new_store(tmp_path)
    qids = ["C1-%d" % n for n in range(1, 6)]
    add(store, qids)
    for qid in qids:
        force_due(store, qid, BASE - timedelta(hours=1))

    session = store.start_session(now=BASE)
    assert len(session["queue"]) == 5
    for qid in qids[:3]:
        store.grade(session["id"], qid, "correct", now=BASE)

    data = build(store, Settings(tmp_path).load(), now=BASE)
    review = task(data, "review")
    assert review["done"] is False
    assert review["progress"] == "3/5"
    assert "已做 3 题" in review["detail"]


def test_plan_task_spreads_the_week_target_over_days_left(tmp_path):
    store = new_store(tmp_path)
    plan = {"week": {"target": 32, "attempted": 10}}
    data = build(store, Settings(tmp_path).load(), now=BASE, plan=plan)
    row = task(data, "plan")
    # 2026-11-04 is a Wednesday: 5 days left, 22 remaining -> 5/day.
    assert row["done"] is False
    assert "今天约 5 题" in row["title"]
    assert "本周 10/32 题" in row["detail"]

    store.add_practice_session(
        day=BASE.date().isoformat(), attempted=5, correct=3, minutes=40
    )
    data = build(store, Settings(tmp_path).load(), now=BASE, plan=plan)
    row = task(data, "plan")
    assert row["done"] is True
    assert "今天已记台账" in row["title"]


def test_practice_and_reading_completion_is_read_from_logs(tmp_path):
    store = new_store(tmp_path)
    store.add_practice_session(
        day=BASE.date().isoformat(), attempted=5, correct=3, minutes=20
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO reading_log"
            " (standard_code, stop_id, at, seconds, status)"
            " VALUES ('GB50011', 's1', ?, 600, 'done')",
            (iso(BASE),),
        )

    reading = {
        "recommendation": {
            "code_key": "GB50011",
            "stop_id": "s2",
            "short": "抗震",
            "section": "5.2",
            "title": "地震影响系数",
            "minutes": 8,
            "reason": "继续上次的路线",
        }
    }
    data = build(store, Settings(tmp_path).load(), now=BASE, reading=reading)
    assert task(data, "practice")["done"] is True
    assert task(data, "reading")["done"] is True
    assert task(data, "reading")["code_key"] == "GB50011"


def test_drill_task_only_when_unfinished_or_drillable(tmp_path):
    store = new_store(tmp_path)
    empty = build(store, Settings(tmp_path).load(), now=BASE)
    assert all(row["key"] != "drill" for row in empty["tasks"])

    baseline = {
        "quadrants": {
            "items": [
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "slow"},
                {"quadrant": "skipped", "triage": "no_time"},
                {"quadrant": "skipped", "triage": "no_idea"},
                {"quadrant": "wrong"},
                {"quadrant": "fast"},
            ]
        }
    }
    data = build(store, Settings(tmp_path).load(), now=BASE, baseline=baseline)
    drill = task(data, "drill")
    assert "5 道可练" in drill["detail"]
    assert drill["done"] is False


def test_daily_new_target_spreads_the_rest_of_the_week():
    plan = {"week": {"target": 35, "attempted": 21}}
    monday = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)
    sunday = datetime(2026, 11, 8, 9, 0, tzinfo=TZ)
    assert daily_new_target(plan, monday) == 2   # 14 / 7 days left
    assert daily_new_target(plan, sunday) == 14  # last day: no spread left


def test_daily_new_target_is_zero_when_the_week_is_done():
    plan = {"week": {"target": 35, "attempted": 35}}
    assert daily_new_target(plan, BASE) == 0
    assert daily_new_target({}, BASE) == 0


def test_daily_quota_is_capped_by_the_time_budget():
    plan = {
        "week": {"target": 20, "attempted": 0},
        "config": {"weekly_hours": 11},
    }
    sunday = datetime(2026, 11, 8, 9, 0, tzinfo=TZ)
    # 94 minutes/day; 16 reviews take 32, leaving 62 -> six new questions.
    assert daily_new_quota(plan, 16, sunday) == 6
    assert daily_new_quota(plan, 0, sunday) == 10  # 94 // 9, share is 20


def test_daily_quota_without_a_config_keeps_the_weekly_share():
    plan = {"week": {"target": 20, "attempted": 0}}
    sunday = datetime(2026, 11, 8, 9, 0, tzinfo=TZ)
    assert daily_new_quota(plan, 16, sunday) == 20


def test_daily_budget_comes_from_weekly_hours():
    assert daily_budget({"config": {"weekly_hours": 11}}) == 94
    assert daily_budget({}) == 0
