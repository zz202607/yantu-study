"""Guided standards reading (规范伴读).

The static reading routes live in `app/reading_data.json`: for each core
standard, a short exam-driven sequence of sections.  A stop is deliberately
small (6–18 minutes) and answers three questions: why this section is tested,
what to look at in the original page, and where the usual mistake is.

Pages come from the annotated PDF set (red teacher marks baked into the
scan), with the indexed clean PDF as a fallback.  Progress is user data and
lives in SQLite; this module never copies or modifies a standard.
"""

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

try:  # PyMuPDF 1.24+ exposes the modern package name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older environments
    import fitz

from app import exam_refs, standards

ROOT = Path(__file__).resolve().parent.parent
GUIDES_PATH = ROOT / "app" / "reading_data.json"
ANNOTATED_INDEX_PATH = ROOT / "index" / "annotated_index.json"
DEFAULT_ANNOTATED_DIR = Path(
    r"E:\其他文件\2025YT\4标注版核心规范"
)
ANNOTATED_DIR = Path(
    os.environ.get("YANTU_ANNOTATED_DIR", DEFAULT_ANNOTATED_DIR)
)
PAGE_CACHE = ROOT / ".cache" / "reading_pages"

_GUIDE_CACHE = {"mtime": None, "data": None}
_ANNOTATED_CACHE = {"mtime": None, "data": None}
_PAGE_LOCK = threading.Lock()


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def load_guides():
    stamp = _mtime(GUIDES_PATH)
    with _PAGE_LOCK:
        if _GUIDE_CACHE["data"] is not None and _GUIDE_CACHE["mtime"] == stamp:
            return _GUIDE_CACHE["data"]
        data = json.loads(GUIDES_PATH.read_text(encoding="utf-8"))
        _GUIDE_CACHE.update(mtime=stamp, data=data)
        return data


def guide(code_key):
    for row in load_guides()["standards"]:
        if row["code_key"] == code_key:
            return row
    return None


