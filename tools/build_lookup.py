"""Merge every extraction file into one question lookup table.

Three numbering schemes live side by side:

```
1-58          chapter-question   exercise workbooks and their solution volumes
2015-P-19     year-sitting-item  past exam papers (R = 2022 make-up sitting)
M2-13         paper-question     the 32 mock papers
```

The output is index/lookup.json: one entry per question, holding the page(s)
where it is printed in each volume, plus the page of its worked answer when the
volume prints one separately.

Chapter-question entries also carry `confirmed`: true when at least two books
independently produced the same label, which is the strongest signal we have
that the OCR did not invent it.
"""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT, "index")
OUT = os.path.join(INDEX_DIR, "lookup.json")


def load(name):
    path = os.path.join(INDEX_DIR, name)
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def scheme(rows):
    """Decide which extraction scheme wrote a file, from its row shape."""
    for row in rows:
        if "paper" in row and "saw_answers" in row:
            return "mock"
        if "year" in row or "side" in row:
            return "exam"
        if row.get("hits") and "qid" in row["hits"][0]:
            return "chapter"
    return None


def add_page(entry, field, page):
    pages = entry.setdefault(field, [])
    if page not in pages:
        pages.append(page)


def ownership():
    """Volume stem -> the chapter numbers that volume actually prints.

    Chapter books are split by 篇, so a `8-16` label found in the 上册 is never
    that question: it is a regulation code the OCR mistook for a label (JGJ
    94-2008 -> `94-200`) or a figure caption shaped like one. books.json already
    records which 篇 each volume holds, so the page can be rejected on exactly
    that evidence.
    """
    path = os.path.join(INDEX_DIR, "books.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        books = json.load(handle)["books"]
    return {os.path.splitext(b["file"])[0]: set(b.get("chapters") or []) for b in books}


def fill_exam_headers(rows):
    """Carry the paper header forward over pages whose sidebar OCR failed.

    A page between two pages of the same paper is almost certainly that paper;
    the last seen header is the best evidence available.
    """
    last = None
    for row in rows:
        if row.get("year") is not None:
            last = {k: row.get(k) for k in ("year", "session", "section", "retake")}
            continue
        if last:
            # A failed sidebar leaves the row claiming section "q"; overwrite it
            # rather than keeping a guess that would file answers as questions.
            row.update(last)
    return rows


def build():
    chapter = {}
    exams = {}
    mocks = {}
    stats = []
    owners = ownership()
    rejected = 0

    for path in sorted(glob.glob(os.path.join(INDEX_DIR, "*.jsonl"))):
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        rows = load(name)
        kind = scheme(rows)
        if kind is None:
            stats.append((stem, "skipped (unknown scheme)", 0))
            continue

        if kind == "chapter":
            # The case volumes and the knowledge volumes both number chapters
            # 1..13, so a bare `1-58` means two different questions. Prefix the
            # family: C for 案例, Z for 知识.
            prefix = "Z" if "专业知识" in stem else "C"
            labels = 0
            for row in rows:
                for hit in row.get("hits", []):
                    qid = hit.get("qid")
                    if not qid:
                        continue
                    chapters = owners.get(stem)
                    if chapters and int(qid.split("-")[0]) not in chapters:
                        rejected += 1
                        continue
                    qid = prefix + qid
                    entry = chapter.setdefault(qid, {"books": {}})
                    pages = entry["books"].setdefault(stem, [])
                    if row["page"] not in pages:
                        pages.append(row["page"])
                    labels += 1
            stats.append((stem, "chapter", labels))

        elif kind == "exam":
            # The two past-exam volumes share the year-sitting-number layout,
            # so the key needs a family letter: A for 案例, K for 知识.
            prefix = "K" if "专业知识" in stem else "A"
            labels = 0
            for row in fill_exam_headers(rows):
                if row.get("year") is None or row.get("section") is None:
                    continue
                key_body = "%s%d%s-%s" % (
                    prefix,
                    row["year"],
                    "R" if row.get("retake") else "",
                    row["session"],
                )
                for hit in row.get("hits", []):
                    qid = "%s-%d" % (key_body, hit["n"])
                    entry = exams.setdefault(qid, {"book": stem})
                    add_page(entry, row["section"], row["page"])
                    labels += 1
            stats.append((stem, "exam", labels))

        elif kind == "mock":
            labels = 0
            for row in rows:
                if row.get("paper") is None:
                    continue
                # Trust the page-level section (inferred from the numbering
                # restart), not the per-line `ans` flag: later papers print
                # answers as `5.【B】` with no 答案 marker, and the flag misfiles
                # them as questions.
                section = "a" if row.get("section") == "a" else "q"
                for hit in row.get("hits", []):
                    qid = "M%d-%d" % (row["paper"], hit["n"])
                    entry = mocks.setdefault(qid, {"book": stem})
                    add_page(entry, section, row["page"])
                    labels += 1
            stats.append((stem, "mock", labels))

    multi = {}
    for qid, entry in chapter.items():
        books = entry["books"]
        multi[qid] = {"books": {stem: pages for stem, pages in books.items() if pages},
                      "confirmed": len(books) >= 2}

    return {"chapter": multi, "exam": exams, "mock": mocks, "stats": stats}, rejected


def main():
    data, rejected = build()
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)

    for stem, kind, labels in data["stats"]:
        print("%-52s %-10s %6d labels" % (stem[:52], kind, labels))
    chapter = data["chapter"]
    confirmed = sum(1 for e in chapter.values() if e["confirmed"])
    print("-" * 70)
    print("chapter qids: %d | confirmed by 2+ books: %d | rejected off-volume labels: %d"
          % (len(chapter), confirmed, rejected))
    print("exam qids: %d | mock qids: %d" % (len(data["exam"]), len(data["mock"])))
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
