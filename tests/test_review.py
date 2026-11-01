"""M3 review scheduling: queue, sessions, grading, undo and summary."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.store import Store

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)


def iso(moment):
    return moment.isoformat(timespec="seconds")


def new_store(tmp_path):
    return Store(tmp_path)


def add(store, qids, at=BASE, tags=None):
    rows = [
        {"qid": qid, "at": iso(at), "tags": tags, "source": "batch"}
        for qid in qids
    ]
    result = store.add_attempts(rows)
    assert not result["errors"]
    return result


def force_due(store, qid, moment):
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE attempts SET next_at = ? WHERE qid = ?", (iso(moment), qid)
        )


def test_new_attempt_starts_with_one_day_interval(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    item = store.get_attempt_by_qid("C1-1")
    assert item["interval_days"] == 1
    assert item["next_at"] == iso(BASE + timedelta(days=1))
    assert item["streak"] == 0
    assert item["reviews"] == 0
    assert item["last_result"] == "wrong"
    assert item["status"] == "active"


def test_correct_review_climbs_the_ladder(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    session = store.start_session(now=BASE + timedelta(days=2))
    assert session["queue"] == ["C1-1"]

    moment = BASE + timedelta(days=2)
    result = store.grade(session["id"], "C1-1", "correct", now=moment)
    item = result["attempt"]
    assert item["interval_days"] == 7
    assert item["next_at"] == iso(moment + timedelta(days=7))
    assert item["streak"] == 1
    assert item["reviews"] == 1
    assert item["times"] == 1
    assert item["last_result"] == "correct"
    assert result["session"]["finished"] is True

    session2 = store.start_session(now=moment + timedelta(days=7))
    assert session2["queue"] == ["C1-1"]
    result2 = store.grade(
        session2["id"], "C1-1", "correct", now=moment + timedelta(days=7)
    )
    item2 = result2["attempt"]
    assert item2["streak"] == 2
    assert item2["status"] == "mastered"
    assert item2["interval_days"] == 15


def test_second_correct_too_soon_does_not_master(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    first = store.start_session(now=BASE + timedelta(days=2))
    store.grade(first["id"], "C1-1", "correct", now=BASE + timedelta(days=2))

    early = BASE + timedelta(days=4)
    force_due(store, "C1-1", early)
    second = store.start_session(now=early)
    result = store.grade(second["id"], "C1-1", "correct", now=early)
    item = result["attempt"]
    assert item["streak"] == 2
    assert item["status"] == "active"
    assert item["interval_days"] == 15


def test_wrong_review_shortens_interval(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    first = store.start_session(now=BASE + timedelta(days=2))
    store.grade(first["id"], "C1-1", "correct", now=BASE + timedelta(days=2))

    late = BASE + timedelta(days=9)
    second = store.start_session(now=late)
    result = store.grade(second["id"], "C1-1", "wrong", now=late)
    item = result["attempt"]
    assert item["interval_days"] == 3
    assert item["streak"] == 0
    assert item["times"] == 2
    assert item["status"] == "active"
    assert item["next_at"] == iso(late + timedelta(days=3))


def test_repeated_wrong_keeps_one_day(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    moment = BASE + timedelta(days=2)
    session = store.start_session(now=moment)
    result = store.grade(session["id"], "C1-1", "wrong", now=moment)
    assert result["attempt"]["interval_days"] == 1


def test_queue_caps_at_twenty_and_interleaves_chapters(tmp_path):
    store = new_store(tmp_path)
    qids = []
    for chapter in range(1, 5):
        qids.extend("C%d-%d" % (chapter, n) for n in range(1, 9))
    add(store, qids, at=BASE - timedelta(days=2))
    queue = store.review_queue(now=BASE)
    assert len(queue) == 20
    heads = [item["qid"].split("-")[0] for item in queue[:4]]
    assert heads == ["C1", "C2", "C3", "C4"]
    for i in range(1, len(queue)):
        assert queue[i - 1]["qid"] != queue[i]["qid"]


def test_queue_skips_mastered_and_adds_fresh_and_stuck(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE attempts SET status = 'mastered' WHERE qid = 'C1-1'")

    # Recorded today, not due until tomorrow: still in today's queue.
    add(store, ["C2-1"])
    # Three recorded errors, already reviewed, not due: a stuck item.
    add(store, ["C3-1"])
    add(store, ["C3-1"])
    add(store, ["C3-1"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE attempts SET reviews = 1, next_at = ? WHERE qid = 'C3-1'",
            (iso(BASE + timedelta(days=5)),),
        )

    queue = store.review_queue(now=BASE)
    by_qid = {item["qid"]: item for item in queue}
    assert "C1-1" not in by_qid
    assert by_qid["C2-1"]["review_reason"] == "new"
    assert by_qid["C3-1"]["review_reason"] == "stuck"


def test_duplicate_grade_does_not_double_schedule(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1", "C2-1"])
    moment = BASE + timedelta(days=2)
    session = store.start_session(now=moment)
    first = store.grade(session["id"], "C1-1", "correct", now=moment)
    assert first["duplicate"] is False

    again = store.grade(session["id"], "C1-1", "wrong", now=moment)
    assert again["duplicate"] is True
    item = again["attempt"]
    assert item["streak"] == 1
    assert item["times"] == 1
    assert item["last_result"] == "correct"


def test_skip_advances_without_grading(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1", "C2-1"])
    moment = BASE + timedelta(days=2)
    session = store.start_session(now=moment)
    after = store.skip(session["id"], "C1-1", now=moment)
    assert after["cursor"] == 1
    assert after["graded_count"] == 0
    assert after["current_qid"] == "C2-1"
    summary = store.session_summary(session["id"], now=moment)
    assert summary["graded"] == 0
    assert summary["skipped"] == 2


def test_undo_restores_schedule_and_tags(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"], tags=["calc"])
    moment = BASE + timedelta(days=2)
    session = store.start_session(now=moment)
    store.grade(
        session["id"], "C1-1", "wrong", tags=["concept"], now=moment
    )
    graded = store.get_attempt_by_qid("C1-1")
    assert graded["times"] == 2
    assert graded["tags"] == ["concept"]
    assert graded["reviews"] == 1

    undone = store.undo(session["id"], now=moment)
    assert undone["undone"] == "C1-1"
    assert undone["session"]["cursor"] == 0
    assert undone["session"]["current_qid"] == "C1-1"
    assert undone["session"]["finished"] is False
    restored = store.get_attempt_by_qid("C1-1")
    assert restored["times"] == 1
    assert restored["tags"] == ["calc"]
    assert restored["reviews"] == 0
    assert restored["interval_days"] == 1
    assert restored["streak"] == 0
    assert restored["next_at"] == iso(BASE + timedelta(days=1))


def test_undo_after_question_deleted_only_rewinds_session(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    moment = BASE + timedelta(days=2)
    session = store.start_session(now=moment)
    store.grade(session["id"], "C1-1", "correct", now=moment)
    item = store.get_attempt_by_qid("C1-1")
    assert store.delete_attempt(item["id"])

    undone = store.undo(session["id"], now=moment)
    assert undone["undone"] == "C1-1"
    assert undone["session"]["cursor"] == 0
    assert store.get_attempt_by_qid("C1-1") is None


def test_summary_reports_data_and_next_due(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1", "C2-1"])
    moment = BASE + timedelta(days=2)
    session = store.start_session(now=moment)
    store.grade(session["id"], "C1-1", "correct", now=moment)
    store.grade(session["id"], "C2-1", "wrong", now=moment)

    summary = store.session_summary(session["id"], now=moment)
    assert summary["queue"] == 2
    assert summary["graded"] == 2
    assert summary["correct"] == 1
    assert summary["wrong"] == 1
    assert summary["accuracy"] == 0.5
    assert summary["finished"] is True
    assert summary["chapters"] == ["C1", "C2"]
    assert summary["next_due"]
    assert summary["next_due_count"] >= 1
    assert summary["duration"] >= 0


def test_existing_m2_database_migrates_in_place(tmp_path):
    db = tmp_path / "wrongbook.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256 TEXT NOT NULL UNIQUE, orig TEXT NOT NULL, thumb TEXT NOT NULL,
            width INTEGER NOT NULL, height INTEGER NOT NULL,
            taken_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT NOT NULL UNIQUE, family TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'single', note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active', times INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, last_at TEXT NOT NULL,
            photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL
        );
        CREATE TABLE attempt_tags (
            attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
            tag TEXT NOT NULL, PRIMARY KEY (attempt_id, tag)
        );
        INSERT INTO attempts (qid, family, created_at, last_at)
        VALUES ('C4-12', 'C', '2026-03-11T10:00:00+08:00', '2026-03-11T10:00:00+08:00');
        """
    )
    conn.commit()
    conn.close()

    store = Store(tmp_path)
    item = store.get_attempt_by_qid("C4-12")
    assert item["interval_days"] == 1
    assert item["next_at"] == "2026-03-11T10:00:00+08:00"
    assert item["streak"] == 0
    assert item["reviews"] == 0
    assert item["status"] == "active"
    with sqlite3.connect(db) as check:
        version = check.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "2"


def test_remark_wrong_resets_a_mastered_question(tmp_path):
    store = new_store(tmp_path)
    add(store, ["C1-1"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE attempts SET status = 'mastered', interval_days = 15,"
            " streak = 2, reviews = 2, next_at = ? WHERE qid = 'C1-1'",
            (iso(BASE + timedelta(days=30)),),
        )
    add(store, ["C1-1"])
    item = store.get_attempt_by_qid("C1-1")
    assert item["status"] == "active"
    assert item["times"] == 2
    assert item["streak"] == 0
    assert item["interval_days"] == 3
    assert item["last_result"] == "wrong"


def test_session_resume_and_empty_state(tmp_path):
    store = new_store(tmp_path)
    empty = store.start_session(now=BASE)
    assert empty["id"] is None
    assert empty["queue"] == []

    add(store, ["C1-1"])
    moment = BASE + timedelta(days=2)
    first = store.start_session(now=moment)
    resumed = store.start_session(now=moment + timedelta(hours=2))
    assert resumed["id"] == first["id"]
    assert resumed["queue"] == ["C1-1"]
