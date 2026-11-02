"""Photo -> question number -> crops, wrong book, review and print.

M1 proved the lookup loop.  M2 adds the study data: marked questions, four
error tags, notes, photo attachments, filters and a one-zip backup.  M3 adds
the zero-decision review queue (sessions, grading, undo), the Z-family answer
mask, and one-click A4 question/answer sheets.  M4 reads a solution crop on
demand and proposes error tags with the sentence that triggered them.  The
catalog stays read-only; only `data/` is user data (decision D5).

Run:
    py -3 app/server.py
    py -3 -m uvicorn app.server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import io
import json
import mimetypes
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import crop_question as CQ  # noqa: E402
import recognize_qid as RQ  # noqa: E402
from app import analytics, standards  # noqa: E402
from app.answer_ocr import analyze_crop  # noqa: E402
from app.catalog import Catalog  # noqa: E402
from app.curriculum import CASE_BY_KEY, category_key, load_exam_tags  # noqa: E402
from app.photo_import import import_photos, scan_photos  # noqa: E402
from app.print_export import MAX_QUESTIONS, export_pdf  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.store import PROCESS_LABELS, Store, TAGS  # noqa: E402

INDEX = ROOT / "index"
CACHE = ROOT / ".cache" / "crops"
ANSWER_OCR = ROOT / ".cache" / "answer_ocr"
STATIC = Path(__file__).resolve().parent / "static"
# YANTU_DATA lets tests point at a scratch database; production uses data/.
DATA = Path(os.environ.get("YANTU_DATA", str(ROOT / "data")))
EXPORTS = DATA / "exports"
LEGACY_PHOTOS = ROOT / "拍照错题"
BASELINE_REPORT = ROOT / "docs" / "coach" / "baseline-2025.md"

store = Store(DATA)
settings_store = Settings(DATA)
catalog = Catalog(INDEX)

CHAPTER_RE = re.compile(r"^([CZ])(\d{1,2})-(\d{1,3})$")
EXAM_RE = re.compile(r"^[AK]\d{4}-[A-Z]-\d{1,3}$")
MOCK_RE = re.compile(r"^M\d{1,2}-\d{1,3}$")

app = FastAPI(title="岩土错题本 M5")


def _warm_ocr():
    """Load the OCR models and run one pass so the first photo is not slow."""
    try:
        eng = RQ.engine()
        eng.recognize_txt([np.full((48, 240, 3), 255, dtype=np.uint8)])
        try:
            eng.detect_and_crop(
                *eng.preprocess_img(np.full((640, 480, 3), 255, dtype=np.uint8))
            )
        except Exception:  # noqa: BLE001 - a blank page finds no boxes
            pass
    except Exception:  # noqa: BLE001 - warming up must never block the app
        pass


threading.Thread(target=_warm_ocr, daemon=True).start()

_ocr_lock = threading.Lock()
_crop_locks = {}
_crop_guard = threading.Lock()
_valid_cache = {}
_lookup = None


def lookup():
    global _lookup
    if _lookup is None:
        _lookup = json.load(open(INDEX / "lookup.json", encoding="utf-8"))
    return _lookup


def valid_for(family):
    family = (family or "C").upper()
    if family not in _valid_cache:
        _valid_cache[family] = RQ.load_valid(family)
    return _valid_cache[family]


def book_rows():
    books = json.load(open(INDEX / "books.json", encoding="utf-8"))
    rows = books if isinstance(books, list) else next(iter(books.values()))
    return rows


def short_name(pdf_file):
    for row in book_rows():
        if row["file"] == pdf_file:
            return row["short"]
    return Path(pdf_file).stem


def normalize_qid(text, family):
    """Accept `3-11`, `C3-11`, `A2015-P-19`, `M2-13` and return the key."""
    t = (text or "").strip().upper()
    t = t.replace("－", "-").replace("—", "-").replace("–", "-").replace(" ", "")
    if not t:
        return None
    if CHAPTER_RE.match(t) or EXAM_RE.match(t) or MOCK_RE.match(t):
        return t
    bare = re.match(r"^(\d{1,2})-(\d{1,3})$", t)
    if bare:
        return "%s%s-%s" % ((family or "C").upper(), bare.group(1), bare.group(2))
    return None


def qid_exists(qid):
    lk = lookup()
    if qid.startswith(("C", "Z")):
        return qid in lk["chapter"]
    if qid.startswith(("A", "K")):
        return qid in lk["exam"]
    if qid.startswith("M"):
        return qid in lk["mock"]
    return False


def crop_path(qid, answer, mask=False):
    suffix = ""
    if mask:
        # Masked halves need their own names: `Z1-10_a.png` is the whole
        # page-with-answer crop used by the lookup tab.
        suffix = "_ma" if answer else "_mq"
    elif answer:
        suffix = "_a"
    return CACHE / ("%s%s.png" % (qid, suffix))


def build_crop(qid, answer, mask=False):
    """Render one question (or its solution) into the cache; return the path.

    `mask=True` is the review view of a Z question: the crop is split at its
    【答案】 line so the question image can never spoil the answer.  Other
    families already keep question and solution in separate places.
    """
    if mask and qid[0] == "Z":
        return build_z_mask(qid, answer)

    path = crop_path(qid, answer)
    if path.exists():
        return path

    lock_key = (qid, answer)
    with _crop_guard:
        lock = _crop_locks.setdefault(lock_key, threading.Lock())
    with lock:
        if path.exists():
            return path
        CACHE.mkdir(parents=True, exist_ok=True)

        if answer and qid[0] in "AKM":
            pair = CQ.answer_pages(qid)
            if not pair:
                raise HTTPException(404, "题 %s 没有答案页" % qid)
            book, pages = pair
        else:
            # The case workbooks split question and solution: the stem lives in
            # the blank volume, the solution in the exercise-set volume.
            prefer = "blank" if (not answer and qid[0] == "C") else "solutions"
            book, pages, _, _ = CQ.resolve_qid(qid, prefer=prefer)

        number = int(qid.split("-")[-1])
        segments = CQ.plan_segments(book, pages, number, qid)
        if not segments:
            raise HTTPException(404, "题 %s 找不到题号位置" % qid)
        CQ.render_segments(book, segments, str(path))
        return path


def build_z_mask(qid, answer):
    """Split a Z crop at 【答案】 once, then serve the question or answer half."""
    qpath = crop_path(qid, False, mask=True)
    apath = crop_path(qid, True, mask=True)
    if qpath.exists() and apath.exists():
        return apath if answer else qpath

    with _crop_guard:
        lock = _crop_locks.setdefault(("mask", qid), threading.Lock())
    with lock:
        if qpath.exists() and apath.exists():
            return apath if answer else qpath
        full = build_crop(qid, False)
        # Reuse the warmed recognizer; the crop lock is already free here.
        with _ocr_lock:
            try:
                split = CQ.split_z_answer(
                    str(full), str(qpath), str(apath), engine=RQ.engine()
                )
            except Exception as exc:  # noqa: BLE001 - report, never leak
                raise HTTPException(409, "答案遮罩失败：%s" % exc)
        if not split:
            for path in (qpath, apath):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise HTTPException(409, "题 %s 没找到【答案】行，无法遮罩" % qid)
    return apath if answer else qpath


def answer_image(qid):
    """The crop M4 reads: the answer half for Z, the solution crop otherwise."""
    if qid.startswith("Z"):
        try:
            return build_z_mask(qid, True)
        except HTTPException:
            pass  # fall back to the whole crop; suggestions may be noisier
    return build_crop(qid, True)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/books")
def books():
    rows = []
    for row in book_rows():
        if row["family"] in ("C", "Z", "A", "K", "M"):
            rows.append(
                {
                    "short": row["short"],
                    "family": row["family"],
                    "role": row["role"],
                    "chapters": row["chapters"],
                    "pages": row["pages"],
                }
            )
    return {"books": rows, "chapters": catalog.chapter_keys(), "tags": TAGS}


# ---------------------------------------------------------------- cockpit

@app.get("/api/home")
def home():
    """Everything the landing screen needs, in one local round-trip."""
    conf = settings_store.load()
    data = analytics.build_home(
        store.learning_snapshot(),
        settings=conf,
        lookup=lookup(),
        exam_tags=exam_tags(),
        baseline=analytics.read_baseline(BASELINE_REPORT),
    )
    overview = store.review_overview()
    data["review"] = {
        "count": overview["count"],
        "est_minutes": overview["est_minutes"],
        "reasons": overview["reasons"],
        "session": _session_view(store.session()),
    }
    return data


@app.get("/api/stats")
def stats(days: int = 30):
    try:
        days = max(7, min(int(days), 180))
    except (TypeError, ValueError):
        days = 30
    conf = settings_store.load()
    return analytics.build_stats(
        store.learning_snapshot(),
        settings=conf,
        lookup=lookup(),
        exam_tags=exam_tags(),
        baseline=analytics.read_baseline(BASELINE_REPORT),
        chapters=catalog.chapters,
        days=days,
    )


@app.get("/api/settings")
def get_settings():
    return settings_store.load()


@app.patch("/api/settings")
def patch_settings(payload: dict = Body(...)):
    try:
        return settings_store.update(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- sources

@app.get("/api/standards")
def standards_map():
    """Nine categories -> core/companion PDFs, plus the local library list."""
    try:
        return standards.standard_map()
    except Exception as exc:  # noqa: BLE001 - a missing E: drive must not 500
        return {
            "categories": standards.category_rows(),
            "library": {"enabled": False, "dir": str(standards.STANDARDS_DIR),
                        "count": 0, "files": [],
                        "error": "规范库读取失败：%s" % exc},
        }


@app.get("/api/standards/file")
def standards_file(name: str):
    path = standards.file_path(name)
    if not path:
        raise HTTPException(404, "规范文件不存在")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path, media_type=media, headers={"Cache-Control": "max-age=3600"}
    )


@app.get("/api/handbook/meta")
def handbook_meta():
    try:
        return standards.meta()
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "error": str(exc), "index_ready": False}


@app.get("/api/handbook/search")
def handbook_search(q: str, limit: int = 20):
    try:
        return standards.search(q, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 - OCR text may be odd, never 500
        raise HTTPException(500, "手册检索失败：%s" % exc)


@app.get("/api/handbook/toc")
def handbook_toc():
    try:
        return standards.toc()
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "手册目录读取失败：%s" % exc)


@app.get("/api/handbook/page")
def handbook_page(p: int, dpi: int = 110):
    try:
        path, page_count = standards.page_image(p, dpi)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "手册页渲染失败：%s" % exc)
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "max-age=86400",
            "X-Page-Count": str(page_count),
        },
    )


@app.get("/manifest.webmanifest")
def web_manifest():
    return FileResponse(
        STATIC / "manifest.webmanifest", media_type="application/manifest+json"
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/icons/{name}")
def app_icon(name: str):
    if not re.match(r"^[a-z0-9-]+\.png$", name or ""):
        raise HTTPException(404, "图标不存在")
    path = STATIC / "icons" / name
    if not path.exists():
        raise HTTPException(404, "图标不存在")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "max-age=604800"}
    )


@app.post("/api/recognize")
async def recognize_image(family: str = Form("C"), file: UploadFile = File(...)):
    """Stream recognition as newline-delimited JSON, one object per stage.

    The line channel is done well before the margin scan, and the page can
    show (or auto-open) those candidates while the rest still runs.  Stages:
    `head`, then `full` unless the head read was confident enough to stop; an
    `error` object ends the stream early.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - the phone can send anything
        raise HTTPException(400, "无法读取图片：%s" % exc)

    valid = valid_for(family)
    started = time.time()
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def push(payload):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except RuntimeError:  # the client hung up and the loop is gone
            pass

    def stage(name, snap):
        push(
            {
                "stage": name,
                "seconds": round(time.time() - started, 1),
                # The one read safe to open without asking; the benchmark and
                # the page use this same field.
                "auto": snap.get("auto"),
                "lines": snap.get("lines"),
                # The packing rule lives beside auto_qid so the benchmark and
                # the page cannot drift from the server.
                "candidates": RQ.pack_candidates(snap["candidates"]),
            }
        )

    def work():
        try:
            with _ocr_lock:
                result = RQ.recognize(
                    img, valid=valid, on_partial=lambda snap: stage("head", snap)
                )
            stage("full", result)
        except Exception as exc:  # noqa: BLE001 - report, do not hang the page
            push({"stage": "error", "detail": str(exc)})
        finally:
            push(None)

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def exam_tags():
    return load_exam_tags(INDEX)


