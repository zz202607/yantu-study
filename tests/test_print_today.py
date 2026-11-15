"""The one-tap day sheet: review first, today's new questions fill the rest."""

from app.print_export import TODAY_MAX_QUESTIONS, TODAY_REVIEW_CAP, day_pack


def shorts(sections):
    return [section["short"] for section in sections]


def test_review_first_then_new_questions_without_duplicates():
    sections, cover = day_pack(
        ["C3-11", "C3-12"],
        ["A2025-A-7", "C3-11", "Z1-2"],
        day_label="11-15",
    )
    assert shorts(sections) == ["复", "新"]
    assert sections[0]["qids"] == ["C3-11", "C3-12"]
    assert sections[1]["qids"] == ["A2025-A-7", "Z1-2"]
    assert cover["qids"] == ["C3-11", "C3-12", "A2025-A-7", "Z1-2"]
    assert cover["title"] == "今日卷"
    assert cover["subtitle"].startswith("11-15")
    assert "复习 2 题 + 新题 2 题" in cover["subtitle"]


def test_review_keeps_its_20_item_cap():
    review = ["C3-%d" % i for i in range(1, 31)]
    sections, _ = day_pack(review, [])
    assert len(sections[0]["qids"]) == TODAY_REVIEW_CAP


def test_new_questions_take_only_the_remaining_room():
    review = ["C3-%d" % i for i in range(1, 6)]
    new = ["Z1-%d" % i for i in range(1, 50)]
    sections, cover = day_pack(review, new, max_total=12)
    assert len(sections[0]["qids"]) == 5
    assert len(sections[1]["qids"]) == 12 - 5
    assert len(cover["qids"]) == 12


def test_default_cap_is_one_printable_sitting():
    sections, cover = day_pack([], ["A2025-A-%d" % i for i in range(1, 60)])
    assert len(cover["qids"]) == TODAY_MAX_QUESTIONS


def test_empty_review_still_makes_a_new_question_sheet():
    sections, cover = day_pack([], ["A2025-A-7"])
    assert shorts(sections) == ["新"]
    assert "复习 0 题 + 新题 1 题" in cover["subtitle"]


def test_lowercase_keys_are_normalised():
    sections, _ = day_pack(["c3-11", "C3-11"], ["a2025-a-7"])
    assert sections[0]["qids"] == ["C3-11"]
    assert sections[1]["qids"] == ["A2025-A-7"]


def test_no_questions_means_no_sections():
    sections, _ = day_pack([], [])
    assert sections == []


def test_minutes_box_keeps_reviews_first():
    review = ["C3-%d" % i for i in range(1, 17)]
    new = ["Z1-%d" % i for i in range(1, 7)]
    sections, cover = day_pack(review, new, minutes=30)
    assert len(sections[0]["qids"]) == 15
    assert len(sections) == 1          # no room for a 9-minute new question
    assert "约 30 分钟" in cover["subtitle"]

    sections, cover = day_pack(review, new, minutes=60)
    assert len(sections[0]["qids"]) == 16
    assert len(sections[1]["qids"]) == 3
    assert "约 59 分钟" in cover["subtitle"]


def test_minutes_never_expand_the_point_set():
    sections, _ = day_pack(["C3-11"], ["A2025-A-7"], minutes=120)
    assert [len(section["qids"]) for section in sections] == [1, 1]
