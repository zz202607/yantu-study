"""Question pacing shared by the daily briefing and the printed day sheet.

The app plans in minutes, not questions: a wrong-book review is budgeted at
2 minutes, a fresh case question at 9.  Keeping the arithmetic in one module
means the home path, the briefing count and the printed sheet cannot disagree
about how much work "today" is.
"""

REVIEW_MINUTES = 2
NEW_MINUTES = 9


def minutes_for(review_count, new_count):
    """Planned minutes for a set of review and new questions."""
    return (max(0, int(review_count or 0)) * REVIEW_MINUTES
            + max(0, int(new_count or 0)) * NEW_MINUTES)


def fit_counts(minutes, review_count, new_count, review_cap=20, total_cap=40):
    """How many review/new questions fit a time box, review first.

    A short session clears due reviews before new questions: review dates
    move, so an unfinished review is the part that compounds.  Returns
    `(review, new)` counts already inside the caps.
    """
    minutes = max(0, int(minutes or 0))
    review_all = min(max(0, int(review_count or 0)), review_cap, total_cap)
    new_all = min(max(0, int(new_count or 0)), max(0, total_cap - review_all))
    if minutes <= 0:
        return review_all, new_all
    review = min(review_all, minutes // REVIEW_MINUTES)
    left = minutes - review * REVIEW_MINUTES
    new = min(new_all, left // NEW_MINUTES)
    return review, new
