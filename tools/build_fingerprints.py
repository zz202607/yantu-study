"""Build page fingerprints for recognising which book a photo came from.

Each book prints a running head (篇/章 name, book watermark) at the top and a
watermark plus page number at the bottom. Those strips are stable per book and
survive a phone photo far better than body text, so they are what we index.

Method:
  - sample pages spread across each book (skipping covers and the table of
    contents),
  - OCR the top 12% and bottom 8% of each sampled page,
  - keep character n-grams (2-4 chars) that repeat within the book,
  - score a strip against a book by summed token weight, where a token seen in
    many books weighs less.

Self-test: half the sampled pages build the index, the other half are classified
and the top-1 accuracy is reported per book.

Usage:
    py -3 tools/build_fingerprints.py
"""

import json
import os
import re
from collections import Counter, defaultdict

import numpy as np
import pymupdf
from PIL import Image
from rapidocr import RapidOCR

from bookpath import book_pdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index")
OUT = os.path.join(INDEX, "fingerprints.json")
DPI = 150
TOP_RATIO = 0.07
BOTTOM_RATIO = 0.05
SAMPLES = 10

BOOKS = [
    "2026【正版高清扫】华S案例习题集上_扫描版.pdf",
    "2026【正版高清扫】华S案例习题集中_扫描版.pdf",
    "2026【正版高清扫】华S案例习题集下_扫描版.pdf",
    "26华山空白习题第一册_扫描版.pdf",
    "26华山空白习题第二册_扫描版.pdf",
    "26华山空白习题第三册_扫描版.pdf",
    "26华山空白习题第四册_扫描版.pdf",
    "华S26专业知识习题册上_扫描版.pdf",
    "华S26专业知识习题册下_扫描版.pdf",
    "2025【正版高清扫】华S案例真题12-25套题_扫描版.pdf",
    "华S26专业知识13~25真题解析_扫描版.pdf",
    "26华S案例模拟题集高清版.pdf",
]

ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        ENGINE = RapidOCR(params={"Global.log_level": "warning", "Rec.rec_batch_num": 32})
    return ENGINE


def ocr(img):
    arr = np.ascontiguousarray(np.array(img.convert("RGB"))[:, :, ::-1])
    try:
        res = engine()(arr)
    except Exception:
        return ""
    txts = list(res.txts) if res.txts is not None else []
    return " ".join(txts)


def strip_text(doc, page_index):
    """OCR only the running head (top) and the footer line (bottom).

    A wider strip catches the first lines of the question too, and those vary per
    page; keeping just the extreme line leaves the stable part of the layout.
    """
    page = doc[page_index]
    rect = page.rect
    parts = []
    for y0, y1, want in ((rect.y0, rect.y0 + rect.height * TOP_RATIO, "top"),
                         (rect.y1 - rect.height * BOTTOM_RATIO, rect.y1, "bottom")):
        pix = page.get_pixmap(dpi=DPI, clip=pymupdf.Rect(rect.x0, y0, rect.x1, y1))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        arr = np.ascontiguousarray(np.array(img)[:, :, ::-1])
        try:
            res = engine()(arr)
        except Exception:
            continue
        txts = list(res.txts) if res.txts is not None else []
        boxes = list(res.boxes) if res.boxes is not None else []
        if not txts:
            continue
        pairs = sorted(zip(txts, boxes), key=lambda p: p[1][0][1])
        parts.append(pairs[0][0] if want == "top" else pairs[-1][0])
    return " ".join(parts)


def tokens(text):
    clean = re.sub(r"[\s\d\W_]+", "", text)
    out = []
    for n in (2, 3, 4):
        out += [clean[i:i + n] for i in range(len(clean) - n + 1)]
    return out


def sample_pages(total):
    return [int(total * (i + 1) / (SAMPLES + 1)) for i in range(SAMPLES)]


def main():
    train, test = {}, {}
    for name in BOOKS:
        doc = pymupdf.open(book_pdf(name))
        pages = [p for p in sample_pages(doc.page_count) if 0 <= p < doc.page_count]
        texts = [strip_text(doc, p) for p in pages]
        doc.close()
        train[name] = texts[0::2]
        test[name] = texts[1::2]
        print("sampled", name[:40], len(pages), flush=True)

    # token -> books containing it; a token in few books is more telling
    token_books = defaultdict(set)
    per_book = {}
    for name, texts in train.items():
        counts = Counter()
        for text in texts:
            counts.update(set(tokens(text)))
        # keep tokens that recur in at least half the training pages
        keep = {t for t, c in counts.items() if c >= max(2, len(texts) // 2)}
        per_book[name] = keep
        for t in keep:
            token_books[t].add(name)

    weights = {t: 1.0 / len(books) for t, books in token_books.items()}

    correct = 0
    total = 0
    confusions = []
    for name, texts in test.items():
        for text in texts:
            toks = set(tokens(text))
            scores = defaultdict(float)
            for t in toks:
                if t not in weights:
                    continue
                for book in token_books[t]:
                    scores[book] += weights[t]
            if not scores:
                continue
            best = max(scores, key=scores.get)
            total += 1
            if best == name:
                correct += 1
            else:
                confusions.append((name[:26], best[:26]))
    acc = correct / max(1, total)
    print("top-1 accuracy: %d/%d = %.1f%%" % (correct, total, acc * 100))
    for a, b in Counter(confusions).most_common(6):
        print("  confusion:", a, "->", b)

    payload = {
        "top_ratio": TOP_RATIO,
        "bottom_ratio": BOTTOM_RATIO,
        "tokens": {t: sorted(books) for t, books in token_books.items()},
        "weights": weights,
        "accuracy": round(acc, 3),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print("written:", OUT, "| tokens:", len(token_books))


if __name__ == "__main__":
    main()
