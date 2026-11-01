"""Cut one question (or answer) out of its page image.

A question starts at its number label and ends where the next label begins, so
the page layout itself gives the crop bounds -- no manual boxes.

Chapter books store label boxes in pixel space at 100 dpi; the exam and mock
books store them in PDF points (see tools/extract_boxes.py). Both are handled.

Usage:
    py -3 tools/crop_question.py C3-8  --out .probe/c3-8.png
    py -3 tools/crop_question.py A2025-P-1 --answer --out .probe/a2025p1.png
"""

import argparse
import json
import os
import re

import pymupdf

from bookpath import book_pdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index")
BOX_DIR = os.path.join(INDEX, "boxes")
PX_DPI = 100          # chapter books store boxes at this render dpi
RENDER_DPI = 200      # output resolution for reading / printing


def load_jsonl(path):
    pages = {}
    if not os.path.exists(path):
        return pages
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            pages[row["page"]] = row
    return pages


def resolve_qid(qid, prefer="solutions"):
    """Return (book, pages, section, scale) for a question key.

    `prefer="blank"` picks the workbook that carries only the question, which
    is what a 题面卷 needs; the default picks the volume with the solution.
    """
    lookup = json.load(open(os.path.join(INDEX, "lookup.json"), encoding="utf-8"))
    if re.match(r"^[CZ]\d+-\d+$", qid):
        entry = lookup["chapter"].get(qid)
        if not entry:
            raise SystemExit("qid not found: %s" % qid)
        books = list(entry["books"])
        if prefer == "blank":
            book = next((b for b in books if "空白" in b), books[0])
        else:
            book = next((b for b in books if "空白" not in b), books[0])
        return book, entry["books"][book], "q", 72.0 / PX_DPI
    entry = lookup["exam"].get(qid)
    if entry:
        return entry["book"], entry["q"], "q", 1.0
    entry = lookup["mock"].get(qid)
    if entry:
        return entry["book"], entry["q"], "q", 1.0
    raise SystemExit("qid not found: %s" % qid)


def answer_pages(qid):
    lookup = json.load(open(os.path.join(INDEX, "lookup.json"), encoding="utf-8"))
    entry = lookup["exam"].get(qid) or lookup["mock"].get(qid)
    if not entry or not entry.get("a"):
        return None
    return entry["book"], entry["a"]


def load_labels(book, qid, number):
    """Return (rows, scale, key) where key picks the matching label in a row.

    Chapter books store pixel boxes at 100 dpi inside index/<book>.jsonl and
    identify labels by `qid` (`3-8`). The exam/mock books store PDF-point boxes
    in index/boxes/ and identify labels by `n` (the number restarts in every
    paper, but a page belongs to exactly one question or answer section).
    """
    stem = os.path.splitext(book)[0] + ".jsonl"
    rows = load_jsonl(os.path.join(INDEX, stem))
    sample = next((r for r in rows.values() if r.get("hits")), None)
    # Only the chapter pass recorded boxes; the exam/mock rows never did.
    if sample and any("box" in hit for hit in sample["hits"]):
        return rows, 72.0 / PX_DPI, ("qid", qid[1:])
    return load_jsonl(os.path.join(BOX_DIR, stem)), 1.0, ("n", number)


def boxes_for(book, pages, number, qid):
    """Find the page and y-extent of a label, in PDF points."""
    rows, scale, (field, value) = load_labels(book, qid, number)
    for page_no in pages:
        row = rows.get(page_no)
        if not row:
            continue
        hits = [h for h in row.get("hits", []) if h.get("box")]
        target = next((h for h in hits if h.get(field) == value), None)
        if target is None:
            continue
        y0 = target["box"][1] * scale
        below = [h["box"][1] * scale for h in hits if h["box"][1] * scale > y0 + 1]
        return page_no, y0, (min(below) if below else None)
    return None


def has_ink(page, strip_points=40):
    """True if the bottom strip of the page holds printed content.

    A question that runs off the bottom of the page leaves text right up to the
    edge; one that ends mid-page leaves white. That difference is enough to
    decide whether to pull in the following page, without another OCR pass.
    """
    rect = page.rect
    clip = pymupdf.Rect(rect.x0, rect.y1 - strip_points, rect.x1, rect.y1)
    pix = page.get_pixmap(dpi=72, clip=clip)
    samples = pix.samples
    dark = sum(1 for i in range(0, len(samples), pix.n) if samples[i] < 200)
    return dark / max(1, pix.width * pix.height) > 0.01