def _describe(item):
    """Attach catalog display fields and Chinese tag labels to one attempt."""
    described = catalog.describe(item["qid"])
    key = category_key(item["qid"], exam_tags())
    described["category_key"] = key
    described["category_name"] = CASE_BY_KEY[key]["name"] if key else ""
    described["tag_labels"] = {
        key: TAGS[key] for key in item.get("tags", []) if key in TAGS
    }
    described["process_status"] = item.get("process_status")
    described["process_status_label"] = PROCESS_LABELS.get(
        item.get("process_status"), ""
    )
    described["process_photo"] = item.get("process_photo")
    described["attempt"] = {
        key: item[key]
        for key in ("id", "times", "tags", "status", "note")
        if key in item
    }
    return described


@app.get("/api/resolve")
def resolve(qid: str, family: str = "C"):
    key = normalize_qid(qid, family)
    if not key:
        raise HTTPException(400, "题号格式看不懂：%s" % qid)
    if not qid_exists(key):
        raise HTTPException(404, "题号 %s 不在当前题库里" % key)
    info = catalog.describe(key)
    category = category_key(key, exam_tags())
    info["category_key"] = category
    info["category_name"] = CASE_BY_KEY[category]["name"] if category else ""
    if key.startswith(("C", "Z")):
        entry = lookup()["chapter"][key]
        info["books"] = list(entry.get("books", {}).keys())
    else:
        entry = lookup()["exam"].get(key) or lookup()["mock"].get(key) or {}
        info["pages"] = entry.get("q", [])
    attempt = store.get_attempt_by_qid(key)
    info["attempt"] = (
        {
            key2: attempt[key2]
            for key2 in ("id", "times", "tags", "status", "note")
        }
        if attempt
        else None
    )
    if attempt:
        info["tag_labels"] = {
            key2: TAGS[key2] for key2 in attempt["tags"] if key2 in TAGS
        }
    return info


