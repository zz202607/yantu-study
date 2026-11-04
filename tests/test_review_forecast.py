"""Week pack forecast: the same 20-a-day cap, carried forward by a day."""

import sqlite3
from datetime import datetime, timedelta, timezone

from app.store import Store

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 11, 4, 9, 0, tzinfo=TZ)


def iso(moment):
    return moment.isoformat(timespec="seconds")


def add(store, qids):
    rows = [{"qid": qid, "at": iso(BASE - timedelta(days=2)),
             "source": "batch"} for qid in qids]
    assert not store.add_attempts(rows)["errors"]


def schedule(store, qid, moment):
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE attempts SET next_at = ? WHERE qid = ?", (iso(moment), qid)
        )


def test_overdue_backlog_spills_to_the_next_day(tmp_path):
    store = Store(tmp_path)
    qids = ["C1-%d" % n for n in range(1, 26)]
    add(store, qids)
    for qid in qids:
        schedule(store, qid, BASE - timedelta(days=1))

    forecast = store.review_forecast(days=2, now=BASE)
    assert [row["count"] for row in forecast] == [20, 5]
    first, second = set(forecast[0]["qids"]), set(forecast[1]["qids"])
    assert not first & second
    assert len(first | second) == 25
    assert forecast[0]["day"] == "2026-11-04"
    assert forecast[0]["weekday"] == "周三"
    assert forecast[0]["overdue"] is True


def test_future_question_lands_on_its_own_day(tmp_path):
    store = Store(tmp_path)
    add(store, ["C2-1"])
    schedule(store, "C2-1", BASE + timedelta(days=2))

    forecast = store.review_forecast(days=3, now=BASE)
    assert forecast[0]["qids"] == []
    assert forecast[1]["qids"] == []
    assert forecast[2]["qids"] == ["C2-1"]
    assert forecast[0]["overdue"] is False


def test_limit_is_clamped_to_the_daily_cap(tmp_path):
    store = Store(tmp_path)
    add(store, ["C3-1"])
    schedule(store, "C3-1", BASE - timedelta(hours=1))

    forecast = store.review_forecast(days=1, now=BASE, limit=500)
    assert forecast[0]["count"] == 1

    store2 = Store(tmp_path / "other")
    add(store2, ["C3-%d" % n for n in range(1, 24)])
    for n in range(1, 24):
        schedule(store2, "C3-%d" % n, BASE - timedelta(hours=1))
    forecast2 = store2.review_forecast(days=1, now=BASE, limit=500)
    assert forecast2[0]["count"] == 20
