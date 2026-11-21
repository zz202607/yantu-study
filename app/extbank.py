"""External daily-homework pairs from the WeChat account.

The WeChat article itself cannot be fetched (the page is behind a captcha), so
the phone saves the question page and the answer page as images; this module
turns that pair into one local question: OCR for metadata, crop the page
header/footer and blank margins, store question/answer images under
``data/ext/<qid>/`` and index them in ``ext_questions``.

Everything stays under ``data/`` — images are never committed or uploaded.
The user's rule is explicit: a page that says ``YYYY年M月D日作业`` counts as a
case, even when the page happens to use A/B/C/D options.
"""

from __future__ import annotations

import io
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
A4 = (595.28, 841.89)
MARGIN = 36
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 12

DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-—–]\s*(\d{1,2})(?!\d)")
SOLUTION_RE = re.compile(r"^\s*(解|答|【解析】|【答案】|解析)")
FONT = Path(r"C:\Windows\Fonts\msyh.ttc")


class ExtError(RuntimeError):
    """User-facing import or print failure."""


def _engine(engine=None):
    if engine is not None:
        return engine
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning", "Rec.rec_batch_num": 32})


def _rows(image, engine):
    array = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])
    result = engine(array)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None) or []
    if boxes is None:
        return []
    rows = []
    for box, text in zip(np.array(boxes), texts):
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        rows.append({
            "x0": min(xs), "x1": max(xs),
            "y0": min(ys), "y1": max(ys),
            "text": str(text or ""),
        })
    rows.sort(key=lambda row: (row["y0"], row["x0"]))
    return rows


def _content(rows, height):
    """Drop the account header and the page footer; keep the question body."""
    top = height * 0.05
    bottom = height * 0.96
    return [row for row in rows if row["y0"] >= top and row["y1"] <= bottom]


def _solution_index(rows):
    for index, row in enumerate(rows):
        if SOLUTION_RE.match(row["text"]):
            return index
    return None


def _crop(image, rows, role, stop_at_solution=False):
    """Crop one screenshot to its content; answers start at the solution.

    `stop_at_solution` is for the answer page that also contains the question:
    the question crop ends just before 「解/答」 so the two halves do not
    repeat each other.
    """
    width, height = image.size
    content = _content(rows, height)
    solution = _solution_index(content)
    if role == "answer":
        if solution is not None:
            content = content[solution:]
    elif stop_at_solution and solution is not None:
        content = content[:solution]
    if not content:
        return image.crop((0, 0, width, height)), (0, 0, width, height)
    x0 = max(0, min(row["x0"] for row in content) - 20)
    x1 = min(width, max(row["x1"] for row in content) + 20)
    y0 = max(0, min(row["y0"] for row in content) - 8)
    y1 = min(height, max(row["y1"] for row in content) + 16)
    if x1 - x0 < 80 or y1 - y0 < 40:
        return image.crop((0, 0, width, height)), (0, 0, width, height)
    box = (int(x0), int(y0), int(x1), int(y1))
    return image.crop(box), box


def _thumb(image, dest):
    thumb = image.copy()
    thumb.thumbnail((480, 480))
    thumb.save(dest, "PNG")


def _meta(rows, fallback_day, title=""):
    text = " ".join(row["text"] for row in rows)
    day = fallback_day
    match = DATE_RE.search(text)
    if match:
        year, month, day_number = (int(part) for part in match.groups())
        try:
            day = datetime(year, month, day_number).date().isoformat()
        except ValueError:
            pass
    else:
        match = SHORT_DATE_RE.search(text)
        if match:
            month, day_number = (int(part) for part in match.groups())
            year = fallback_day[:4]
            try:
                day = datetime(int(year), month, day_number).date().isoformat()
            except ValueError:
                pass
    if not title:
        for row in rows:
            if "作业" in row["text"]:
                title = row["text"].strip()
                break
    if not title:
        title = "公众号题 · %s" % day
    return day, title


def _load_image(src):
    try:
        image = Image.open(src)
        image.load()
    except Exception as exc:  # noqa: BLE001 - one bad upload must not break the batch
        raise ExtError("图片读不了：%s" % exc)
    if image.width * image.height < 20_000:
        raise ExtError("图片太小，拍清楚一点再传")
    return image.convert("RGB")


def _crop_texts(rows, box):
    x0, y0, x1, y1 = box
    return [
        row["text"] for row in rows
        if row["x1"] > x0 and row["x0"] < x1
        and row["y1"] > y0 and row["y0"] < y1
    ]


def _save_image(src, dest_original, dest_crop, dest_thumb, role, engine,
                rows=None, stop_at_solution=False):
    image = _load_image(src)
    image.save(dest_original, "PNG")
    if rows is None:
        rows = _rows(image, engine)
    cropped, box = _crop(image, rows, role, stop_at_solution=stop_at_solution)
    cropped.save(dest_crop, "PNG")
    _thumb(cropped, dest_thumb)
    return _crop_texts(rows, box)


