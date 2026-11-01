"""M4: solution OCR -> citations and error-cause candidates (text rules only)."""

from app.answer_ocr import (
    analyze_lines,
    citation_label,
    extract_citations,
    join_lines,
    suggest_tags,
)


def test_wrapped_title_and_parenthesized_code_merge_into_one_citation():
    lines = [
        "试按《建筑地基基",
        "础设计规范》(GB50007-2011)计算……",
        "【点评】①根据《建筑地基基础设计规范》第5.2.4条；②……",
    ]
    result = analyze_lines(lines)
    assert len(result["citations"]) == 1
    citation = result["citations"][0]
    assert citation["title"] == "建筑地基基础设计规范"
    assert citation["code"] == "GB50007-2011"
    assert citation["clauses"] == ["5.2.4"]
    assert "第5.2.4条" in citation_label(citation)
    assert citation["code"] in citation_label(citation)


def test_clause_ranges_and_appendices():
    text = join_lines(
        ["根据《岩土工程勘察规范》第3.2.2条，第14.2.2~14.2.4条及附录J；"]
    )
    [citation] = extract_citations(text)
    assert citation["clauses"] == ["3.2.2", "14.2.2~14.2.4", "附录J"]
    label = citation_label(citation)
    assert "3.2.2" in label
    assert "14.2.2~14.2.4" in label
    assert "附录J" in label


def test_page_reference_before_clause():
    text = join_lines([
        "【点评】①根据《水运工程岩土工程勘察规范》p81页第14.7.4.5条，静止土压力系数采用有效应力；",
        "②根据《工程地质手册》第五版p294页，采用有效应力计算。",
    ])
    citations = {c["title"]: c for c in extract_citations(text)}
    assert citations["水运工程岩土工程勘察规范"]["clauses"] == ["14.7.4.5"]
    assert citations["工程地质手册"]["clauses"] == []


def test_title_only_mention_is_not_a_basis():
    text = join_lines(
        ["【点评】未指定《土工试验方法标准》计算标准值时，不需要验算3倍标准差。"]
    )
    citations = extract_citations(text)
    assert citations and citations[0]["title"] == "土工试验方法标准"
    assert all(c["tag"] != "standard" for c in suggest_tags(text, citations))


def test_caution_tags_carry_their_sentence():
    text = join_lines(
        ["【点评】①注意题目折减系数取小值；②细心计算即可得分；③概念要清楚。"]
    )
    candidates = suggest_tags(text, [])
    tags = {c["tag"]: c for c in candidates}
    assert "折减系数取小值" in tags["misread"]["reason"]
    assert "细心计算" in tags["calc"]["reason"]
    assert "concept" in tags
    assert all(c["evidence"] for c in candidates)


def test_caution_beats_a_lone_citation_and_caps_at_three():
    text = join_lines(["【点评】①注意取小值；②细心计算；③概念清楚。"])
    citations = [{
        "title": "建筑地基基础设计规范",
        "code": "GB50007-2011",
        "clauses": ["5.2.4"],
        "raw": "",
    }]
    candidates = suggest_tags(text, citations)
    assert len(candidates) == 3
    assert all(c["tag"] in ("misread", "calc", "concept") for c in candidates)


def test_lone_citation_yields_no_standard_when_a_caution_exists():
    text = join_lines(["【点评】注意题目问的是夹层土。"])
    citations = extract_citations(join_lines([
        "【解析】根据《水运工程岩土勘察规范》第4.2.7条计算。",
        "【点评】注意题目问的是夹层土。",
    ]))
    tags = [c["tag"] for c in suggest_tags(text, citations)]
    assert tags == ["misread"]


def test_two_standards_keep_the_standard_candidate():
    text = join_lines([
        "【解析】根据《建筑地基基础设计规范》第5.2.4条及"
        "《建筑桩基技术规范》第5.3.2条计算。",
        "【点评】注意取小值。",
    ])
    candidates = suggest_tags(text, extract_citations(text))
    tags = [c["tag"] for c in candidates]
    assert "standard" in tags
    assert tags.count("misread") == 1


def test_one_citation_still_suggests_standard():
    text = join_lines(["【点评】①根据《建筑桩基技术规范》5.2.2、5.2.5；②……"])
    citations = extract_citations(text)
    candidates = suggest_tags(text, citations)
    assert [c["tag"] for c in candidates] == ["standard"]
    assert "5.2.2" in candidates[0]["reason"]


def test_plain_solution_suggests_nothing():
    text = join_lines([
        "13.答案：（D）",
        "主要答题过程：",
        "(1)Ra1=3.14×0.6×(1×10+6×8+3×13)+0.4×3.14×0.32×150=199.7kN",
        "取小值R=113.04kN",
    ])
    assert suggest_tags(text, []) == []


def test_commentary_prefers_the_last_review_note():
    text = join_lines([
        "【解析】根据《水运工程岩土勘察规范》第4.2.7条计算。",
        "【点评】注意题目问的是夹层土。",
    ])
    candidates = suggest_tags(text, extract_citations(text))
    tags = {c["tag"]: c for c in candidates}
    assert "夹层土" in tags["misread"]["reason"]
