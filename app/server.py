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
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import crop_question as CQ  # noqa: E402
import recognize_qid as RQ  # noqa: E402
from app import analytics, baseline as baseline_app, briefing as briefing_app, clauses as clauses_app, drill as drill_app, plan as study_plan, reading as reading_app, report as report_app, standards  # noqa: E402
from app.answer_ocr import analyze_crop  # noqa: E402
from app.net import lan_ip  # noqa: E402
from app.catalog import Catalog  # noqa: E402
from app.curriculum import CASE_BY_KEY, category_key, load_exam_tags  # noqa: E402
from app.photo_import import import_photos, scan_photos  # noqa: E402
from app.print_export import (  # noqa: E402
    MAX_QUESTIONS, MAX_WEEK_QUESTIONS, export_pdf, parse_qid_list,
    print_filename,
)
from app.settings import Settings  # noqa: E402
from app.store import PRACTICE_SOURCES, PROCESS_LABELS, Store, TAGS  # noqa: E402

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

app = FastAPI(title="岩土学习驾驶舱 M12.1")


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
        baseline=baseline_view(),
    )
    overview = store.review_overview()
    data["review"] = {
        "count": overview["count"],
        "est_minutes": overview["est_minutes"],
        "reasons": overview["reasons"],
        "session": _session_view(store.session()),
    }
    plan_view = study_plan.overview(store, DATA, conf, baseline=baseline_view())
    data["plan"] = {
        "phase": plan_view["phase"],
        "week": plan_view["week"],
        "targets": plan_view["targets"],
        "advice": plan_view["advice"][:1],
    }
    try:
        read_view = reading_app.overview(store)
        data["reading"] = {
            "stats": read_view["stats"],
            "recommendation": read_view["recommendation"],
        }
    except Exception:  # noqa: BLE001 - reading is a bonus, home must load
        data["reading"] = None
    return data


@app.get("/api/briefing")
def briefing():
    """The "what do I do today" sheet; the phone opens it once per day."""
    conf = settings_store.load()
    try:
        reading_view = reading_app.overview(store)
    except Exception:  # noqa: BLE001 - a missing route must not block the day
        reading_view = None
    try:
        plan_view = study_plan.overview(store, DATA, conf)
    except Exception:  # noqa: BLE001 - the daily sheet still has review
        plan_view = None
    return briefing_app.build(
        store, conf, reading=reading_view, baseline=baseline_view(),
        plan=plan_view,
    )


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
        baseline=baseline_view(),
        chapters=catalog.chapters,
        days=days,
    )


