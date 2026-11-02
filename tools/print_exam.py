"""Print one past case exam as two A4 booklets for a timed re-sit.

The 2025 paper is the baseline and has been used once; a December retest needs
a paper with unseen answers.  This tool renders any 2018+ case paper from the
generated index: question pages first, answers sealed at the end, plus a
one-page cover with a result tick table.

Usage:
    py -3 tools/print_exam.py --year 2024                # both halves
    py -3 tools/print_exam.py --year 2024 --half A       # morning only
    py -3 tools/print_exam.py --year 2024 --no-cover     # no result sheet
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

LOOKUP = os.path.join(ROOT, "index", "lookup.json")
HALF_NAMES = {"A": "上午", "P": "下午"}


def half_qids(lookup, year, half):
    """Question ids for one half, in exam order; rejects gaps/phantoms."""
    prefix = "A%d-%s-" % (int(year), half)
    rows = []
    for qid in (lookup or {}).get("exam") or {}:
        if not qid.startswith(prefix):
            continue
        try:
            number = int(qid.rsplit("-", 1)[-1])
        except ValueError:
            continue
        if number > 0:  # 2021-P has a phantom qid 0 in lookup.json
            rows.append((number, qid))
    rows.sort()
    numbers = [number for number, _ in rows]
    if numbers != list(range(1, len(rows) + 1)):
        raise SystemExit("%d %s卷题号不连续：%s"
                         % (year, HALF_NAMES[half], numbers))
    expected = 30 if int(year) <= 2017 else 25
    if len(rows) != expected:
        raise SystemExit("%d %s卷应有 %d 题，实际 %d"
                         % (year, HALF_NAMES[half], expected, len(rows)))
    return [qid for _, qid in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--half", choices=["A", "P", "both"], default="both")
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "print_samples"))
    parser.add_argument("--jpeg-dpi", type=int, default=220)
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--no-cover", action="store_true")
    args = parser.parse_args()

    with open(LOOKUP, encoding="utf-8") as fh:
        lookup = json.load(fh)
    os.makedirs(args.out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="exam_%d_" % args.year)
    halves = ["A", "P"] if args.half == "both" else [args.half]

    for half in halves:
        qids = half_qids(lookup, args.year, half)
        title = "%d 案例%s · 整卷复测" % (args.year, HALF_NAMES[half])
        filename = "exam_%d_%s.pdf" % (args.year, half)
        out = os.path.join(args.out_dir, filename)
        cover = None if args.no_cover else {
            "title": title,
            "subtitle": "限时 180 分钟 · 按考场规则写完整过程",
            "tasks": [
                {"label": "只打题面页；答案页封存到两半都做完"},
                {"label": "做完在 App 计划页 → 整卷复测 记四个数"},
                {"label": "案例按过程自评；不按资料书 A/B/C/D 判分"},
            ],
            "qids": qids,
        }
        started = time.time()
        print("[%s] %d 题 → %s" % (half, len(qids), out), flush=True)
        result = export_pdf(
            qids, out, title, tmp_dir,
            jpeg=(args.jpeg_dpi, args.jpeg_quality),
            cover=cover,
        )
        size_mb = os.path.getsize(out) / 1024.0 / 1024.0
        print("  封面 %d 页 + 题面 %d 页 + 答案 %d 页 · %.1f MB · %.1f s"
              % (1 if cover else 0, result["question_pages"],
                 result["answer_pages"], size_mb, time.time() - started))
        for error in result.get("errors", []):
            print("  ! %(qid)s: %(detail)s" % error)

    print("\n复测规则：两半各限时 180 分钟；两半都做完再对答案。")
    print("只记四个数（作答/做对/放弃/超时），案例按过程自评。")


if __name__ == "__main__":
    main()
