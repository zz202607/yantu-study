"""Build a printed-page -> annotated-PDF-page map for the core standards.

The annotated set under `E:\\其他文件\\2025YT\\4标注版核心规范` has no text
layer and no PDF annotations: each PDF page is a 2-up scan of the *same*
printed book page, with the teacher's red marks baked into the image.  The
only machine-readable landmark is the printed page number in the footer.

This tool OCRs a thin footer strip on sampled pages, interpolates the
monotonic printed-page curve, then verifies every reading stop from
`app/reading_data.json` by OCRing a small window around the estimate.  The
result is `index/annotated_index.json`, consumed by `app/reading.py`.

Run it once, or after the annotated folder changes:

    PYTHONIOENCODING=utf-8 py -3 tools/build_annotated_index.py
    PYTHONIOENCODING=utf-8 py -3 tools/build_annotated_index.py --only "GB 50007"
    PYTHONIOENCODING=utf-8 py -3 tools/build_annotated_index.py --force

OCR is the only slow part (~2 s/page).  Re-running without --force reuses a
file whose size and mtime have not changed.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import standards  # noqa: E402

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older environments
    import fitz

import numpy as np  # noqa: E402

DEFAULT_DIR = Path(r"E:\其他文件\2025YT\4标注版核心规范")
GUIDES_PATH = ROOT / "app" / "reading_data.json"
OUT_PATH = ROOT / "index" / "annotated_index.json"
ANCHOR_STEP = 20
FRONT_STEP = 12
FRONT_PAGES = 48
SEARCH_RADIUS = 2

_ENGINE = {"engine": None}


def engine():
    if _ENGINE["engine"] is None:
        from rapidocr import RapidOCR

        _ENGINE["engine"] = RapidOCR()
    return _ENGINE["engine"]


def load_guides():
    return json.loads(GUIDES_PATH.read_text(encoding="utf-8"))


def annotated_files(folder):
    if not folder.exists():
        return []
    return sorted(
        (path for path in folder.iterdir()
         if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name,
    )


def _clean_stem(path):
    stem = path.stem
    stem = re.sub(r"[（(]\s*标注板?\s*[）)]", " ", stem)
    stem = re.sub(r"2本版本|A4本|\s*-\s*副本", " ", stem)
    stem = standards._PREFIX_RE.sub("", stem)
    return stem


def match_file(guide, files):
    """Match one guide to an annotated PDF by title containment."""
    wanted = standards.title_key(guide["name"])
    if len(wanted) < 4:
        return None
    scored = []
    for path in files:
        candidate = standards.title_key(_clean_stem(path))
        if wanted in candidate or candidate in wanted:
            scored.append((len(candidate), path))
    if not scored:
        return None
    scored.sort(key=lambda row: row[0])
    # A shorter filename is usually the exact title, not a collection name.
    return scored[0][1]


def _clean_number(text):
    clean = re.sub(r"\s+", "", str(text or ""))
    if re.fullmatch(r"\d{1,4}", clean):
        return int(clean)
    # OCR sometimes returns both duplicated page numbers in one box.
    match = re.fullmatch(r"(\d{1,4})(\d{1,4})", clean)
    if match and match.group(1) == match.group(2):
        return int(match.group(1))
    return None


def _footer_numbers(out, height, two_up):
    """Pure numeric boxes in the footer strip.

    A 2-up sheet duplicates the page number (one per half) and may also carry
    table numbers; keep only the lower half there.  A single-page sheet puts
    the page number on the outer edge, often above the 48% line, and usually
    has no other standalone number in the footer, so keep everything.
    """
    txts = list(getattr(out, "txts", None) or [])
    boxes = getattr(out, "boxes", None)
    boxes = list(boxes) if boxes is not None else []
    values = []
    for position, text in enumerate(txts):
        value = _clean_number(text)
        if value is None or not (1 <= value <= 999):
            continue
        center_y = None
        if position < len(boxes):
            points = np.array(boxes[position]).reshape(-1, 2)
            center_y = float(points[:, 1].mean())
        if two_up and center_y is not None and center_y < height * 0.48:
            continue  # body text / table numbers sit above the footer line
        values.append(value)
    if two_up and not values:
        # The footer number may sit slightly higher on some scans; retry
        # without the vertical cut rather than losing the anchor.
        for text in txts:
            value = _clean_number(text)
            if value is not None and 1 <= value <= 999:
                values.append(value)
    return values


def ocr_footer(doc, index, expected=None):
    """OCR a thin bottom strip; return the most likely printed page."""
    page = doc[index]
    rect = page.rect
    two_up = rect.width / max(1.0, rect.height) > 1.0
    for fraction in (0.12, 0.16):
        clip = fitz.Rect(
            rect.x0, rect.y1 - rect.height * fraction, rect.x1, rect.y1
        )
        pix = page.get_pixmap(dpi=110, clip=clip)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        out = engine()(image)
        values = _footer_numbers(out, pix.height, two_up)
        if values:
            counts = {}
            for value in values:
                counts[value] = counts.get(value, 0) + 1
            best = max(counts.values())
            candidates = [
                value for value, count in counts.items() if count == best
            ]
            if expected is not None:
                return min(candidates, key=lambda value: abs(value - expected))
            return max(candidates)
    return None


def _monotonic(anchors):
    out = []
    for page, printed in sorted(anchors):
        if out and printed < out[-1][1]:
            continue
        if out and printed - out[-1][1] > 3 * max(1, page - out[-1][0]):
            continue
        out.append((page, printed))
    return out


def content_anchors(anchors):
    """Drop the front-matter TOC pages from the page-number curve.

    The scanned book numbers its contents pages 11, 12, ... and then starts
    the body again at printed page 1.  Those TOC pages therefore share page
    numbers with real content pages and would win a naive nearest-page search.
    The body has a large, stable `pdf_page - printed_page` offset; use the
    first anchor in that offset cluster to estimate where content begins.
    """
    if not anchors:
        return [], 1
    offsets = [page - printed for page, printed in anchors]
    body_offset = max(offsets)
    body = [(page, printed) for page, printed in anchors
            if page - printed >= body_offset - 4]
    if not body:
        return anchors, 1
    first_page, first_printed = sorted(body)[0]
    start = max(1, first_page - (first_printed - 1))
    # Use only the body-offset cluster.  A single misread footer (for example
    # "207" read as "107") would otherwise bend the whole interpolation.
    return sorted(body), start


def estimate_page(anchors, printed):
    """Linear interpolation over (pdf_page, printed_page) anchors.

    Outside the anchor range, extrapolate one printed page per PDF page:
    these books are page-for-page scans, only the front matter shifts the
    offset.  Returning the first/last anchor instead would send every early
    stop to the first body page.
    """
    if not anchors:
        return None
    if printed <= anchors[0][1]:
        return max(1, anchors[0][0] - (anchors[0][1] - printed))
    if printed >= anchors[-1][1]:
        return anchors[-1][0] + (printed - anchors[-1][1])
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if y0 <= printed <= y1:
            if y1 == y0:
                return x0
            ratio = (printed - y0) / (y1 - y0)
            return int(round(x0 + ratio * (x1 - x0)))
    return anchors[-1][0]


def build_anchors(doc):
    count = doc.page_count
    indices = set(range(0, count, ANCHOR_STEP))
    indices.update(range(0, min(FRONT_PAGES, count), FRONT_STEP))
    indices.add(count - 1)
    anchors = []
    for index in sorted(indices):
        printed = ocr_footer(doc, index)
        if printed:
            anchors.append((index + 1, printed))
    return _monotonic(anchors)


def resolve_stop(doc, anchors, printed, cache, start=1):
    estimate = estimate_page(anchors, printed)
    if estimate is None:
        return None, False
    estimate = max(estimate, start)
    for radius in (0, 1, SEARCH_RADIUS):
        offsets = range(-radius, radius + 1) if radius else [0]
        for offset in offsets:
            page = estimate + offset
            if page < start or page > doc.page_count:
                continue
            key = page
            if key not in cache:
                cache[key] = ocr_footer(doc, page - 1, expected=printed)
            if cache[key] == printed:
                return page, False
    # Interpolation between anchors is monotonic and near-linear; when the
    # footer OCR cannot confirm a page, keep the estimate rather than scan
    # dozens of pages for minutes.  The page is flagged as estimated.
    return estimate, True


def load_existing():
    if not OUT_PATH.exists():
        return {"files": {}}
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="code/title substring filter")
    parser.add_argument("--force", action="store_true", help="redo unchanged files")
    parser.add_argument("--dir", default=os.environ.get(
        "YANTU_ANNOTATED_DIR", str(DEFAULT_DIR)))
    args = parser.parse_args()

    folder = Path(args.dir)
    files = annotated_files(folder)
    if not files:
        print("没有找到标注版 PDF：%s" % folder)
        return 1

    guides = load_guides()["standards"]
    if args.only:
        needle = re.sub(r"\s+", "", args.only.lower())
        guides = [
            row for row in guides
            if needle in re.sub(r"\s+", "", row["code_key"].lower())
            or needle in re.sub(r"\s+", "", row["name"].lower())
        ]
    existing = load_existing()
    out = {
        "built_at": existing.get("built_at"),
        "folder": str(folder),
        "files": existing.get("files", {}),
    }

    for guide in guides:
        code_key = guide["code_key"]
        path = match_file(guide, files)
        if not path:
            print("[skip] %s：没有对应的标注版" % code_key)
            continue
        stat = path.stat()
        cached = out["files"].get(code_key) or {}
        unchanged = (
            cached.get("size") == stat.st_size
            and cached.get("mtime") == int(stat.st_mtime)
            and bool(cached.get("anchors"))
        )
        wanted_stops = {stop["id"] for stop in guide["stops"]}
        cached_stops = cached.get("stops") or {}
        done_stops = {key for key, value in cached_stops.items() if value}
        if (unchanged and wanted_stops <= done_stops
                and len(done_stops) == len(wanted_stops) and not args.force):
            print("[keep] %s ← %s" % (code_key, path.name))
            continue

        started = time.time()
        doc = fitz.open(path)
        page_count = doc.page_count
        print("[ocr ] %s ← %s (%d 页)" % (code_key, path.name, page_count))
        anchors = build_anchors(doc)
        anchors, content_start = content_anchors(anchors)
        print("       anchors %d · 正文起 p%d：%s"
              % (len(anchors), content_start, anchors[:3]))
        cache = {}
        stops = dict(cached.get("stops") or {})
        estimated = []
        for stop in guide["stops"]:
            printed = int(stop["page"])
            page, guessed = resolve_stop(
                doc, anchors, printed, cache, start=content_start
            )
            stops[stop["id"]] = page
            if guessed:
                estimated.append(stop["id"])
            status = ("p%d%s" % (page, "（估）" if guessed else "")
                      if page else "未命中")
            print("       %-28s 印刷 p%-3d -> %s" % (stop["id"], printed, status))
        doc.close()
        out["files"][code_key] = {
            "code": guide["code"],
            "title": guide["name"],
            "file": path.name,
            "page_count": page_count,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "anchors": anchors,
            "content_start": content_start,
            "stops": stops,
            "estimated": estimated,
            "built_at": datetime.now().isoformat(timespec="seconds"),
        }
        out["built_at"] = datetime.now().isoformat(timespec="seconds")
        OUT_PATH.write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("       %.1fs" % (time.time() - started))

    out["built_at"] = out.get("built_at") or datetime.now().isoformat(timespec="seconds")
    OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("写出 %s：%d 本" % (OUT_PATH, len(out["files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
