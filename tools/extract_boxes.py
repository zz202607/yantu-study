"""Record the page position of every question/answer label in the exam and
mock volumes.

The three past-exam/mock books were indexed by number only (`extract_exam.py`,
`extract_mock.py`), so the app knows which page a question lives on but not
where on the page it starts. Without that it cannot cut a question out of a
page image.

This pass re-reads those pages and writes, per page, every label it finds with
its box in PDF points (resolution-independent) plus the page height, so the
crop is `[this label's top, next label's top)`.

Output: index/boxes/<book stem>.jsonl, one line per page, resumable.
The existing index/*.jsonl files are left untouched.

Usage:
    py -3 tools/extract_boxes.py                 # all three books
    py -3 tools/extract_boxes.py --book "26华S案例模拟题集高清版.pdf"
    py -3 tools/extract_boxes.py --limit 20      # sanity check
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
OUTDIR = os.path.join(ROOT, "index", "boxes")

# file -> (render dpi, label regex, crop rule)
#   "strip": recognise the left slice of every line (question lines span the column)
#   "short": recognise short lines whole; their labels sit mid-line (`1.答案：（B）`)
BOOKS = {
    "2025【正版高清扫】华S案例真题12-25套题_扫描版.pdf": (100, re.compile(r"^\s*(\d{1,2})[.．](?!\d)"), "strip"),
    "华S26专业知识13~25真题解析_扫描版.pdf": (100, re.compile(r"^\s*(\d{1,2})[.．](?!\d)"), "strip"),
    "26华S案例模拟题集高清版.pdf": (40, re.compile(r"^\s*(\d{1,2})[.．](?!\d)"), "short"),
}

CROP_RATIO = 0.15
CROP_MIN = 60
SHORT_LINE = 400


def page_labels(doc, index, dpi, pattern, rule):
    engine = _engine()
    page = doc[index]
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    arr = np.ascontiguousarray(arr[:, :, ::-1])

    pre, op = engine.preprocess_img(arr)
    try:
        crops, det = engine.detect_and_crop(pre, op)
    except Exception:
        return []
    if not crops:
        return []

    if rule == "short":
        narrow = [c if c.shape[1] <= SHORT_LINE else c[:, : max(CROP_MIN, int(c.shape[1] * CROP_RATIO))]
                  for c in crops]
    else:
        narrow = [c[:, : max(CROP_MIN, int(c.shape[1] * CROP_RATIO))] for c in crops]

    rec = engine.recognize_txt(narrow)
    txts, scores, boxes = _as_list(rec.txts), _as_list(rec.scores), _as_list(det.boxes)
    scale = 72.0 / dpi  # rendered px -> PDF points

    hits = []
    for j, text in enumerate(txts):
        box = boxes[j] if j < len(boxes) else None
        if not _is_question_line(box, pix.width):
            continue
        match = pattern.match(text)
        if not match:
            continue
        hits.append({
            "n": int(match.group(1)),
            "box": [round(float(v) * scale, 2) for v in (box[0][0], box[0][1], box[2][0], box[2][1])],
            "raw": text[:30],
            "score": round(float(scores[j]), 3) if j < len(scores) else 0.0,
        })
    hits.sort(key=lambda h: h["box"][1])
    return hits


def completed(out_path):
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["page"])
                except Exception:
                    continue
    return done


def run(name, dpi, pattern, rule, limit):
    doc = pymupdf.open(book_pdf(name))
    total = doc.page_count if not limit else min(limit, doc.page_count)
    out_path = os.path.join(OUTDIR, os.path.splitext(name)[0] + ".jsonl")
    done = completed(out_path)
    todo = [i for i in range(total) if (i + 1) not in done]
    if not todo:
        print("skip (complete): %s" % name, flush=True)
        return
    print("start: %s | %d/%d done | %d to go" % (name, len(done), total, len(todo)), flush=True)

    started = time.time()
    with open(out_path, "a", encoding="utf-8") as fh:
        for i in todo:
            hits = page_labels(doc, i, dpi, pattern, rule)
            fh.write(json.dumps({"page": i + 1, "h": round(doc[i].rect.height, 2), "hits": hits},
                                ensure_ascii=False) + "\n")
            fh.flush()
            if (i + 1) % 20 == 0:
                elapsed = time.time() - started
                print("  %4d/%d | %.0fs | %.2f s/页"
                      % (i + 1, total, elapsed, elapsed / max(i + 1 - (total - len(todo)), 1)), flush=True)
    print("done: %s | %.0fs" % (name, time.time() - started), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", default="", help="single file name")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    names = [args.book] if args.book else list(BOOKS)
    for name in names:
        if name not in BOOKS:
            print("unknown book:", name)
            continue
        dpi, pattern, rule = BOOKS[name]
        run(name, dpi, pattern, rule, args.limit)


if __name__ == "__main__":
    main()
