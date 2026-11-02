"""Build a chapter/section index for the local standards library.

Standards are scans or text PDFs with inconsistent bookmarks.  The index uses
three sources, in order:

1. a text-layer table of contents ("目 次" / "目录") — exact printed pages;
2. OCR of the front pages when there is no text layer;
3. embedded bookmarks when neither TOC is usable (coarse, but still navigable).

Printed page -> PDF page offset is estimated by finding the first chapter title
after the TOC, or by reading a standalone page number near a page edge.

Output: index/standards_index.json (titles and page numbers only).
Body text of text-layer standards goes to .cache/standards_text/<code>.json,
never into the repo.

Usage:
    py -3 tools/build_standards_index.py --only GB50007
    py -3 tools/build_standards_index.py --no-ocr          # fast, text/bookmarks only
    py -3 tools/build_standards_index.py                   # all, OCR where needed
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

from app.standards import _decode_name, code_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FOLDER = Path(
    r"E:\其他文件\2025YT\3注册岩土全套电子版规范（2024大纲）"
)
OUT = ROOT / "index" / "standards_index.json"
TEXT_CACHE = ROOT / ".cache" / "standards_text"
ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        # Import lazily so parser/index tests and the CI gate do not need the
        # ONNX OCR stack just to read a TOC.
        from rapidocr import RapidOCR
        ENGINE = RapidOCR(params={"Global.log_level": "warning", "Rec.rec_batch_num": 32})
    return ENGINE


def _clean_line(line):
    text = str(line or "")
    text = text.replace("．", ".").replace("。", ".").replace("，", ",")
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"[…⋯·]{2,}", " ", text)
    text = re.sub(r"\.{2,}", " ", text)
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return text.strip(" .·…")


_NUMBERED = re.compile(r"^(\d+(?:\.\d+){0,3})\s+(.+?)\s+(\d{1,4})$")
_APPENDIX = re.compile(r"^附录\s*([A-Z])\s*[.、]?\s*(.+?)\s+(\d{1,4})$")
_CHAPTER_CN = re.compile(r"^第([一二三四五六七八九十百]+)章\s*(.+?)\s+(\d{1,4})$")


def _merge_split_toc_lines(lines):
    """Join Chinese TOCs that put number / title / page on separate lines."""
    cleaned = [_clean_line(line) for line in lines]
    cleaned = [line for line in cleaned if line]
    merged = []
    index = 0
    while index < len(cleaned):
        line = cleaned[index]
        number_only = bool(re.fullmatch(r"\d+(?:\.\d+){0,3}", line)
                           or re.fullmatch(r"附录\s*[A-Z]", line)
                           or re.fullmatch(r"第[一二三四五六七八九十百]+章", line))
        if number_only and index + 1 < len(cleaned):
            next_line = cleaned[index + 1]
            if re.search(r"\s\d{1,4}$", next_line):
                merged.append(line + " " + next_line)
                index += 2
                continue
            if index + 2 < len(cleaned) and re.fullmatch(r"\d{1,4}", cleaned[index + 2]):
                merged.append(line + " " + next_line + " " + cleaned[index + 2])
                index += 3
                continue
        if (re.match(r"^\d+(?:\.\d+){0,3}\s+\D", line)
                and index + 1 < len(cleaned)
                and re.fullmatch(r"\d{1,4}", cleaned[index + 1])):
            merged.append(line + " " + cleaned[index + 1])
            index += 2
            continue
        merged.append(line)
        index += 1
    return merged


def parse_toc_lines(lines):
    """Parse cleaned TOC lines into chapter/section entries.

    OCR of dotted leaders is noisy: keep line order, drop impossible page
    jumps, and reject 3-digit "chapter" numbers that are actually page
    fragments or names.  Chinese and English TOCs may both appear; prefer the
    Chinese title for the same section number.
    """
    entries = []
    by_number = {}
    last_page = 0
    for line in _merge_split_toc_lines(lines):
        match = _NUMBERED.match(line)
        number = title = None
        page = None
        if match:
            number, title, page = match.group(1), match.group(2), int(match.group(3))
        else:
            match = _APPENDIX.match(line)
            if match:
                number, title, page = "附录" + match.group(1), match.group(2), int(match.group(3))
            else:
                match = _CHAPTER_CN.match(line)
                if match:
                    number, title, page = "第%s章" % match.group(1), match.group(2), int(match.group(3))
        if not title or not page or page > 3000:
            continue
        title = re.sub(r"\s+", " ", title).strip(" .·…")
        if not title or len(title) > 80:
            continue
        if re.match(r"^\d", title):
            continue  # merged line: "4 7.3 水泥土..." is not a chapter title
        first = re.match(r"^(\d+)", number)
        if first and int(first.group(1)) > 30:
            continue  # OCR fragment such as "101 李峰" / "44 座干与"
        if page < last_page - 12:
            continue  # OCR misread a page number (e.g. 6.8 -> 5)
        current = by_number.get(number)
        has_cjk = bool(re.search(r"[一-鿿]", title))
        if current is not None:
            current_cjk = bool(re.search(r"[一-鿿]", current["title"]))
            if has_cjk and not current_cjk:
                current.update(title=title, printed_page=page)
            continue
        last_page = max(last_page, page)
        level = 1 if "章" in number or number.startswith("附录") else number.count(".") + 1
        entry = {
            "level": level,
            "number": number,
            "title": title,
            "printed_page": page,
        }
        by_number[number] = entry
        entries.append(entry)
    return entries


def _ocr_lines(page, dpi=110):
    """OCR one page, group boxes on the same baseline into one line."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, 3
    )[:, :, ::-1]
    try:
        result = engine()(np.ascontiguousarray(arr))
    except Exception:  # noqa: BLE001 - one bad page must not kill the build
        return []
    boxes = list(result.boxes) if result.boxes is not None else []
    texts = list(result.txts) if result.txts is not None else []
    items = []
    for box, text in zip(boxes, texts):
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((min(ys), min(xs), str(text)))
    items.sort()
    groups = []
    for y, x, text in items:
        if groups and abs(y - groups[-1]["y"]) <= 10:
            groups[-1]["parts"].append((x, text))
        else:
            groups.append({"y": y, "parts": [(x, text)]})
    lines = []
    for group in groups:
        parts = [text for _, text in sorted(group["parts"])]
        lines.append(" ".join(parts))
    return lines


