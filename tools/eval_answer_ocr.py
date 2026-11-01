"""M4 benchmark: read answer crops, show citations and error-cause candidates.

The acceptance for M4 is subjective ("候选可用"), so this tool prints what the
user would see in the detail page.  It also reports timing, because the first
read must stay tolerable (cached reads are instant).

Usage:
    py -3 tools/eval_answer_ocr.py
    py -3 tools/eval_answer_ocr.py --qids C3-12 Z1-10 M2-13
    py -3 tools/eval_answer_ocr.py --json .probe/m4_eval.json
"""

import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crop_question as CQ  # noqa: E402

OUT_DIR = os.path.join(ROOT, ".cache", "answer_ocr", "bench")

DEFAULT_QIDS = [
    # C: worked examples (question + solution in one crop)
    "C1-2", "C3-2", "C3-3", "C3-7", "C3-8", "C3-11", "C3-12",
    "C4-1", "C4-12", "C4-13", "C6-2", "C8-16", "C10-2",
    # Z: question, answer and solution on one page
    "Z1-10", "Z2-4", "Z3-5", "Z5-54", "Z9-20", "Z10-3",
    # A/K: real exams
    "A2025-P-1", "A2025-A-1", "K2014-A-1", "K2014-A-47",
    "K2020-P-9", "K2022R-A-10",
    # M: mock papers
    "M2-13", "M8-16", "M19-15", "M33-2",
]


def build_crop(qid, path):
    """Same crop the server serves for `answer=1` (masked half for Z)."""
    if qid[0] in "AKM":
        pair = CQ.answer_pages(qid)
        if not pair:
            raise SystemExit("no answer pages for %s" % qid)
        book, pages = pair
    else:
        book, pages, _, _ = CQ.resolve_qid(qid, prefer="solutions")
    number = int(qid.split("-")[-1])
    segments = CQ.plan_segments(book, pages, number, qid)
    if not segments:
        raise SystemExit("no label position for %s" % qid)
    CQ.render_segments(book, segments, str(path))


def answer_crop(qid, engine):
    os.makedirs(OUT_DIR, exist_ok=True)
    if qid.startswith("Z"):
        whole = os.path.join(OUT_DIR, qid + "_full.png")
        if not os.path.exists(whole):
            build_crop(qid, whole)
        half = os.path.join(OUT_DIR, qid + "_a.png")
        if not os.path.exists(half):
            split = CQ.split_z_answer(
                whole,
                os.path.join(OUT_DIR, qid + "_q.png"),
                half,
                engine=engine,
            )
            if not split:
                shutil.copyfile(whole, half)
        return half
    path = os.path.join(OUT_DIR, qid + "_a.png")
    if not os.path.exists(path):
        build_crop(qid, path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qids", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    from app.answer_ocr import analyze_crop, citation_label
    import recognize_qid as RQ

    qids = args.qids or DEFAULT_QIDS
    if args.limit:
        qids = qids[:args.limit]
    engine = RQ.engine()
    rows = []
    for qid in qids:
        crop_started = time.time()
        try:
            path = answer_crop(qid, engine)
        except SystemExit as exc:
            print("[%s] CROP FAILED: %s" % (qid, exc))
            continue
        crop_seconds = time.time() - crop_started
        result = analyze_crop(
            qid, path, os.path.join(ROOT, ".cache", "answer_ocr"), engine=engine
        )
        row = {
            "qid": qid,
            "crop": path,
            "crop_seconds": round(crop_seconds, 2),
            "cached": result["cached"],
            "ocr_seconds": result["seconds"],
            "chars": result["chars"],
            "citations": [
                {"label": citation_label(c), **c} for c in result["citations"]
            ],
            "candidates": result["candidates"],
        }
        rows.append(row)

        print("=" * 12, qid, "  %.1fs%s" % (
            result["seconds"],
            " (cached)" if result["cached"] else "",
        ))
        if not result["citations"]:
            print("  citations: -")
        for citation in row["citations"]:
            print("  cite:", citation["label"])
        if not result["candidates"]:
            print("  candidates: -")
        for candidate in result["candidates"]:
            print("  tag: [%s] %s" % (candidate["label"], candidate["reason"]))

    print("\n--- summary ---")
    print("questions      :", len(rows))
    with_cite = sum(1 for row in rows if row["citations"])
    with_tag = sum(1 for row in rows if row["candidates"])
    print("with citations : %d/%d" % (with_cite, len(rows)))
    print("with candidates: %d/%d" % (with_tag, len(rows)))
    counts = {}
    for row in rows:
        for candidate in row["candidates"]:
            counts[candidate["tag"]] = counts.get(candidate["tag"], 0) + 1
    print("by tag         :", ", ".join(
        "%s=%d" % (key, counts[key]) for key in sorted(counts)
    ) or "-")
    fresh = [row["ocr_seconds"] for row in rows if not row["cached"]]
    if fresh:
        print("ocr seconds    : avg %.2f, max %.2f" % (
            sum(fresh) / len(fresh), max(fresh)
        ))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print("json           :", args.json)


if __name__ == "__main__":
    main()
