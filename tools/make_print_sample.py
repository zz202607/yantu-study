"""Lay cropped questions and answers onto A4 sheets for a physical print check.

Questions are packed, not one-per-page: each crop is placed at its native
physical size and followed by a reserved writing area, and as many questions as
fit go on one sheet. Short case questions land two per page; knowledge
questions, which are only a couple of lines tall, fit more.

Answers go on their own sheets, packed tighter (no writing space needed).

For the C family the question comes from the blank workbook and the answer from
the solution volume. The Z volume prints question and answer on one page, so the
crop is split at its 【答案】 line; M/A/K already separate them.

Images are rendered at 300 dpi and placed at their original physical width,
never enlarged past 125%. Light watermarks are whitened and a detached page
number at the bottom of the crop is trimmed.

Usage:
    py -3 tools/make_print_sample.py --label C --qid C3-8 --qid C1-1
    py -3 tools/make_print_sample.py --label Z --qid Z1-10 --qid Z9-20
"""

import argparse
import os
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crop_question import answer_pages, plan_segments, render_segments, resolve_qid  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "print_samples")
A4 = (595.276, 841.89)
MARGIN = 36
TOP = 62
BOTTOM = 800
LABEL_H = 15
GAP = 16
CROP_DPI = 300
FONT = r"C:\Windows\Fonts\msyh.ttc"

# Writing space reserved under each crop, in points.
WRITE = {"question_case": 150, "question_know": 60, "answer": 26}


def text_line(page, text, y, size, right=False, color=(0.25, 0.25, 0.25)):
    width = A4[0] - 2 * MARGIN
    box = pymupdf.Rect(MARGIN, y - size - 2, MARGIN + width, y + size + 4)
    page.insert_textbox(box, text, fontname="msyh", fontfile=FONT, fontsize=size,
                        color=color, align=2 if right else 0)


def clean_and_trim(path):
    """Whiten the scan's grey watermark and drop a detached page-number line."""
    import numpy as np
    from PIL import Image

    im = Image.open(path).convert("L")
    im = im.point(lambda v: 255 if v > 175 else v)
    width, height = im.size
    dark = np.array(im) < 120
    counts = dark.sum(axis=1)

    bands, inb, start = [], False, 0
    for y, count in enumerate(counts):
        if count > 2 and not inb:
            start, inb = y, True
        elif count <= 2 and inb:
            bands.append((start, y))
            inb = False
    if inb:
        bands.append((start, height))

    cut = None
    while len(bands) >= 2:
        a, b = bands[-1]
        xs = np.where(dark[a:b, :].any(axis=0))[0]
        narrow = len(xs) > 0 and (xs[-1] - xs[0]) < width * 0.25
        gap = a - bands[-2][1]
        at_bottom = b > height * 0.78 and (b - a) < height * 0.06
        if at_bottom and (gap > height * 0.25 or (gap > height * 0.04 and narrow)):
            cut = a - 6
            bands.pop()
        else:
            break

    if bands:
        pad = int(20 / 72 * CROP_DPI)
        end = min(height, bands[-1][1] + pad)
        if cut is not None:
            end = min(end, cut)   # never let the padding reach back into the page number
        im = im.crop((0, 0, width, end))
    im.convert("RGB").save(path)


def split_z_answer(path, question_path, answer_path, engine=None):
    """Split a knowledge-book page at its 【答案】 line."""
    from crop_question import split_z_answer as cut

    return cut(path, question_path, answer_path, engine=engine) is not None


def image_size(path):
    img = pymupdf.open(path)
    rect = img[0].rect
    img.close()
    return rect.width / CROP_DPI * 72, rect.height / CROP_DPI * 72


