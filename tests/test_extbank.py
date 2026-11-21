"""WeChat daily-homework pairs: metadata, crop, storage, weekly PDF."""

from pathlib import Path

import pytest
from PIL import Image

from app import extbank
from app.store import Store

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_Q = ROOT / "data" / "wechat_samples" / "640.png"
SAMPLE_A = ROOT / "data" / "wechat_samples" / "6400.png"
HAS_SAMPLE = SAMPLE_Q.exists() and SAMPLE_A.exists()


def rows(*values):
    """OCR rows with just enough geometry for the crop tests."""
    out = []
    y = 20
    for text in values:
        out.append({"x0": 60, "x1": 1000, "y0": y, "y1": y + 28, "text": text})
        y += 40
    return out


def test_meta_reads_the_wechat_date_and_title():
    day, title = extbank._meta(
        rows("2027华山论剑注册岩土", "2026年11月19日作业（27年专业培训第一轮土力学）",
             "11-19（华山论剑岩土每日作业）已知某均质黏性土场地"),
        "2026-11-21",
    )
    assert day == "2026-11-19"
    assert title.startswith("2026年11月19日作业")


def test_meta_falls_back_when_the_date_is_missing():
    day, title = extbank._meta(rows("今天做题了吗"), "2026-11-21")
    assert day == "2026-11-21"
    assert title == "公众号题 · 2026-11-21"


def test_answer_crop_starts_at_the_solution():
    image = Image.new("RGB", (1080, 1526), "white")
    fake = [
        {"x0": 60, "x1": 1000, "y0": 30, "y1": 70, "text": "2027华山论剑"},
        {"x0": 60, "x1": 1000, "y0": 120, "y1": 150, "text": "题干"},
        {"x0": 60, "x1": 1000, "y0": 220, "y1": 250, "text": "(A）选项"},
        {"x0": 60, "x1": 1000, "y0": 300, "y1": 330, "text": "解:(1)e=0.826"},
        {"x0": 60, "x1": 1000, "y0": 360, "y1": 390, "text": "综上，可塑"},
        {"x0": 60, "x1": 1000, "y0": 1500, "y1": 1520, "text": "第3页"},
    ]
    cropped, box = extbank._crop(image, fake, "answer")
    assert box[1] >= 290  # header, question and options are above the solution
    assert cropped.width < image.width
    assert cropped.height < image.height

    question, qbox = extbank._crop(image, fake, "question", stop_at_solution=True)
    assert qbox[3] <= 300  # question half ends before 「解」
    assert qbox[1] >= 110  # account header is still dropped
    assert question.height > 0


def test_store_ext_rows_round_trip(tmp_path):
    store = Store(tmp_path)
    qid = store.next_ext_qid("2026-11-19")
    assert qid == "W2026-11-19-1"
    item = store.add_ext_question({
        "qid": qid,
        "day": "2026-11-19",
        "title": "11-19 作业",
        "kind": "case",
        "question_images": ["ext/%s/q1.png" % qid],
        "answer_images": ["ext/%s/a1.png" % qid],
    })
    assert item["question_images"] == ["ext/%s/q1.png" % qid]
    assert store.next_ext_qid("2026-11-19") == "W2026-11-19-2"
    assert store.ext_questions()[0]["qid"] == qid
    assert store.ext_questions(unprinted=True)
    assert store.mark_ext_printed([qid]) == 1
    assert not store.ext_questions(unprinted=True)
    assert store.ext_question(qid)["printed_at"]


@pytest.mark.skipif(not HAS_SAMPLE, reason="local WeChat sample is not present")
def test_import_answer_page_alone_and_build_weekly_pdf(tmp_path):
    """The answer page contains the question; one image is the new default."""
    store = Store(tmp_path)
    item = extbank.import_pair(
        store, tmp_path, [], [SAMPLE_A], kind="case",
    )
    assert item["qid"] == "W2026-11-19-1"
    assert item["day"] == "2026-11-19"
    assert item["kind"] == "case"
    for rel in item["question_images"] + item["answer_images"]:
        assert (tmp_path / rel).is_file()
    assert "可塑" in item["question_text"] or "选项" in item["question_text"]
    assert "解" in item["answer_text"]

    pdf, filename, qids = extbank.build_pdf(store, tmp_path)
    assert qids == [item["qid"]]
    assert filename.startswith("岩土-公众号题-")
    assert len(pdf) > 10_000
    import pymupdf

    doc = pymupdf.open(stream=pdf)
    try:
        assert doc.page_count == 3  # cover + question + answer
        assert "公众号题" in doc[0].get_text()
        assert "题面" in doc[1].get_text()
        assert "答案" in doc[2].get_text()
    finally:
        doc.close()
