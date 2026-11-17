"""Build index/exam_refs.json: which past-exam questions cite which standard.

The exam volumes are scans without a text layer, so the only offline evidence
is OCR.  For each case question (A-family) the builder reads the answer crop
first -- the solution names the standard the question is built on -- and falls
back to the question stem when the answer carries no citation.  The 2025 paper
already has machine text in index/tags/, so it is read without OCR.

Only citations that resolve to one of the 50 indexed standards are kept; an
unreadable or unknown title is dropped, never guessed.  Clause numbers are kept
when OCR sees them, which is what lets the reading room place a question next
to a section.

The run is resumable: every processed qid is written back to the index, so an
interrupted build can be restarted without redoing OCR.  Progress prints every
few questions and the JSON is replaced atomically.

Usage:
    py -3 tools/build_exam_refs.py --limit 6        # smoke test
    py -3 tools/build_exam_refs.py                  # all case questions
    py -3 tools/build_exam_refs.py --year 2024,2025
    py -3 tools/build_exam_refs.py --force --year 2025
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import crop_question as CQ  # noqa: E402
from app import standards  # noqa: E402
from app.answer_ocr import extract_citations, ocr_lines  # noqa: E402

INDEX = ROOT / "index"
OUT_PATH = INDEX / "exam_refs.json"
CACHE_DIR = ROOT / ".cache" / "exam_refs"
TMP_DIR = CACHE_DIR / "tmp"
TAGS = {
    "A2025-A": ROOT / "index" / "tags" / "A2025-A.jsonl",
    "A2025-P": ROOT / "index" / "tags" / "A2025-P.jsonl",
}
VERSION = 1
OCR_DPI = 150
WRITE_EVERY = 15
SESSION_ORDER = {"A": 0, "P": 1}


def _atomic_write(path, payload):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_index():
    if not OUT_PATH.exists():
        return {
            "version": VERSION,
            "built_at": None,
            "source": "OCR of the 2012-2025 case answer/stem crops; 2025 from text",
            "questions": {},
        }
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"version": VERSION, "built_at": None, "questions": {}}
    if data.get("version") != VERSION:
        data = {"version": VERSION, "built_at": None, "questions": {}}
    data.setdefault("questions", {})
    return data


def case_qids(years=None):
    """Every A-family qid that has both a question and an answer page."""
    with open(INDEX / "lookup.json", encoding="utf-8") as fh:
        exam = json.load(fh).get("exam") or {}
    wanted = set(years or ())
    rows = []
    for qid, entry in exam.items():
        if not qid.startswith("A") or not entry.get("q") or not entry.get("a"):
            continue
        match = re.match(r"^A(\d{4})-([A-Z])-(\d{1,3})$", qid)
        if not match:
            continue
        year = match.group(1)
        if wanted and year not in wanted:
            continue
        rows.append((int(year), match.group(2), int(match.group(3)), qid))
    rows.sort(key=lambda row: (-row[0], SESSION_ORDER.get(row[1], 9), row[2]))
    return [row[3] for row in rows]


def _resolve(citation):
    """One citation -> a row of the standards index, or None."""
    found = None
    if citation.get("code"):
        found = standards._find_standard(citation["code"])
    if not found and citation.get("title"):
        found = standards._find_standard(citation["title"])
    if not found or not found.get("code_key"):
        return None
    return {
        "code_key": found["code_key"],
        "code": found.get("code") or "",
        "title": found.get("title") or citation.get("title") or "",
        "clauses": [
            standards.normalize_clause(clause)
            for clause in (citation.get("clauses") or [])
            if standards.normalize_clause(clause)
        ],
    }


def refs_from_text(text, evidence):
    refs = {}
    order = []
    for citation in extract_citations(text or ""):
        # A bare「按照《…规范》」is a real basis in a question stem, so a
        # resolvable title counts even without a code or clause number.
        row = _resolve(citation)
        if not row:
            continue
        key = row["code_key"]
        if key not in refs:
            refs[key] = dict(row, evidence=evidence)
            order.append(key)
            continue
        entry = refs[key]
        for clause in row["clauses"]:
            if clause not in entry["clauses"]:
                entry["clauses"].append(clause)
    return [refs[key] for key in order]


def _ocr_crop(engine, qid, answer):
    """Crop and OCR one question or its answer; returns text or None."""
    if answer:
        pair = CQ.answer_pages(qid)
        if not pair:
            return None
        book, pages = pair
    else:
        try:
            book, pages, _section, _scale = CQ.resolve_qid(qid, prefer="solutions")
        except SystemExit:
            return None
    number = int(qid.rsplit("-", 1)[-1])
    try:
        segments = CQ.plan_segments(book, pages, number, qid)
    except Exception:  # noqa: BLE001 - a broken crop is a data gap, not a crash
        return None
    if not segments:
        return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix=qid + "_", suffix=".png", dir=str(TMP_DIR))
    os.close(handle)
    try:
        CQ.render_segments(book, segments, path, dpi=OCR_DPI)
        lines, _seconds = ocr_lines(path, engine=engine)
        return "".join(lines)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _tag_texts():
    """2025 qid -> full text already extracted from the paper (no OCR)."""
    texts = {}
    for path in TAGS.values():
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                qid = str(row.get("qid") or "").upper()
                if qid:
                    texts[qid] = (row.get("text") or "") + (row.get("answer") or "")
    return texts


def _coverage(data, qids):
    """Counts for the questions this index is supposed to cover."""
    questions = data.get("questions") or {}
    rows = [questions.get(qid) or {} for qid in qids]
    done = sum(1 for row in rows if row.get("done"))
    with_refs = sum(1 for row in rows if row.get("done") and row.get("refs"))
    standards_seen = set()
    for row in rows:
        for ref in row.get("refs") or []:
            standards_seen.add(ref.get("code_key"))
    return {
        "total": len(qids),
        "done": done,
        "with_refs": with_refs,
        "standards": len(standards_seen),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--year", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-question", action="store_true",
                        help="skip the question-stem fallback")
    args = parser.parse_args()

    years = [part.strip() for part in args.year.split(",") if part.strip()]
    data = load_index()
    questions = data["questions"]
    qids = case_qids(years)
    texts = _tag_texts()

    pending = [
        qid for qid in qids
        if args.force or not (questions.get(qid) or {}).get("done")
    ]
    if args.limit:
        pending = pending[:args.limit]
    if not pending:
        print("nothing to do")
        return

    engine = None
    started = time.time()
    for position, qid in enumerate(pending, start=1):
        row = questions.get(qid) or {}
        match = re.match(r"^A(\d{4})-([A-Z])-(\d{1,3})$", qid)
        year, session, number = int(match.group(1)), match.group(2), int(match.group(3))
        refs = []

        text = texts.get(qid)
        if text:
            refs = refs_from_text(text, "text")
            evidence = "text"
        else:
            if engine is None:
                from rapidocr import RapidOCR

                engine = RapidOCR(
                    params={"Global.log_level": "warning", "Rec.rec_batch_num": 32}
                )
            answer_text = _ocr_crop(engine, qid, answer=True)
            refs = refs_from_text(answer_text or "", "answer")
            evidence = "answer" if refs else ""
            if not refs and not args.no_question:
                question_text = _ocr_crop(engine, qid, answer=False)
                refs = refs_from_text(question_text or "", "question")
                if refs:
                    evidence = "question"

        questions[qid] = {
            "done": True,
            "year": year,
            "session": session,
            "num": number,
            "evidence": evidence,
            "refs": refs,
        }
        if position % 10 == 0 or position == len(pending):
            data["built_at"] = datetime.now().isoformat(timespec="seconds")
            data["coverage"] = _coverage(data, qids)
            _atomic_write(OUT_PATH, data)
            elapsed = time.time() - started
            rate = elapsed / position
            left = (len(pending) - position) * rate
            print(
                "%d/%d  %s  refs=%d  %.1fs/q  eta %.0f min"
                % (position, len(pending), qid, len(refs), rate, left / 60),
                flush=True,
            )

    data["built_at"] = datetime.now().isoformat(timespec="seconds")
    data["coverage"] = _coverage(data, qids)
    _atomic_write(OUT_PATH, data)
    print("wrote %s  coverage=%s" % (OUT_PATH, data["coverage"]))


if __name__ == "__main__":
    main()
