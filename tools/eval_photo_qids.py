"""Score the photo recogniser against 拍照错题/ (filenames are ground truth only).

M1 acceptance: correct qids appear in the candidate list for >= 95% of photos,
and the single high-confidence auto-accept path is >= 98% accurate.  The
auto-accept rule lives in recognize_qid.auto_qid so the server, the page and
this benchmark can never drift apart.

Usage:
    py -3 tools/eval_photo_qids.py
    py -3 tools/eval_photo_qids.py --only 4-
"""

import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTO_DIR = os.path.join(ROOT, "拍照错题")
sys.path.insert(0, os.path.join(ROOT, "tools"))

from recognize_qid import load_valid, pack_candidates, recognize  # noqa: E402

NAME_RE = re.compile(r"(\d{1,2})-(\d{1,3})")


def expected_qids(filename):
    stem = os.path.splitext(filename)[0]
    return ["%s-%s" % m for m in NAME_RE.findall(stem)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="substring filter on filenames")
    parser.add_argument("--family", default="C", help="book family the photos came from")
    args = parser.parse_args()

    valid = load_valid(args.family)

    photos = sorted(
        p for p in os.listdir(PHOTO_DIR) if p.lower().endswith((".jpg", ".jpeg", ".png"))
    )
    if args.only:
        photos = [p for p in photos if args.only in p]

    recall_hits = packed_hits = 0
    auto_total = auto_ok = 0
    times = []
    print("%-22s %-10s %-28s %s" % ("photo", "expected", "candidates", "verdict"))
    for name in photos:
        want = expected_qids(name)
        result = recognize(os.path.join(PHOTO_DIR, name), valid=valid)
        got = [c["qid"] for c in result["candidates"]]
        # The page only ever sees the packed list, so score that list too: a
        # question dropped by packing is lost to the user just the same.
        packed = [c["qid"] for c in pack_candidates(result["candidates"])]
        times.append(result["seconds"])
        hit = all(q in got for q in want)
        recall_hits += 1 if hit else 0
        packed_hits += 1 if all(q in packed for q in want) else 0
        # Auto-accept is the recogniser's own rule (recognize_qid.auto_qid),
        # the same one the server reports and the page opens.
        if result.get("auto"):
            auto_total += 1
            auto_ok += 1 if result["auto"] in want else 0
        shown = ",".join("%s(%.2f)" % (c["qid"], c["score"]) for c in result["candidates"][:4])
        print(
            "%-22s %-10s %-28s %s"
            % (name, ",".join(want), shown or "-", "OK" if hit else "MISS")
        )

    n = len(photos)
    print()
    print("candidate recall : %d/%d = %.0f%%" % (recall_hits, n, 100.0 * recall_hits / n))
    print("packed recall    : %d/%d = %.0f%%" % (packed_hits, n, 100.0 * packed_hits / n))
    if auto_total:
        print(
            "auto-accept      : %d/%d correct (%.0f%%)"
            % (auto_ok, auto_total, 100.0 * auto_ok / auto_total)
        )
    else:
        print("auto-accept      : no photo met the rule")
    print(
        "time             : mean %.2fs  max %.2fs"
        % (sum(times) / len(times), max(times))
    )


if __name__ == "__main__":
    main()