def load_annotated():
    stamp = _mtime(ANNOTATED_INDEX_PATH)
    with _PAGE_LOCK:
        if _ANNOTATED_CACHE["data"] is not None and _ANNOTATED_CACHE["mtime"] == stamp:
            return _ANNOTATED_CACHE["data"]
        if not ANNOTATED_INDEX_PATH.exists():
            data = {"built_at": None, "folder": str(ANNOTATED_DIR), "files": {}}
        else:
            try:
                data = json.loads(ANNOTATED_INDEX_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {"built_at": None, "folder": str(ANNOTATED_DIR), "files": {}}
        _ANNOTATED_CACHE.update(mtime=stamp, data=data)
        return data


def annotated_entry(code_key):
    return (load_annotated().get("files") or {}).get(code_key)


def _toc_pdf_page(code, section):
    """Look up the clean-PDF page for a section number, if indexed."""
    index = standards.load_standard_index()
    for row in index.get("standards") or []:
        if row.get("code_key") != standards.code_key(code):
            continue
        toc = row.get("toc") or []
        number = (section or "").split("/")[0].strip()
        for entry in toc:
            if entry.get("number") == number and entry.get("pdf_page"):
                return int(entry["pdf_page"])
            # "附录K" is stored as a whole title, not a section number.
            if number.startswith("附录") and entry.get("title", "").startswith(number):
                return int(entry["pdf_page"])
        offset = int(row.get("offset") or 0)
        return None
    return None


def _stop_pdf_page(guide_row, stop):
    page = _toc_pdf_page(guide_row["code"], stop.get("section"))
    if page:
        return page
    offset = guide_row.get("fallback_offset")
    if offset is None:
        index = standards.load_standard_index()
        for row in index.get("standards") or []:
            if row.get("code_key") == guide_row["code_key"]:
                offset = int(row.get("offset") or 0)
                break
    return int(stop.get("page") or 0) + int(offset or 0)


def _summary(guide_row, stops, progress):
    done = sum(1 for stop in stops if (progress.get((guide_row["code_key"], stop["id"])) or {}).get("status") == "done")
    review = sum(1 for stop in stops if (progress.get((guide_row["code_key"], stop["id"])) or {}).get("status") == "review")
    total = len(stops)
    minutes_total = sum(int(stop.get("minutes") or 0) for stop in stops)
    minutes_done = sum(
        int(stop.get("minutes") or 0) for stop in stops
        if (progress.get((guide_row["code_key"], stop["id"])) or {}).get("status") == "done"
    )
    return {
        "total": total,
        "done": done,
        "review": review,
        "remaining": max(0, total - done),
        "minutes_total": minutes_total,
        "minutes_done": minutes_done,
        "percent": round(done / total, 3) if total else 0,
    }


def _decorate_stops(guide_row, progress, annotated):
    stops = []
    entries = (annotated or {}).get("stops") or {}
    for stop in guide_row["stops"]:
        row = dict(stop)
        saved = progress.get((guide_row["code_key"], stop["id"])) or {}
        row["status"] = saved.get("status")
        row["done"] = saved.get("status") == "done"
        row["seconds"] = saved.get("seconds", 0)
        row["note"] = saved.get("note", "")
        row["updated_at"] = saved.get("updated_at")
        row["annotated_page"] = entries.get(stop["id"])
        row["annotated_estimated"] = stop["id"] in ((annotated or {}).get("estimated") or [])
        row["pdf_page"] = _stop_pdf_page(guide_row, stop)
        stops.append(row)
    return stops


def _entry_two_up(code_key, entry):
    if not entry or not entry.get("file"):
        return False
    return _is_two_up(code_key, ANNOTATED_DIR / entry["file"])


def detail(code_key, progress=None):
    guide_row = guide(code_key)
    if not guide_row:
        return None
    progress = progress or {}
    annotated = annotated_entry(code_key) or {}
    stops = _decorate_stops(guide_row, progress, annotated)
    refs = exam_refs.for_standard(code_key)
    by_stop = exam_refs.for_stops(stops, code_key, refs=refs)
    for stop in stops:
        stop["exam_refs"] = by_stop.get(stop["id"], [])
        stop["exam_count"] = len(stop["exam_refs"])
    entry = annotated_entry(code_key) or {}
    payload = {
        "code_key": guide_row["code_key"],
        "code": guide_row["code"],
        "name": guide_row["name"],
        "short": guide_row["short"],
        "category_key": guide_row.get("category_key"),
        "category": guide_row.get("category"),
        "weight": guide_row.get("weight"),
        "order": guide_row.get("order"),
        "tagline": guide_row.get("tagline"),
        "why_read": guide_row.get("why_read"),
        "exam_evidence": guide_row.get("exam_evidence"),
        "stops": stops,
        "summary": _summary(guide_row, stops, progress),
        "exam": {
            "count": len(refs),
            "refs": refs,
            "coverage": exam_refs.summary(),
            "note": "只收录解析或题面明确引用本规范的真题；没写规范号的题不在内。",
        },
        "annotated": {
            "available": bool(entry.get("file")),
            "file": entry.get("file"),
            "page_count": entry.get("page_count"),
            "coverage": sum(1 for stop in stops if stop["annotated_page"]),
            # False means the client must not ask for half=left: the scan is
            # a single portrait page and cropping would remove content.
            "two_up": _entry_two_up(code_key, entry),
        },
    }
    return payload


def _next_stop(guide_row, stops):
    for stop in stops:
        if stop["status"] == "review":
            return stop
    for stop in stops:
        if not stop["status"]:
            return stop
    return None


def overview(store, now=None):
    now = now or datetime.now().astimezone()
    progress = store.reading_progress()
    guides = sorted(load_guides()["standards"], key=lambda row: row.get("order", 99))
    exam_counts = exam_refs.counts_by_standard()
    summaries = []
    by_code = {}
    for guide_row in guides:
        stops = _decorate_stops(guide_row, progress, annotated_entry(guide_row["code_key"]))
        summary = _summary(guide_row, stops, progress)
        nxt = _next_stop(guide_row, stops)
        by_code[guide_row["code_key"]] = {
            "code_key": guide_row["code_key"],
            "code": guide_row["code"],
            "name": guide_row["name"],
            "short": guide_row["short"],
            "category_key": guide_row.get("category_key"),
            "category": guide_row.get("category"),
            "order": guide_row.get("order"),
            "weight": guide_row.get("weight"),
            "tagline": guide_row.get("tagline"),
            "summary": summary,
            "next_stop": nxt,
            "annotated": bool((annotated_entry(guide_row["code_key"]) or {}).get("file")),
            "exam_count": exam_counts.get(guide_row["code_key"], 0),
        }
        summaries.append(by_code[guide_row["code_key"]])

    total = sum(row["summary"]["total"] for row in summaries)
    done = sum(row["summary"]["done"] for row in summaries)
    review = sum(row["summary"]["review"] for row in summaries)
    minutes_done = sum(row["summary"]["minutes_done"] for row in summaries)

    # The most recently touched standard gets first refusal, so "continue"
    # always resumes where the user left off.
    recent = sorted(
        ((key, value) for key, value in progress.items()),
        key=lambda row: row[1].get("updated_at") or "",
        reverse=True,
    )
    recommended = None
    for (code_key, _stop_id), _value in recent:
        row = by_code.get(code_key)
        if row and row["next_stop"]:
            recommended = row
            break
    if recommended is None:
        for row in summaries:
            if row["next_stop"]:
                recommended = row
                break
    if recommended is None and summaries:
        recommended = summaries[0]

    next_stop = recommended.get("next_stop") if recommended else None
    recommendation = None
    if recommended and next_stop:
        reason = "继续上次的路线" if recent else "按考试权重从第一本开始"
        if next_stop["status"] == "review":
            reason = "这一站标了“需回看”"
        recommendation = {
            "code_key": recommended["code_key"],
            "code": recommended["code"],
            "name": recommended["name"],
            "short": recommended["short"],
            "stop_id": next_stop["id"],
            "section": next_stop.get("section"),
            "title": next_stop.get("title"),
            "minutes": next_stop.get("minutes"),
            "page": next_stop.get("page"),
            "annotated_page": next_stop.get("annotated_page"),
            "reason": reason,
        }

    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
    logs = store.reading_log(since=week_start + "T00:00:00")
    today_key = now.date().isoformat()
    today_logs = [row for row in logs if (row.get("at") or "")[:10] == today_key]
    week_seconds = sum(int(row.get("seconds") or 0) for row in logs)
    today_seconds = sum(int(row.get("seconds") or 0) for row in today_logs)
    fallback = ANNOTATED_DIR.exists()

    return {
        "title": load_guides().get("title") or "规范伴读",
        "note": load_guides().get("note"),
        "built_at": load_annotated().get("built_at"),
        "annotated_dir": str(ANNOTATED_DIR),
        "annotated_available": fallback,
        "stats": {
            "stops_total": total,
            "stops_done": done,
            "stops_review": review,
            "minutes_done": minutes_done,
            "today_stops": len(today_logs),
            "today_minutes": round(today_seconds / 60),
            "week_stops": len(logs),
            "week_minutes": round(week_seconds / 60),
        },
        "standards": summaries,
        "recommendation": recommendation,
        "exam": exam_refs.summary(),
    }


def record(store, code_key, stop_id, status, seconds=0, note=""):
    guide_row = guide(code_key)
    if not guide_row:
        raise ValueError("没有这本规范的伴读路线：%s" % code_key)
    if stop_id not in {stop["id"] for stop in guide_row["stops"]}:
        raise ValueError("没有这一站：%s" % stop_id)
    return store.set_reading_progress(
        code_key, stop_id, status, seconds=seconds, note=note
    )


HALVES = ("left", "right")
_LAYOUT_CACHE = {}


def _clean_half(half):
    return half if half in HALVES else ""


def _is_two_up(code_key, path):
    """True when the scan really stores two copies side by side.

    Most annotated books are landscape 2-up spreads, but GB 50011 is a plain
    portrait scan.  Cropping that book would cut away half of the only copy,
    so the layout is detected from the PDF page rectangles and cached per file.
    """
    stamp = _mtime(path)
    key = (str(path), stamp)
    with _PAGE_LOCK:
        if key in _LAYOUT_CACHE:
            return _LAYOUT_CACHE[key]
    two_up = False
    try:
        doc = fitz.open(path)
        try:
            count = doc.page_count
            samples = sorted({
                1,
                max(1, count // 4),
                max(1, count // 2),
                max(1, (count * 3) // 4),
                count,
            })
            landscape = 0
            for number in samples:
                rect = doc[number - 1].rect
                if rect.width > rect.height * 1.1:
                    landscape += 1
            two_up = landscape * 2 > len(samples)
        finally:
            doc.close()
    except Exception:  # noqa: BLE001 - an unreadable PDF falls back to no crop
        two_up = False
    with _PAGE_LOCK:
        _LAYOUT_CACHE[key] = two_up
    return two_up


def _cache_path(code_key, page, dpi, half=""):
    suffix = ("_" + half[0]) if half else ""
    return PAGE_CACHE / code_key / ("p%d_%d%s.png" % (page, dpi, suffix))


def annotated_page_at(code_key, page, dpi=140, half=""):
    """Render any page of the annotated PDF for this standard.

    The annotated scans are 2-up: every PDF page holds the same printed page
    twice, side by side.  `half="left"|"right"` renders just one copy, which
    doubles the on-screen text size without any loss of content.
    """
    entry = annotated_entry(code_key)
    if not entry or not entry.get("file"):
        return None
    path = ANNOTATED_DIR / entry["file"]
    if not path.exists():
        return None
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    count = int(entry.get("page_count") or 0)
    if page < 1 or (count and page > count):
        return None
    half = _clean_half(half)
    if half and not _is_two_up(code_key, path):
        half = ""
    cache = _cache_path(code_key, page, int(dpi), half)
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        fresh = cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime
    except OSError:
        fresh = False
    if not fresh:
        with _PAGE_LOCK:
            if not (cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime):
                doc = fitz.open(path)
                try:
                    source = doc[page - 1]
                    clip = None
                    if half:
                        rect = source.rect
                        middle = rect.x0 + rect.width / 2.0
                        clip = (
                            fitz.Rect(rect.x0, rect.y0, middle, rect.y1)
                            if half == "left"
                            else fitz.Rect(middle, rect.y0, rect.x1, rect.y1)
                        )
                    pix = source.get_pixmap(dpi=int(dpi), clip=clip)
                    pix.save(cache)
                finally:
                    doc.close()
    return cache, page, count


def annotated_page(code_key, stop_id, dpi=140, half=""):
    """Render the page mapped to one reading stop."""
    entry = annotated_entry(code_key)
    if not entry:
        return None
    page = (entry.get("stops") or {}).get(stop_id)
    if not page:
        return None
    return annotated_page_at(code_key, page, dpi=dpi, half=half)


def fallback_page(code, pdf_page, dpi=120):
    """Render the clean indexed PDF when the annotated set is unavailable."""
    try:
        path, count, _standard = standards.standard_page(code, int(pdf_page), dpi)
    except Exception:  # noqa: BLE001 - E: may be offline
        return None
    return path, int(pdf_page), int(count or 0)


def fallback_for(code_key, stop_id):
    guide_row = guide(code_key)
    if not guide_row:
        return None
    for stop in guide_row["stops"]:
        if stop["id"] == stop_id:
            return guide_row["code"], _stop_pdf_page(guide_row, stop)
    return None


def annotated_file(code_key):
    entry = annotated_entry(code_key)
    if not entry or not entry.get("file"):
        return None
    base = Path(entry["file"])
    if base.name != entry["file"]:
        return None
    path = ANNOTATED_DIR / base.name
    return path if path.exists() else None
