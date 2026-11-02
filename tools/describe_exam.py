"""Read one past-exam paper and dump, per question, its opening text and answer.

The nine exam categories are not printed anywhere in the book, so a paper has
to be tagged by reading it. This tool does the mechanical half: crop each
question, OCR its first lines, and OCR the first line of its answer.  Some
2025 case questions are printed by the publisher as an option exercise, so the
answer crop may start with `【答案】（C）`; that is a **publisher option**, not
an exam scoring key (the real case exam is process-scored).  A human -- or the
tagging pass -- then assigns the category.  Never use this letter for scoring.

Output: index/tags/<paper>.jsonl, one line per question, resumable.

Usage:
    py -3 tools/describe_exam.py --year 2025 --session A
    py -3 tools/describe_exam.py --year 2025 --session P
"""

import argparse
import json
import os
import sys

import numpy as np
import pymupdf
from PIL import Image
from rapidocr import RapidOCR

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bookpath import book_pdf
from crop_question import answer_pages, plan_segments, resolve_qid  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "index", "tags")
DPI = 150
ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        ENGINE = RapidOCR(params={"Global.log_level": "warning", "Rec.rec_batch_num": 32})
    return ENGINE


def ocr_image(path):
    im = Image.open(path).convert("RGB")
    arr = np.ascontiguousarray(np.array(im)[:, :, ::-1])
    try:
        res = engine()(arr)
    except Exception:
        return ""
    txts = list(res.txts) if res.txts is not None else []
    return " ".join(txts)


def crop(qid, out_path, answer=False):
    if answer:
        pair = answer_pages(qid)
        if not pair:
            return None
        book, pages = pair
    else:
        book, pages, _, _ = resolve_qid(qid)
    segments = plan_segments(book, pages, int(qid.split("-")[-1]), qid)
    if not segments:
        return None
    doc = pymupdf.open(book_pdf(book))
    images = []
    for page_no, y0, y1 in segments:
        page = doc[page_no - 1]
        pix = page.get_pixmap(dpi=DPI, clip=pymupdf.Rect(page.rect.x0, y0, page.rect.x1, y1))
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    doc.close()
    canvas = images[0]
    if len(images) > 1:
        canvas = Image.new("RGB", (max(i.width for i in images), sum(i.height for i in images)), "white")
        y = 0
        for im in images:
            canvas.paste(im, (0, y))
            y += im.height
    canvas.save(out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--session", choices=["A", "P"], required=True)
    args = parser.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    paper = "A%d-%s" % (args.year, args.session)
    out_path = os.path.join(OUTDIR, paper + ".jsonl")
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            try:
                done.add(json.loads(line)["qid"])
            except Exception:
                continue

    tmp = os.path.join(OUTDIR, "_tmp.png")
    with open(out_path, "a", encoding="utf-8") as fh:
        for i in range(1, 26):
            qid = "%s-%d" % (paper, i)
            if qid in done:
                continue
            row = {"qid": qid, "text": "", "answer": ""}
            if crop(qid, tmp) is None:
                continue
            row["text"] = ocr_image(tmp)[:400]
            if crop(qid, tmp, answer=True):
                row["answer"] = ocr_image(tmp)[:80]
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(qid, "|", row["text"][:40], "|", row["answer"][:20], flush=True)
    print("done:", out_path)


if __name__ == "__main__":
    main()
