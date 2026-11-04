"""Print files carry the sheet title and a timestamp, never one shared name."""

import re
from datetime import datetime

from app.print_export import _normalize_sections, print_filename, safe_title


def test_filename_has_title_and_timestamp():
    name = print_filename("今日复习卷", now=datetime(2026, 11, 4, 15, 30, 12))
    assert name == "岩土-今日复习卷-20261104-153012.pdf"


def test_two_prints_of_the_same_scope_differ():
    a = print_filename("本周任务卷", now=datetime(2026, 11, 4, 15, 30, 12))
    b = print_filename("本周任务卷", now=datetime(2026, 11, 4, 15, 30, 13))
    assert a != b


def test_windows_reserved_characters_are_replaced():
    title = '错题卷-案例/知识:近7天*?"<>|'
    name = print_filename(title, now=datetime(2026, 11, 4, 8, 0, 0))
    assert not re.search(r'[\\/:*?"<>|]', name)
    assert name.startswith("岩土-错题卷-案例-知识-近7天")
    assert name.endswith(".pdf")


def test_unsafe_or_empty_titles_fall_back():
    assert safe_title("   ") == "打印卷"
    assert safe_title("...") == "打印卷"
    assert safe_title("x" * 80) == "x" * 40


def test_sections_dedupe_and_respect_the_global_cap():
    sections, kept = _normalize_sections(
        None,
        [
            {"title": "D1", "qids": ["C1-1", "C1-2"]},
            {"title": "D2", "qids": ["C1-2", "C1-3"]},
            {"title": "D3", "qids": []},
        ],
        max_questions=2,
    )
    assert kept == ["C1-1", "C1-2"]
    assert len(sections) == 1
    assert sections[0]["qids"] == ["C1-1", "C1-2"]