def _save_preserved(src, dest_original, dest_crop, dest_thumb, engine=None,
                    read_text=True):
    """Store an already-cropped exam slide without cropping it again."""
    image = _load_image(src)
    image.save(dest_original, "PNG")
    image.save(dest_crop, "PNG")
    _thumb(image, dest_thumb)
    if not read_text:
        return []
    return [row["text"] for row in _rows(image, engine)]


def _save_answer_page(src, dest_original, dest_question, dest_qthumb,
                      dest_answer, dest_athumb, engine, rows=None):
    """Split one answer page into question and answer halves."""
    image = _load_image(src)
    image.save(dest_original, "PNG")
    if rows is None:
        rows = _rows(image, engine)
    if _solution_index(_content(rows, image.height)) is None:
        raise ExtError("答案页里没找到「解 / 答 / 解析」；只传答案页时，答案页要含解答")
    q_crop, q_box = _crop(image, rows, "question", stop_at_solution=True)
    a_crop, a_box = _crop(image, rows, "answer")
    q_crop.save(dest_question, "PNG")
    _thumb(q_crop, dest_qthumb)
    a_crop.save(dest_answer, "PNG")
    _thumb(a_crop, dest_athumb)
    return _crop_texts(rows, q_box), _crop_texts(rows, a_box), rows


