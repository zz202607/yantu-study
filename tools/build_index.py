"""Build a question index from the OCR JSONL files and report coverage.

Reads index/*.jsonl produced by extract_qids.py, groups question labels per book,
and reports duplicates, sequence gaps, and how the blank workbooks and the
solution volumes cover each other.
"""

import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT, "index")
QID_RE = re.compile(r"^(\d{1,2})-(\d{1,3})$")

# Rows written before the regex gained its trailing-digit guard still carry
# regulation codes (`94-2008` was stored as `94-200`). Drop them on load rather
# than re-OCR 4,056 pages for a handful of hits.
CODE_SUFFIX_RE = re.compile(r"^\s*\d{1,2}\s*[-—–]\s*\d{4}")


def load_book(path):
    """Return ({qid: [page, ...]}, error rows, dropped regulation codes)."""
    pages = collections.OrderedDict()
    errors = []
    dropped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "error" in row:
                errors.append(row)
                continue
            for hit in row.get("hits", []):
                if CODE_SUFFIX_RE.match(hit.get("raw", "")):
                    dropped += 1
                    continue
                qid = hit.get("qid")
                if not qid:
                    # Volumes keyed by another scheme (year-sitting-number) live
                    # in the same folder and are indexed separately.
                    continue
                entry = pages.setdefault(qid, {"pages": [], "raw": hit.get("raw", "")})
                if row["page"] not in entry["pages"]:
                    entry["pages"].append(row["page"])
    return pages, errors, dropped


def classify(name):
    if "案例习题集" in name:
        return "solution"
    if "空白习题" in name:
        return "blank"
    if "真题" in name:
        return "exam"
    if "专业知识习题册" in name:
        return "knowledge_questions"
    if "专业知识" in name:
        return "knowledge_solution"
    return "other"


def sequence_gaps(qids):
    """Find missing numbers inside each chapter group."""
    groups = collections.defaultdict(set)
    for qid in qids:
        match = QID_RE.match(qid)
        if not match:
            continue
        chapter, number = int(match.group(1)), int(match.group(2))
        groups[chapter].add(number)
    gaps = {}
    for chapter, numbers in sorted(groups.items()):
        if not numbers:
            continue
        missing = sorted(set(range(1, max(numbers) + 1)) - numbers)
        if missing:
            gaps[chapter] = missing
    return gaps, {k: (min(v), max(v), len(v)) for k, v in groups.items()}


def detect_anomalies(qid_map):
    """Flag labels that break the expected monotonic sequence within a chapter."""
    ordered = sorted(qid_map.items(), key=lambda kv: min(kv[1]["pages"]))
    anomalies = []
    prev = None
    for qid, info in ordered:
        match = QID_RE.match(qid)
        if not match:
            anomalies.append((qid, info["pages"], "unparsable"))
            continue
        chapter, number = int(match.group(1)), int(match.group(2))
        if prev is not None:
            prev_chapter, prev_number = prev
            if chapter == prev_chapter and number != prev_number + 1:
                anomalies.append((qid, info["pages"], "jump from %d-%d" % prev))
            elif chapter != prev_chapter:
                anomalies.append((qid, info["pages"], "chapter change from %d-%d" % prev))
        prev = (chapter, number)
    return anomalies


def main():
    if not os.path.isdir(INDEX_DIR):
        print("no index directory:", INDEX_DIR)
        return 1

    files = sorted(f for f in os.listdir(INDEX_DIR) if f.endswith(".jsonl"))
    if not files:
        print("no jsonl files yet in", INDEX_DIR)
        return 1

    books = {}
    for name in files:
        qid_map, errors, dropped = load_book(os.path.join(INDEX_DIR, name))
        books[name] = qid_map
        gaps, ranges = sequence_gaps(qid_map.keys())
        dupes = {k: v["pages"] for k, v in qid_map.items() if len(v["pages"]) > 1}
        print("=" * 70)
        print(name, "| kind:", classify(name))
        print("  question labels:", len(qid_map), "| pages with labels:",
              len({p for v in qid_map.values() for p in v["pages"]}),
              "| parse errors:", len(errors), "| regulation codes dropped:", dropped)
        if ranges:
            print("  chapters:", ", ".join("%s(%d-%d, %d)" % (c, lo, hi, n)
                                           for c, (lo, hi, n) in sorted(ranges.items())))
        if dupes:
            print("  DUPLICATE labels on multiple pages:", len(dupes))
            for k, v in list(dupes.items())[:8]:
                print("    ", k, "->", v)
        if gaps:
            for chapter, missing in sorted(gaps.items()):
                shown = missing[:15]
                print("  GAP ch%s: missing %d numbers %s%s"
                      % (chapter, len(missing), shown, " ..." if len(missing) > 15 else ""))
        if errors:
            print("  ERROR pages:", len(errors), "e.g.", errors[0])
        anomalies = detect_anomalies(qid_map)
        if anomalies:
            print("  SUSPECT labels (sequence breaks):", len(anomalies))
            for qid, pages, why in anomalies[:10]:
                print("    ", qid, "on page", pages, "->", why)

    # Cross-book coverage between blank workbooks and solution volumes.
    blank = set()
    solution = set()
    for name, qid_map in books.items():
        kind = classify(name)
        if kind == "blank":
            blank |= set(qid_map)
        elif kind == "solution":
            solution |= set(qid_map)

    print("=" * 70)
    print("CROSS-BOOK COVERAGE")
    print("  blank labels:", len(blank))
    print("  solution labels:", len(solution))
    only_blank = sorted(blank - solution)
    only_solution = sorted(solution - blank)
    print("  in both:", len(blank & solution))
    print("  only in blank (%d):" % len(only_blank), only_blank[:30])
    print("  only in solution (%d):" % len(only_solution), only_solution[:30])

    report = {
        "books": {
            name: {
                "kind": classify(name),
                "labels": {qid: v["pages"] for qid, v in qid_map.items()},
            }
            for name, qid_map in books.items()
        },
        "coverage": {
            "blank_only": only_blank,
            "solution_only": only_solution,
            "both": sorted(blank & solution),
        },
    }
    out = os.path.join(INDEX_DIR, "report.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print("report written:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
