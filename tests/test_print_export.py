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
