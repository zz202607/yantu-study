"""Wrong-book store: one SQLite file plus an attachment directory.

User data never mixes with the generated index (decision D5).  Everything the
app owns lives under one data root, small enough to zip and restore:

    wrongbook.db                        attempts, tags, photo index
    attachments/orig/<ab>/<sha>.jpg     EXIF-normalised copies (max 3200 px)
    attachments/thumb/<ab>/<sha>.jpg    thumbnails (640 px)
    backups/wrongbook-*.zip             one-click backups

A question appears at most once (UNIQUE qid); marking it wrong again bumps
`times` and refreshes `last_at` instead of cloning a row.  Legacy photo import
skips qids that are already known, which keeps re-imports idempotent.
"""

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps

TAGS = {
    "concept": "不会",
    "calc": "算错",
    "standard": "规范错",
    "misread": "审题错",
}
TAG_KEYS = tuple(TAGS)
STATUSES = ("active", "mastered")
SOURCES = ("batch", "single", "import")

# Case questions (C/A/M) are subjective and scored on the written process.
# These statuses are chosen by the user, never inferred from an option letter.
# Knowledge questions (Z/K) remain objective and use plain correct/wrong.
PROCESS_LABELS = {
    "process_right_result_right": "过程对 · 结果对",
    "process_right_result_wrong": "过程对 · 结果错",
    "process_wrong": "过程错",
    "no_idea": "没思路",
}
PROCESS_RESULT = {
    "process_right_result_right": "correct",
    "process_right_result_wrong": "wrong",
    "process_wrong": "wrong",
    "no_idea": "wrong",
}
PROCESS_STATUSES = tuple(PROCESS_RESULT)

PRACTICE_SOURCES = {
    "real_exam": "真题",
    "exercise": "习题",
    "mock": "模拟",
    "other": "其他",
}
PLAN_CATEGORIES = tuple("C%d" % i for i in range(1, 11)) + ("mixed", "knowledge")
PLAN_STATUSES = ("todo", "doing", "done")

# M3 scheduling.  A wrong answer comes back in a day (three when it was a
# relapse after a long gap); correct answers climb a short ladder.  Mastery
# needs two correct reviews at least seven days apart, so a single lucky pass
# can never retire a question.
REVIEW_DAY_LIMIT = 20
NEW_ERROR_WINDOW_DAYS = 3
STUCK_TIMES = 3
STUCK_IN_QUEUE = 3
CORRECT_LADDER = (7, 15, 30)

# A phone photo of a question/process page is legible well below camera
# resolution.  2400 px q85 is the storage/legibility compromise; thumbnails
# are only for list rows.
PHOTO_MAX_SIDE = 2400
PHOTO_JPEG_QUALITY = 85
THUMB_MAX_SIDE = 480
THUMB_JPEG_QUALITY = 78
NOTE_MAX = 2000
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    orig TEXT NOT NULL,
    thumb TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    taken_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qid TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'single',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    times INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_at TEXT NOT NULL,
    photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    interval_days INTEGER NOT NULL DEFAULT 1,
    next_at TEXT,
    streak INTEGER NOT NULL DEFAULT 0,
    reviews INTEGER NOT NULL DEFAULT 0,
    last_review_at TEXT,
    last_result TEXT,
    process_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    last_process_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_last ON attempts(last_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_family ON attempts(family);
CREATE TABLE IF NOT EXISTS attempt_tags (
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (attempt_id, tag)
);
CREATE TABLE IF NOT EXISTS review_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    queue TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON review_sessions(day, id DESC);
CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'exercise',
    category TEXT NOT NULL DEFAULT 'mixed',
    attempted INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    abandoned INTEGER NOT NULL DEFAULT 0,
    overtime INTEGER NOT NULL DEFAULT 0,
    minutes INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    baseline_half TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_practice_day ON practice_sessions(day, id DESC);
CREATE TABLE IF NOT EXISTS plan_progress (
    key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'todo',
    accuracy REAL,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baseline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    half TEXT NOT NULL,
    day TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 180,
    answered INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0,
    overtime INTEGER NOT NULL DEFAULT 0,
    items_done INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (year, half)
);
CREATE TABLE IF NOT EXISTS baseline_items (
    run_id INTEGER NOT NULL REFERENCES baseline_runs(id) ON DELETE CASCADE,
    qid TEXT NOT NULL,
    seq INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    overtime INTEGER NOT NULL DEFAULT 0,
    process_status TEXT,
    PRIMARY KEY (run_id, qid)
);
CREATE TABLE IF NOT EXISTS review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES review_sessions(id) ON DELETE CASCADE,
    attempt_id INTEGER NOT NULL,
    qid TEXT NOT NULL,
    at TEXT NOT NULL,
    result TEXT NOT NULL,
    interval_before INTEGER,
    interval_after INTEGER,
    streak_before INTEGER,
    streak_after INTEGER,
    status_before TEXT,
    status_after TEXT,
    times_before INTEGER,
    times_after INTEGER,
    next_before TEXT,
    next_after TEXT,
    last_at_before TEXT,
    last_at_after TEXT,
    last_review_before TEXT,
    last_review_after TEXT,
    last_result_before TEXT,
    last_result_after TEXT,
    tags_before TEXT,
    tags_after TEXT,
    process_status_before TEXT,
    process_status_after TEXT,
    process_photo_before INTEGER,
    process_photo_after INTEGER,
    UNIQUE (session_id, qid)
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '4');
"""


def now_iso(moment=None):
    return (moment or datetime.now().astimezone()).isoformat(timespec="seconds")


def _parse_iso(value):
    """Best-effort parse; malformed stamps never break a scheduling decision."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _add_days(value, days):
    parsed = _parse_iso(value) or datetime.now().astimezone()
    return (parsed + timedelta(days=days)).isoformat(timespec="seconds")


def _chapter_key(qid):
    """Group key for chapter interleaving: C3, Z10, A2015-P, M12."""
    parts = (qid or "").split("-")
    if len(parts) >= 2 and parts[0][0] in "AK":
        return parts[0] + "-" + parts[1]
    return parts[0]


def _next_correct_interval(current):
    for step in CORRECT_LADDER:
        if current < step:
            return step
    return CORRECT_LADDER[-1]


def _next_wrong_interval(current):
    # A first failure is retried tomorrow; a relapse after a long interval
    # gets a little more room so it is not a nightly grind.
    return 1 if (current or 0) <= 3 else 3


def _local_day(moment=None):
    return (moment or datetime.now().astimezone()).strftime("%Y-%m-%d")


def _clean_note(note):
    text = (note or "").strip()
    if len(text) > NOTE_MAX:
        raise ValueError("备注太长（上限 %d 字）" % NOTE_MAX)
    return text


def _normalize_tags(tags):
    if tags is None:
        return None
    if not isinstance(tags, (list, tuple, set)):
        raise ValueError("标签格式不对")
    seen = []
    for tag in tags:
        key = str(tag).strip().lower()
        if key not in TAGS:
            raise ValueError("未知错因标签：%s" % tag)
        if key not in seen:
            seen.append(key)
    return seen