@app.get("/api/report")
def get_report(period: str = "week", anchor: str = ""):
    try:
        return report_app.build(
            store, DATA, settings_store.load(),
            exam_tags=exam_tags(), baseline=baseline_view(),
            period=period, anchor=anchor or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/report/save")
def save_report(payload: dict = Body(...)):
    try:
        data = report_app.build(
            store, DATA, settings_store.load(),
            exam_tags=exam_tags(), baseline=baseline_view(),
            period=payload.get("period") or "week",
            anchor=payload.get("anchor") or None,
        )
        saved = report_app.save(DATA, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "report": data,
        "saved": saved,
        "reports": report_app.list_saved(DATA),
    }


@app.get("/api/reports")
def list_reports():
    return {"reports": report_app.list_saved(DATA), "dir": "data/reports"}


@app.get("/api/reports/{key}")
def get_saved_report(key: str):
    try:
        return report_app.load_saved(DATA, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except KeyError:
        raise HTTPException(404, "报告不存在")


@app.get("/api/reports/{key}/file")
def download_report(key: str):
    try:
        path = report_app.report_file(DATA, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except KeyError:
        raise HTTPException(404, "报告不存在")
    return FileResponse(path, media_type="text/markdown; charset=utf-8",
                        filename=path.name)


@app.delete("/api/reports/{key}")
def delete_report(key: str):
    try:
        removed = report_app.delete_saved(DATA, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not removed:
        raise HTTPException(404, "报告不存在")
    return {"deleted": True, "reports": report_app.list_saved(DATA)}


@app.get("/api/plan")
def get_plan():
    view = baseline_view()
    return study_plan.overview(store, DATA, settings_store.load(), baseline=view)


@app.patch("/api/plan")
def patch_plan(payload: dict = Body(...)):
    try:
        study_plan.save_config(DATA, payload.get("weekly_hours"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    view = baseline_view()
    return study_plan.overview(store, DATA, settings_store.load(), baseline=view)


@app.get("/api/baseline")
def get_baseline():
    return baseline_payload()


@app.put("/api/baseline/{half}")
def put_baseline(half: str, payload: dict = Body(...)):
    try:
        result = baseline_app.save(store, half, payload, exam_tags())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    view = baseline_payload()
    return {
        "baseline": view,
        "plan": study_plan.overview(store, DATA, settings_store.load(),
                                    baseline=view),
    }


@app.delete("/api/baseline/{half}")
def delete_baseline(half: str):
    key = (half or "").strip().upper()
    if key not in ("A", "P"):
        raise HTTPException(404, "基线只有上午卷 A 或下午卷 P")
    if not store.delete_baseline(key, baseline_app.YEAR):
        raise HTTPException(404, "这一半还没有基线记录")
    view = baseline_payload()
    return {
        "deleted": True,
        "baseline": view,
        "plan": study_plan.overview(store, DATA, settings_store.load(),
                                    baseline=view),
    }


BASELINE_IMPORT_KINDS = {
    "wrong": "2025 基线·错题",
    "skipped": "2025 基线·放弃",
    "overtime": "2025 基线·超时做对",
}

# Full-paper re-sits use a past paper with unseen answers.  2018+ papers have
# 25 questions per half, which is what the baseline scoring contract expects.
RETEST_MIN_YEAR = 2018
RETEST_MAX_YEAR = 2024
RETEST_DEFAULT_YEAR = 2024


def _retest_path(year, half):
    return ROOT / "print_samples" / ("exam_%d_%s.pdf" % (int(year), half))


def _require_retest_year(year):
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise HTTPException(400, "复测年份必须是整数")
    if not RETEST_MIN_YEAR <= year <= RETEST_MAX_YEAR:
        raise HTTPException(
            400, "整卷复测只支持 %d–%d 年；2025 已在基线页"
            % (RETEST_MIN_YEAR, RETEST_MAX_YEAR))
    return year


def _retest_payload(year):
    year = _require_retest_year(year)
    view = baseline_app.summary(store, exam_tags(), year=year)
    view["year"] = year
    view["kind"] = "retest"
    view["papers"] = {
        half: _retest_path(year, half).exists() for half in ("A", "P")
    }
    return view


@app.get("/api/retest")
def get_retest(year: int = RETEST_DEFAULT_YEAR):
    return _retest_payload(year)


@app.put("/api/retest/{year}/{half}")
def put_retest(year: int, half: str, payload: dict = Body(...)):
    year = _require_retest_year(year)
    try:
        baseline_app.save(store, half, payload, exam_tags(), year=year,
                          require_paper=False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    view = _retest_payload(year)
    return {
        "retest": view,
        "plan": study_plan.overview(store, DATA, settings_store.load(),
                                    baseline=baseline_view()),
    }


@app.delete("/api/retest/{year}/{half}")
def delete_retest(year: int, half: str):
    year = _require_retest_year(year)
    key = (half or "").strip().upper()
    if key not in ("A", "P"):
        raise HTTPException(404, "整卷只有上午卷 A 或下午卷 P")
    if not store.delete_baseline(key, year):
        raise HTTPException(404, "这一半还没有复测记录")
    view = _retest_payload(year)
    return {
        "deleted": True,
        "retest": view,
        "plan": study_plan.overview(store, DATA, settings_store.load(),
                                    baseline=baseline_view()),
    }


@app.get("/api/retest/file/{year}/{half}")
def retest_file(year: int, half: str):
    key = (half or "").upper()
    if key not in ("A", "P"):
        raise HTTPException(404, "整卷只有上午卷 A 或下午卷 P")
    year = _require_retest_year(year)
    path = _retest_path(year, key)
    if not path.exists():
        raise HTTPException(
            404, "%d 年%s卷还没生成：py -3 tools/print_exam.py --year %d"
            % (year, "上午" if key == "A" else "下午", year))
    return FileResponse(path, media_type="application/pdf", filename=path.name)


def _import_baseline(half, kind, triage=None):
    """Put one baseline list into the review queue, idempotently."""
    key = (half or "").strip().upper()
    if key not in ("A", "P"):
        raise HTTPException(404, "基线只有上午卷 A 或下午卷 P")
    kind = (kind or "wrong").strip().lower()
    if kind not in BASELINE_IMPORT_KINDS:
        raise HTTPException(400, "导入类型只能是 wrong / skipped / overtime")
    try:
        qids = baseline_app.qids_for_import(store, key, kind, triage)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not qids:
        return {"added": 0, "updated": 0, "skipped": 0, "errors": [],
                "qids": [], "kind": kind}
    rows = [
        {
            "qid": qid,
            "family": qid[0],
            "tags": None,
            "note": BASELINE_IMPORT_KINDS[kind],
            "source": "batch",
        }
        for qid in qids
    ]
    result = store.add_attempts(rows, skip_existing=True)
    for item in result["created"] + result["updated"]:
        item.update(_describe(item))
    return {
        "added": len(result["created"]),
        "updated": len(result["updated"]),
        "skipped": len(result["skipped"]),
        "errors": result["errors"],
        "qids": qids,
        "kind": kind,
    }


@app.post("/api/baseline/{half}/wrongs")
def import_baseline_wrongs(half: str):
    """Backwards-compatible shortcut for the wrong-question import."""
    return _import_baseline(half, "wrong")


@app.post("/api/baseline/{half}/import")
def import_baseline_list(half: str, payload: dict = Body(default=None)):
    payload = payload or {}
    return _import_baseline(
        half, payload.get("kind") or "wrong", payload.get("triage")
    )


@app.post("/api/baseline/{half}/triage")
def triage_baseline_question(half: str, payload: dict = Body(...)):
    """Label an abandoned question: no_time (clock) or no_idea (knowledge)."""
    try:
        row = baseline_app.set_triage(
            store, half, payload.get("qid"), payload.get("triage")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"triage": row, "baseline": baseline_payload()}


@app.get("/api/drill/overview")
def drill_overview():
    return drill_app.overview(store, baseline_view(), qid_exists)


@app.post("/api/drill")
def drill_start(payload: dict = Body(...)):
    try:
        return drill_app.start(store, payload, baseline_view(), qid_exists)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/drill/{session_id}")
def drill_state(session_id: int):
    try:
        return drill_app.get_state(store, session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/drill/{session_id}/item")
def drill_record(session_id: int, payload: dict = Body(...)):
    try:
        return drill_app.record(store, session_id, payload, exam_tags())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/drill/{session_id}/finish")
def drill_finish(session_id: int):
    try:
        return drill_app.finish(store, session_id, exam_tags())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/plan/sessions")
def plan_sessions(limit: int = 30):
    return {
        "sessions": store.list_practice_sessions(limit=limit),
        "sources": PRACTICE_SOURCES,
    }


@app.post("/api/plan/sessions")
def create_plan_session(payload: dict = Body(...)):
    try:
        session = store.add_practice_session(
            day=payload.get("day"),
            source=payload.get("source") or "exercise",
            category=payload.get("category") or "mixed",
            attempted=payload.get("attempted"),
            correct=payload.get("correct"),
            abandoned=payload.get("abandoned") or 0,
            overtime=payload.get("overtime") or 0,
            minutes=payload.get("minutes") or 0,
            note=payload.get("note") or "",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    view = baseline_view()
    return {
        "session": session,
        "plan": study_plan.overview(store, DATA, settings_store.load(),
                                    baseline=view),
    }


@app.delete("/api/plan/sessions/{session_id}")
def delete_plan_session(session_id: int):
    try:
        deleted = store.delete_practice_session(session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "练习记录不存在")
    view = baseline_view()
    return {"deleted": True, "plan": study_plan.overview(store, DATA,
                                                         settings_store.load(),
                                                         baseline=view)}


@app.patch("/api/plan/progress/{key:path}")
def patch_plan_progress(key: str, payload: dict = Body(...)):
    if not study_plan.valid_progress_key(key):
        raise HTTPException(400, "不认识的计划项：%s" % key)
    try:
        row = store.set_plan_progress(
            key,
            status=payload.get("status") or "done",
            accuracy=payload.get("accuracy"),
            note=payload.get("note") or "",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    view = baseline_view()
    return {"progress": row, "plan": study_plan.overview(store, DATA,
                                                         settings_store.load(),
                                                         baseline=view)}


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


@app.get("/api/standards/index")
def standards_index():
    index = standards.load_standard_index()
    return {
        "built_at": index.get("built_at"),
        "folder": index.get("folder"),
        "standards": standards.standard_summaries(),
    }


@app.get("/api/standards/toc")
def standards_toc(code: str):
    result = standards.standard_toc(code)
    if not result:
        raise HTTPException(404, "规范索引里没有：%s" % code)
    return result


@app.get("/api/standards/locate")
def standards_locate(code: str, clause: str):
    result = standards.locate_clause(code, clause)
    if not result:
        raise HTTPException(404, "没定位到条文：%s %s" % (code, clause))
    return result


@app.get("/api/standards/search")
def standards_search(code: str, q: str, limit: int = 20):
    return standards.search_standard(code, q, limit=limit)


@app.get("/api/standards/page")
def standards_page(code: str, p: int, dpi: int = 110):
    try:
        path, page_count, standard = standards.standard_page(code, p, dpi)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "规范页渲染失败：%s" % exc)
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "max-age=86400",
            "X-Page-Count": str(page_count),
            "X-Standard": standards.header_safe(
                standard.get("code_key") or standard.get("code")),
        },
    )


@app.get("/api/standards/file")
def standards_file(name: str):
    path = standards.file_path(name)
    if not path:
        raise HTTPException(404, "规范文件不存在")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path, media_type=media, headers={"Cache-Control": "max-age=3600"}
    )


@app.get("/api/reading")
def reading_overview():
    """Exam-driven standards reading: routes, progress and what to read next."""
    try:
        return reading_app.overview(store)
    except Exception as exc:  # noqa: BLE001 - a missing E: drive must not 500
        raise HTTPException(500, "伴读数据读取失败：%s" % exc)


@app.get("/api/reading/{code_key}")
def reading_detail(code_key: str):
    data = reading_app.detail(code_key, store.reading_progress())
    if not data:
        raise HTTPException(404, "没有这本规范的伴读路线：%s" % code_key)
    return data


@app.post("/api/reading/{code_key}/{stop_id}")
def reading_record(code_key: str, stop_id: str, payload: dict = Body(...)):
    try:
        saved = reading_app.record(
            store, code_key, stop_id,
            payload.get("status") or "done",
            seconds=payload.get("seconds") or 0,
            note=payload.get("note") or "",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "progress": saved,
        "overview": reading_app.overview(store),
        "detail": reading_app.detail(code_key, store.reading_progress()),
    }


@app.get("/api/reading/{code_key}/{stop_id}/page")
def reading_page(code_key: str, stop_id: str, dpi: int = 140, p: int = 0):
    """Annotated page image; `p` flips within the scanned standard."""
    try:
        dpi = max(80, min(int(dpi), 200))
    except (TypeError, ValueError):
        dpi = 140
    if p:
        rendered = reading_app.annotated_page_at(code_key, p, dpi=dpi)
    else:
        rendered = reading_app.annotated_page(code_key, stop_id, dpi=dpi)
    source = "annotated"
    if rendered is None:
        fallback = reading_app.fallback_for(code_key, stop_id)
        if fallback is None:
            raise HTTPException(404, "这一站没有可用的规范页")
        rendered = reading_app.fallback_page(fallback[0], fallback[1], dpi=dpi)
        source = "clean"
    if rendered is None:
        raise HTTPException(404, "规范页渲染失败（E: 盘可能不在线）")
    path, page, count = rendered
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "max-age=86400",
            "X-Page-Count": str(count),
            "X-Page": str(page),
            "X-Source": source,
        },
    )


@app.get("/api/reading/{code_key}/file")
def reading_file(code_key: str):
    path = reading_app.annotated_file(code_key)
    if not path:
        raise HTTPException(404, "标注版规范不在本机")
    return FileResponse(
        path, media_type="application/pdf",
        headers={"Cache-Control": "max-age=3600"},
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


def baseline_view():
    """DB baseline first; the legacy markdown report is a read-only fallback."""
    data = baseline_app.summary(store, exam_tags())
    if data.get("available"):
        return data
    legacy = analytics.read_baseline(BASELINE_REPORT)
    if legacy.get("available"):
        legacy["source"] = "file"
        legacy["complete"] = len(legacy.get("halves") or []) >= 2
        legacy["passed"] = bool(legacy.get("complete")
                                and (legacy.get("score") or 0) >= 60)
        return legacy
    return data


def baseline_payload():
    data = baseline_view()
    data["questions"] = baseline_app.paper_questions(exam_tags())
    data.setdefault("year", baseline_app.YEAR)
    data.setdefault("pass_line", baseline_app.PASS_SCORE)
    return data


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
            clauses = citation.get("clauses") or []
            citation["locate"] = (
                standards.locate_clause(citation.get("code"), clauses[0])
                if clauses else None
            )
    except Exception:  # noqa: BLE001 - the panel still works without links
        for citation in result.get("citations", []):
            citation.setdefault("file", None)
            citation.setdefault("locate", None)
    return result


@app.get("/api/attempts/{attempt_id}/clauses")
def attempt_clauses(attempt_id: int):
    """M11: clause-level hints for one wrong-book question.

    Never blocks the detail page: missing OCR cache, missing category or an
    unreadable index all degrade to fewer hints or an `error` string.
    """
    item = store.get_attempt(attempt_id)
    if not item:
        raise HTTPException(404, "错题记录不存在")
    try:
        return clauses_app.hints(item["qid"])
    except Exception as exc:  # noqa: BLE001 - hints are advisory
        return {
            "qid": item["qid"],
            "hints": [],
            "unresolved": [],
            "coverage": {},
            "error": "条文推荐读取失败：%s" % exc,
        }


def _all_attempts():
    """Every wrong-book attempt, not just the first list page."""
    items = []
    offset = 0
    while True:
        page = store.list_attempts(limit=200, offset=offset)
        items.extend(page["items"])
        if not page.get("has_more") or not page["items"]:
            break
        offset += len(page["items"])
    return items


@app.get("/api/clause-index")
def clause_index(include_mastered: int = 0):
    """M12: clause -> wrong-book questions, exact evidence first.

    Advisory like M11: any failure returns an `error` instead of a 500 so the
    data page can still render the rest of the cockpit.
    """
    try:
        result = clauses_app.reverse(
            _all_attempts(), include_mastered=bool(include_mastered)
        )
        result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return result
    except Exception as exc:  # noqa: BLE001 - the page degrades, not breaks
        return {
            "error": "依据清单生成失败：%s" % exc,
            "summary": {},
            "exact": [],
            "suggested": [],
            "unmatched": [],
            "unresolved": [],
            "errors": [],
        }


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


@app.get("/api/storage")
def storage():
    return store.storage_stats()


@app.post("/api/storage/prune")
def prune_storage(payload: dict = Body(...)):
    if not payload.get("confirm"):
        raise HTTPException(400, "清理旧过程照片需要确认")
    try:
        return store.prune_process_photos(
            keep=payload.get("keep", 2)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/print")
def print_questions(
    scope: str = "filter",
    qids: str = None,
    title: str = None,
    session_id: int = None,
    family: str = None,
    tag: str = None,
    chapter: str = None,
    status: str = "active",
    days: int = 0,
    writing_scale: float = 1.0,
    result_boxes: int = 0,
    cover: int = 0,
    per_prototype: int = 5,
    jpeg_dpi: int = 220,
    jpeg_quality: int = 85,
):
    """One-click A4 sheet: question pages first, answers on their own pages."""
    scope = (scope or "filter").lower()
    cover_data = None
    if scope == "qids":
        # A hand-picked set from the viewer, a wrong-book detail or a drill
        # session.  Only full keys ("C3-11", "A2025-A-13") are accepted: a bare
        # "3-11" is ambiguous between families and must be resolved upstream.
        qids = [qid for qid in parse_qid_list(qids) if qid_exists(qid)]
        if not qids:
            raise HTTPException(400, "没有可打印的题（题号无效或不在题库）")
        title = re.sub(r"\s+", " ", (title or "").strip())[:60] or "自选题目卷"
        if cover:
            cover_data = {
                "title": title,
                "subtitle": "自选 %d 题" % len(qids),
                "qids": qids,
            }
    elif scope == "plan":
        selection = study_plan.week_drill(
            store, DATA, settings_store.load(),
            per_prototype=per_prototype, limit=MAX_QUESTIONS,
        )
        qids = selection["qids"]
        phase = selection.get("phase") or {}
        week = selection.get("week") or {}
        title = "第 %s 周任务卷" % phase.get("week", "?")
        if phase.get("key") == "baseline":
            subtitle = "基线准备 · 先交 2025 上午/下午基线，不按周题量"
        else:
            subtitle = "%s · 第 %s/%s 周 · 目标 %s 题" % (
                phase.get("name", ""), phase.get("week", "?"),
                phase.get("weeks", "?"), week.get("target", "?"))
        cover_data = {
            "title": "本周任务卷",
            "subtitle": subtitle,
            "tasks": selection.get("tasks") or [],
            "qids": qids,
        }
    elif scope == "review":
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
    elif scope == "week":
        # Travel pack: the next N days projected from the current schedule.
        # Results typed into the app later move the real dates; the cover says
        # so instead of pretending the forecast is a promise.
        try:
            days = max(1, min(int(days or 7), 10))
        except (TypeError, ValueError):
            days = 7
        forecast = store.review_forecast(days=days)
        sections = []
        for index, bucket in enumerate(forecast, 1):
            day_qids = bucket["qids"]
            if not day_qids:
                continue
            sections.append({
                "title": "第 %d 天 · %s %s" % (
                    index, bucket["day"][5:], bucket["weekday"]),
                "subtitle": "复习 %d 题 · 预计 %d 分钟" % (
                    len(day_qids), max(1, len(day_qids) * 2)),
                "short": "D%d" % index,
                "qids": day_qids,
            })
        # The week's new-question drill rides along: a travel pack that only
        # held a thin review queue would not be "a week's worth".
        week_qids = []
        try:
            selection = study_plan.week_drill(
                store, DATA, settings_store.load(),
                per_prototype=per_prototype, limit=MAX_QUESTIONS,
            )
            seen = {qid for section in sections for qid in section["qids"]}
            week_qids = [qid for qid in selection["qids"] if qid not in seen]
        except Exception:  # noqa: BLE001 - review pages are still worth printing
            week_qids = []
        review_total = sum(len(section["qids"]) for section in sections)
        room, dropped = MAX_WEEK_QUESTIONS - len(week_qids), 0
        kept = []
        for section in sections:
            if room <= 0:
                dropped += len(section["qids"])
                continue
            if len(section["qids"]) > room:
                dropped += len(section["qids"]) - room
                section["qids"] = section["qids"][:room]
            room -= len(section["qids"])
            kept.append(section)
        sections = kept
        if week_qids:
            sections.append({
                "title": "本周任务 · 未完成原型",
                "subtitle": "新题 %d 题 · 做完在计划页记四个数" % len(week_qids),
                "short": "W",
                "qids": week_qids,
            })
        if not sections:
            raise HTTPException(404, "未来 %d 天没有要复习的题" % days)
        title = "未来 %d 天复习包" % days
        subtitle = "%s 起 · 按当前排期 复习 %d 题" % (
            forecast[0]["day"], review_total - dropped)
        if week_qids:
            subtitle += " + 新题 %d 题" % len(week_qids)
        if dropped:
            subtitle += "（只排前 %d 题）" % MAX_WEEK_QUESTIONS
        subtitle += "；今天做对/做错会改变后面几天"
        cover_data = {
            "title": title,
            "subtitle": subtitle,
            "tasks": [
                {"label": "%s · %d 题" % (section["title"],
                                          len(section["qids"]))}
                for section in sections
            ],
            "qids": [],
        }
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
        label = []
        if family:
            label.append({"C": "案例", "Z": "知识"}.get(family.upper(), family))
        if tag:
            label.append(TAGS.get(tag, tag))
        if chapter:
            label.append(chapter)
        if days:
            label.append("近%d天" % days)
        if (status or "") == "active":
            label.append("未掌握")
        title = "错题卷" + ("-" + "-".join(label) if label else "")
    if scope != "week":
        qids = list(dict.fromkeys(qids))[:MAX_QUESTIONS]
        if not qids:
            raise HTTPException(404, "没有可打印的题")
    try:
        EXPORTS.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 7 * 86400
        # Old naming plus the current 岩土-<title>-<stamp> names both expire.
        for pattern in ("yantu-print-*.pdf", "岩土-*.pdf"):
            for old in EXPORTS.glob(pattern):
                try:
                    if old.stat().st_mtime < cutoff:
                        old.unlink()
                except OSError:
                    pass
        filename = print_filename(title)
        out = EXPORTS / filename
        header = "%s · %s" % (title, time.strftime("%Y-%m-%d %H:%M"))
        scratch_root = CACHE.parent
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(scratch_root)) as tmp:
            jpeg = None
            if jpeg_dpi and jpeg_dpi > 0:
                jpeg = (max(120, min(int(jpeg_dpi), 300)),
                        max(60, min(int(jpeg_quality), 95)))
            stats = export_pdf(
                None if scope == "week" else qids, out, header, tmp,
                engine=RQ.engine(), ocr_lock=_ocr_lock,
                jpeg=jpeg,
                writing_scale=writing_scale,
                result_boxes=bool(result_boxes),
                cover=cover_data if cover else None,
                sections=sections if scope == "week" else None,
                max_questions=(MAX_WEEK_QUESTIONS if scope == "week"
                               else MAX_QUESTIONS),
            )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 - one bad crop must not 500 the page
        raise HTTPException(500, "生成打印卷失败：%s" % exc)
    stats["filename"] = out.name
    stats["scope"] = scope
    if scope == "week":
        stats["days"] = days
        stats["truncated"] = dropped
    response = FileResponse(out, media_type="application/pdf", filename=out.name)
    response.headers["X-Print-Stats"] = json.dumps(stats, ensure_ascii=True)
    response.headers["X-Print-Filename"] = quote(out.name, safe="")
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print("岩土学习驾驶舱 M12.1  ->  http://%s:%d" % (lan_ip(), port), flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