def import_pair(store, data_dir, question_paths, answer_paths,
                day="", title="", kind="case", engine=None, source="wechat",
                preserve=False, read_text=True):
    """Store one question/answer pair and return its ext_questions row.

    `preserve=True` is for pre-cropped exam slides: store the supplied image
    as-is instead of running the page-content crop again.
    """
    question_paths = [Path(path) for path in question_paths or []]
    answer_paths = [Path(path) for path in answer_paths or []]
    if not answer_paths:
        raise ExtError("至少要有一张答案图；只存答案页也可以")
    if len(question_paths) > MAX_IMAGES or len(answer_paths) > MAX_IMAGES:
        raise ExtError("一次最多 %d 张图" % MAX_IMAGES)
    for path in question_paths + answer_paths:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ExtError("单张图片超过 %d MB" % (MAX_IMAGE_BYTES // 1024 // 1024))

    engine = _engine(engine)
    today = datetime.now().astimezone().date().isoformat()
    meta_rows = []
    if read_text:
        meta_source = question_paths[0] if question_paths else answer_paths[0]
        meta_image = Image.open(meta_source).convert("RGB")
        meta_rows = _rows(meta_image, engine)
    day = str(day or "").strip() or today
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise ExtError("日期格式应为 YYYY-MM-DD")
    if read_text:
        day, title = _meta(
            meta_rows, day, str(title or "").strip()
        )
    else:
        title = str(title or "").strip() or ("公众号题 · %s" % day)

    qid = store.next_ext_qid(day)
    folder = Path(data_dir) / "ext" / qid
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    question_images, answer_images = [], []
    question_text, answer_text = [], []
    try:
        if question_paths:
            for index, path in enumerate(question_paths, 1):
                name = "q%d" % index
                if preserve:
                    texts = _save_preserved(
                        path, folder / ("src_%s.png" % name),
                        folder / ("%s.png" % name), folder / ("%st.png" % name),
                        engine, read_text=read_text,
                    )
                else:
                    texts = _save_image(
                        path, folder / ("src_%s.png" % name),
                        folder / ("%s.png" % name), folder / ("%st.png" % name),
                        "question", engine,
                        rows=meta_rows if index == 1 else None,
                    )
                question_text.extend(texts)
                question_images.append("ext/%s/%s.png" % (qid, name))
            for index, path in enumerate(answer_paths, 1):
                name = "a%d" % index
                if preserve:
                    texts = _save_preserved(
                        path, folder / ("src_%s.png" % name),
                        folder / ("%s.png" % name), folder / ("%st.png" % name),
                        engine, read_text=read_text,
                    )
                else:
                    texts = _save_image(
                        path, folder / ("src_%s.png" % name),
                        folder / ("%s.png" % name), folder / ("%st.png" % name),
                        "answer", engine,
                    )
                answer_text.extend(texts)
                answer_images.append("ext/%s/%s.png" % (qid, name))
        else:
            # Only the answer page was supplied: it contains the question, the
            # solution, or both.  Split it at 「解/答」; extra answer pages are
            # kept as answer continuations.
            q_text, a_text, _ = _save_answer_page(
                answer_paths[0],
                folder / "src_a1.png", folder / "q1.png", folder / "q1t.png",
                folder / "a1.png", folder / "a1t.png", engine, rows=meta_rows,
            )
            question_text.extend(q_text)
            answer_text.extend(a_text)
            question_images.append("ext/%s/q1.png" % qid)
            answer_images.append("ext/%s/a1.png" % qid)
            for index, path in enumerate(answer_paths[1:], 2):
                name = "a%d" % index
                answer_text.extend(_save_image(
                    path, folder / ("src_%s.png" % name), folder / ("%s.png" % name),
                    folder / ("%st.png" % name), "answer", engine,
                ))
                answer_images.append("ext/%s/%s.png" % (qid, name))
        row = store.add_ext_question({
            "qid": qid,
            "day": day,
            "title": title,
            "kind": kind or "case",
            "source": source or "wechat",
            "question_images": question_images,
            "answer_images": answer_images,
            "question_text": "\n".join(question_text),
            "answer_text": "\n".join(answer_text),
        })
        return row
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _text(page, text, y, size=10, right=False, color=(0.25, 0.25, 0.25)):
    width = page.rect.width - 2 * MARGIN
    box = pymupdf.Rect(MARGIN, y - size - 2, MARGIN + width, y + size + 4)
    kwargs = (
        {"fontname": "msyh", "fontfile": str(FONT)}
        if FONT.exists() else {"fontname": "china-s"}
    )
    page.insert_textbox(
        box, text, fontsize=size, color=color,
        align=1 if right else 0, **kwargs,
    )


def _image_page(doc, path, label, number, total, page_size=A4,
                compress=False):
    page = doc.new_page(width=page_size[0], height=page_size[1])
    _text(page, label, 40, 9)
    _text(page, "%s 第 %d/%d 页" % (
        "答案" if "答案" in label else "题面", number, total), 40, 9, right=True)
    page.draw_line(
        pymupdf.Point(MARGIN, 48),
        pymupdf.Point(page.rect.width - MARGIN, 48),
        color=(0.6, 0.6, 0.6), width=0.5,
    )
    try:
        with Image.open(path) as image:
            width, height = image.size
            if compress:
                image = image.convert("RGB")
                if image.width > 1800:
                    width = 1800
                    height = int(round(image.height * 1800 / image.width))
                    image = image.resize((width, height), Image.LANCZOS)
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=85, optimize=True)
                payload = buffer.getvalue()
            else:
                payload = None
    except Exception as exc:  # noqa: BLE001 - a missing crop must not kill all
        _text(page, "图片读不了：%s" % exc, 80, 10, color=(0.7, 0.2, 0.2))
        return
    area = pymupdf.Rect(
        MARGIN, 64, page.rect.width - MARGIN, page.rect.height - MARGIN
    )
    scale = min(area.width / width, area.height / height)
    draw_w, draw_h = width * scale, height * scale
    x = area.x0 + (area.width - draw_w) / 2
    y = area.y0
    rect = pymupdf.Rect(x, y, x + draw_w, y + draw_h)
    if payload is not None:
        page.insert_image(rect, stream=payload)
    else:
        page.insert_image(rect, filename=str(path))


def build_pdf(store, data_dir, scope="unprinted", source=None):
    """Return (pdf bytes, filename, qids) for the selected ext questions."""
    items = store.ext_questions(
        unprinted=(scope == "unprinted"), limit=500, source=source or None
    )
    if scope == "all":
        items = store.ext_questions(limit=500, source=source or None)
    if not items:
        raise ExtError("没有可打印的公众号题")
    doc = pymupdf.open()
    cover = doc.new_page(width=A4[0], height=A4[1])
    cover_title = "公众号题 · 每周卷"
    if source and source.startswith("wechat-exam"):
        cover_title = items[0]["title"].rsplit(" ", 1)[0] + " 真题"
    _text(cover, cover_title, 80, 20, color=(0.1, 0.16, 0.2))
    _text(cover, "%d 组 · 题面在前，答案封在最后" % len(items), 108, 10,
          color=(0.35, 0.35, 0.35))
    y = 150
    for item in items[:16]:
        _text(cover, "%s · %s" % (item["day"], item["title"]), y, 9)
        y += 18
    if len(items) > 16:
        _text(cover, "……还有 %d 组" % (len(items) - 16), y, 9)
    _text(cover, "建议：题面部分做完再翻最后的答案；错题回 App 标记。", 780, 9,
          color=(0.4, 0.4, 0.4))

    questions = []
    answers = []
    for item in items:
        base = "ext/%s/" % item["qid"]
        for index, rel in enumerate(item["question_images"]):
            name = Path(rel).name
            questions.append((item, name, Path(data_dir) / rel))
        for index, rel in enumerate(item["answer_images"]):
            name = Path(rel).name
            answers.append((item, name, Path(data_dir) / rel))

    exam = bool(source and source.startswith("wechat-exam"))
    page_size = (A4[1], A4[0]) if exam else A4
    for number, (item, name, path) in enumerate(questions, 1):
        _image_page(doc, path, "%s · %s" % (item["day"], item["title"]),
                    number, len(questions), page_size=page_size, compress=exam)
    for number, (item, name, path) in enumerate(answers, 1):
        _image_page(doc, path, "%s · %s · 答案" % (item["day"], item["title"]),
                    number, len(answers), page_size=page_size, compress=exam)
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001 - subsetting must not kill the weekly paper
        pass
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return data, "岩土-公众号题-%s.pdf" % stamp, [item["qid"] for item in items]
