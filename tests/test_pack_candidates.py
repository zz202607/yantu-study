"""The picker's candidate list: questions survive, page order, noise capped.

Run:
    py -3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from recognize_qid import pack_candidates  # noqa: E402


def cand(qid, score, kind="loose", y=0.0, x=100.0):
    return {
        "qid": qid,
        "score": score,
        "kind": kind,
        "text": "text",
        "box": [x, y, x + 200, y + 40],
    }


class PackCandidatesTest(unittest.TestCase):
    def test_page_order_is_top_to_bottom(self):
        rows = pack_candidates(
            [
                cand("2-5", 0.99, "start", y=900),
                cand("2-4", 0.99, "start", y=300),
                cand("2-6", 0.95, "start", y=1500),
            ]
        )
        self.assertEqual([r["qid"] for r in rows], ["2-4", "2-5", "2-6"])

    def test_every_start_survives_even_below_noise_cut(self):
        # A start is an anchored label resolved against the book index; it is
        # a real question and must never be dropped as noise.
        rows = pack_candidates(
            [cand("2-4", 0.56, "start", y=500), cand("2-9", 0.99, "loose", y=100)]
        )
        self.assertEqual({r["qid"] for r in rows}, {"2-4", "2-9"})
        self.assertTrue(next(r for r in rows if r["qid"] == "2-4")["start"])

    def test_loose_below_cut_is_dropped(self):
        self.assertEqual(pack_candidates([cand("2-4", 0.54)]), [])

    def test_loose_list_is_capped(self):
        noise = [
            cand("2-%d" % i, 0.9 - i / 1000.0, "loose", y=i * 10)
            for i in range(1, 13)
        ]
        rows = pack_candidates(noise)
        self.assertEqual(len(rows), 8)

    def test_starts_are_not_crowded_out_by_loud_noise(self):
        starts = [cand("3-%d" % i, 0.6, "start", y=i * 100) for i in range(1, 6)]
        noise = [cand("8-%d" % i, 0.99, "loose", y=i) for i in range(1, 13)]
        rows = pack_candidates(starts + noise)
        self.assertEqual(
            {r["qid"] for r in rows if r["start"]}, {"3-1", "3-2", "3-3", "3-4", "3-5"}
        )
        self.assertEqual(len([r for r in rows if not r["start"]]), 8)

    def test_duplicate_qid_keeps_the_best_read(self):
        rows = pack_candidates(
            [
                cand("2-4", 0.70, "loose", y=100),
                cand("2-4", 0.95, "start", y=100),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 0.95)
        self.assertTrue(rows[0]["start"])

    def test_shape_has_start_flag_and_y(self):
        (row,) = pack_candidates([cand("2-4", 0.99, "start", y=123.456)])
        self.assertEqual(row["qid"], "2-4")
        self.assertTrue(row["start"])
        self.assertEqual(row["y"], 123.5)


if __name__ == "__main__":
    unittest.main()
