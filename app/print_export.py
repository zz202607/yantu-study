"""Build one A4 question sheet plus one answer sheet from wrong-book rows.

The packing rules are the M0 print samples' rules: native physical size (max
125%), a writing area under every question, answers on their own pages.  The
only addition is per-item writing space, so a mixed C/Z sheet gives case
questions room and lets short knowledge questions share a page.

Z-family questions print answer and solution on the same page, so their crop
is split at 【答案】 and the two halves go to opposite sheets.
"""

import os
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import crop_question as CQ  # noqa: E402
import make_print_sample as MPS  # noqa: E402

MAX_QUESTIONS = 30
CROP_DPI = 300
CASE_FAMILIES = set("CAM")
CASE_WRITE = MPS.WRITE["question_case"]
KNOW_WRITE = MPS.WRITE["question_know"]


def _short(book):
    name = Path(book).stem
    return name.replace("_扫描版", "").replace("高清版", "")


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


def _stamp(doc, title, answer_from):
    for i, page in enumerate(doc):
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


def export_pdf(qids, out_path, title, tmp_dir, engine=None, ocr_lock=None):
    """Write one PDF; returns page counts and per-question problems."""
    qids = [str(qid).strip().upper() for qid in qids if str(qid).strip()]
    qids = list(dict.fromkeys(qids))[:MAX_QUESTIONS]
    if not qids:
        raise ValueError("没有可打印的题")

    doc = pymupdf.open()
    questions = MPS.Sheet(doc, "question_case", CASE_WRITE)
    answers = MPS.Sheet(doc, "answer", MPS.WRITE["answer"])
    errors = []
    answered = 0

    for qid in qids:
        family = qid[0]
        try:
            prefer = "blank" if family == "C" else "solutions"
            full, source, short = _render(qid, prefer, tmp_dir, "q")
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - one bad qid only
            errors.append({"qid": qid, "detail": "题面失败：%s" % exc})
            continue
        label = "%s · %s" % (qid, short)
        write = CASE_WRITE if family in CASE_FAMILIES else KNOW_WRITE

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
        questions.add(label, source, str(full), min_write=write)

        if family == "Z":
            if answer_path:
                answers.add("%s 答案" % qid, source, str(answer_path))
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
            answers.add("%s 答案" % qid, asource, str(apath))
            answered += 1
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            errors.append({"qid": qid, "detail": "答案失败：%s" % exc})

    try:
        questions.render()
        answer_from = len(questions.pages)
        answers.render()
        if not questions.pages:
            raise ValueError("所有题面都没生成出来")
        _stamp(doc, title, answer_from)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out_path), garbage=4)
        return {
            "questions": len(questions.items),
            "answers": answered,
            "question_pages": len(questions.pages),
            "answer_pages": len(answers.pages),
            "pages": doc.page_count,
            "errors": errors,
        }
    finally:
        doc.close()
