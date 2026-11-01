"""Rebuild the scanned books as smaller PDFs without touching the originals.

The originals were deleted on 2026-11-01 (see docs/journal/2026-11-01-delete-originals.md),
so this script can no longer rebuild them -- it is kept for the record.

Each page in every question book is exactly one full-page image. We pull that
image out, convert it to grayscale, optionally downsample it, re-encode it as
JPEG q80, and place it back on a page of the same physical size. Page count and
page size are preserved; nothing is deleted.

The mock book is the only one downsampled: its pages are A4 blown up to 277%,
so the stored 4402 px is ~530 dpi on A4. 2200 px keeps ~265 dpi, which is far
more than a laser printer needs.

Usage:
    py -3 tools/compress_pdfs.py --limit 3 --only "2026【正版高清扫】华S案例习题集上_扫描版.pdf"
    py -3 tools/compress_pdfs.py                 # all 12 books, ~30-45 min
"""

import argparse
import io
import os
import time

import pymupdf
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Books that must not be recompressed: the handbook carries a real text layer
# and bookmarks, and re-encoding its pages would throw both away.
SKIP = {"工程地质手册（第五版）.pdf"}

# file -> long-side pixel cap (None = keep native pixels)
DOWNSCALE = {"26华S案例模拟题集高清版.pdf": 2200}


def jpeg_page(doc, index, quality, cap):
    """Return (jpeg_bytes, width, height) for one page, or None if unreadable."""
    page = doc[index]
    images = page.get_images()
    data = None
    size = None
    if len(images) == 1:
        info = doc.extract_image(images[0][0])
        data, size = info["image"], (info["width"], info["height"])
    if data is None:
        # Fallback: render the page. 192 dpi matches the stored 1376 px width
        # of the other books closely enough to keep physical size intact.
        pix = page.get_pixmap(dpi=192)
        data, size = pix.tobytes("png"), (pix.width, pix.height)

    image = Image.open(io.BytesIO(data)).convert("L")
    if cap and max(image.size) > cap:
        ratio = cap / max(image.size)
        image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                             Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), image.width, image.height


def compress(path, out_path, quality, cap, limit=None):
    src = pymupdf.open(path)
    total = src.page_count if not limit else min(limit, src.page_count)
    dst = pymupdf.open()
    started = time.time()
    for i in range(total):
        page = src[i]
        jpeg, w, h = jpeg_page(src, i, quality, cap)
        new = dst.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=jpeg)
        if (i + 1) % 50 == 0:
            rate = (time.time() - started) / (i + 1)
            print("    %4d/%d | %.1f s/页 | 已用 %.0f s" % (i + 1, total, rate, time.time() - started), flush=True)
    dst.save(out_path, garbage=4, deflate=True)
    dst.close()
    src.close()
    return total


def verify(src_path, out_path, limit=None):
    a = pymupdf.open(src_path)
    b = pymupdf.open(out_path)
    ok = True
    if not limit and a.page_count != b.page_count:
        ok = False
    pages = min(a.page_count, b.page_count, limit or a.page_count)
    for i in (0, pages // 2, pages - 1):
        ra, rb = a[i].rect, b[i].rect
        if abs(ra.width - rb.width) > 0.6 or abs(ra.height - rb.height) > 0.6:
            ok = False
    a.close()
    b.close()
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", type=int, default=80)
    parser.add_argument("--limit", type=int, default=0, help="pages per book (testing)")
    parser.add_argument("--only", default="", help="single file name")
    parser.add_argument("--outdir", default="compressed")
    args = parser.parse_args()

    outdir = os.path.join(ROOT, args.outdir)
    os.makedirs(outdir, exist_ok=True)

    names = sorted(n for n in os.listdir(ROOT)
                   if n.lower().endswith(".pdf") and n not in SKIP)
    if args.only:
        names = [n for n in names if n == args.only]
    if not names:
        print("没有可处理的文件")
        return

    for name in names:
        src_path = os.path.join(ROOT, name)
        out_path = os.path.join(outdir, name)
        cap = DOWNSCALE.get(name)
        src_mb = os.path.getsize(src_path) / 1e6
        print("start: %s | %.0f MB | cap=%s" % (name, src_mb, cap or "native"), flush=True)
        started = time.time()
        compress(src_path, out_path, args.quality, cap, args.limit or None)
        out_mb = os.path.getsize(out_path) / 1e6
        good = verify(src_path, out_path, args.limit or None)
        print("done: %s | %.0f MB -> %.0f MB (-%.0f%%) | %.0f s | 校验%s"
              % (name, src_mb, out_mb, 100 * (1 - out_mb / src_mb),
                 time.time() - started, "通过" if good else "失败"), flush=True)


if __name__ == "__main__":
    main()
