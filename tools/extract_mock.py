"""Extract question numbers from the mock-exam volume (26华S案例模拟题集).

That book holds 32 mock papers. Inside each paper the questions are numbered
1..25, then an answer section follows and restarts at 1..25, each answer line
reading `23.答案：（B）`. So a question is addressed as paper plus number, and
the paper index has to be recovered from the numbering restarts.

The page box is 1651x2331 pt (a scan stored at ~576 dpi), 3.2x the other
volumes, so it is rendered at 40 dpi to land at the same pixel size.

Usage:
    py -3 tools/extract_mock.py                # whole book
    py -3 tools/extract_mock.py --limit 40     # first 40 pages
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
BOOK = book_pdf("26华S案例模拟题集高清版.pdf")
OUT = os.path.join(ROOT, "index", "26华S案例模拟题集高清版.jsonl")

DPI = 40
CROP_RATIO = 0.15
CROP_MIN = 60
# Question and answer lines read `16.某永久...` / `16.答案`; a dot followed by
# another digit is a decimal (`4.28m`) or a clause number (`9.2.3`), and the
# preface pages enumerate with `1、`.
QNUM_RE = re.compile(r"^\s*(\d{1,2})[.．](?!\d)")
ANSWER_MARK = "答案"


def page_hits(doc, index):
    """Return [{n, ans, raw, score}] for one page, in reading order."""
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
        return []
    if not crops:
        return []

    # Answer heads like `1.【答案】（C）` are detected as ~150 px fragments;
    # cropping 15% of those cuts the word 答案 in half, so short lines are
    # recognised whole.
    narrow = [
        c if c.shape[1] <= 400 else c[:, : max(CROP_MIN, int(c.shape[1] * CROP_RATIO))]
        for c in crops
    ]
    rec = engine.recognize_txt(narrow)
    txts = _as_list(rec.txts)
    scores = _as_list(rec.scores)
    boxes = _as_list(det.boxes)

    hits = []
    for j, text in enumerate(txts):
        box = boxes[j] if j < len(boxes) else None
        if not _is_question_line(box, pix.width):
            continue
        match = QNUM_RE.match(text)
        if not match:
            continue
        hits.append(
            {
                "n": int(match.group(1)),
                "ans": ANSWER_MARK in text[:8],
                "raw": text[:30],
                "score": round(float(scores[j]), 3) if j < len(scores) else 0.0,
            }
        )
    return hits


def load_state(out_path):
    """(completed pages, paper, saw_answers, max_question) from an existing JSONL."""
    done = set()
    paper, saw_answers, max_question = 1, False, 0
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                done.add(row["page"])
                paper = row.get("paper", paper)
                saw_answers = row.get("saw_answers", saw_answers)
                max_question = row.get("max_question", max_question)
    return done, paper, saw_answers, max_question


def next_state(hits, paper, saw_answers, max_question):
    """Advance the paper/section state by one page.

    Section is read from the numbering, not from the `答案` marker: the marker
    disappears on many later pages, but the structure never changes. Questions
    run 1..25, then the answers restart at 1..25, then the next paper restarts
    at 1 again. So a page whose first number is 1 starts an answer section when
    the question run is already long, and a new paper when answers were seen.
    """
    if not hits:
        return paper, saw_answers, max_question, None
    first = hits[0]
    if first["n"] == 1:
        if saw_answers:
            paper += 1
            saw_answers = False
            max_question = 0
            section = "q"
        elif max_question >= 10:
            saw_answers = True
            section = "a"
        else:
            section = "q"
    else:
        section = "a" if saw_answers else "q"
    if section == "q":
        max_question = max(max_question, max(hit["n"] for hit in hits))
    return paper, saw_answers, max_question, section


def reindex(path):
    """Recompute paper/section from the stored numbers, without any OCR."""
    with open(path, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    paper, saw_answers, max_question = 1, False, 0
    for row in rows:
        paper, saw_answers, max_question, section = next_state(
            row["hits"], paper, saw_answers, max_question
        )
        row.update(paper=paper, section=section, saw_answers=saw_answers, max_question=max_question)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="max pages (testing)")
    parser.add_argument("--outdir", default=os.path.dirname(OUT))
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="recompute paper/section from stored numbers (no OCR)",
    )
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, os.path.basename(OUT))
    doc = pymupdf.open(BOOK)
    total = doc.page_count
    if args.limit:
        total = min(total, args.limit)

    if args.reindex:
        rows = reindex(out_path)
        print(
            "reindexed %d pages | papers 1-%d"
            % (len(rows), max(row["paper"] for row in rows)),
            flush=True,
        )
        return

    done, paper, saw_answers, max_question = load_state(out_path)
    todo = [i for i in range(total) if (i + 1) not in done]
    if not todo:
        print("already complete:", out_path, flush=True)
        return
    if done and max(done) > min(todo):
        # Missing pages sit before finished ones: the cached tail state belongs
        # to a later page, so rebuilding the whole book is the only safe resume.
        print("gap in output; restarting from page 1", flush=True)
        open(out_path, "w", encoding="utf-8").close()
        todo = list(range(total))
        paper, saw_answers = 1, False
    print(
        "start: %d/%d pages done | %d to go | paper %d"
        % (len(done), total, len(todo), paper),
        flush=True,
    )

    started = time.time()
    with open(out_path, "a", encoding="utf-8") as handle:
        for i in todo:
            hits = page_hits(doc, i)
            paper, saw_answers, max_question, section = next_state(
                hits, paper, saw_answers, max_question
            )
            row = {
                "page": i + 1,
                "paper": paper,
                "section": section,
                "saw_answers": saw_answers,
                "max_question": max_question,
                "hits": hits,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            if (i + 1) % 20 == 0:
                elapsed = time.time() - started
                print(
                    "  %4d/%d | %.0fs | %.2f s/page"
                    % (i + 1, total, elapsed, elapsed / (i + 1 - (total - len(todo)))),
                    flush=True,
                )
    print("done | %.0fs" % (time.time() - started), flush=True)


if __name__ == "__main__":
    main()