def find_text_toc(text_pages, max_front=30):
    for index, text in enumerate(text_pages[:max_front]):
        if "目" in text and ("次" in text or "录" in text):
            pages = [index]
            for extra in range(1, 4):
                if index + extra < len(text_pages):
                    pages.append(index + extra)
            lines = []
            for page_index in pages:
                lines.extend(
                    line for line in text_pages[page_index].splitlines()
                    if line.strip()
                )
            entries = parse_toc_lines(lines)
            if len(entries) >= 3:
                return entries, pages
    return [], []


def _section_key(entry):
    number = str(entry.get("number") or "")
    if number.startswith("附录"):
        return (1000, number)
    if "章" in number:
        return (900, number)
    try:
        return (0, tuple(int(part) for part in number.split(".")))
    except ValueError:
        return (2000, number)


def sanitize_entries(entries):
    """Clamp OCR page numbers that contradict the section order.

    Example from GB 50007: 6.4 was OCR'd as page 68, between 6.2 (35) and
    6.7 (45).  The page is clamped to the next known section and marked
    estimated rather than opening a wildly wrong page.
    """
    rows = [dict(row) for row in entries if row.get("printed_page")]
    rows.sort(key=_section_key)
    for index, row in enumerate(rows):
        following = next(
            (later["printed_page"] for later in rows[index + 1:]
             if later.get("printed_page")),
            None,
        )
        previous = None
        for earlier in reversed(rows[:index]):
            if earlier.get("printed_page"):
                previous = earlier["printed_page"]
                break
        page = row["printed_page"]
        if following is not None and page > following:
            page = following
            row["estimated"] = True
        if previous is not None and page < previous:
            page = previous
            row["estimated"] = True
        row["printed_page"] = page
    rows.sort(key=lambda row: (row["printed_page"], row["level"], row["number"]))
    return rows


