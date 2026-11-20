"""Crop cleaning must keep figure ink: chart frames and dashed axes survive.

C3-40 lost the foundation outline and the dashed centreline on the printed
sheet because the old scan-crease remover treated any long isolated vertical
run as a crease.  These tests pin the contract: nothing darker than the
watermark threshold may be erased.
"""

import os
import sys
import unittest

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crop_question as CQ  # noqa: E402


def chart_page():
    """A white page with a chart frame, a dashed axis and a grey watermark."""
    im = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(im)
    # foundation outline: two long vertical edges, 600 px tall
    draw.rectangle([120, 300, 760, 900], outline="black", width=2)
    # dashed centreline: 30 px dashes with 18 px gaps
    for y in range(320, 890, 48):
        draw.line([(440, y), (440, y + 30)], fill="black", width=3)
    # light grey watermark band that must still be whitened
    draw.rectangle([80, 980, 820, 1020], fill=(225, 225, 225))
    return im


class ChartCleaningTest(unittest.TestCase):
    def setUp(self):
        self.raw = chart_page()
        self.clean = CQ._clean_page(self.raw)
        self.raw_arr = np.array(self.raw.convert("L"))
        self.clean_arr = np.array(self.clean.convert("L"))

    def test_nothing_dark_is_whitened(self):
        # The crease remover used to white out whole columns of a figure.
        lost = (self.raw_arr < 175) & (self.clean_arr >= 250)
        self.assertEqual(int(lost.sum()), 0)

    def test_foundation_vertical_edges_survive(self):
        for x in (120, 121, 759, 760):
            col = self.clean_arr[310:890, x]
            self.assertGreater((col < 120).mean(), 0.9, "edge x=%d was erased" % x)

    def test_dashed_centreline_survives(self):
        col = self.clean_arr[310:890, 440]
        self.assertGreater((col < 120).mean(), 0.5)

    def test_grey_watermark_is_whitened(self):
        band = self.clean_arr[985:1015, 100:800]
        self.assertGreater((band >= 250).mean(), 0.99)


@unittest.skipUnless(
    os.path.isdir(os.path.join(ROOT, "compressed")),
    "compressed scans are local-only",
)
class C340RegressionTest(unittest.TestCase):
    """The reported page: both figures kept every vertical line."""

    def test_no_dark_pixel_is_removed(self):
        import pymupdf
        from bookpath import book_pdf

        book, pages, _, _ = CQ.resolve_qid("C3-40", prefer="blank")
        segments = CQ.plan_segments(book, pages, 40, "C3-40")
        doc = pymupdf.open(book_pdf(book))
        try:
            page = doc[segments[0][0] - 1]
            pix = page.get_pixmap(
                dpi=200,
                clip=pymupdf.Rect(page.rect.x0, segments[0][1],
                                  page.rect.x1, segments[0][2]),
            )
            raw = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
        clean = CQ._clean_page(raw)
        raw_arr = np.array(raw.convert("L"))
        clean_arr = np.array(clean.convert("L"))
        lost = (raw_arr < 175) & (clean_arr >= 250)
        self.assertEqual(int(lost.sum()), 0)


if __name__ == "__main__":
    unittest.main()