@app.post("/api/photos")
async def upload_photo(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    try:
        return store.save_photo(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/photos/{sha}")
def get_photo(sha: str, size: str = "thumb"):
    path = store.photo_file(sha, "thumb" if size == "thumb" else "orig")
    if not path:
        raise HTTPException(404, "照片不存在")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"}
    )


@app.post("/api/attempts")
def create_attempts(payload: dict = Body(...)):
    """Batch-mark questions.  Re-marking bumps `times`, never duplicates."""
    family = (payload.get("family") or "C").upper()
    raw_qids = payload.get("qids") or []
    if not isinstance(raw_qids, list) or not raw_qids:
        raise HTTPException(400, "至少给一个题号")
    if len(raw_qids) > 50:
        raise HTTPException(400, "一次最多记 50 题")
    tags = payload.get("tags")
    if tags is not None and not tags:
        tags = None  # an empty tick set keeps whatever tags were there
    note = payload.get("note")
    note = None if note is None or note == "" else str(note)
    source = payload.get("source") or ("batch" if len(raw_qids) > 1 else "single")
    photo_sha = payload.get("photo_sha")

    rows, errors = [], []
    for raw in raw_qids:
        key = normalize_qid(str(raw), family)
        if not key:
            errors.append({"qid": raw, "detail": "题号格式看不懂"})
            continue
        if not catalog.exists(key):
            errors.append({"qid": key, "detail": "题库里没有这个题号"})
            continue
        rows.append(
            {
                "qid": key,
                "family": key[0],
                "tags": tags,
                "note": note,
                "source": source,
                "photo_sha": photo_sha,
            }
        )
    try:
        result = store.add_attempts(rows)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    for item in result["created"] + result["updated"]:
        item.update(_describe(item))
    result["errors"].extend(errors)
    return result


@app.get("/api/attempts")
def list_attempts(
    family: str = None,
    tag: str = None,
    chapter: str = None,
    status: str = None,
    days: int = 0,
    qid: str = None,
    limit: int = 50,
    offset: int = 0,
):
    key = None
    if qid:
        key = normalize_qid(qid, family or "C")
        if not key:
            raise HTTPException(400, "题号格式看不懂：%s" % qid)
    try:
        result = store.list_attempts(
            family=family,
            tag=tag,
            chapter=chapter,
            status=status,
            days=days or None,
            qid=key,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    for item in result["items"]:
        item.update(_describe(item))
    return result


@app.get("/api/attempts/{attempt_id}")
def get_attempt(attempt_id: int):
    item = store.get_attempt(attempt_id)
    if not item:
        raise HTTPException(404, "错题记录不存在")
    item.update(_describe(item))
    return item


@app.get("/api/attempts/{attempt_id}/suggest")
def suggest_attempt(attempt_id: int):
    """M4: read this question's solution and propose error-cause tags.

    Non-blocking by contract: the page shows the panel while this runs, and a
    failed OCR returns an `error` string instead of breaking the detail view.
    """
    item = store.get_attempt(attempt_id)
    if not item:
        raise HTTPException(404, "错题记录不存在")
    qid = item["qid"]
    started = time.time()
    try:
        path = answer_image(qid)
        with _ocr_lock:
            result = analyze_crop(qid, str(path), ANSWER_OCR, engine=RQ.engine())
    except HTTPException as exc:
        return {
            "qid": qid,
            "error": str(exc.detail),
            "citations": [],
            "candidates": [],
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - a suggestion must never 500
        return {
            "qid": qid,
            "error": "解析读取失败：%s" % exc,
            "citations": [],
            "candidates": [],
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    result.update({
        "qid": qid,
        "elapsed_ms": int((time.time() - started) * 1000),
    })
    # A citation is only useful if it opens the book.  Match the OCR'd code
    # (or, failing that, its title) against the local 2024-outline library.
    try:
        files = standards.load_library()["files"]
        for citation in result.get("citations", []):
            citation["file"] = standards.match_standard(
                citation.get("title"), citation.get("code"), files
            )
    except Exception:  # noqa: BLE001 - the panel still works without links
        for citation in result.get("citations", []):
            citation.setdefault("file", None)
    return result


@app.patch("/api/attempts/{attempt_id}")
def patch_attempt(attempt_id: int, payload: dict = Body(...)):
    kwargs = {}
    if "tags" in payload:
        kwargs["tags"] = payload.get("tags") or []
    if "note" in payload:
        kwargs["note"] = payload.get("note") or ""
    if "status" in payload:
        kwargs["status"] = payload.get("status")
    if "process_status" in payload:
        kwargs["process_status"] = payload.get("process_status")
    if "process_photo_sha" in payload:
        kwargs["process_photo_sha"] = payload.get("process_photo_sha")
    try:
        item = store.update_attempt(attempt_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not item:
        raise HTTPException(404, "错题记录不存在")
    item.update(_describe(item))
    return item


@app.delete("/api/attempts/{attempt_id}")
def delete_attempt(attempt_id: int):
    if not store.delete_attempt(attempt_id):
        raise HTTPException(404, "错题记录不存在")
    store.prune_orphan_photos()
    return {"deleted": True, "stats": store.stats()}


def _review_item(item):
    """One queue/session item with catalog display fields and its schedule."""
    described = _describe(item)
    for key in (
        "id",
        "times",
        "status",
        "interval_days",
        "next_at",
        "streak",
        "reviews",
        "last_review_at",
        "last_result",
        "stuck",
        "tags",
        "process_status",
        "process_photo",
    ):
        described[key] = item.get(key)
    return described


def _session_view(session):
    view = dict(session)
    view["items"] = [_review_item(item) for item in session.get("items", [])]
    view["count"] = len(view.get("queue") or [])
    return view


@app.get("/api/review/today")
def review_today():
    """Queue size, reasons and the resumable session; answers stay out."""
    overview = store.review_overview()
    overview["session"] = _session_view(store.session())
    return overview


@app.post("/api/review/session")
def review_start(payload: dict = Body(default={})):
    """One tap enters today's review: resume, or freeze a fresh queue."""
    try:
        limit = int(payload.get("limit") or 0) or None
        session = store.start_session(limit=limit)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return _session_view(session)


@app.get("/api/review/session")
def review_session(session_id: int = None):
    return _session_view(store.session(session_id=session_id))


@app.post("/api/review/grade")
def review_grade(payload: dict = Body(...)):
    try:
        session_id = int(payload.get("session_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "session_id 不对")
    qid = (payload.get("qid") or "").strip().upper()
    if not session_id or not qid:
        raise HTTPException(400, "缺 session_id 或题号")
    try:
        result = store.grade(
            session_id,
            qid,
            payload.get("result"),
            tags=payload.get("tags"),
            process_status=payload.get("process_status"),
            process_photo_sha=payload.get("process_photo_sha"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    item = result["attempt"]
    item.update(_describe(item))
    result["attempt"] = item
    result["session"] = _session_view(result["session"])
    return result


@app.post("/api/review/skip")
def review_skip(payload: dict = Body(...)):
    try:
        session_id = int(payload.get("session_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "session_id 不对")
    qid = (payload.get("qid") or "").strip().upper()
    if not session_id or not qid:
        raise HTTPException(400, "缺 session_id 或题号")
    try:
        return _session_view(store.skip(session_id, qid))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/review/undo")
def review_undo(payload: dict = Body(...)):
    try:
        session_id = int(payload.get("session_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "session_id 不对")
    if not session_id:
        raise HTTPException(400, "缺 session_id")
    try:
        result = store.undo(session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    result["session"] = _session_view(result["session"])
    return result


@app.get("/api/review/summary")
def review_summary(session_id: int):
    try:
        summary = store.session_summary(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    summary["mastered_items"] = [
        catalog.describe(qid) for qid in summary["mastered"]
    ]
    summary["recovered_items"] = [
        catalog.describe(qid) for qid in summary["recovered"]
    ]
    return summary


@app.get("/api/import/preview")
def import_preview(family: str = "C"):
    items = scan_photos(store, catalog, LEGACY_PHOTOS, family)
    return {
        "dir": str(LEGACY_PHOTOS),
        "items": items,
        "new": sum(item["new_count"] for item in items),
    }


@app.post("/api/import")
def run_import(payload: dict = Body(default={})):
    family = (payload.get("family") or "C").upper()
    shas = payload.get("shas") or None
    try:
        result = import_photos(store, catalog, LEGACY_PHOTOS, family, shas=shas)
    except Exception as exc:  # noqa: BLE001 - report instead of a dead page
        raise HTTPException(500, "导入失败：%s" % exc)
    result["stats"] = store.stats()
    return result


@app.get("/api/backup")
def backup():
    path = store.backup()
    return FileResponse(
        path, media_type="application/zip", filename=path.name
    )


@app.get("/api/print")
def print_questions(
    scope: str = "filter",
    session_id: int = None,
    family: str = None,
    tag: str = None,
    chapter: str = None,
    status: str = "active",
    days: int = 0,
):
    """One-click A4 sheet: question pages first, answers on their own pages."""
    scope = (scope or "filter").lower()
    if scope == "review":
        session = store.session(session_id=session_id) if session_id else store.session()
        qids = list(session.get("queue") or [])
        if not qids:
            qids = [item["qid"] for item in store.review_queue()]
        title = "错题复习卷"
    elif scope == "recent":
        qids = [
            item["qid"]
            for item in store.list_attempts(
                status="active", days=7, limit=MAX_QUESTIONS
            )["items"]
        ]
        title = "最近 7 天错题卷"
    else:
        qids = [
            item["qid"]
            for item in store.list_attempts(
                family=family or None,
                tag=tag or None,
                chapter=chapter or None,
                status=status or None,
                days=days or None,
                limit=MAX_QUESTIONS,
            )["items"]
        ]
        title = "错题筛选卷"
    qids = list(dict.fromkeys(qids))[:MAX_QUESTIONS]
    if not qids:
        raise HTTPException(404, "没有可打印的题")
    try:
        EXPORTS.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 7 * 86400
        for old in EXPORTS.glob("yantu-print-*.pdf"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = EXPORTS / ("yantu-print-%s.pdf" % stamp)
        header = "%s · %s" % (title, time.strftime("%Y-%m-%d"))
        scratch_root = CACHE.parent
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(scratch_root)) as tmp:
            stats = export_pdf(
                qids, out, header, tmp, engine=RQ.engine(), ocr_lock=_ocr_lock
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - one bad crop must not 500 the page
        raise HTTPException(500, "生成打印卷失败：%s" % exc)
    response = FileResponse(out, media_type="application/pdf", filename=out.name)
    response.headers["X-Print-Stats"] = json.dumps(stats, ensure_ascii=True)
    return response


@app.get("/api/baseline/file/{half}")
def baseline_file(half: str):
    """Serve one half's print-ready baseline paper from print_samples/."""
    key = (half or "").upper()
    if key not in ("A", "P"):
        raise HTTPException(404, "基线卷只有 A（上午）或 P（下午）")
    path = ROOT / "print_samples" / ("baseline_2025_%s.pdf" % key)
    if not path.exists():
        raise HTTPException(404, "基线卷还没生成：%s" % path.name)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/crop")
def crop(qid: str, family: str = "C", answer: int = 0, mask: int = 0):
    key = normalize_qid(qid, family)
    if not key or not qid_exists(key):
        raise HTTPException(404, "题号不存在")
    try:
        path = build_crop(key, bool(answer), mask=bool(mask))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "裁图失败：%s" % exc)
    return FileResponse(path, media_type="image/png")


def lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except Exception:  # noqa: BLE001 - offline is fine
        return "127.0.0.1"
    finally:
        sock.close()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print("岩土错题本 M5  ->  http://%s:%d" % (lan_ip(), port), flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
