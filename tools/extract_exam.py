"""Extract question numbers from the past-exam volume (2025华S案例真题12-25套题).

This book holds the real case questions for 2012-2025. Each page carries a
vertical sidebar strip with the paper it belongs to -- `2012年案例（下午卷）`,
and `...答案解析` once the answer section begins -- so every page identifies
itself; no sequencing heuristics are needed.

Questions are numbered `17.某双洞...`, answers `19.【答案】（B）`, restarting
inside each paper. The resulting key is year + sitting + number.

Usage:
    py -3 tools/extract_exam.py                # whole book
    py -3 tools/extract_exam.py --limit 60     # first 60 pages
"""

import argparse
import json
import os
import re
import time

import numpy as np
import pymupdf

from bookpath import book_pdf
from extract_qids import _as_list, _engine, _is_question_line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Both past-exam volumes (case questions and knowledge questions) share the
# sidebar + per-sitting numbering layout, so one extractor serves both.
DEFAULT_BOOK = "2025【正版高清扫】华S案例真题12-25套题_扫描版.pdf"
BOOK = book_pdf(DEFAULT_BOOK)

DPI = 100
CROP_RATIO = 0.15
CROP_MIN = 60
# The strip sits in the outer margin, so it alternates sides on facing pages.
SIDEBAR_RATIO = 0.12
QNUM_RE = re.compile(r"^\s*(\d{1,2})[.．](?!\d)")
YEAR_RE = re.compile(r"(20\d{2})\s*年")
# The case volume labels its answer sections `答案解析`; the knowledge volume
# only writes `真题解析`, so both words have to count as an answer marker.
ANSWER_MARKS = ("答案", "解析")


def _read_strip(engine, strip):
    rotated = np.ascontiguousarray(np.rot90(strip, -1))
    result = engine(np.ascontiguousarray(rotated[:, :, ::-1]))
    return " ".join(_as_list(result.txts))


def sidebar_text(engine, arr, page_number):
    """OCR the outer-margin strip; it names the paper and section.

    The strip lives in the outer margin, so it is on the right of odd pages and
    on the left of even ones. Try the expected side first and fall back to the
    other, which keeps a mis-scanned page from losing its year.
    """
    width = int(arr.shape[1] * SIDEBAR_RATIO)
    left, right = arr[:, :width], arr[:, -width:]
    first, second = (right, left) if page_number % 2 else (left, right)
    text = _read_strip(engine, first)
    if YEAR_RE.search(text):
        return text
    return (text + " " + _read_strip(engine, second)).strip()


def page_row(doc, index):
    engine = _engine()
    pix = doc[index].get_pixmap(dpi=DPI)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]

    side = sidebar_text(engine, arr, index + 1)
    year_match = YEAR_RE.search(side)
    row = {
        "page": index + 1,
        "side": side[:60],
        "year": int(year_match.group(1)) if year_match else None,
        "session": "P" if "下午" in side else ("A" if "上午" in side else None),
        # 2022 was run both normally and as a make-up sitting; without this flag
        # the two papers collide on the same key.
        "retake": "补考" in side,
        "section": "a" if any(mark in side for mark in ANSWER_MARKS) else ("q" if side else None),
        "hits": [],
    }

    rgb = np.ascontiguousarray(arr[:, :, ::-1])
    pre, op = engine.preprocess_img(rgb)
    try:
        crops, det = engine.detect_and_crop(pre, op)
    except Exception:
        return row
    if not crops:
        return row

    narrow = [c[:, : max(CROP_MIN, int(c.shape[1] * CROP_RATIO))] for c in crops]
    rec = engine.recognize_txt(narrow)
    txts = _as_list(rec.txts)
    scores = _as_list(rec.scores)
    boxes = _as_list(det.boxes)

    for j, text in enumerate(txts):
        box = boxes[j] if j < len(boxes) else None
        if not _is_question_line(box, pix.width):
            continue
        match = QNUM_RE.match(text)
        if not match:
            continue
        row["hits"].append(
            {
                "n": int(match.group(1)),
                "raw": text[:30],
                "score": round(float(scores[j]), 3) if j < len(scores) else 0.0,
            }
        )
    return row


def completed_pages(out_path):
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["page"])
                except Exception:
                    continue
    return done


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", default=DEFAULT_BOOK, help="PDF file name in the project root")
    parser.add_argument("--limit", type=int, default=0, help="max pages (testing)")
    parser.add_argument("--outdir", default=os.path.join(ROOT, "index"))
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    book_path = book_pdf(args.book)
    out_path = os.path.join(args.outdir, os.path.splitext(args.book)[0] + ".jsonl")
    doc = pymupdf.open(book_path)
    total = doc.page_count
    if args.limit:
        total = min(total, args.limit)

    done = completed_pages(out_path)
    todo = [i for i in range(total) if (i + 1) not in done]
    if not todo:
        print("already complete:", out_path, flush=True)
        return
    print("start: %d/%d pages done | %d to go" % (len(done), total, len(todo)), flush=True)

    started = time.time()
    with open(out_path, "a", encoding="utf-8") as handle:
        for i in todo:
            row = page_row(doc, i)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            if (i + 1) % 20 == 0:
                elapsed = time.time() - started
                print(
                    "  %4d/%d | %.0fs | %.2f s/page"
                    % (i + 1, total, elapsed, elapsed / max(i + 1 - (total - len(todo)), 1)),
                    flush=True,
                )
    print("done | %.0fs" % (time.time() - started), flush=True)


if __name__ == "__main__":
    main()
