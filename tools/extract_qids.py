"""Extract question numbers from scanned PDFs with local OCR.

Two-stage pass: detect text lines on the whole page, then recognise only the
left edge of each line, where the `<chapter>-<number>` label always sits.

One JSONL per PDF in index/. Resumable at page granularity: every finished page
is appended immediately, and pages already present are skipped on the next run,
so shutting the machine down mid-run costs at most one page.

Usage:
    py -3 tools/extract_qids.py --workers 1                  # all PDFs in project root
    py -3 tools/extract_qids.py --workers 1 "book.pdf"       # one book
"""

import argparse
import json
import os
import re
import time
from multiprocessing import Pool

from bookpath import book_names, book_pdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT, "index")
DPI = 100
CROP_RATIO = 0.15
CROP_MIN = 60
# A trailing digit means the label is a regulation code (JGJ 94-2008 reads as
# `94-2008`), not a question number.
QID_RE = re.compile(r"^\s*(\d{1,2})\s*[-—–]\s*(\d{1,3})(?!\d)")

# A real question line starts at the left margin and spans the text column;
# numbers detected inside figures are short fragments sitting mid-page.
# Measured on the case-solution volumes: real lines are >= 0.7 page-widths wide
# and start at x/width <= 0.16, fragments are <= 0.07 wide and start at >= 0.21.
FULL_LINE_RATIO = 0.35
LEFT_MARGIN_RATIO = 0.18
MIN_STEM_WIDTH = 70  # px; keeps short left-margin stems such as "4.【答案】C" (87 px)

_ENGINE = None
_DOCS = {}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import RapidOCR

        params = {"Global.log_level": "warning", "Rec.rec_batch_num": 32}
        threads = os.environ.get("OCR_THREADS")
        if threads:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = int(threads)
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
        _ENGINE = RapidOCR(params=params)
    return _ENGINE


def _doc(path):
    if path not in _DOCS:
        import pymupdf

        _DOCS[path] = pymupdf.open(path)
    return _DOCS[path]


def _as_list(value):
    return [] if value is None else [v for v in value]


def _is_question_line(box, page_width):
    """True if a detected line is a full text line, not a figure fragment."""
    if box is None:
        return False
    width = box[2][0] - box[0][0]
    if width >= page_width * FULL_LINE_RATIO:
        return True
    return box[0][0] <= page_width * LEFT_MARGIN_RATIO and width >= MIN_STEM_WIDTH


def page_qids(doc, index):
    """Return the question-number labels found on one page."""
    import numpy as np

    engine = _engine()
    pix = doc[index].get_pixmap(dpi=DPI)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    arr = np.ascontiguousarray(arr[:, :, ::-1])

    pre, op = engine.preprocess_img(arr)
    try:
        crops, det = engine.detect_and_crop(pre, op)
    except Exception:
        # RapidOCR raises when a page holds no text at all (cover art, blank leaf).
        return []
    if not crops:
        return []
    narrow = [c[:, : max(CROP_MIN, int(c.shape[1] * CROP_RATIO))] for c in crops]
    rec = engine.recognize_txt(narrow)

    txts = _as_list(rec.txts)
    scores = _as_list(rec.scores)
    boxes = _as_list(det.boxes)
    hits = []
    for j, text in enumerate(txts):
        box = boxes[j] if j < len(boxes) else None
        if not _is_question_line(box, pix.width):
            continue
        match = QID_RE.match(text)
        if not match:
            continue
        hits.append(
            {
                "qid": "%s-%s" % (match.group(1), match.group(2)),
                "raw": text[:50],
                "score": round(float(scores[j]), 3) if j < len(scores) else 0.0,
                "box": [round(float(v), 1) for v in (box[0][0], box[0][1], box[2][0], box[2][1])]
                if box is not None
                else None,
            }
        )
    return hits


def process_page(task):
    """OCR one page. Task is (pdf_path, zero_based_page_index)."""
    pdf_path, page_index = task
    doc = _doc(pdf_path)
    try:
        hits = page_qids(doc, page_index)
    except Exception as exc:
        return {"page": page_index + 1, "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"page": page_index + 1, "hits": hits}


def completed_pages(out_path):
    """Pages already written to a JSONL file, tolerating a torn last line."""
    done = set()
    if not os.path.exists(out_path):
        return done
    with open(out_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                done.add(json.loads(line)["page"])
            except Exception:
                continue
    return done


def count_pages(path):
    import pymupdf

    doc = pymupdf.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


def run_book(path, out_path, workers, limit):
    total = count_pages(path)
    if limit:
        total = min(total, limit)
    done = completed_pages(out_path)
    todo = [(path, i) for i in range(total) if (i + 1) not in done]

    if not todo:
        print("skip (complete): %s | %d pages" % (os.path.basename(path), total), flush=True)
        return
    print(
        "start: %s | %d/%d pages done | %d to go"
        % (os.path.basename(path), len(done), total, len(todo)),
        flush=True,
    )

    started = time.time()
    finished = len(done)
    with open(out_path, "a", encoding="utf-8") as handle:
        if workers <= 1:
            for task in todo:
                handle.write(json.dumps(process_page(task), ensure_ascii=False) + "\n")
                handle.flush()
                finished += 1
                if finished % 20 == 0:
                    elapsed = time.time() - started
                    processed = finished - len(done)
                    rate = processed / max(elapsed, 1e-6)
                    print(
                        "  %4d/%d | %.0fs | %.2f s/page"
                        % (finished, total, elapsed, 1.0 / max(rate, 1e-6)),
                        flush=True,
                    )
        else:
            with Pool(workers) as pool:
                for row in pool.imap_unordered(process_page, todo):
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                    finished += 1
                    if finished % 20 == 0:
                        elapsed = time.time() - started
                        processed = finished - len(done)
                        rate = processed / max(elapsed, 1e-6)
                        print(
                            "  %4d/%d | %.0fs | %.2f s/page"
                            % (finished, total, elapsed, 1.0 / max(rate, 1e-6)),
                            flush=True,
                        )
    print("done: %s | %.0fs" % (os.path.basename(path), time.time() - started), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0, help="max pages per file (testing)")
    parser.add_argument("--outdir", default=INDEX_DIR)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    if args.files:
        paths = args.files
    else:
        paths = [book_pdf(name) for name in book_names()]

    for path in paths:
        name = os.path.basename(path)
        out_path = os.path.join(args.outdir, os.path.splitext(name)[0] + ".jsonl")
        run_book(path, out_path, args.workers, args.limit)


if __name__ == "__main__":
    main()