def _merge_ocr_pages(low_lines, high_lines):
    """110 dpi keeps page numbers; 150 dpi keeps titles. Merge by number."""
    low = parse_toc_lines(low_lines)
    high = parse_toc_lines(high_lines)
    by_number = {row["number"]: dict(row) for row in low}
    for row in high:
        current = by_number.get(row["number"])
        if current is None:
            by_number[row["number"]] = dict(row)
        else:
            # Prefer the sharper title and a page number from either pass.
            if row["title"] and len(row["title"]) >= len(current.get("title", "")):
                current["title"] = row["title"]
            if row["printed_page"]:
                current["printed_page"] = row["printed_page"]
    rows = [row for row in by_number.values() if row.get("printed_page")]
    rows.sort(key=lambda row: (row["printed_page"], row["level"], row["number"]))
    return rows


def find_ocr_toc(doc, max_front=16, dpi=150):
    if doc.page_count == 0:
        return [], []
    toc_pages = []
    for index in range(min(max_front, doc.page_count)):
        lines = _ocr_lines(doc[index], dpi=110)
        joined = " ".join(lines)
        if ("目" in joined and ("次" in joined or "录" in joined)) or toc_pages:
            toc_pages.append(index)
            if len(toc_pages) >= 4:
                break
    if not toc_pages:
        return [], []
    entries = []
    for index in toc_pages:
        low_lines = _ocr_lines(doc[index], dpi=110)
        high_lines = _ocr_lines(doc[index], dpi=dpi)
        entries.extend(_merge_ocr_pages(low_lines, high_lines))
    # Deduplicate across pages; keep the first occurrence.
    merged = {}
    for row in entries:
        merged.setdefault(row["number"], row)
    rows = list(merged.values())
    rows.sort(key=lambda row: (row["printed_page"], row["level"], row["number"]))
    return rows, toc_pages


def _normalize_title(text):
    return re.sub(r"[\s.．·…]", "", str(text or ""))


def _find_offset_text(text_pages, entries, toc_pages):
    if not entries:
        return None
    start = (max(toc_pages) + 1) if toc_pages else 1
    votes = []
    cjk = [row for row in entries if re.search(r"[一-鿿]", row["title"])]
    for entry in (cjk or entries)[:6]:
        title = _normalize_title(entry["title"])
        if len(title) < 2:
            continue
        for index in range(start, min(len(text_pages), start + 30)):
            page_text = _normalize_title(text_pages[index])
            if title and title in page_text:
                votes.append((index + 1) - entry["printed_page"])
                break
    if not votes:
        # Fallback: a standalone printed number in the first/last lines.
        for index in range(start, min(len(text_pages), start + 20)):
            lines = [line.strip() for line in text_pages[index].splitlines()
                     if line.strip()]
            for line in lines[:3] + lines[-3:]:
                if re.fullmatch(r"\d{1,4}", line):
                    number = int(line)
                    if 1 <= number <= len(text_pages):
                        votes.append((index + 1) - number)
                    break
    if not votes:
        return None
    value, count = Counter(votes).most_common(1)[0]
    return {"offset": value, "confidence": "high" if count >= 2 else "medium"}


def _find_offset_ocr(doc, entries, toc_pages, max_pages=4, dpi=110):
    if not entries:
        return None
    start = (max(toc_pages) + 1) if toc_pages else 1
    votes = []
    cjk = [row for row in entries if re.search(r"[一-鿿]", row["title"])]
    for index in range(start, min(doc.page_count, start + max_pages)):
        lines = _ocr_lines(doc[index], dpi=dpi)
        joined = _normalize_title(" ".join(lines))
        for entry in (cjk or entries)[:5]:
            title = _normalize_title(entry["title"])
            if len(title) >= 2 and title in joined:
                votes.append((index + 1) - entry["printed_page"])
                break
        # A standalone page number near the bottom is a weaker fallback.
        numbers = []
        for line in lines[::-1]:
            match = re.fullmatch(r"\s*(\d{1,4})\s*", line)
            if match:
                numbers.append(int(match.group(1)))
                break
        if numbers and numbers[0] <= doc.page_count:
            votes.append((index + 1) - numbers[0])
    if not votes:
        return None
    value, count = Counter(votes).most_common(1)[0]
    return {"offset": value, "confidence": "high" if count >= 2 else "medium"}


def bookmark_entries(doc):
    entries = []
    for level, title, page in doc.get_toc(simple=True):
        if page < 1:
            continue
        entries.append({
            "level": int(level),
            "number": "",
            "title": str(title),
            "printed_page": None,
            "pdf_page": int(page),
        })
    return entries


