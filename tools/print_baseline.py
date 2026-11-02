"""Print the 2025 case exam as two timed-baseline A4 booklets.

Baseline protocol (docs/coach/playbook.md §7): sit 2025 案例上午 and 下午,
3 hours each, exam rules, then record four numbers.  The source is a scan and
the originals are gone, so this renders question crops at native physical size
with generous writing space, answers on their own pages at the end.

Print only the 题面 pages — keep the 答案 pages sealed until both halves are
done.  Images are embedded as 220 dpi JPEG to keep the file printable.

Usage:
    py -3 tools/print_baseline.py                 # both halves
    py -3 tools/print_baseline.py --half A        # morning only
    py -3 tools/print_baseline.py --jpeg-dpi 260  # sharper, bigger file
"""

import argparse
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.print_export import export_pdf  # noqa: E402

TAGS = os.path.join(ROOT, "index", "tags", "A2025.json")
HALVES = {
    "A": ("2025 案例上午 · 基线卷", "baseline_2025_A.pdf"),
    "P": ("2025 案例下午 · 基线卷", "baseline_2025_P.pdf"),
}


def half_qids(tags, half):
    """The 25 qids of one half, in exam order."""
    prefix = "A2025-%s-" % half
    qids = [qid for qid in tags if qid.startswith(prefix)]
    qids.sort(key=lambda qid: int(qid.rsplit("-", 1)[-1]))
    if len(qids) != 25:
        raise SystemExit("%s 应有 25 题，实际 %d" % (half, len(qids)))
    return qids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--half", choices=["A", "P", "both"], default="both")
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "print_samples"))
    parser.add_argument("--jpeg-dpi", type=int, default=220)
    parser.add_argument("--jpeg-quality", type=int, default=85)
    args = parser.parse_args()

    with open(TAGS, encoding="utf-8") as fh:
        tags = json.load(fh)
    os.makedirs(args.out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="baseline_")
    halves = ["A", "P"] if args.half == "both" else [args.half]

    for half in halves:
        title, filename = HALVES[half]
        qids = half_qids(tags, half)
        out = os.path.join(args.out_dir, filename)
        started = time.time()
        print("[%s] %d 题 → %s" % (half, len(qids), out), flush=True)
        result = export_pdf(
            qids, out, title, tmp_dir,
            jpeg=(args.jpeg_dpi, args.jpeg_quality),
        )
        size_mb = os.path.getsize(out) / 1024.0 / 1024.0
        print(
            "  题面 %d 页 + 答案 %d 页 · %.1f MB · %.1f s"
            % (
                result["question_pages"],
                result["answer_pages"],
                size_mb,
                time.time() - started,
            )
        )
        for error in result.get("errors", []):
            print("  ! %(qid)s: %(detail)s" % error)

    print("\n做题规则：题面页在前、答案页在后。只打题面页，答案页封存到两半都做完。")
    print("上午/下午各限时 3 小时。案例按过程自评四个数，")
    print("做完运行 tools/score_baseline.py --answered ... --correct ... --write。")
    print("不要按资料书选项字母判案例分。")


if __name__ == "__main__":
    main()
