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

PHOTO_MAX_SIDE = 3200
THUMB_MAX_SIDE = 640
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
    photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_last ON attempts(last_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_family ON attempts(family);
CREATE TABLE IF NOT EXISTS attempt_tags (
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (attempt_id, tag)
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
"""


def now_iso(moment=None):
    return (moment or datetime.now().astimezone()).isoformat(timespec="seconds")


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
            conn.commit()
        finally:
            conn.close()

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

        self._write_jpeg(img, orig_path, quality=92)
        thumb = img.copy()
        thumb.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE), Image.LANCZOS)
        self._write_jpeg(thumb, thumb_path, quality=80)

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

    def prune_orphan_photos(self):
        """Drop preview-only photos that never landed in an attempt."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM photos WHERE id NOT IN"
                    " (SELECT photo_id FROM attempts WHERE photo_id IS NOT NULL)"
                ).fetchall()
                if not rows:
                    return 0
                conn.execute(
                    "DELETE FROM photos WHERE id NOT IN"
                    " (SELECT photo_id FROM attempts WHERE photo_id IS NOT NULL)"
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
                            conn.execute(
                                "UPDATE attempts SET times = times + 1, last_at = ?,"
                                " source = ?, note = CASE WHEN ? IS NULL THEN note ELSE ? END,"
                                " photo_id = COALESCE(?, photo_id) WHERE id = ?",
                                (at, source, note, note, photo_id, existing["id"]),
                            )
                            if tags is not None:
                                self._set_tags(conn, existing["id"], tags)
                            updated.append(qid)
                        else:
                            cur = conn.execute(
                                "INSERT INTO attempts"
                                " (qid, family, source, note, status, times,"
                                "  created_at, last_at, photo_id)"
                                " VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?)",
                                (
                                    qid,
                                    qid[0],
                                    source,
                                    note or "",
                                    at,
                                    at,
                                    photo_id,
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
                    "tags": sorted(tags.get(row["id"], []), key=TAG_KEYS.index),
                    "photo": photos.get(row["photo_id"]),
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

    def _stats(self, conn):
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
        return {
            "total": total,
            "mastered": mastered,
            "active": total - mastered,
            "by_tag": by_tag,
            "by_family": by_family,
            "tags": TAGS,
        }

    def stats(self):
        conn = self._connect()
        try:
            return self._stats(conn)
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

    def update_attempt(self, attempt_id, tags=None, note=None, status=None):
        if status is not None and status not in STATUSES:
            raise ValueError("未知状态：%s" % status)
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
                        conn.execute(
                            "UPDATE attempts SET status = ? WHERE id = ?",
                            (status, attempt_id),
                        )
                    if clean_tags is not None:
                        self._set_tags(conn, attempt_id, clean_tags)
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
