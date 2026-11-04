"""Build one A4 question sheet plus one answer sheet from wrong-book rows.

The packing rules are the M0 print samples' rules: native physical size (max
125%), a writing area under every question, answers on their own pages.  The
only addition is per-item writing space, so a mixed C/Z sheet gives case
questions room and lets short knowledge questions share a page.

Z-family questions print answer and solution on the same page, so their crop
is split at 【答案】 and the two halves go to opposite sheets.
"""

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import crop_question as CQ  # noqa: E402
import make_print_sample as MPS  # noqa: E402

MAX_QUESTIONS = 30
# A travel pack prints several days at once; the per-request cap is higher but
# still bounded so one request cannot occupy the printer loop for minutes.
MAX_WEEK_QUESTIONS = 100
DEFAULT_PRINT_NAME = "打印卷"
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
CROP_DPI = 300
CASE_FAMILIES = set("CAM")
CASE_WRITE = MPS.WRITE["question_case"]
KNOW_WRITE = MPS.WRITE["question_know"]
QID_RE = re.compile(
    r"^(?:[CZ]\d{1,2}-\d{1,3}|[AK]\d{4}-[A-Z]-\d{1,3}|M\d{1,2}-\d{1,3})$"
)


def safe_title(text, fallback=DEFAULT_PRINT_NAME, limit=40):
    """A Windows-safe label for a downloaded file; Chinese is kept."""
    name = BAD_NAME_CHARS.sub("-", str(text or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .-")
    if not name:
        name = fallback
    return name[:limit].strip(" .-") or fallback


def print_filename(title, fallback=DEFAULT_PRINT_NAME, now=None):
    """Distinct download name: 岩土-<title>-YYYYMMDD-HHMMSS.pdf.

    A bare timestamp is hard to tell apart in a download folder; the title is
    what the user printed.  Seconds keep two prints of the same scope apart.
    """
    moment = now.timestamp() if isinstance(now, datetime) else now
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
    name = safe_title(title, fallback)
    if not name.startswith("岩土-"):
        name = "岩土-" + name
    return "%s-%s.pdf" % (name, stamp)


def parse_qid_list(text):
    """Split a hand-picked qid list into full keys, keeping first-seen order.

    Only full keys are accepted: a bare "3-11" exists in both the case and the
    knowledge book, so the caller has to resolve the family before printing.
    """
    out = []
    for part in re.split(r"[,;，；、\s]+", text or ""):
        candidate = part.strip().upper()
        if QID_RE.match(candidate):
            out.append(candidate)
    return list(dict.fromkeys(out))


def _short(book):
    """A readable book name: drop 【…】 tags, a leading year and scan suffixes."""
    name = re.sub(r"【[^】]*】", "", Path(book).stem)
    name = name.replace("_扫描版", "").replace("高清版", "")
    name = re.sub(r"^\d{4}", "", name)
    return name.strip()


def _render(qid, prefer, tmp_dir, suffix):
    book, pages, _, _ = CQ.resolve_qid(qid, prefer=prefer)
    number = int(qid.split("-")[-1])
    segments = CQ.plan_segments(book, pages, number, qid)
    if not segments:
        raise ValueError("找不到题号位置（第 %s 页）" % pages[0])
    path = Path(tmp_dir) / ("%s_%s.png" % (qid.replace("-", "_"), suffix))
    CQ.render_segments(book, segments, str(path), dpi=CROP_DPI)
    MPS.clean_and_trim(str(path))
    short = _short(book)
    source = "来源：%s 第 %s 页" % (short, pages[0])
    return path, source, short


def _split_z(full_path, tmp_dir, qid, engine, ocr_lock):
    qpath = Path(tmp_dir) / ("%s_zq.png" % qid.replace("-", "_"))
    apath = Path(tmp_dir) / ("%s_za.png" % qid.replace("-", "_"))
    if ocr_lock is not None:
        with ocr_lock:
            split = CQ.split_z_answer(
                str(full_path), str(qpath), str(apath), engine=engine
            )
    else:
        split = CQ.split_z_answer(
            str(full_path), str(qpath), str(apath), engine=engine
        )
    if not split:
        return None, None
    MPS.clean_and_trim(str(qpath))
    MPS.clean_and_trim(str(apath))
    return qpath, apath


def _stamp(doc, title, answer_from, cover_pages=0, skip_pages=None):
    skip = set(range(cover_pages)) | set(skip_pages or ())
    for i, page in enumerate(doc):
        if i in skip:
            continue
        suffix = " · 答案" if i >= answer_from else " · 题面"
        MPS.text_line(page, title + suffix, 40, 9, color=(0.25, 0.25, 0.25))
        MPS.text_line(
            page, "第 %d/%d 页" % (i + 1, doc.page_count), 40, 9, right=True,
            color=(0.25, 0.25, 0.25),
        )
        page.draw_line(
            pymupdf.Point(MPS.MARGIN, 48),
            pymupdf.Point(MPS.A4[0] - MPS.MARGIN, 48),
            color=(0.6, 0.6, 0.6),
            width=0.5,
        )


def _cover_page(doc, cover, title):
    """A one-page task sheet: what to do, qids, and a result tick table."""
    page = doc.new_page(width=MPS.A4[0], height=MPS.A4[1])
    MPS.text_line(page, cover.get("title") or title, 72, 18)
    if cover.get("subtitle"):
        MPS.text_line(page, cover["subtitle"], 96, 10, color=(0.35, 0.35, 0.35))
    y = 128
    for task in (cover.get("tasks") or [])[:10]:
        MPS.text_line(page, "□ " + task.get("label", ""), y, 10)
        if task.get("qids"):
            MPS.text_line(
                page, "   " + "、".join(task["qids"]), y + 14, 8,
                color=(0.4, 0.4, 0.4),
            )
        y += 34
    qids = cover.get("qids") or []
    if qids:
        y = max(y + 6, 260)
        MPS.text_line(page, "结果回填（做完直接照此录 App）", y, 11)
        y += 22
        half = (len(qids) + 1) // 2
        for index, qid in enumerate(qids):
            column = 0 if index < half else 1
            row = index if column == 0 else index - half
            x = MPS.MARGIN if column == 0 else MPS.A4[0] / 2 + 8
            text = "%s   对□ 错□ 放弃□ 超时□" % qid
            box = pymupdf.Rect(x, y + row * 15, x + 250, y + row * 15 + 14)
            page.insert_textbox(
                box, text, fontname="msyh", fontfile=MPS.FONT, fontsize=8,
                color=(0.3, 0.3, 0.3),
            )
    return page


def _normalize_sections(qids, sections, max_questions):
    """One internal shape for a plain sheet and a multi-day pack.

    Returns `(sections, kept)` where every section keeps its title/subtitle
    and only qids inside the global cap; first-seen order wins.
    """
    if sections:
        seen, normalized = set(), []
        for raw in sections:
            keep = []
            for qid in raw.get("qids") or []:
                qid = str(qid).strip().upper()
                if qid and qid not in seen:
                    seen.add(qid)
                    keep.append(qid)
            if keep:
                normalized.append({**raw, "qids": keep})
        sections = normalized
    else:
        seen, keep = set(), []
        for qid in qids or []:
            qid = str(qid).strip().upper()
            if qid and qid not in seen:
                seen.add(qid)
                keep.append(qid)
        sections = [{"qids": keep}]
    kept = [qid for section in sections for qid in section["qids"]]
    allowed = kept[:max_questions]
    allowed_set = set(allowed)
    for section in sections:
        section["qids"] = [qid for qid in section["qids"] if qid in allowed_set]
    return [section for section in sections if section["qids"]], allowed


def export_pdf(qids=None, out_path=None, title="", tmp_dir=None, engine=None,
               ocr_lock=None, jpeg=None, writing_scale=1.0, result_boxes=False,
               cover=None, sections=None, max_questions=MAX_QUESTIONS):
    """Write one PDF; returns page counts and per-question problems.

    `jpeg=(dpi, quality)` embeds page images as compressed JPEG.  Default is
    the lossless PNG path used by the print samples.  `writing_scale` gives
    more/less writing space; `result_boxes` prints a tick line under each
    question; `cover` prepends a task/result sheet.

    `sections` turns the sheet into a multi-day pack: each `{title, subtitle,
    qids}` gets a small cover page, its question pages follow in order, and
    every answer waits on the sealed answer pages at the end.
    """
    sections, qids = _normalize_sections(qids, sections, max_questions)
    if not qids:
        raise ValueError("没有可打印的题")
    try:
        writing_scale = max(0.6, min(float(writing_scale or 1.0), 1.8))
    except (TypeError, ValueError):
        writing_scale = 1.0

    doc = pymupdf.open()
    cover_pages = set()
    if cover:
        # An explicit empty list is meaningful (a week pack: day covers carry
        # their own tables, and 100 qids would overflow one page).
        cover_qids = cover["qids"] if "qids" in cover else qids
        _cover_page(doc, {**cover, "qids": cover_qids}, title)
        cover_pages.add(0)
    errors = []
    answered = 0
    question_pages = 0
    question_count = 0
    answer_items = []
    printed_sections = []

    for section in sections:
        section_cover = None
        if section.get("title"):
            _cover_page(
                doc,
                {
                    "title": section["title"],
                    "subtitle": section.get("subtitle"),
                    "qids": section["qids"],
                },
                title,
            )
            section_cover = doc.page_count - 1
            cover_pages.add(section_cover)
        sheet = MPS.Sheet(doc, "question_case", CASE_WRITE * writing_scale)
        sheet.jpeg = jpeg
        sheet.result_boxes = bool(result_boxes)
        section_answers = []
        for qid in section["qids"]:
            family = qid[0]
            try:
                prefer = "blank" if family == "C" else "solutions"
                full, source, _ = _render(qid, prefer, tmp_dir, "q")
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - one bad qid
                errors.append({"qid": qid, "detail": "题面失败：%s" % exc})
                continue
            label = qid  # the source line already names the book and page
            write = (CASE_WRITE if family in CASE_FAMILIES
                     else KNOW_WRITE) * writing_scale

            answer_path = None
            if family == "Z":
                qpath, apath = _split_z(full, tmp_dir, qid, engine, ocr_lock)
                if qpath:
                    full = qpath
                    answer_path = apath
                else:
                    errors.append(
                        {"qid": qid, "detail": "没找到【答案】行，题面可能带答案"}
                    )
            sheet.add(label, source, str(full), min_write=write)

            if family == "Z":
                if answer_path:
                    section_answers.append(
                        ("%s 答案" % qid, source, str(answer_path))
                    )
                    answered += 1
                continue
            try:
                if family == "C":
                    apath, asource, _ = _render(qid, "solutions", tmp_dir, "a")
                else:
                    pair = CQ.answer_pages(qid)
                    if not pair:
                        errors.append({"qid": qid, "detail": "没有答案页"})
                        continue
                    book, pages = pair
                    number = int(qid.split("-")[-1])
                    segments = CQ.plan_segments(book, pages, number, qid)
                    if not segments:
                        errors.append({"qid": qid, "detail": "找不到答案位置"})
                        continue
                    apath = Path(tmp_dir) / ("%s_a.png" % qid.replace("-", "_"))
                    CQ.render_segments(book, segments, str(apath), dpi=CROP_DPI)
                    MPS.clean_and_trim(str(apath))
                    asource = "来源：%s 第 %s 页" % (_short(book), pages[0])
                section_answers.append(
                    ("%s 答案" % qid, asource, str(apath))
                )
                answered += 1
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                errors.append({"qid": qid, "detail": "答案失败：%s" % exc})
        if sheet.items:
            sheet.render()
            question_pages += len(sheet.pages)
            question_count += len(sheet.items)
            printed_sections.append({
                "title": section.get("title"),
                "count": len(sheet.items),
            })
        elif section_cover is not None:
            # Every crop failed: an empty day cover would claim work that
            # cannot be printed.  The index is the last page, nothing follows.
            doc.delete_page(section_cover)
            cover_pages.discard(section_cover)
        answer_items.append(section_answers)

    try:
        if not question_count:
            raise ValueError("所有题面都没生成出来")
        answer_from = doc.page_count
        answer_page_count = 0
        for section, entries in zip(sections, answer_items):
            sheet = MPS.Sheet(doc, "answer", MPS.WRITE["answer"])
            sheet.jpeg = jpeg
            prefix = section.get("short") or ""
            for label, source, path in entries:
                sheet.add(("%s %s" % (prefix, label)).strip(), source, path)
            if sheet.items:
                sheet.render()
                answer_page_count += len(sheet.pages)
        _stamp(doc, title, answer_from, skip_pages=cover_pages)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out_path), garbage=4)
        return {
            "questions": question_count,
            "answers": answered,
            "question_pages": question_pages,
            "answer_pages": answer_page_count,
            "cover_pages": len(cover_pages),
            "pages": doc.page_count,
            "sections": printed_sections,
            "errors": errors,
        }
    finally:
        doc.close()
