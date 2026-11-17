"""Past-exam question references for a standard (M15).

`index/exam_refs.json` is built offline by `tools/build_exam_refs.py`: for each
case question (2012-2025) it records the standards its solution or stem cites,
plus any clause numbers OCR could read.  This module only reads that file and
groups the rows for the reading room:

    standard -> refs sorted newest first
    reading stop -> refs whose clause falls inside the stop section

Nothing here guesses.  A question without a resolvable citation stays out of
the index; a ref without a clause stays at standard level instead of being
pinned to a section.
"""

import json
import threading
from pathlib import Path

from app import standards

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "index" / "exam_refs.json"
SESSION_LABEL = {"A": "上午", "P": "下午"}
EVIDENCE_LABEL = {
    "text": "真题原文",
    "answer": "答案解析",
    "question": "题面引用",
}

_CACHE = {"mtime": None, "data": None}
_LOCK = threading.Lock()


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def load(path=None):
    """Load the index.  A missing file is an empty index, not an error."""
    path = Path(path or INDEX_PATH)
    stamp = _mtime(path)
    with _LOCK:
        if _CACHE["data"] is not None and _CACHE["mtime"] == stamp:
            return _CACHE["data"]
        data = {"questions": {}, "coverage": {}, "built_at": None}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    data = payload
            except (OSError, ValueError):
                pass
        data.setdefault("questions", {})
        data.setdefault("coverage", {})
        _CACHE.update(mtime=stamp, data=data)
        return data


def _ref(qid, row, ref):
    year = int(row.get("year") or 0)
    session = str(row.get("session") or "")
    number = int(row.get("num") or 0)
    return {
        "qid": qid,
        "year": year,
        "session": session,
        "session_label": SESSION_LABEL.get(session, session),
        "num": number,
        "label": "%d%s%d" % (year, SESSION_LABEL.get(session, session), number),
        "short_label": "%s%s%d" % (
            str(year)[2:], "上" if session == "A" else "下", number
        ),
        "long_label": "%d 年%s第 %d 题" % (year, SESSION_LABEL.get(session, session), number),
        "clauses": list(ref.get("clauses") or []),
        "evidence": ref.get("evidence") or "",
        "evidence_label": EVIDENCE_LABEL.get(ref.get("evidence") or "", ""),
        "code": ref.get("code") or "",
        "title": ref.get("title") or "",
    }


def _sort_key(ref):
    return (-ref["year"], 0 if ref["session"] == "A" else 1, ref["num"])


def for_standard(code_key, path=None):
    """All indexed questions that cite this standard, newest first."""
    data = load(path)
    found = {}
    for qid, row in (data.get("questions") or {}).items():
        if not row.get("done"):
            continue
        for ref in row.get("refs") or []:
            if ref.get("code_key") != code_key:
                continue
            key = (qid, tuple(ref.get("clauses") or []))
            found[key] = _ref(qid, row, ref)
    refs = list(found.values())
    refs.sort(key=_sort_key)
    return refs


def _normalise(value):
    return standards.normalize_clause(value)


def _stop_matches(stop, clause):
    """True when a clause number belongs to this curated reading stop."""
    target = _normalise(clause)
    if not target:
        return False
    for own in stop.get("clauses") or []:
        if _normalise(own) == target:
            return True
    for part in str(stop.get("section") or "").split("/"):
        part = part.strip()
        if not part:
            continue
        if target == part or target.startswith(part + "."):
            return True
        if part.startswith("附录") and target.startswith(part):
            return True
    return False


def for_stops(stops, code_key, refs=None, path=None):
    """stop_id -> refs whose clause lands in that stop.

    Clause-less refs are deliberately not assigned: the filter here is meant to
    be exact, and the full standard list is still available in the side panel.
    """
    if refs is None:
        refs = for_standard(code_key, path=path)
    by_stop = {}
    for stop in stops or []:
        matched = {
            (ref["qid"], tuple(ref["clauses"])): ref
            for ref in refs
            if any(_stop_matches(stop, clause) for clause in ref["clauses"])
        }
        if matched:
            rows = list(matched.values())
            rows.sort(key=_sort_key)
            by_stop[stop["id"]] = rows
    return by_stop


def summary(path=None):
    data = load(path)
    stats = {"total": 0, "done": 0, "with_refs": 0, "standards": 0}
    stats.update(data.get("coverage") or {})
    stats["built_at"] = data.get("built_at")
    return stats


def counts_by_standard(path=None):
    data = load(path)
    counts = {}
    for row in (data.get("questions") or {}).values():
        if not row.get("done"):
            continue
        for ref in row.get("refs") or []:
            key = ref.get("code_key")
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts
