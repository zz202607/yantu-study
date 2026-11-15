"""Time budgeting: a day is planned in minutes, reviews first."""

from app.pacing import fit_counts, minutes_for


def test_short_box_clears_reviews_before_new_questions():
    # 30 minutes at 2 min/review: 15 reviews, no room for a 9-minute new one.
    assert fit_counts(30, 16, 6) == (15, 0)
    # 60 minutes: all 16 reviews (32 min) + 3 new (27 min) = 59.
    assert fit_counts(60, 16, 6) == (16, 3)


def test_short_box_with_a_small_review_queue_still_fits_new_questions():
    assert fit_counts(30, 3, 10) == (3, 2)   # 6 + 18 = 24
    assert fit_counts(10, 0, 10) == (0, 1)   # one new question, 9 min


def test_no_time_box_keeps_the_existing_caps():
    assert fit_counts(0, 16, 6) == (16, 6)
    assert fit_counts(0, 30, 50) == (20, 20)


def test_minutes_are_planned_at_the_documented_rates():
    assert minutes_for(16, 6) == 32 + 54
    assert minutes_for(0, 0) == 0