def build_standard(path, use_ocr=True, front_pages=16, dpi=110):
    doc = pymupdf.open(str(path))
    try:
        parsed = _decode_name(path.name)
        code = parsed["code"]
        title = parsed["title"]
        text_pages = [page.get_text("text") for page in doc]
        text_chars = sum(len(text) for text in text_pages)
        has_text = text_chars > 2000
        source = "bookmark"
        entries = []
        toc_pages = []
        offset_info = None
        if has_text:
            entries, toc_pages = find_text_toc(text_pages)
            if entries:
                source = "text_toc"
                offset_info = _find_offset_text(text_pages, entries, toc_pages)
        if not entries and use_ocr:
            entries, toc_pages = find_ocr_toc(doc, max_front=front_pages, dpi=dpi)
            if entries:
                source = "ocr_toc"
                offset_info = _find_offset_ocr(doc, entries, toc_pages, dpi=dpi)
        if entries:
            offset = (offset_info or {}).get("offset", 0)
            entries = sanitize_entries(entries)
            for entry in entries:
                entry["pdf_page"] = max(
                    1, min(doc.page_count, entry["printed_page"] + offset)
                )
            toc = entries
            confidence = (offset_info or {}).get("confidence", "low")
        else:
            toc = bookmark_entries(doc)
            source = "bookmark" if toc else "none"
            confidence = "low"
            offset = 0
        if has_text:
            TEXT_CACHE.mkdir(parents=True, exist_ok=True)
            cache_path = TEXT_CACHE / ("%s.json" % (code_key(code) or path.stem))
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump({"pages": [
                    re.sub(r"[ \t\u3000]+", " ", page).strip()
                    for page in text_pages
                ]}, fh, ensure_ascii=False)
        return {
            "name": path.name,
            "code": code,
            "code_key": code_key(code),
            "title": title,
            "page_count": doc.page_count,
            "source": source,
            "offset": offset,
            "confidence": confidence,
            "has_text": has_text,
            "toc_pages": [index + 1 for index in toc_pages],
            "toc": toc,
        }
    finally:
        doc.close()


def main():
    parser = argparse.ArgumentParser(description="Build standards chapter index")
    parser.add_argument("--folder", default=str(DEFAULT_FOLDER))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--only", default="",
                        help="只处理文件名/编号包含该字符串的标准")
    parser.add_argument("--no-ocr", action="store_true",
                        help="只读文字层/书签，不 OCR")
    parser.add_argument("--front-pages", type=int, default=16)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--merge", action="store_true",
                        help="保留旧输出中的其它标准，只替换本次处理项")
    args = parser.parse_args()

    folder = Path(args.folder)
    files = [path for path in sorted(folder.glob("*.pdf"))
             if "工程地质手册" not in path.name]
    if args.only:
        needles = [part.strip().upper() for part in args.only.split(",")
                   if part.strip()]
        files = [path for path in files
                 if any(needle in path.name.upper() for needle in needles)]
    if not files:
        raise SystemExit("没有找到 PDF：%s" % folder)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    standards = []
    if args.merge and out.exists():
        try:
            with open(out, encoding="utf-8") as fh:
                standards = json.load(fh).get("standards", [])
        except (OSError, ValueError):
            standards = []
    by_key = {
        row.get("code_key") or row.get("name"): row for row in standards
    }
    for index, path in enumerate(files, start=1):
        started = time.time()
        print("[%d/%d] %s" % (index, len(files), path.name), flush=True)
        try:
            row = build_standard(
                path, use_ocr=not args.no_ocr,
                front_pages=args.front_pages, dpi=args.dpi,
            )
        except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the rest
            print("  ! %s" % exc, flush=True)
            continue
        print(
            "  %s · %s 页 · %d 条 · offset %s (%s) · %.1fs"
            % (row["source"], row["page_count"], len(row["toc"]),
               row["offset"], row["confidence"], time.time() - started),
            flush=True,
        )
        by_key[row.get("code_key") or row.get("name")] = row
        standards = list(by_key.values())
        tmp_out = out.with_suffix(".tmp")
        with open(tmp_out, "w", encoding="utf-8") as fh:
            json.dump({
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "folder": str(folder),
                "standards": standards,
            }, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_out, out)
    print("wrote %s (%d standards)" % (out, len(standards)))


if __name__ == "__main__":
    main()
