"""Check every compressed book against its original before the original goes away.

Three levels, cheapest first:

1. page count and page size (as in compress_pdfs.verify, but over more pages)
2. stored image pixel dimensions (native except the downsampled mock book)
3. rendered pixel diff at 72 dpi -- catches a blank / shifted / truncated page
   that dimension checks alone would wave through

Usage:
    py -3 tools/verify_compressed.py                # all books with a compressed copy
    py -3 tools/verify_compressed.py --diff-limit 12
"""

import argparse
import os

import numpy as np
import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPRESSED = os.path.join(ROOT, "compressed")
SKIP = {"工程地质手册（第五版）.pdf"}
# Long-side pixel cap applied at compression time; these books are expected to
# come back smaller, so the stored-pixel check only confirms the cap was hit.
DOWNSCALE = {"26华S案例模拟题集高清版.pdf": 2200}


def sample_pages(total, count):
    return sorted({0, total // 2, total - 1} | {round(i * (total - 1) / max(1, count - 1)) for i in range(count)})


def image_size(doc, page_no):
    images = doc[page_no].get_images()
    if not images:
        return None
    info = doc.extract_image(images[0][0])
    return info["width"], info["height"]


def render_gray(doc, page_no, dpi):
    pix = doc[page_no].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def diff_score(a_doc, b_doc, page_no, dpi):
    a = render_gray(a_doc, page_no, dpi)
    b = render_gray(b_doc, page_no, dpi)
    if a.shape != b.shape:
        return None, None, (a.shape, b.shape)
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(d.mean()), int(d.max()), None


def verify(name, diff_pages, size_pages):
    src_path = os.path.join(ROOT, name)
    out_path = os.path.join(COMPRESSED, name)
    a, b = pymupdf.open(src_path), pymupdf.open(out_path)
    problems = []

    if a.page_count != b.page_count:
        problems.append("页数 %d -> %d" % (a.page_count, b.page_count))

    for p in sample_pages(min(a.page_count, b.page_count), size_pages):
        ra, rb = a[p].rect, b[p].rect
        if abs(ra.width - rb.width) > 0.6 or abs(ra.height - rb.height) > 0.6:
            problems.append("p%d 尺寸 %s -> %s" % (p + 1, tuple(ra), tuple(rb)))
        sa, sb = image_size(a, p), image_size(b, p)
        if sa and sb:
            cap = DOWNSCALE.get(name)
            if cap and max(sb) != cap:
                problems.append("p%d 降采样长边 %d != %d" % (p + 1, max(sb), cap))
            elif not cap and sa != sb:
                problems.append("p%d 像素 %dx%d -> %dx%d" % (p + 1, *sa, *sb))

    worst = (0.0, 0, None)
    shapes = None
    for p in sample_pages(min(a.page_count, b.page_count), diff_pages):
        mean, peak, shapes = diff_score(a, b, p, 72)
        if mean is None:
            problems.append("p%d 渲染尺寸不一致 %s" % (p + 1, shapes))
            break
        if mean > worst[0]:
            worst = (mean, peak, p + 1)

    a.close()
    b.close()
    return problems, worst


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff-limit", type=int, default=8, help="pages per book for the render diff")
    parser.add_argument("--size-pages", type=int, default=16, help="pages per book for size/pixel checks")
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    names = sorted(n for n in os.listdir(COMPRESSED) if n.lower().endswith(".pdf"))
    if args.only:
        names = [n for n in names if args.only in n]
    if not names:
        print("compressed/ 为空")
        return

    failed = []
    for name in names:
        if name in SKIP:
            continue
        if not os.path.exists(os.path.join(ROOT, name)):
            print("skip (无原件): %s" % name)
            continue
        problems, (mean, peak, worst_page) = verify(name, args.diff_limit, args.size_pages)
        status = "OK" if not problems else "FAIL"
        print("%-4s %-46s 最大平均差 %.2f 灰度级 (p%s, 峰值 %d)"
              % (status, name[:46], mean, worst_page, peak), flush=True)
        for problem in problems[:8]:
            print("      - %s" % problem)
        if problems:
            failed.append(name)

    print("\n%d/%d 通过" % (len(names) - len(failed), len(names)))
    for name in failed:
        print("  FAIL", name)


if __name__ == "__main__":
    main()