class Sheet:
    """Two-pass page packer.

    First pass decides which questions share a page (using a minimum writing
    area per question). Second pass lays them out with the leftover space split
    evenly, so every question on the page gets the same writing area instead of
    the last one swallowing the remaining page.
    """

    def __init__(self, doc, kind, min_write):
        self.doc = doc
        self.kind = kind
        self.min_write = min_write
        self.items = []
        self.pages = []

    def add(self, label, source, image_path, min_write=None):
        w, h = image_size(image_path)
        scale = min(1.25, (A4[0] - 2 * MARGIN) / w)
        self.items.append(
            (
                label,
                source,
                image_path,
                w * scale,
                h * scale,
                self.min_write if min_write is None else min_write,
            )
        )

    def _plan(self):
        """Split items into pages that each fit with min_write per item."""
        pages, current, used = [], [], 0.0
        for item in self.items:
            h = item[4]
            need = LABEL_H + h + GAP + item[5]
            if current and used + need > BOTTOM - TOP:
                pages.append(current)
                current, used = [], 0.0
            current.append(item)
            used += need
        if current:
            pages.append(current)
        return pages

    def render(self):
        for group in self._plan():
            page = self.doc.new_page(width=A4[0], height=A4[1])
            self.pages.append(page)
            heights = [item[4] for item in group]
            used = sum(LABEL_H + h + GAP for h in heights)
            spare = BOTTOM - TOP - used
            # Every item keeps its own minimum; the leftover is split evenly.
            # With uniform minimums this is exactly the old even distribution.
            extra = spare - sum(item[5] for item in group)
            y = TOP
            for item in group:
                label, source, path, w, h, min_write = item
                write = min_write + max(0.0, extra) / len(group)
                caption = label + ("  ·  " + source if source else "")
                text_line(page, caption, y + 10, 8, color=(0.35, 0.35, 0.35))
                y += LABEL_H
                page.insert_image(pymupdf.Rect(MARGIN, y, MARGIN + w, y + h), filename=path)
                y += h + write + GAP
        return self.pages


def stamp(doc, title):
    total = doc.page_count
    for i, page in enumerate(doc):
        text_line(page, title, 40, 9, right=False, color=(0.25, 0.25, 0.25))
        text_line(page, "第 %d/%d 页" % (i + 1, total), 40, 9, right=True, color=(0.25, 0.25, 0.25))
        page.draw_line(pymupdf.Point(MARGIN, 48), pymupdf.Point(A4[0] - MARGIN, 48),
                       color=(0.6, 0.6, 0.6), width=0.5)


def build(label, qids, out_path, z_inline=False):
    doc = pymupdf.open()
    kind = "question_case" if label in ("C", "M", "A") else "question_know"
    questions = Sheet(doc, kind, WRITE[kind])
    answers = Sheet(doc, "answer", WRITE["answer"])
    z_answers = []

    for qid in qids:
        prefer = "blank" if label == "C" else "solutions"
        book, pages, _, _ = resolve_qid(qid, prefer=prefer)
        png = os.path.join(OUTDIR, "q_%s.png" % qid.replace("-", "_"))
        render_segments(book, plan_segments(book, pages, int(qid.split("-")[-1]), qid), png, dpi=CROP_DPI)
        clean_and_trim(png)
        label_text = "%s · %s" % (qid, book.replace("_扫描版", ""))
        source = "来源：%s 第 %s 页" % (book, pages[0])

        if label == "Z" and not z_inline:
            qpng = os.path.join(OUTDIR, "zq_%s.png" % qid.replace("-", "_"))
            apng = os.path.join(OUTDIR, "za_%s.png" % qid.replace("-", "_"))
            if split_z_answer(png, qpng, apng):
                clean_and_trim(qpng)
                clean_and_trim(apng)
                questions.add(label_text, source, qpng)
                z_answers.append((qid, apng, book, pages))
                continue
        questions.add(label_text, source, png)

    for qid in qids:
        if label == "Z":
            continue
        if label == "C":
            book, pages, _, _ = resolve_qid(qid, prefer="solutions")
        else:
            pair = answer_pages(qid)
            if not pair:
                continue
            book, pages = pair
        png = os.path.join(OUTDIR, "a_%s.png" % qid.replace("-", "_"))
        render_segments(book, plan_segments(book, pages, int(qid.split("-")[-1]), qid), png, dpi=CROP_DPI)
        clean_and_trim(png)
        answers.add("%s 答案" % qid, "来源：%s 第 %s 页" % (book, pages[0]), png)

    for qid, apng, book, pages in z_answers:
        answers.add("%s 答案" % qid, "来源：%s 第 %s 页" % (book, pages[0]), apng)

    questions.render()
    answers.render()
    kind_title = {"C": "案例题", "M": "模拟题", "A": "案例真题", "Z": "专业知识"}
    stamp(doc, "题面卷 · %s" % kind_title.get(label, label))
    doc.save(out_path, garbage=4)
    doc.close()
    print("wrote %s | %d 页（题面 %d + 答案 %d）"
          % (out_path, len(questions.pages) + len(answers.pages),
             len(questions.pages), len(answers.pages)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--qid", action="append", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--z-inline", action="store_true",
                        help="knowledge questions: keep answer on the same page (查答案模式)")
    args = parser.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)
    out = args.out or os.path.join(OUTDIR, "%s_sample.pdf" % args.label)
    build(args.label, args.qid, out, z_inline=args.z_inline)


if __name__ == "__main__":
    main()
