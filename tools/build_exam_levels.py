"""Build index/exam_levels.json from the 2026 solution PDFs.

The 2026 HuaShan solution books ship one page per half called
「考点分布」: for every question it lists the point tested, the governing
standard, a frequency tag (偏 / 常规 / 灵活), the calculation size
(小 / 中 / 大) and the difficulty (易 / 中 / 难).  That table is the only
year with per-question labels, so it is worth turning into structured data:
the mock-paper review uses it to check whether a question was recognised as
"偏" before the clock was spent on it.

Both PDFs are pure scans, so the table is read with RapidOCR and the column
layout is recovered from token x-coordinates.  The two books use different
margins; each page passes its own column bounds.

Run:
    python tools/build_exam_levels.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE = ROOT / ".cache" / "exam_levels"
OUT = ROOT / "index" / "exam_levels.json"

FREQ = ("偏", "常规", "灵活")
CALC = ("小", "中", "大")
LEVEL = ("易", "中", "难")

# page -> (first, last, column bounds).  Bounds are x cut points for
# num / point / ref / freq / calc / level; everything right of the last one is
# the mock-paper reference plus the nine-category label.
LAYOUT = {
    ("A", 4): (1, 14, (400, 2200, 3700, 4150, 4500, 4900)),
    ("A", 5): (15, 25, (400, 2200, 3700, 4150, 4500, 4900)),
    ("P", 4): (1, 15, (170, 1000, 1800, 2050, 2250, 2600)),
    ("P", 5): (14, 25, (170, 1000, 1800, 2050, 2250, 2600)),
}

FILES = {
    "A": "2026.11.19 华山论剑岩土 2026年注册岩土专业案例真题（上午卷）详细答案.pdf",
    "P": "2026.11.19 华山论剑岩土 2026年注册岩土专业案例真题（下午卷）详细答案.pdf",
}


def ocr_page(pdf, page_no, engine):
    """OCR one page; returns [{t, x, y}] with top-left token coordinates."""
    import numpy as np
    from PIL import Image

    page = pdf[page_no - 1]
    pix = page.get_pixmap(dpi=200)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    array = np.ascontiguousarray(np.array(image)[:, :, ::-1])
    result = engine(array)
    boxes = result.boxes if result.boxes is not None else []
    items = []
    for text, box in zip(result.txts if result.txts is not None else [], boxes):
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        items.append({"t": text, "x": round(min(xs)), "y": round(min(ys))})
    return items


def _column(x, bounds):
    for name, cut in zip(("num", "point", "ref", "freq", "calc", "level"),
                         bounds):
        if x < cut:
            return name
    return "extra"


def parse_table(items, first, last, bounds):
    """Rows keyed by question number; a row window runs to the next number."""
    tagged = [dict(item, col=_column(item["x"], bounds)) for item in items]
    numbers = [
        item for item in tagged
        if item["col"] == "num" and re.fullmatch(r"\d{1,2}", item["t"])
        and first <= int(item["t"]) <= last
    ]
    numbers.sort(key=lambda item: item["y"])
    rows = {}
    for index, number in enumerate(numbers):
        y0 = number["y"] - 40
        y1 = numbers[index + 1]["y"] - 40 if index + 1 < len(numbers) else 10 ** 9
        row = {"point": "", "ref": "", "freq": "", "calc": "", "level": "",
               "extra": ""}
        window = [item for item in tagged if y0 <= item["y"] < y1]
        for item in sorted(window, key=lambda i: (i["col"], i["y"], i["x"])):
            col, text = item["col"], item["t"]
            if col == "num":
                continue
            if col == "freq" and text in FREQ and not row["freq"]:
                row["freq"] = text
            elif col == "calc" and text in CALC and not row["calc"]:
                row["calc"] = text
            elif col == "level" and text in LEVEL and not row["level"]:
                row["level"] = text
            elif col in ("point", "ref", "extra"):
                row[col] += text
        seq = int(number["t"])
        if seq not in rows:  # a duplicated number (running header) is ignored
            rows[seq] = row
    return rows


def build(pdf_dir=ROOT, use_cache=True):
    import pymupdf
    from rapidocr import RapidOCR

    CACHE.mkdir(parents=True, exist_ok=True)
    engine = None
    years = {}
    missed = []
    for (half, page_no), (first, last, bounds) in LAYOUT.items():
        cache_path = CACHE / ("%s_%d.json" % (half, page_no))
        items = None
        if use_cache and cache_path.exists():
            try:
                items = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                items = None
        if items is None:
            path = Path(pdf_dir) / FILES[half]
            if not path.exists():
                missed.append(str(path))
                continue
            pdf = pymupdf.open(path)
            try:
                if engine is None:
                    engine = RapidOCR(params={
                        "Global.log_level": "warning", "Rec.rec_batch_num": 32,
                    })
                items = ocr_page(pdf, page_no, engine)
            finally:
                pdf.close()
            cache_path.write_text(
                json.dumps(items, ensure_ascii=False), encoding="utf-8")
        rows = parse_table(items, first, last, bounds)
        years.setdefault(str(2026), {}).setdefault(half, {}).update(
            {str(seq): row for seq, row in sorted(rows.items())})
    return years, missed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", default=str(ROOT))
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    years, missed = build(args.pdf_dir, use_cache=not args.no_cache)
    payload = {
        "version": 1,
        "source": "2026 华山论剑详细答案 PDF 考点分布表",
        "note": "频率=偏/常规/灵活，计算量=小/中/大，难易=易/中/难；空格表示该格 OCR 缺失。",
        "question_count": {
            year: {half: len(rows) for half, rows in halves.items()}
            for year, halves in years.items()
        },
        "years": years,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    counts = payload["question_count"]
    print("wrote %s" % OUT)
    print("parsed:", counts)
    if missed:
        print("missing PDFs:", missed)


if __name__ == "__main__":
    main()