def _exif_taken_at(img):
    try:
        raw = img.getexif().get(36867)  # DateTimeOriginal
        if not raw:
            return None
        return now_iso(datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").astimezone())
    except Exception:  # noqa: BLE001 - a bad EXIF stamp must not block a photo
        return None


class Store:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.db_path = self.root / "wrongbook.db"
        self.orig_dir = self.root / "attachments" / "orig"
        self.thumb_dir = self.root / "attachments" / "thumb"
        self.backup_dir = self.root / "backups"
        self._lock = threading.RLock()
        self._init()

    # -- plumbing ---------------------------------------------------------

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn):
        """Bring older databases up to the schedule + case-process columns."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(attempts)")}
        additions = {
            "interval_days": "INTEGER NOT NULL DEFAULT 1",
            "next_at": "TEXT",
            "streak": "INTEGER NOT NULL DEFAULT 0",
            "reviews": "INTEGER NOT NULL DEFAULT 0",
            "last_review_at": "TEXT",
            "last_result": "TEXT",
            "process_photo_id": "INTEGER REFERENCES photos(id) ON DELETE SET NULL",
            "last_process_status": "TEXT",
        }
        added_next = False
        for name, decl in additions.items():
            if name not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN %s %s" % (name, decl))
                added_next = added_next or name == "next_at"
        if added_next:
            # M2 rows carry no schedule: treat the record time as the due date,
            # so yesterday's errors are waiting today.
            conn.execute("UPDATE attempts SET next_at = last_at WHERE next_at IS NULL")
        log_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(review_log)")
        }
        log_additions = {
            "process_status_before": "TEXT",
            "process_status_after": "TEXT",
            "process_photo_before": "INTEGER",
            "process_photo_after": "INTEGER",
        }
        for name, decl in log_additions.items():
            if name not in log_columns:
                conn.execute(
                    "ALTER TABLE review_log ADD COLUMN %s %s" % (name, decl)
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attempts_next ON attempts(next_at)"
        )
        practice_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(practice_sessions)")
        }
        if "baseline_half" not in practice_columns:
            conn.execute(
                "ALTER TABLE practice_sessions ADD COLUMN baseline_half TEXT"
            )
        # One baseline half owns at most one ledger row, so editing a score
        # updates the same week entry instead of inflating the plan.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_baseline"
            " ON practice_sessions(baseline_half)"
            " WHERE baseline_half IS NOT NULL"
        )
        conn.execute(
            "UPDATE meta SET value = '5' WHERE key = 'schema_version'"
        )

    # -- photos -----------------------------------------------------------

    def photo(self, sha):
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM photos WHERE sha256 = ?", (sha,)).fetchone()
        finally:
            conn.close()
        return self._photo_dict(row) if row else None

    def _photo_dict(self, row):
        return {
            "sha256": row["sha256"],
            "thumb": "/api/photos/%s?size=thumb" % row["sha256"],
            "orig": "/api/photos/%s?size=orig" % row["sha256"],
            "width": row["width"],
            "height": row["height"],
            "taken_at": row["taken_at"],
            "created_at": row["created_at"],
        }

    def photo_file(self, sha, size="orig"):
        if not SHA_RE.match(sha or ""):
            return None
        row = self.photo(sha)
        if not row:
            return None
        rel = self._photo_rel(sha, size)
        path = self.root / rel
        return path if path.exists() else None

    def _photo_rel(self, sha, size="orig"):
        folder = "thumb" if size == "thumb" else "orig"
        return "attachments/%s/%s/%s.jpg" % (folder, sha[:2], sha)

    def save_photo(self, data, taken_at=None):
        """Normalise, thumbnail and store one image; identical bytes reuse a row."""
        if not data:
            raise ValueError("空文件")
        sha = hashlib.sha256(data).hexdigest()
        existing = self.photo(sha)
        if existing:
            return existing

        try:
            img = Image.open(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001 - whatever the phone sends
            raise ValueError("无法读取图片：%s" % exc)

        # The camera's own stamp outranks the file mtime; both outrank "now".
        stamped = _exif_taken_at(img) or taken_at
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.size) > PHOTO_MAX_SIDE:
            img = img.copy()
            img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE), Image.LANCZOS)

        orig_path = self.root / self._photo_rel(sha, "orig")
        thumb_path = self.root / self._photo_rel(sha, "thumb")
        for path in (orig_path, thumb_path):
            path.parent.mkdir(parents=True, exist_ok=True)

        self._write_jpeg(img, orig_path, quality=PHOTO_JPEG_QUALITY)
        thumb = img.copy()
        thumb.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE), Image.LANCZOS)
        self._write_jpeg(thumb, thumb_path, quality=THUMB_JPEG_QUALITY)

        moment = now_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO photos"
                    " (sha256, orig, thumb, width, height, taken_at, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        sha,
                        self._photo_rel(sha, "orig"),
                        self._photo_rel(sha, "thumb"),
                        img.width,
                        img.height,
                        stamped or moment,
                        moment,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return self.photo(sha)

    @staticmethod
    def _write_jpeg(img, path, quality):
        tmp = path.with_suffix(".tmp")
        img.save(tmp, "JPEG", quality=quality, optimize=True)
        os.replace(tmp, path)

    def save_photo_file(self, path, taken_at=None):
        data = Path(path).read_bytes()
        if not taken_at:
            try:
                taken_at = now_iso(datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone())
            except OSError:
                taken_at = None
        return self.save_photo(data, taken_at=taken_at)

    _PHOTO_REFS = (
        "SELECT photo_id FROM attempts WHERE photo_id IS NOT NULL"
        " UNION SELECT process_photo_id FROM attempts"
        "       WHERE process_photo_id IS NOT NULL"
        " UNION SELECT process_photo_before FROM review_log"
        "       WHERE process_photo_before IS NOT NULL"
        " UNION SELECT process_photo_after FROM review_log"
        "       WHERE process_photo_after IS NOT NULL"
        " UNION SELECT photo_id FROM baseline_runs WHERE photo_id IS NOT NULL"
    )

    @staticmethod
    def _dir_bytes(path):
        total = 0
        root = Path(path)
        if not root.exists():
            return 0
        for item in root.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total

    def storage_stats(self):
        """Disk usage for the one data root, including the DB and backups."""
        conn = self._connect()
        try:
            photo_count = conn.execute("SELECT COUNT(*) AS n FROM photos").fetchone()["n"]
            orphan = conn.execute(
                "SELECT COUNT(*) AS n FROM photos WHERE id NOT IN (%s)"
                % self._PHOTO_REFS
            ).fetchone()["n"]
        finally:
            conn.close()
        attachments = self.root / "attachments"
        backups = self.root / "backups"
        exports = self.root / "exports"
        db_bytes = 0
        for name in ("wrongbook.db", "wrongbook.db-wal", "wrongbook.db-shm"):
            path = self.root / name
            if path.exists():
                try:
                    db_bytes += path.stat().st_size
                except OSError:
                    pass
        attachments_bytes = self._dir_bytes(attachments)
        backups_bytes = self._dir_bytes(backups)
        exports_bytes = self._dir_bytes(exports)
        return {
            "photos": photo_count,
            "orphan_photos": orphan,
            "attachments_bytes": attachments_bytes,
            "backups_bytes": backups_bytes,
            "exports_bytes": exports_bytes,
            "database_bytes": db_bytes,
            "total_bytes": (attachments_bytes + backups_bytes + exports_bytes
                            + db_bytes),
        }

    def prune_process_photos(self, keep=2):
        """Keep only the latest N process photos per attempt; backup first.

        Marking photos (`attempts.photo_id`) are never removed.  Old process
        references in review_log are cleared, then orphan files are deleted.
        This is destructive by design; the caller must confirm.
        """
        try:
            keep = int(keep)
        except (TypeError, ValueError):
            raise ValueError("保留张数必须是整数")
        if not 0 <= keep <= 5:
            raise ValueError("每题保留张数应在 0–5 之间")
        before = self.storage_stats()
        backup_path = self.backup() if before["photos"] else None
        removed = 0
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    attempts = conn.execute(
                        "SELECT id, process_photo_id FROM attempts"
                    ).fetchall()
                    for attempt in attempts:
                        logs = conn.execute(
                            "SELECT id, process_photo_before, process_photo_after"
                            " FROM review_log WHERE attempt_id = ? ORDER BY id DESC",
                            (attempt["id"],),
                        ).fetchall()
                        keep_ids = []
                        for log in logs:
                            if log["process_photo_after"] and log["process_photo_after"] not in keep_ids:
                                keep_ids.append(log["process_photo_after"])
                        if attempt["process_photo_id"] and attempt["process_photo_id"] not in keep_ids:
                            keep_ids.insert(0, attempt["process_photo_id"])
                        kept = set(keep_ids[:keep])
                        all_ids = set()
                        for log in logs:
                            for key in ("process_photo_before", "process_photo_after"):
                                if log[key]:
                                    all_ids.add(log[key])
                        if attempt["process_photo_id"]:
                            all_ids.add(attempt["process_photo_id"])
                        drop = all_ids - kept
                        if drop:
                            marks = ",".join("?" for _ in drop)
                            conn.execute(
                                "UPDATE review_log SET"
                                " process_photo_before = CASE"
                                "   WHEN process_photo_before IN (%s) THEN NULL"
                                "   ELSE process_photo_before END,"
                                " process_photo_after = CASE"
                                "   WHEN process_photo_after IN (%s) THEN NULL"
                                "   ELSE process_photo_after END"
                                " WHERE attempt_id = ?" % (marks, marks),
                                tuple(drop) + tuple(drop) + (attempt["id"],),
                            )
                        conn.execute(
                            "UPDATE attempts SET process_photo_id = ? WHERE id = ?",
                            (keep_ids[0] if keep_ids and keep > 0 else None,
                             attempt["id"]),
                        )
            finally:
                conn.close()
        removed = self.prune_orphan_photos()
        after = self.storage_stats()
        return {
            "removed_photos": removed,
            "keep_per_attempt": keep,
            "backup": str(backup_path) if backup_path else None,
            "freed_bytes": max(0, before["attachments_bytes"] - after["attachments_bytes"]),
            "before": before,
            "after": after,
        }

    def prune_orphan_photos(self):
        """Drop preview-only photos that never landed in an attempt or review."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM photos WHERE id NOT IN (%s)" % self._PHOTO_REFS
                ).fetchall()
                if not rows:
                    return 0
                conn.execute(
                    "DELETE FROM photos WHERE id NOT IN (%s)" % self._PHOTO_REFS
                )
                conn.commit()
            finally:
                conn.close()
        for row in rows:
            for rel in (row["orig"], row["thumb"]):
                try:
                    (self.root / rel).unlink()
                except OSError:
                    pass
        return len(rows)

    # -- attempts ---------------------------------------------------------

    def add_attempts(self, rows, skip_existing=False):
        """Insert or refresh attempts.  Tags replace, notes only when provided."""
        created, updated, skipped, errors = [], [], [], []
        moment = now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:  # one transaction for the whole batch
                    for row in rows:
                        qid = (row.get("qid") or "").strip().upper()
                        if not qid:
                            errors.append({"qid": qid, "detail": "空题号"})
                            continue
                        try:
                            tags = _normalize_tags(row.get("tags"))
                        except ValueError as exc:
                            errors.append({"qid": qid, "detail": str(exc)})
                            continue
                        source = row.get("source") or "single"
                        if source not in SOURCES:
                            errors.append({"qid": qid, "detail": "未知来源 %s" % source})
                            continue
                        try:
                            note = row.get("note")
                            note = None if note is None else _clean_note(note)
                        except ValueError as exc:
                            errors.append({"qid": qid, "detail": str(exc)})
                            continue
                        photo_id = None
                        sha = row.get("photo_sha")
                        if sha:
                            found = conn.execute(
                                "SELECT id FROM photos WHERE sha256 = ?", (sha,)
                            ).fetchone()
                            if found:
                                photo_id = found["id"]
                            else:
                                errors.append({"qid": qid, "detail": "照片不存在：%s" % sha})
                                continue
                        at = row.get("at") or moment

                        existing = conn.execute(
                            "SELECT * FROM attempts WHERE qid = ?", (qid,)
                        ).fetchone()
                        if existing:
                            if skip_existing:
                                skipped.append(qid)
                                continue
                            # Marking a question wrong again is another failure:
                            # it resets the streak, re-activates a mastered row
                            # and re-enters the schedule at the short interval.
                            interval = _next_wrong_interval(
                                existing["interval_days"] or 1
                            )
                            conn.execute(
                                "UPDATE attempts SET times = times + 1, last_at = ?,"
                                " source = ?, note = CASE WHEN ? IS NULL THEN note ELSE ? END,"
                                " photo_id = COALESCE(?, photo_id),"
                                " status = 'active', interval_days = ?, next_at = ?,"
                                " streak = 0, last_result = 'wrong'"
                                " WHERE id = ?",
                                (
                                    at,
                                    source,
                                    note,
                                    note,
                                    photo_id,
                                    interval,
                                    _add_days(at, interval),
                                    existing["id"],
                                ),
                            )
                            if tags is not None:
                                self._set_tags(conn, existing["id"], tags)
                            updated.append(qid)
                        else:
                            cur = conn.execute(
                                "INSERT INTO attempts"
                                " (qid, family, source, note, status, times,"
                                "  created_at, last_at, photo_id, interval_days,"
                                "  next_at, streak, reviews, last_result)"
                                " VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?, 1, ?, 0, 0, 'wrong')",
                                (
                                    qid,
                                    qid[0],
                                    source,
                                    note or "",
                                    at,
                                    at,
                                    photo_id,
                                    _add_days(at, 1),
                                ),
                            )
                            if tags:
                                self._set_tags(conn, cur.lastrowid, tags)
                            created.append(qid)
                ids = []
                for qid in created + updated:
                    row = conn.execute(
                        "SELECT id FROM attempts WHERE qid = ?", (qid,)
                    ).fetchone()
                    if row:
                        ids.append(row["id"])
                items = self._items(conn, ids)
            finally:
                conn.close()
        by_qid = {item["qid"]: item for item in items}
        return {
            "created": [by_qid.get(q) for q in created if by_qid.get(q)],
            "updated": [by_qid.get(q) for q in updated if by_qid.get(q)],
            "skipped": skipped,
            "errors": errors,
        }

    def _set_tags(self, conn, attempt_id, tags):
        conn.execute("DELETE FROM attempt_tags WHERE attempt_id = ?", (attempt_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO attempt_tags(attempt_id, tag) VALUES (?, ?)",
            [(attempt_id, tag) for tag in tags],
        )

    def _items(self, conn, ids):
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT * FROM attempts WHERE id IN (%s)" % marks, ids
        ).fetchall()
        order = {attempt_id: i for i, attempt_id in enumerate(ids)}
        tags = {}
        photos = {}
        for row in conn.execute(
            "SELECT attempt_id, tag FROM attempt_tags WHERE attempt_id IN (%s)" % marks,
            ids,
        ):
            tags.setdefault(row["attempt_id"], []).append(row["tag"])
        photo_ids = [r["photo_id"] for r in rows if r["photo_id"]]
        photo_ids += [r["process_photo_id"] for r in rows if r["process_photo_id"]]
        photo_ids = list(dict.fromkeys(photo_ids))
        if photo_ids:
            pmarks = ",".join("?" for _ in photo_ids)
            for row in conn.execute(
                "SELECT * FROM photos WHERE id IN (%s)" % pmarks, photo_ids
            ):
                photos[row["id"]] = self._photo_dict(row)
        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "qid": row["qid"],
                    "family": row["family"],
                    "source": row["source"],
                    "note": row["note"],
                    "status": row["status"],
                    "times": row["times"],
                    "created_at": row["created_at"],
                    "last_at": row["last_at"],
                    "interval_days": row["interval_days"],
                    "next_at": row["next_at"] or row["last_at"],
                    "streak": row["streak"],
                    "reviews": row["reviews"],
                    "last_review_at": row["last_review_at"],
                    "last_result": row["last_result"],
                    "stuck": row["times"] >= STUCK_TIMES and row["status"] != "mastered",
                    "tags": sorted(tags.get(row["id"], []), key=TAG_KEYS.index),
                    "photo": photos.get(row["photo_id"]),
                    "process_status": row["last_process_status"],
                    "process_photo": photos.get(row["process_photo_id"]),
                }
            )
        return sorted(items, key=lambda item: order.get(item["id"], 0))

    def _where(self, family=None, tag=None, chapter=None, status=None,
               days=None, qid=None):
        clauses, args = [], []
        if family:
            clauses.append("a.family = ?")
            args.append(family.upper())
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM attempt_tags t"
                " WHERE t.attempt_id = a.id AND t.tag = ?)"
            )
            args.append(tag)
        if status:
            clauses.append("a.status = ?")
            args.append(status)
        if qid:
            clauses.append("a.qid = ?")
            args.append(qid.upper())
        if chapter and re.match(r"^[CZ]\d{1,2}$", chapter.upper()):
            clauses.append("a.qid GLOB ?")
            args.append(chapter.upper() + "-*")
        if days:
            cutoff = now_iso(
                datetime.now().astimezone() - timedelta(days=int(days))
            )
            clauses.append("a.last_at >= ?")
            args.append(cutoff)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, args

    def list_attempts(self, family=None, tag=None, chapter=None, status=None,
                      days=None, qid=None, limit=50, offset=0):
        if tag:
            tag = str(tag).strip().lower()
            if tag not in TAGS:
                raise ValueError("未知错因标签：%s" % tag)
        if status and status not in STATUSES:
            raise ValueError("未知状态：%s" % status)
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        where, args = self._where(family, tag, chapter, status, days, qid)
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM attempts a " + where, args
            ).fetchone()["n"]
            ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT a.id FROM attempts a " + where
                    + " ORDER BY a.last_at DESC, a.id DESC LIMIT ? OFFSET ?",
                    args + [limit, offset],
                )
            ]
            items = self._items(conn, ids)
            stats = self._stats(conn)
        finally:
            conn.close()
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
            "stats": stats,
        }

    def _stats(self, conn, now=None):
        by_tag = {key: 0 for key in TAG_KEYS}
        for row in conn.execute(
            "SELECT tag, COUNT(*) AS n FROM attempt_tags GROUP BY tag"
        ):
            if row["tag"] in by_tag:
                by_tag[row["tag"]] = row["n"]
        by_family = {}
        for row in conn.execute(
            "SELECT family, COUNT(*) AS n FROM attempts GROUP BY family"
        ):
            by_family[row["family"]] = row["n"]
        total = conn.execute("SELECT COUNT(*) AS n FROM attempts").fetchone()["n"]
        mastered = conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE status = 'mastered'"
        ).fetchone()["n"]
        stamp = now_iso(now)
        due = conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE status = 'active'"
            " AND (next_at IS NULL OR next_at <= ?)",
            (stamp,),
        ).fetchone()["n"]
        stuck = conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE status = 'active'"
            " AND times >= ?",
            (STUCK_TIMES,),
        ).fetchone()["n"]
        return {
            "total": total,
            "mastered": mastered,
            "active": total - mastered,
            "due": due,
            "stuck": stuck,
            "by_tag": by_tag,
            "by_family": by_family,
            "tags": TAGS,
        }

    def stats(self, now=None):
        conn = self._connect()
        try:
            return self._stats(conn, now)
        finally:
            conn.close()

    def learning_snapshot(self):
        """Read-only rows for the dashboard.

        The analytics layer needs attempts, tags and the review log together;
        handing it a snapshot keeps SQL in one place and leaves the write API
        untouched.  Nothing here mutates the database.
        """
        conn = self._connect()
        try:
            attempts = [dict(row) for row in conn.execute(
                "SELECT * FROM attempts ORDER BY id"
            )]
            tags = {}
            for row in conn.execute(
                "SELECT attempt_id, tag FROM attempt_tags ORDER BY attempt_id, tag"
            ):
                tags.setdefault(row["attempt_id"], []).append(row["tag"])
            logs = [dict(row) for row in conn.execute(
                "SELECT qid, at, result, status_before, status_after,"
                "       last_result_before"
                "       FROM review_log ORDER BY id"
            )]
            sessions = [dict(row) for row in conn.execute(
                "SELECT id, day, cursor, queue, started_at, finished_at"
                " FROM review_sessions ORDER BY id"
            )]
            return {"attempts": attempts, "tags": tags, "logs": logs,
                    "sessions": sessions}
        finally:
            conn.close()

    # -- review schedule ---------------------------------------------------

    @staticmethod
    def _interleave(rows):
        """Round-robin rows by chapter so one chapter cannot monopolize a day."""
        groups, order = {}, []
        for row in rows:
            key = _chapter_key(row["qid"])
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        out, pending = [], list(order)
        while pending:
            following = []
            for key in pending:
                bucket = groups[key]
                if bucket:
                    out.append(bucket.pop(0))
                if bucket:
                    following.append(key)
            pending = following
        return out

    def review_queue(self, now=None, limit=REVIEW_DAY_LIMIT):
        """Today's zero-decision queue: due, then fresh errors, then a few stuck.

        Mastered questions stay out until a v2 revival rule exists.  The order
        is chapter-interleaved inside each bucket, and the hard cap is 20 so a
        backlog never turns into a guilt pile.
        """
        stamp = now_iso(now)
        now_dt = _parse_iso(stamp)
        recent_cut = (now_dt - timedelta(days=NEW_ERROR_WINDOW_DAYS)).isoformat(
            timespec="seconds"
        )
        limit = max(1, min(int(limit or REVIEW_DAY_LIMIT), REVIEW_DAY_LIMIT))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM attempts WHERE status = 'active'"
            ).fetchall()
            due, fresh, stuck = [], [], []
            for row in rows:
                next_at = row["next_at"] or row["last_at"]
                if next_at <= stamp:
                    due.append(row)
                elif not row["reviews"] and row["last_at"] >= recent_cut:
                    fresh.append(row)
                elif row["times"] >= STUCK_TIMES:
                    stuck.append(row)
            due.sort(key=lambda r: (r["next_at"] or r["last_at"], -r["times"], r["id"]))
            fresh.sort(key=lambda r: (r["last_at"], r["id"]), reverse=True)
            stuck.sort(key=lambda r: (-r["times"], r["last_at"], r["id"]))

            chosen, reasons = [], {}

            def take(bucket, reason, cap=None):
                for row in self._interleave(bucket):
                    if len(chosen) >= limit:
                        return
                    if cap is not None and cap <= 0:
                        return
                    chosen.append(row)
                    reasons[row["id"]] = reason
                    if cap is not None:
                        cap -= 1

            take(due, "due")
            take(fresh, "new")
            if len(chosen) < limit:
                seen = {row["id"] for row in chosen}
                spare = [row for row in stuck if row["id"] not in seen]
                take(spare, "stuck", cap=STUCK_IN_QUEUE)

            items = self._items(conn, [row["id"] for row in chosen])
            for item in items:
                item["review_reason"] = reasons.get(item["id"], "due")
            return items
        finally:
            conn.close()

    def review_overview(self, now=None):
        items = self.review_queue(now)
        reasons = {"due": 0, "new": 0, "stuck": 0}
        for item in items:
            reasons[item["review_reason"]] = reasons.get(item["review_reason"], 0) + 1
        stats = self.stats(now)
        return {
            "day": _local_day(now),
            "count": len(items),
            "est_minutes": max(1, round(len(items) * 2)) if items else 0,
            "reasons": reasons,
            "stats": stats,
        }

    def _session_row(self, conn, session_id=None, day=None):
        if session_id is not None:
            return conn.execute(
                "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        day = day or _local_day()
        row = conn.execute(
            "SELECT * FROM review_sessions WHERE day = ? AND finished_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM review_sessions WHERE day = ? ORDER BY id DESC LIMIT 1",
                (day,),
            ).fetchone()
        return row

    def _session_payload(self, conn, row, now=None):
        if row is None:
            day = _local_day(now)
            return {
                "id": None,
                "day": day,
                "queue": [],
                "cursor": 0,
                "current_qid": None,
                "graded": {},
                "graded_count": 0,
                "finished": False,
                "started_at": None,
                "finished_at": None,
                "items": [],
            }
        queue = json.loads(row["queue"])
        logs = conn.execute(
            "SELECT qid, result FROM review_log WHERE session_id = ?",
            (row["id"],),
        ).fetchall()
        graded = {log["qid"]: log["result"] for log in logs}
        cursor = row["cursor"]
        while cursor < len(queue) and queue[cursor] in graded:
            cursor += 1
        current = queue[cursor] if cursor < len(queue) else None
        attempts = []
        for qid in queue:
            found = conn.execute(
                "SELECT id FROM attempts WHERE qid = ?", (qid,)
            ).fetchone()
            if found:
                attempts.append(found["id"])
        items = {item["qid"]: item for item in self._items(conn, attempts)}
        return {
            "id": row["id"],
            "day": row["day"],
            "queue": queue,
            "cursor": cursor,
            "current_qid": current,
            "graded": graded,
            "graded_count": len(graded),
            "finished": bool(row["finished_at"]),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "items": [items[qid] for qid in queue if qid in items],
        }

    def session(self, session_id=None, now=None):
        conn = self._connect()
        try:
            row = self._session_row(conn, session_id=session_id, day=_local_day(now))
            return self._session_payload(conn, row, now)
        finally:
            conn.close()

    def start_session(self, now=None, limit=REVIEW_DAY_LIMIT):
        """Resume today's unfinished round, or freeze a new queue for today."""
        day = _local_day(now)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM review_sessions WHERE day = ? AND finished_at IS NULL"
                " ORDER BY id DESC LIMIT 1",
                (day,),
            ).fetchone()
            if row:
                return self._session_payload(conn, row, now)
        finally:
            conn.close()

        queue = [item["qid"] for item in self.review_queue(now, limit)]
        if not queue:
            return self.session(now=now)
        stamp = now_iso(now)
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute(
                        "INSERT INTO review_sessions (day, queue, cursor, started_at)"
                        " VALUES (?, ?, 0, ?)",
                        (day, json.dumps(queue, ensure_ascii=False), stamp),
                    )
                    row = conn.execute(
                        "SELECT * FROM review_sessions WHERE id = ?", (cur.lastrowid,)
                    ).fetchone()
                return self._session_payload(conn, row, now)
            finally:
                conn.close()

    def _queue_for_session(self, conn, session_id):
        row = self._session_row(conn, session_id=session_id)
        if not row:
            raise ValueError("复习会话不存在")
        if row["finished_at"]:
            raise ValueError("这轮复习已经结束")
        return row, json.loads(row["queue"])

    def grade(self, session_id, qid, result, tags=None, now=None,
              process_status=None, process_photo_sha=None):
        """Apply one review verdict and advance the session cursor.

        Case questions can carry a manual process status and a handwritten
        process photo.  The status is chosen by the user; the option letter in
        the printed source book is never used to judge a case answer.
        """
        qid = (qid or "").strip().upper()
        if process_status:
            process_status = str(process_status).strip()
            if process_status not in PROCESS_RESULT:
                raise ValueError("未知过程状态：%s" % process_status)
            if qid[0] not in "CAM":
                raise ValueError("过程状态只用于案例题")
            expected = PROCESS_RESULT[process_status]
            if result and str(result).strip().lower() != expected:
                raise ValueError("过程状态与判分不一致")
            result = expected
        result = (result or "").strip().lower()
        if result not in ("correct", "wrong"):
            raise ValueError("评分只能是 correct / wrong")
        clean_tags = _normalize_tags(tags) if tags else None
        stamp = now_iso(now)
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    session, queue = self._queue_for_session(conn, session_id)
                    if qid not in queue:
                        raise ValueError("题 %s 不在本轮复习里" % qid)
                    index = queue.index(qid)
                    attempt = conn.execute(
                        "SELECT * FROM attempts WHERE qid = ?", (qid,)
                    ).fetchone()
                    if not attempt:
                        raise ValueError("题 %s 已经被删除" % qid)
                    process_status_before = attempt["last_process_status"]
                    process_photo_before = attempt["process_photo_id"]
                    process_photo_after = process_photo_before
                    if process_photo_sha:
                        photo = conn.execute(
                            "SELECT id FROM photos WHERE sha256 = ?",
                            (str(process_photo_sha),),
                        ).fetchone()
                        if not photo:
                            raise ValueError("过程照片不存在")
                        process_photo_after = photo["id"]
                    process_status_after = (
                        process_status if process_status is not None
                        else process_status_before
                    )
                    duplicate = conn.execute(
                        "SELECT id FROM review_log WHERE session_id = ? AND qid = ?",
                        (session_id, qid),
                    ).fetchone()
                    if duplicate:
                        cursor = max(session["cursor"], index + 1)
                        conn.execute(
                            "UPDATE review_sessions SET cursor = ? WHERE id = ?",
                            (cursor, session_id),
                        )
                        row = conn.execute(
                            "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
                        ).fetchone()
                        payload = self._session_payload(conn, row, now)
                        item = self._items(conn, [attempt["id"]])[0]
                        return {"duplicate": True, "session": payload, "attempt": item}

                    tags_before = [
                        row["tag"]
                        for row in conn.execute(
                            "SELECT tag FROM attempt_tags WHERE attempt_id = ?",
                            (attempt["id"],),
                        )
                    ]
                    interval_before = attempt["interval_days"] or 1
                    streak_before = attempt["streak"] or 0
                    reviews_before = attempt["reviews"] or 0
                    if result == "correct":
                        previous = _parse_iso(attempt["last_review_at"])
                        gap_ok = bool(
                            previous
                            and (_parse_iso(stamp) - previous) >= timedelta(days=7)
                        )
                        streak_after = streak_before + 1
                        interval_after = _next_correct_interval(interval_before)
                        status_after = attempt["status"]
                        if status_after != "mastered" and streak_after >= 2 and gap_ok:
                            status_after = "mastered"
                        times_after = attempt["times"]
                        last_at_after = attempt["last_at"]
                    else:
                        streak_after = 0
                        interval_after = _next_wrong_interval(interval_before)
                        status_after = "active"
                        times_after = attempt["times"] + 1
                        last_at_after = stamp
                    next_at = _add_days(stamp, interval_after)
                    conn.execute(
                        "UPDATE attempts SET status = ?, times = ?,"
                        " interval_days = ?, next_at = ?, streak = ?, reviews = ?,"
                        " last_review_at = ?, last_result = ?, last_at = ?,"
                        " last_process_status = ?, process_photo_id = ?"
                        " WHERE id = ?",
                        (
                            status_after,
                            times_after,
                            interval_after,
                            next_at,
                            streak_after,
                            reviews_before + 1,
                            stamp,
                            result,
                            last_at_after,
                            process_status_after,
                            process_photo_after,
                            attempt["id"],
                        ),
                    )
                    tags_after = tags_before
                    if result == "wrong" and clean_tags is not None:
                        self._set_tags(conn, attempt["id"], clean_tags)
                        tags_after = list(clean_tags)
                    conn.execute(
                        "INSERT INTO review_log"
                        " (session_id, attempt_id, qid, at, result,"
                        "  interval_before, interval_after, streak_before, streak_after,"
                        "  status_before, status_after, times_before, times_after,"
                        "  next_before, next_after, last_at_before, last_at_after,"
                        "  last_review_before, last_review_after,"
                        "  last_result_before, last_result_after, tags_before, tags_after,"
                        "  process_status_before, process_status_after,"
                        "  process_photo_before, process_photo_after)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                        "         ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            session_id,
                            attempt["id"],
                            qid,
                            stamp,
                            result,
                            interval_before,
                            interval_after,
                            streak_before,
                            streak_after,
                            attempt["status"],
                            status_after,
                            attempt["times"],
                            times_after,
                            attempt["next_at"] or attempt["last_at"],
                            next_at,
                            attempt["last_at"],
                            last_at_after,
                            attempt["last_review_at"],
                            stamp,
                            attempt["last_result"],
                            result,
                            json.dumps(tags_before, ensure_ascii=False),
                            json.dumps(tags_after, ensure_ascii=False),
                            process_status_before,
                            process_status_after,
                            process_photo_before,
                            process_photo_after,
                        ),
                    )
                    cursor = max(session["cursor"], index + 1)
                    finished = stamp if cursor >= len(queue) else None
                    conn.execute(
                        "UPDATE review_sessions SET cursor = ?,"
                        " finished_at = COALESCE(finished_at, ?) WHERE id = ?",
                        (cursor, finished, session_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    payload = self._session_payload(conn, row, now)
                    item = self._items(conn, [attempt["id"]])[0]
                return {"duplicate": False, "session": payload, "attempt": item}
            finally:
                conn.close()

    def skip(self, session_id, qid, now=None):
        qid = (qid or "").strip().upper()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    session, queue = self._queue_for_session(conn, session_id)
                    if qid not in queue:
                        raise ValueError("题 %s 不在本轮复习里" % qid)
                    cursor = max(session["cursor"], queue.index(qid) + 1)
                    finished = now_iso(now) if cursor >= len(queue) else None
                    conn.execute(
                        "UPDATE review_sessions SET cursor = ?,"
                        " finished_at = COALESCE(finished_at, ?) WHERE id = ?",
                        (cursor, finished, session_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    return self._session_payload(conn, row, now)
            finally:
                conn.close()

    def undo(self, session_id, now=None):
        """Revert the last verdict in this session, schedule included."""
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    session = self._session_row(conn, session_id=session_id)
                    if not session:
                        raise ValueError("复习会话不存在")
                    log = conn.execute(
                        "SELECT * FROM review_log WHERE session_id = ?"
                        " ORDER BY id DESC LIMIT 1",
                        (session_id,),
                    ).fetchone()
                    if not log:
                        raise ValueError("这轮还没有可撤销的评分")
                    queue = json.loads(session["queue"])
                    exists = conn.execute(
                        "SELECT id FROM attempts WHERE id = ?", (log["attempt_id"],)
                    ).fetchone()
                    if exists:
                        conn.execute(
                            "UPDATE attempts SET status = ?, times = ?,"
                            " interval_days = ?, next_at = ?, streak = ?,"
                            " last_review_at = ?, last_result = ?, last_at = ?,"
                            " last_process_status = ?, process_photo_id = ?"
                            " WHERE id = ?",
                            (
                                log["status_before"],
                                log["times_before"],
                                log["interval_before"],
                                log["next_before"],
                                log["streak_before"],
                                log["last_review_before"],
                                log["last_result_before"],
                                log["last_at_before"],
                                log["process_status_before"],
                                log["process_photo_before"],
                                log["attempt_id"],
                            ),
                        )
                        # `reviews` only counts up; one log means one increment.
                        conn.execute(
                            "UPDATE attempts SET reviews = MAX(0, reviews - 1)"
                            " WHERE id = ?",
                            (log["attempt_id"],),
                        )
                        try:
                            before = json.loads(log["tags_before"] or "[]")
                        except ValueError:
                            before = []
                        self._set_tags(conn, log["attempt_id"], before)
                    # A deleted question only needs its log and cursor rewound.
                    conn.execute("DELETE FROM review_log WHERE id = ?", (log["id"],))
                    cursor = queue.index(log["qid"]) if log["qid"] in queue else 0
                    conn.execute(
                        "UPDATE review_sessions SET cursor = ?, finished_at = NULL"
                        " WHERE id = ?",
                        (cursor, session_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    payload = self._session_payload(conn, row, now)
                    return {"undone": log["qid"], "session": payload}
            finally:
                conn.close()

    def session_summary(self, session_id, now=None):
        stamp = now_iso(now)
        conn = self._connect()
        try:
            session = self._session_row(conn, session_id=session_id)
            if not session:
                raise ValueError("复习会话不存在")
            queue = json.loads(session["queue"])
            logs = conn.execute(
                "SELECT * FROM review_log WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            graded = [log["qid"] for log in logs]
            correct = [log["qid"] for log in logs if log["result"] == "correct"]
            wrong = [log["qid"] for log in logs if log["result"] == "wrong"]
            mastered = [
                log["qid"]
                for log in logs
                if log["status_before"] != "mastered" and log["status_after"] == "mastered"
            ]
            recovered = [
                log["qid"]
                for log in logs
                if log["result"] == "correct" and not (log["streak_before"] or 0)
            ]
            attempts = []
            for qid in queue:
                found = conn.execute(
                    "SELECT id FROM attempts WHERE qid = ?", (qid,)
                ).fetchone()
                if found:
                    attempts.append(found["id"])
            items = self._items(conn, attempts)
            stubborn = sorted(
                [item for item in items if item["stuck"]],
                key=lambda item: (-item["times"], item["qid"]),
            )[:3]
            started = _parse_iso(session["started_at"])
            ended = _parse_iso(session["finished_at"]) or _parse_iso(stamp)
            duration = int((ended - started).total_seconds()) if started and ended else 0

            next_row = conn.execute(
                "SELECT MIN(next_at) AS nxt FROM attempts WHERE status = 'active'"
            ).fetchone()
            next_at = next_row["nxt"] if next_row else None
            next_count = 0
            next_day = ""
            if next_at:
                day = str(next_at)[:10]
                next_day = day
                next_count = conn.execute(
                    "SELECT COUNT(*) AS n FROM attempts WHERE status = 'active'"
                    " AND substr(next_at, 1, 10) = ?",
                    (day,),
                ).fetchone()["n"]
            chapters = sorted({_chapter_key(qid) for qid in graded})
            queue_now = conn.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE status = 'active'"
                " AND (next_at IS NULL OR next_at <= ?)",
                (stamp,),
            ).fetchone()["n"]
            return {
                "session_id": session["id"],
                "day": session["day"],
                "queue": len(queue),
                "graded": len(graded),
                "skipped": len(queue) - len(graded),
                "correct": len(correct),
                "wrong": len(wrong),
                "accuracy": round(len(correct) / len(graded), 3) if graded else 0,
                "duration": duration,
                "mastered": mastered,
                "recovered": recovered,
                "recovered_count": len(recovered),
                "stubborn": [
                    {
                        "qid": item["qid"],
                        "times": item["times"],
                        "chapter": _chapter_key(item["qid"]),
                    }
                    for item in stubborn
                ],
                "chapters": chapters,
                "next_due": next_day or None,
                "next_due_count": next_count,
                "queue_now": queue_now,
                "finished": bool(session["finished_at"]),
                "results": [
                    {"qid": log["qid"], "result": log["result"]} for log in logs
                ],
            }
        finally:
            conn.close()

    def get_attempt(self, attempt_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            items = self._items(conn, [row["id"]]) if row else []
        finally:
            conn.close()
        return items[0] if items else None

    def get_attempt_by_qid(self, qid):
        if not qid:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM attempts WHERE qid = ?", (qid.upper(),)
            ).fetchone()
            items = self._items(conn, [row["id"]]) if row else []
        finally:
            conn.close()
        return items[0] if items else None

    def update_attempt(self, attempt_id, tags=None, note=None, status=None,
                       process_status=None, process_photo_sha=None):
        if status is not None and status not in STATUSES:
            raise ValueError("未知状态：%s" % status)
        if process_status is not None and process_status not in PROCESS_STATUSES:
            raise ValueError("未知过程状态：%s" % process_status)
        clean_tags = _normalize_tags(tags) if tags is not None else None
        clean_note = _clean_note(note) if note is not None else None
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    exists = conn.execute(
                        "SELECT id FROM attempts WHERE id = ?", (attempt_id,)
                    ).fetchone()
                    if not exists:
                        return None
                    if clean_note is not None:
                        conn.execute(
                            "UPDATE attempts SET note = ? WHERE id = ?",
                            (clean_note, attempt_id),
                        )
                    if status is not None:
                        if status == "active":
                            # Manually un-mastering puts the question back in
                            # today's queue with a clean streak.
                            conn.execute(
                                "UPDATE attempts SET status = 'active',"
                                " interval_days = 1, streak = 0, next_at = ?"
                                " WHERE id = ?",
                                (now_iso(), attempt_id),
                            )
                        else:
                            conn.execute(
                                "UPDATE attempts SET status = ? WHERE id = ?",
                                (status, attempt_id),
                            )
                    if clean_tags is not None:
                        self._set_tags(conn, attempt_id, clean_tags)
                    if process_status is not None:
                        conn.execute(
                            "UPDATE attempts SET last_process_status = ? WHERE id = ?",
                            (process_status, attempt_id),
                        )
                    if process_photo_sha:
                        photo = conn.execute(
                            "SELECT id FROM photos WHERE sha256 = ?",
                            (str(process_photo_sha),),
                        ).fetchone()
                        if not photo:
                            raise ValueError("过程照片不存在")
                        conn.execute(
                            "UPDATE attempts SET process_photo_id = ? WHERE id = ?",
                            (photo["id"], attempt_id),
                        )
            finally:
                conn.close()
        return self.get_attempt(attempt_id)

    def delete_attempt(self, attempt_id):
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM attempts WHERE id = ?", (attempt_id,)
                    )
                return cur.rowcount > 0
            finally:
                conn.close()

    # -- practice sessions and plan progress --------------------------------

    @staticmethod
    def _practice_dict(row):
        return {
            "id": row["id"],
            "day": row["day"],
            "source": row["source"],
            "source_label": PRACTICE_SOURCES.get(row["source"], row["source"]),
            "category": row["category"],
            "attempted": row["attempted"],
            "correct": row["correct"],
            "abandoned": row["abandoned"],
            "overtime": row["overtime"],
            "minutes": row["minutes"],
            "note": row["note"],
            "baseline_half": row["baseline_half"],
            "created_at": row["created_at"],
        }

    def add_practice_session(self, day=None, source="exercise", category="mixed",
                             attempted=0, correct=0, abandoned=0, overtime=0,
                             minutes=0, note=""):
        """Record one paper/category sitting as aggregate counts.

        The app cannot infer how many questions were practised from the wrong
        book (it only stores errors), so the plan's throughput comes from these
        explicit four-number records.
        """
        try:
            attempted = int(attempted)
            correct = int(correct)
            abandoned = int(abandoned or 0)
            overtime = int(overtime or 0)
            minutes = int(minutes or 0)
        except (TypeError, ValueError):
            raise ValueError("题数和用时必须是整数")
        if not 1 <= attempted <= 200:
            raise ValueError("作答题数应在 1–200 之间")
        if not 0 <= correct <= attempted:
            raise ValueError("做对数应在 0–作答题数之间")
        if not 0 <= abandoned <= 200:
            raise ValueError("放弃题数应在 0–200 之间")
        if not 0 <= overtime <= attempted:
            raise ValueError("超时题数应在 0–作答题数之间")
        if not 0 <= minutes <= 600:
            raise ValueError("用时应在 0–600 分钟之间")
        source = str(source or "exercise").strip().lower()
        if source not in PRACTICE_SOURCES:
            raise ValueError("未知来源：%s" % source)
        category = str(category or "mixed").strip().upper()
        if category == "MIXED":
            category = "mixed"
        if category == "KNOWLEDGE":
            category = "knowledge"
        if category not in PLAN_CATEGORIES:
            raise ValueError("未知类别：%s" % category)
        day = str(day or datetime.now().date().isoformat())[:10]
        try:
            datetime.fromisoformat(day)
        except ValueError:
            raise ValueError("日期应为 YYYY-MM-DD")
        clean_note = _clean_note(note)
        stamp = now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute(
                        "INSERT INTO practice_sessions"
                        " (day, source, category, attempted, correct, abandoned,"
                        "  overtime, minutes, note, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (day, source, category, attempted, correct, abandoned,
                         overtime, minutes, clean_note or "", stamp),
                    )
                    row = conn.execute(
                        "SELECT * FROM practice_sessions WHERE id = ?",
                        (cur.lastrowid,),
                    ).fetchone()
                return self._practice_dict(row)
            finally:
                conn.close()

    def list_practice_sessions(self, limit=30, since=None):
        limit = max(1, min(int(limit or 30), 200))
        conn = self._connect()
        try:
            if since:
                rows = conn.execute(
                    "SELECT * FROM practice_sessions WHERE day >= ?"
                    " ORDER BY day DESC, id DESC LIMIT ?",
                    (str(since)[:10], limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM practice_sessions"
                    " ORDER BY day DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._practice_dict(row) for row in rows]
        finally:
            conn.close()

    def delete_practice_session(self, session_id):
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT baseline_half FROM practice_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if row and row["baseline_half"]:
                    raise ValueError("基线记录请在基线页修改或删除")
                with conn:
                    cur = conn.execute(
                        "DELETE FROM practice_sessions WHERE id = ?", (session_id,)
                    )
                return cur.rowcount > 0
            finally:
                conn.close()

    def plan_progress(self):
        conn = self._connect()
        try:
            return {
                row["key"]: {
                    "key": row["key"],
                    "status": row["status"],
                    "accuracy": row["accuracy"],
                    "note": row["note"],
                    "updated_at": row["updated_at"],
                }
                for row in conn.execute("SELECT * FROM plan_progress")
            }
        finally:
            conn.close()

    def set_plan_progress(self, key, status="done", accuracy=None, note=""):
        key = str(key or "").strip()
        if not key or len(key) > 120:
            raise ValueError("进度 key 不合法")
        status = str(status or "done").strip().lower()
        if status not in PLAN_STATUSES:
            raise ValueError("未知进度状态：%s" % status)
        if accuracy is not None:
            try:
                accuracy = float(accuracy)
            except (TypeError, ValueError):
                raise ValueError("正确率必须是数字")
            if not 0 <= accuracy <= 1:
                raise ValueError("正确率应在 0–1 之间")
        clean_note = _clean_note(note)
        stamp = now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO plan_progress(key, status, accuracy, note, updated_at)"
                        " VALUES (?, ?, ?, ?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET"
                        " status = excluded.status, accuracy = excluded.accuracy,"
                        " note = excluded.note, updated_at = excluded.updated_at",
                        (key, status, accuracy, clean_note or "", stamp),
                    )
                    row = conn.execute(
                        "SELECT * FROM plan_progress WHERE key = ?", (key,)
                    ).fetchone()
                return {
                    "key": row["key"],
                    "status": row["status"],
                    "accuracy": row["accuracy"],
                    "note": row["note"],
                    "updated_at": row["updated_at"],
                }
            finally:
                conn.close()

    # -- 2025 case baseline ------------------------------------------------

    @staticmethod
    def _photo_urls(sha):
        if not sha:
            return None
        return {
            "sha": sha,
            "thumb": "/api/photos/%s?size=thumb" % sha,
            "orig": "/api/photos/%s?size=orig" % sha,
        }

    def baseline_runs(self, year=None):
        """Recorded baseline halves with their answer-sheet photo, if any."""
        conn = self._connect()
        try:
            sql = (
                "SELECT r.*, p.sha256 AS photo_sha FROM baseline_runs r"
                " LEFT JOIN photos p ON p.id = r.photo_id"
            )
            params = ()
            if year is not None:
                sql += " WHERE r.year = ?"
                params = (int(year),)
            sql += " ORDER BY r.half"
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "id": row["id"],
                    "year": row["year"],
                    "half": row["half"],
                    "day": row["day"],
                    "minutes": row["minutes"],
                    "answered": row["answered"],
                    "correct": row["correct"],
                    "skipped": row["skipped"],
                    "overtime": row["overtime"],
                    "items_done": row["items_done"],
                    "note": row["note"],
                    "photo": self._photo_urls(row["photo_sha"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def baseline_items(self, run_id):
        conn = self._connect()
        try:
            return [
                {
                    "qid": row["qid"],
                    "seq": row["seq"],
                    "category": row["category"],
                    "result": row["result"],
                    "overtime": row["overtime"],
                    "process_status": row["process_status"],
                }
                for row in conn.execute(
                    "SELECT * FROM baseline_items WHERE run_id = ?"
                    " ORDER BY seq, qid",
                    (run_id,),
                )
            ]
        finally:
            conn.close()

    def save_baseline(self, half, year, day, minutes, answered, correct,
                      skipped, overtime, items, note="", photo_sha=None):
        """Upsert one half and its ledger row in one transaction.

        The four numbers are the scored record; items are optional detail.
        Re-saving a half updates the same `practice_sessions` row so the
        weekly plan never double-counts a corrected entry.
        """
        half = str(half or "").upper()
        if half not in ("A", "P"):
            raise ValueError("基线只有上午卷 A 或下午卷 P")
        try:
            year = int(year)
            minutes = int(minutes)
            answered = int(answered)
            correct = int(correct)
            skipped = int(skipped)
            overtime = int(overtime)
        except (TypeError, ValueError):
            raise ValueError("年份、题数和用时必须是整数")
        normalized = []
        for item in items or []:
            result = str(item.get("result") or "").lower()
            if result not in ("correct", "wrong", "skipped"):
                raise ValueError("逐题状态不合法：%s" % result)
            normalized.append({
                "qid": str(item.get("qid") or "").strip().upper(),
                "seq": int(item.get("seq") or 0),
                "category": str(item.get("category") or ""),
                "result": result,
                "overtime": 1 if item.get("overtime") else 0,
                "process_status": item.get("process_status") or None,
            })
        note = _clean_note(note) or ""
        stamp = now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    photo_id = None
                    if photo_sha:
                        found = conn.execute(
                            "SELECT id FROM photos WHERE sha256 = ?",
                            (str(photo_sha),),
                        ).fetchone()
                        if not found:
                            raise ValueError("照片不存在：%s" % photo_sha)
                        photo_id = found["id"]
                    existing = conn.execute(
                        "SELECT id FROM baseline_runs WHERE year = ? AND half = ?",
                        (year, half),
                    ).fetchone()
                    if existing:
                        run_id = existing["id"]
                        conn.execute(
                            "UPDATE baseline_runs SET day = ?, minutes = ?,"
                            " answered = ?, correct = ?, skipped = ?, overtime = ?,"
                            " items_done = ?, note = ?, photo_id = ?, updated_at = ?"
                            " WHERE id = ?",
                            (day, minutes, answered, correct, skipped, overtime,
                             len(normalized), note, photo_id, stamp, run_id),
                        )
                    else:
                        cur = conn.execute(
                            "INSERT INTO baseline_runs"
                            " (year, half, day, minutes, answered, correct,"
                            "  skipped, overtime, items_done, note, photo_id,"
                            "  created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (year, half, day, minutes, answered, correct,
                             skipped, overtime, len(normalized), note, photo_id,
                             stamp, stamp),
                        )
                        run_id = cur.lastrowid
                    conn.execute(
                        "DELETE FROM baseline_items WHERE run_id = ?", (run_id,)
                    )
                    for item in normalized:
                        conn.execute(
                            "INSERT INTO baseline_items"
                            " (run_id, qid, seq, category, result, overtime,"
                            "  process_status)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (run_id, item["qid"], item["seq"], item["category"],
                             item["result"], item["overtime"],
                             item["process_status"]),
                        )
                    ledger_note = note or ("2025 基线·%s"
                                           % ("上午卷" if half == "A" else "下午卷"))
                    ledger = conn.execute(
                        "SELECT id FROM practice_sessions WHERE baseline_half = ?",
                        (half,),
                    ).fetchone()
                    if ledger:
                        conn.execute(
                            "UPDATE practice_sessions SET day = ?,"
                            " source = 'real_exam', category = 'mixed',"
                            " attempted = ?, correct = ?, abandoned = ?,"
                            " overtime = ?, minutes = ?, note = ? WHERE id = ?",
                            (day, answered, correct, skipped, overtime, minutes,
                             ledger_note, ledger["id"]),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO practice_sessions"
                            " (day, source, category, attempted, correct,"
                            "  abandoned, overtime, minutes, note, baseline_half,"
                            "  created_at)"
                            " VALUES (?, 'real_exam', 'mixed', ?, ?, ?, ?, ?, ?, ?, ?)",
                            (day, answered, correct, skipped, overtime, minutes,
                             ledger_note, half, stamp),
                        )
                row = conn.execute(
                    "SELECT r.*, p.sha256 AS photo_sha FROM baseline_runs r"
                    " LEFT JOIN photos p ON p.id = r.photo_id WHERE r.id = ?",
                    (run_id,),
                ).fetchone()
                return {
                    "id": row["id"],
                    "year": row["year"],
                    "half": row["half"],
                    "day": row["day"],
                    "minutes": row["minutes"],
                    "answered": row["answered"],
                    "correct": row["correct"],
                    "skipped": row["skipped"],
                    "overtime": row["overtime"],
                    "items_done": row["items_done"],
                    "note": row["note"],
                    "photo": self._photo_urls(row["photo_sha"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            finally:
                conn.close()

    def delete_baseline(self, half, year):
        """Remove one half and its ledger row; returns whether it existed."""
        half = str(half or "").upper()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM baseline_runs WHERE year = ? AND half = ?",
                        (int(year), half),
                    )
                    conn.execute(
                        "DELETE FROM practice_sessions WHERE baseline_half = ?",
                        (half,),
                    )
                return cur.rowcount > 0
            finally:
                conn.close()

    # -- backup / restore -------------------------------------------------

    def _snapshot_db(self, dest):
        conn = self._connect()
        target = sqlite3.connect(dest)
        try:
            conn.backup(target)
        finally:
            target.close()
            conn.close()

    def backup(self, dest=None):
        """Consistent SQLite snapshot plus attachments, in one zip."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = Path(dest) if dest else self.backup_dir / ("wrongbook-%s.zip" % stamp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "wrongbook.db"
            self._snapshot_db(snap)
            counts = self.stats()
            originals = list((self.root / "attachments" / "orig").rglob("*.jpg"))
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(snap, "wrongbook.db")
                for extra in ("settings.json", "plan.json"):
                    path = self.root / extra
                    if path.exists():
                        zf.write(path, extra)
                for kind in ("orig", "thumb"):
                    folder = self.root / "attachments" / kind
                    if not folder.exists():
                        continue
                    for path in sorted(folder.rglob("*.jpg")):
                        zf.write(path, path.relative_to(self.root).as_posix())
                zf.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "app": "岩土错题本",
                            "version": 1,
                            "created_at": now_iso(),
                            "attempts": counts["total"],
                            "photos": len(originals),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        return dest

    @staticmethod
    def restore_from_zip(zip_path, data_dir):
        """Replace the store under `data_dir` with the archive's contents.

        The current data is backed up first; a corrupt archive never touches it.
        """
        zip_path = Path(zip_path)
        data_dir = Path(data_dir)
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            if "wrongbook.db" not in names:
                raise ValueError("压缩包里没有 wrongbook.db")
            bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts]
            if bad:
                raise ValueError("压缩包含非法路径：%s" % bad[0])

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    target = tmp / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)

            db = tmp / "wrongbook.db"
            conn = sqlite3.connect(db)
            try:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise ValueError("备份数据库损坏：%s" % check)
                attempts = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
                photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
            finally:
                conn.close()

            safety = None
            if (data_dir / "wrongbook.db").exists():
                safety = Store(data_dir).backup()

            data_dir.mkdir(parents=True, exist_ok=True)
            # A stale WAL must never replay onto the restored file: clear it
            # before the snapshot lands.
            for extra in ("wrongbook.db-wal", "wrongbook.db-shm"):
                try:
                    (data_dir / extra).unlink()
                except OSError:
                    pass
            shutil.copy2(db, data_dir / "wrongbook.db")
            for extra in ("settings.json", "plan.json"):
                if (tmp / extra).exists():
                    shutil.copy2(tmp / extra, data_dir / extra)
            attach = data_dir / "attachments"
            if attach.exists():
                shutil.rmtree(attach)
            if (tmp / "attachments").exists():
                shutil.copytree(tmp / "attachments", attach)
        return {
            "attempts": attempts,
            "photos": photos,
            "safety_backup": str(safety) if safety else None,
        }
