"""The hand-picked qid list behind the viewer / detail / drill print keys."""

from app.print_export import parse_qid_list


def test_full_keys_keep_first_seen_order():
    text = " C3-8, Z1-10；A2025-A-13\nm2-13，C3-8 "
    assert parse_qid_list(text) == ["C3-8", "Z1-10", "A2025-A-13", "M2-13"]


def test_bare_numbers_and_noise_are_dropped():
    # A bare "3-11" exists in both the case and knowledge books; the caller
    # must resolve the family first, so it never reaches the printer.
    assert parse_qid_list("3-11, 12-3, 2025-A-13, hello") == []
    assert parse_qid_list("") == []
    assert parse_qid_list(None) == []
