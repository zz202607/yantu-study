"""A4 export: question sheets and answer sheets really are separate."""

import sys
import threading
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

pytestmark = pytest.mark.skipif(
    not (ROOT / "compressed").exists(),
    reason="compressed scans are local-only",
)


def test_mixed_c_z_sheet_separates_answers(tmp_path):
    import recognize_qid as RQ

    from app.print_export import export_pdf

    out = tmp_path / "sheet.pdf"
    stats = export_pdf(
        ["C3-8", "Z1-10"],
        out,
        "测试卷 · 2026-11-02",
        tmp_path,
        engine=RQ.engine(),
        ocr_lock=threading.Lock(),
    )
    assert stats["errors"] == []
    assert stats["questions"] == 2
    assert stats["answers"] == 2
    assert stats["question_pages"] >= 1
    assert stats["answer_pages"] >= 1
    assert out.exists() and out.stat().st_size > 10_000

    doc = pymupdf.open(out)
    try:
        assert doc.page_count == stats["pages"]
        first = doc[0].get_text()
        assert "题面" in first
        answer_pages = [
            doc[i].get_text()
            for i in range(stats["question_pages"], doc.page_count)
        ]
        assert answer_pages
        assert all("答案" in text for text in answer_pages)
    finally:
        doc.close()


def test_empty_selection_is_rejected(tmp_path):
    from app.print_export import export_pdf

    with pytest.raises(ValueError):
        export_pdf([], tmp_path / "empty.pdf", "空卷", tmp_path)


def test_week_pack_has_day_covers_and_sealed_answers(tmp_path):
    """A travel pack: one cover per day, questions in day order, answers last."""
    import recognize_qid as RQ

    from app.print_export import export_pdf

    out = tmp_path / "week.pdf"
    stats = export_pdf(
        None, out, "未来 2 天复习包", tmp_path,
        engine=RQ.engine(), ocr_lock=threading.Lock(),
        cover={"title": "未来 2 天复习包", "subtitle": "测试", "qids": []},
        sections=[
            {"title": "第 1 天 · 11-04 周三", "short": "D1", "qids": ["C3-8"]},
            {"title": "第 2 天 · 11-05 周四", "short": "D2", "qids": ["Z1-10"]},
        ],
    )
    assert stats["errors"] == []
    assert stats["questions"] == 2
    assert stats["cover_pages"] == 3  # overall + one per day
    assert [row["count"] for row in stats["sections"]] == [1, 1]
    doc = pymupdf.open(out)
    try:
        assert doc.page_count == stats["pages"]
        texts = [doc[i].get_text().replace("\xa0", " ")
                 for i in range(doc.page_count)]
        assert "未来 2 天复习包" in texts[0]
        assert "第 1 天" in texts[1]
        answer_pages = texts[doc.page_count - stats["answer_pages"]:]
        assert answer_pages and all("答案" in text for text in answer_pages)
        assert any("D1" in text for text in answer_pages)
        assert any("D2" in text for text in answer_pages)
    finally:
        doc.close()


def test_cover_and_result_boxes(tmp_path):
    import recognize_qid as RQ

    from app.print_export import export_pdf

    out = tmp_path / "plan.pdf"
    stats = export_pdf(
        ["C3-8"], out, "本周任务卷", tmp_path,
        engine=RQ.engine(), ocr_lock=threading.Lock(),
        writing_scale=1.25, result_boxes=True,
        cover={"title": "本周任务卷", "subtitle": "基础期 · 第 1 周",
               "tasks": [{"key": "A1", "label": "原型 A1", "qids": ["C3-8"]}]},
    )
    assert stats["cover_pages"] == 1
    assert stats["errors"] == []
    doc = pymupdf.open(out)
    try:
        assert doc.page_count == stats["pages"]
        assert "本周任务卷" in doc[0].get_text()
        assert "结果回填" in doc[0].get_text()
        body = doc[1].get_text()
        assert "题面" in body
        assert "对" in body and "错" in body
    finally:
        doc.close()