def render(book, page_no, y0, y1, out, dpi=RENDER_DPI):
    # lookup.json stores the stem without `.pdf`
    filename = book if book.lower().endswith(".pdf") else book + ".pdf"
    doc = pymupdf.open(book_pdf(filename))
    page = doc[page_no - 1]
    rect = page.rect
    clip = pymupdf.Rect(rect.x0, y0, rect.x1, y1 if y1 else rect.y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    pix.save(out)
    doc.close()
    return pix.width, pix.height


def _clean_page(im):
    """Strip what a cropped page must not carry into a study sheet.

    - whitens the light grey watermark,
    - drops the sidebar strip the mock volume prints in the outer margin,
    - drops a page number that sits alone near the bottom edge.

    Runs per page segment, so a question stitched across a page break does not
    end up with the first page's number in the middle of the image.
    """
    import numpy as np
    from PIL import Image

    im = im.convert("L").point(lambda v: 255 if v > 175 else v)
    arr = np.array(im)
    height, width = arr.shape
    dark = arr < 120

    # sidebar: a near-solid dark column band within the outer 12% of the page
    col = dark.mean(axis=0)
    strong = np.where(col > 0.5)[0]
    x0, x1 = 0, width
    left = strong[strong < width * 0.12]
    right = strong[strong > width * 0.88]
    if len(left) > 10:
        x0 = int(left.max()) + 3
    if len(right) > 10:
        x1 = int(right.min()) - 3
    if x0 or x1 < width:
        im = im.crop((x0, 0, x1, height))
        arr = np.array(im)
        height, width = arr.shape
        dark = arr < 120

    # scan crease: a thin, near-vertical line that survives thresholding. Remove
    # only the pixels where nothing else is printed beside them, so text that
    # crosses the line stays intact.
    colfrac = dark.mean(axis=0)
    threshold = max(0.12, float(np.median(colfrac)) * 4)
    suspect = np.where(colfrac > threshold)[0]
    if len(suspect):
        groups, run = [], [suspect[0]]
        for x in suspect[1:]:
            if x - run[-1] <= 2:
                run.append(x)
            else:
                groups.append(run)
                run = [x]
        groups.append(run)
        for group in groups:
            if not (2 <= len(group) <= 14):
                continue
            x0, x1 = int(group[0]), int(group[-1])
            for x in range(max(0, x0 - 3), min(width, x1 + 4)):
                # Only wipe pixels that are (a) isolated left and right -- not
                # part of a glyph's body -- and (b) inside a vertical run at
                # least 120 px long. A character stroke is ~40 px; a scan crease
                # runs through the gaps between lines for hundreds of pixels.
                # This is what keeps `1`, `+` and `【` intact.
                col = arr[:, x] < 210
                if not col.any():
                    continue
                # look past the line's own anti-aliased fringe (about 6 px each side)
                l0, l1 = max(0, x - 20), max(0, x - 6)
                r0, r1 = min(width, x + 7), min(width, x + 21)
                left = arr[:, l0:l1].min(axis=1) if l1 > l0 else np.full(height, 255)
                right = arr[:, r0:r1].min(axis=1) if r1 > r0 else np.full(height, 255)
                cand = col & (left > 210) & (right > 210)
                ys = np.where(cand)[0]
                if len(ys) == 0:
                    continue
                start = prev = ys[0]
                runs = []
                for y in ys[1:]:
                    if y == prev + 1:
                        prev = y
                    else:
                        runs.append((start, prev))
                        start = prev = y
                runs.append((start, prev))
                for a, b in runs:
                    if b - a + 1 >= 70:
                        arr[a:b + 1, x] = 255
        im = Image.fromarray(arr)
        dark = arr < 120

    # page number: a short band near the bottom, separated by a real gap
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
    if len(bands) >= 2:
        a, b = bands[-1]
        gap = a - bands[-2][1]
        xs = np.where(dark[a:b, :].any(axis=0))[0]
        narrow = len(xs) > 0 and (xs[-1] - xs[0]) < width * 0.25
        # A footer can be wide: the blank workbooks print the watermark
        # 「华山论剑岩土」on the left and the page number on the right, on one
        # line. There the giveaway is distance, not width -- body text never
        # leaves a quarter of the page empty above its last line.
        at_bottom = b > height * 0.78 and (b - a) < height * 0.06
        footer = at_bottom and (gap > height * 0.25 or (gap > height * 0.04 and narrow))
        if footer:
            # Cut just above the number, never through it -- a fixed pad would
            # shave the top off a digit that sits close to the last text line.
            cut = max(bands[-2][1], a - 12)
            im = im.crop((0, 0, width, cut))

    return im.convert("RGB")


def split_z_answer(path, question_path=None, answer_path=None, engine=None):
    """Cut a knowledge-book crop at its 【答案】 line.

    Z-family questions print question, answer and solution on one page.  The
    review flow must show the question alone.  Returns a dict with the cut
    height and the two PIL images, or None when no answer line is found (the
    caller then degrades to the whole crop rather than guessing).
    """
    from PIL import Image
    import numpy as np

    im = Image.open(path).convert("RGB")
    arr = np.ascontiguousarray(np.array(im)[:, :, ::-1])
    if engine is None:
        from rapidocr import RapidOCR

        engine = RapidOCR(
            params={"Global.log_level": "warning", "Rec.rec_batch_num": 32}
        )
    try:
        res = engine(arr)
    except Exception:  # noqa: BLE001 - no answer line beats a crashed crop
        return None
    txts = list(res.txts) if getattr(res, "txts", None) is not None else []
    boxes = list(res.boxes) if getattr(res, "boxes", None) is not None else []
    ys = [box[0][1] for text, box in zip(txts, boxes) if "答案" in text]
    if not ys:
        return None
    cut = max(0, int(min(ys)) - 8)
    # An answer line pressed against the top means the crop starts in the
    # answer section already; splitting there would leave next to no question.
    if cut < im.height * 0.03:
        return None
    question = im.crop((0, 0, im.width, cut))
    answer = im.crop((0, cut, im.width, im.height))
    if question_path:
        question.save(question_path)
    if answer_path:
        answer.save(answer_path)
    return {
        "cut": cut,
        "width": im.width,
        "height": im.height,
        "question": question,
        "answer": answer,
    }


def render_segments(book, segments, out, dpi=RENDER_DPI):
    """Render [(page_no, y0, y1)] and stitch them into one image."""
    from PIL import Image

    filename = book if book.lower().endswith(".pdf") else book + ".pdf"
    doc = pymupdf.open(book_pdf(filename))
    images = []
    for page_no, y0, y1 in segments:
        page = doc[page_no - 1]
        clip = pymupdf.Rect(page.rect.x0, y0, page.rect.x1, y1)
        pix = page.get_pixmap(dpi=dpi, clip=clip)
        images.append(_clean_page(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)))
    doc.close()
    if len(images) == 1:
        canvas = images[0]
    else:
        width = max(im.width for im in images)
        canvas = Image.new("RGB", (width, sum(im.height for im in images)), "white")
        y = 0
        for im in images:
            canvas.paste(im, (0, y))
            y += im.height
    canvas.save(out)
    return canvas.width, canvas.height


def plan_segments(book, pages, number, qid, max_pages=3):
    """Work out which page slices make up one question.

    A page either contains the label (crop from it to the next label) or is a
    continuation of an earlier page (crop from the top to the next label).
    Page lists in lookup.json only hold the first page, so continuations are
    discovered by following the ink at the bottom edge.
    """
    rows, scale, (field, value) = load_labels(book, qid, number)
    filename = book if book.lower().endswith(".pdf") else book + ".pdf"
    doc = pymupdf.open(book_pdf(filename))
    queue = list(pages) + [p + 1 for p in pages]
    seen = set()
    segments = []
    try:
        while queue and len(segments) < max_pages:
            page_no = queue.pop(0)
            if page_no in seen or page_no < 1 or page_no > doc.page_count:
                continue
            seen.add(page_no)
            page = doc[page_no - 1]
            row = rows.get(page_no) or {}
            hits = [h for h in row.get("hits", []) if h.get("box")]

            target = next((h for h in hits if h.get(field) == value), None)
            if target is None:
                if not segments:
                    continue  # label not here yet
                # continuation: everything above the next label belongs to us
                y1 = min((h["box"][1] * scale for h in hits), default=page.rect.y1)
                segments.append((page_no, page.rect.y0, y1))
                if hits:
                    break
                if not has_ink(page) or len(segments) >= max_pages:
                    break
                queue.append(page_no + 1)
                continue

            y0 = target["box"][1] * scale
            below = [h["box"][1] * scale for h in hits if h["box"][1] * scale > y0 + 1]
            segments.append((page_no, y0, min(below) if below else page.rect.y1))
            if below:
                break
            # The label is the last one on the page, so whatever sits above the
            # next page's first label belongs to this question, even when this
            # page happens to end in white space (solutions often break there).
            if len(segments) >= max_pages or page_no >= doc.page_count:
                break
            # A new page that starts its first label right under the header is a
            # fresh section, not a continuation; a page whose first label sits
            # far down carries the tail of this question above it.
            nxt = page_no + 1
            nrow = rows.get(nxt) or {}
            nhits = [h["box"][1] * scale for h in nrow.get("hits", []) if h.get("box")]
            first_y = min(nhits) if nhits else doc[nxt - 1].rect.y1
            if first_y < doc[nxt - 1].rect.height * 0.15:
                break
            queue.append(nxt)
        return segments
    finally:
        doc.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("qid")
    parser.add_argument("--answer", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.answer:
        pair = answer_pages(args.qid)
        if not pair:
            raise SystemExit("no answer page for %s" % args.qid)
        book, pages = pair
    else:
        book, pages, _, _ = resolve_qid(args.qid)
    if args.out == "":
        args.out = os.path.join(ROOT, ".probe", "%s%s.png" % (args.qid.replace("-", "_"), "_a" if args.answer else ""))

    number = int(args.qid.split("-")[-1])
    segments = plan_segments(book, pages, number, args.qid)
    if not segments:
        raise SystemExit("no label box found (page %s)" % pages)
    w, h = render_segments(book, segments, args.out)
    print("%s -> 页 %s | %d 段 | %dx%d px | %s"
          % (args.qid, ",".join(str(s[0]) for s in segments), len(segments), w, h, args.out))


if __name__ == "__main__":
    main()
