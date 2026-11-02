"""M9.2: turn the baseline lists into training, not more reading.

Two modes share one session ledger:

* ``timed``     -- 5-10 questions, 8 minutes each, hard stop.  Every item
                   records a result (correct / wrong / overtime / abandoned)
                   and a first-step card.  Abandoning is allowed only after
                   writing the first step, because that is the process credit
                   the case paper actually pays for.
* ``recognize`` -- 5-10 questions, 30 seconds each, name the prototype and
                   the main standard before revealing the answer.  Questions
                   come from the prototype ranges in ``app.plan``, so the
                   ground truth is the range itself, never guessed.

A finished timed drill writes one aggregate row into ``practice_sessions``
(source ``drill``) so the weekly plan counts it.  Recognition cards train a
different skill and deliberately stay out of the question ledger.
"""

import math
import re
from datetime import datetime

from app.curriculum import CASE_BY_KEY, category_key
from app.plan import PROTOTYPES, PROTOTYPE_RANGES

MODES = {"timed": "限时拆解", "recognize": "认题卡"}
RESULT_LABELS = {
    "correct": "做对",
    "wrong": "做错",
    "overtime": "超时",
    "abandoned": "放弃",
}
TIMED_SOURCES = {
    "mixed": "混合：慢 + 放弃·没时间 + 错",
    "slow": "会但慢",
    "skipped": "放弃·没时间",
    "wrong": "做错",
    "prototype": "按原型",
}
RECOGNIZE_SOURCES = {
    "weak": "弱项原型",
    "core": "核心原型",
    "all": "全部原型",
    "prototype": "单个原型",
}
MIN_QUESTIONS = 5
MAX_QUESTIONS = 10
DEFAULT_TIMED_QUESTIONS = 5
DEFAULT_RECOGNIZE_QUESTIONS = 8
DEFAULT_MINUTES = 8
RECOGNIZE_SECONDS = 30

_PROTOTYPE_BY_KEY = {
    row[0]: {"key": row[0], "label": row[1], "tier": row[2], "core": row[3]}
    for row in PROTOTYPES
}

# Which exam category a prototype belongs to.  The ranges already encode the
# C-family book; this table is the one place that maps them to the nine
# exam categories used by the baseline and the plan.
_PROTOTYPE_CATEGORY = {}
for _key in ("A1", "A2", "A3", "A4"):
    _PROTOTYPE_CATEGORY[_key] = "C3"
for _key in ("A5", "A6", "A7", "A8"):
    _PROTOTYPE_CATEGORY[_key] = "C5"
for _key in ("A9", "A10", "A11", "A12", "A13", "A14"):
    _PROTOTYPE_CATEGORY[_key] = "C8"
for _key in ("A15", "A16", "A17"):
    _PROTOTYPE_CATEGORY[_key] = "C9"
for _key in ("B1", "B2", "B3"):
    _PROTOTYPE_CATEGORY[_key] = "C2"
for _key in ("B4", "B5", "B6"):
    _PROTOTYPE_CATEGORY[_key] = "C4"
for _key in ("B7", "B8"):
    _PROTOTYPE_CATEGORY[_key] = "C10"
for _key in ("C1", "C4", "C5", "C6"):
    _PROTOTYPE_CATEGORY[_key] = "C6"
for _key in ("D1", "D3", "D6"):
    _PROTOTYPE_CATEGORY[_key] = "C7"

# (category, index into that category's `core` list).  None means the main
# standard is not unique at prototype level (冻土/滑坡/泥石流), so the card
# asks only for the prototype instead of inventing a code.
_PROTOTYPE_CORE = {
    "A1": ("C3", 0), "A2": ("C3", 0), "A3": ("C3", 0), "A4": ("C3", 0),
    "A5": ("C5", 0), "A6": ("C5", 0), "A7": ("C5", 0), "A8": ("C5", 0),
    "A9": ("C8", 0), "A10": ("C8", 1), "A11": ("C8", 2),
    "A15": ("C9", 0), "A16": ("C9", 0), "A17": ("C9", 0),
    "B1": ("C2", 0), "B2": ("C2", 0), "B3": ("C2", 0),
    "B4": ("C4", 0), "B5": ("C4", 0), "B6": ("C4", 0),
    "B7": ("C10", 0), "B8": ("C10", 1),
    "C1": ("C6", 0), "C4": ("C6", 0), "C5": ("C6", 0), "C6": ("C6", 0),
    "D1": ("C7", 0), "D3": ("C7", 0), "D6": ("C7", 0),
}

_RANGE_ENTRIES = []
for _key, _label, _tier, _core in PROTOTYPES:
    if _key not in PROTOTYPE_RANGES:
        continue
    _start, _end = PROTOTYPE_RANGES[_key]
    _prefix = _start.split("-")[0]
    _first = int(_start.split("-")[1])
    _last = int(_end.split("-")[1])
    _RANGE_ENTRIES.append({
        "key": _key,
        "label": _label,
        "tier": _tier,
        "core": _core,
        "prefix": _prefix,
        "first": _first,
        "last": _last,
        "n": _last - _first + 1,
        "category": _PROTOTYPE_CATEGORY.get(_key),
    })

_QID_INDEX = None


def _qid_index():
    """qid -> prototype range entries that contain it (most specific first)."""
    global _QID_INDEX
    if _QID_INDEX is None:
        index = {}
        for entry in _RANGE_ENTRIES:
            for number in range(entry["first"], entry["last"] + 1):
                qid = "%s-%d" % (entry["prefix"], number)
                index.setdefault(qid, []).append(entry)
        for entries in index.values():
            entries.sort(key=lambda row: (row["n"], row["key"]))
        _QID_INDEX = index
    return _QID_INDEX


def prototype_options():
    rows = []
    for entry in _RANGE_ENTRIES:
        standard = standard_for(entry["key"])
        rows.append({
            "key": entry["key"],
            "label": entry["label"],
            "tier": entry["tier"],
            "core": entry["core"],
            "category": entry["category"],
            "category_name": (
                CASE_BY_KEY[entry["category"]]["name"]
                if entry["category"] in CASE_BY_KEY else ""
            ),
            "range": [PROTOTYPE_RANGES[entry["key"]][0],
                      PROTOTYPE_RANGES[entry["key"]][1]],
            "n": entry["n"],
            "standard": standard,
        })
    return rows


def standard_for(prototype_key):
    """The prototype's main standard from the curriculum core list."""
    spec = _PROTOTYPE_CORE.get(prototype_key)
    if not spec:
        return None
    category, index = spec
    core = CASE_BY_KEY.get(category, {}).get("core") or []
    if index >= len(core):
        return None
    row = core[index]
    if not row.get("code"):
        return None
    return {"code": row["code"], "title": row["title"]}


def standard_options():
    seen = []
    for key in ("C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10"):
        for row in CASE_BY_KEY.get(key, {}).get("core") or []:
            if row.get("code") and all(item["code"] != row["code"] for item in seen):
                seen.append({"code": row["code"], "title": row["title"]})
    return seen


def prototype_for(qid):
    """Ground truth for one question, or None when the range map has no entry.

    A question inside B5's range is also inside B4's range, so ``keys`` lists
    every accepted prototype while ``key`` is the most specific one shown as
    the answer.
    """
    entries = _qid_index().get((qid or "").upper())
    if not entries:
        return None
    chosen = entries[0]
    return {
        "qid": qid,
        "key": chosen["key"],
        "label": chosen["label"],
        "keys": [row["key"] for row in entries],
        "labels": {row["key"]: row["label"] for row in entries},
        "tier": chosen["tier"],
        "category": chosen["category"],
        "category_name": (
            CASE_BY_KEY[chosen["category"]]["name"]
            if chosen["category"] in CASE_BY_KEY else ""
        ),
        "standard": standard_for(chosen["key"]),
    }


def _today(now=None):
    if now is None:
        return datetime.now().date().isoformat()
    if isinstance(now, datetime):
        return now.date().isoformat()
    return str(now)[:10]


def _clean(text, limit=1000):
    return str(text or "").strip()[:limit]


def _baseline_buckets(baseline):
    """Split the baseline quadrants into training lists, preserving order."""
    out = {
        "slow": [],
        "skipped": [],
        "wrong": [],
        "untriaged": [],
    }
    items = ((baseline or {}).get("quadrants") or {}).get("items") or []
    for row in items:
        bucket = None
        if row.get("quadrant") == "slow":
            bucket = "slow"
        elif row.get("quadrant") == "wrong":
            bucket = "wrong"
        elif row.get("quadrant") == "skipped":
            if row.get("triage") == "no_time":
                bucket = "skipped"
            elif not row.get("triage"):
                out["untriaged"].append(row)
        if bucket:
            out[bucket].append(row)
    return out


def _dedupe(rows):
    seen = set()
    out = []
    for row in rows:
        qid = row.get("qid")
        if not qid or qid in seen:
            continue
        seen.add(qid)
        out.append(row)
    return out


def _meta_from_baseline(row):
    return {
        "seq": row.get("seq"),
        "half": row.get("half"),
        "qid": row.get("qid"),
        "category": row.get("category") or "",
        "category_key": row.get("category_key"),
        "prototype": None,
    }


def _attempt_map(store):
    try:
        return {
            row["qid"]: row
            for row in (store.learning_snapshot() or {}).get("attempts") or []
        }
    except Exception:  # noqa: BLE001 - selection still works without it
        return {}


def _range_qids(key, exists=None):
    """Existing qids inside one prototype range, in question order."""
    entry = next((row for row in _RANGE_ENTRIES if row["key"] == key), None)
    if not entry:
        return []
    out = []
    for number in range(entry["first"], entry["last"] + 1):
        qid = "%s-%d" % (entry["prefix"], number)
        if exists is None or exists(qid):
            out.append(qid)
    return out


def _prototype_meta(qid):
    info = prototype_for(qid)
    if not info:
        return {"seq": None, "qid": qid, "category": "", "category_key": None,
                "prototype": None}
    return {
        "seq": None,
        "qid": qid,
        "category": info["category_name"],
        "category_key": info["category"],
        "prototype": info["key"],
        "prototype_label": info["label"],
    }


def timed_queue(source, source_key=None, count=DEFAULT_TIMED_QUESTIONS,
                baseline=None, store=None, exists=None):
    """Pick 5-10 questions for an 8-minute drill.

    Baseline sources are already ordered A then P; ``mixed`` puts the slow
    questions first, because those exist in the wrong book and still need a
    clock.  Prototype drills prefer questions never recorded in the wrong
    book, then active ones, and rotate across sessions.
    """
    count = max(MIN_QUESTIONS, min(int(count or DEFAULT_TIMED_QUESTIONS),
                                   MAX_QUESTIONS))
    source = str(source or "mixed").lower()
    if source not in TIMED_SOURCES:
        raise ValueError("限时拆解来源只能是 %s" % " / ".join(TIMED_SOURCES))
    buckets = _baseline_buckets(baseline)
    pick_rows = []
    hint = ""
    if source in ("mixed", "slow", "skipped", "wrong"):
        if source == "mixed":
            pick_rows = _dedupe(buckets["slow"] + buckets["skipped"] +
                                buckets["wrong"])
            hint = ("会但慢 %d · 放弃·没时间 %d · 做错 %d"
                    % (len(buckets["slow"]), len(buckets["skipped"]),
                       len(buckets["wrong"])))
        elif source == "slow":
            pick_rows = _dedupe(buckets["slow"])
            hint = "会但慢 %d 题；这些题做对过，但超过 10 分钟。" % len(pick_rows)
        elif source == "skipped":
            pick_rows = _dedupe(buckets["skipped"])
            hint = ("放弃·没时间 %d 题" % len(pick_rows))
            if buckets["untriaged"]:
                hint += "；还有 %d 题未分流，先标「没时间/不会」" % len(
                    buckets["untriaged"])
        else:
            pick_rows = _dedupe(buckets["wrong"])
            hint = "做错 %d 题；这些题也应在错题本里。" % len(pick_rows)
        if not pick_rows:
            raise ValueError(
                "没有可练的题：先录入 2025 基线逐题状态，并给放弃题分流"
            )
        picked = pick_rows[:count]
        return {
            "qids": [row["qid"] for row in picked],
            "items": {row["qid"]: _meta_from_baseline(row) for row in picked},
            "hint": hint,
            "available": len(pick_rows),
        }

    # prototype drill
    if source != "prototype":
        raise ValueError("不认识的训练来源：%s" % source)
    key = (source_key or "").strip().upper()
    if key not in _PROTOTYPE_BY_KEY or key not in PROTOTYPE_RANGES:
        raise ValueError("这个原型没有题号区间：%s" % (key or "空"))
    qids = _range_qids(key, exists)
    if not qids:
        raise ValueError("原型 %s 的题号区间没有可用题目" % key)
    attempts = _attempt_map(store) if store else {}
    history = set()
    if store:
        try:
            history = store.drill_history_qids("timed", "prototype", key)
        except Exception:  # noqa: BLE001
            history = set()
    fresh = [qid for qid in qids if qid not in attempts and qid not in history]
    active = [qid for qid in qids if qid in attempts
              and attempts[qid].get("status") != "mastered"
              and qid not in history]
    rest = [qid for qid in qids if qid not in fresh and qid not in active]
    ordered = fresh + active + rest
    picked = ordered[:count]
    label = _PROTOTYPE_BY_KEY[key]["label"]
    return {
        "qids": picked,
        "items": {qid: _prototype_meta(qid) for qid in picked},
        "hint": "原型 %s · %s · 区间共 %d 题；新题 %d" % (
            key, label, len(qids), len(fresh)),
        "available": len(qids),
    }


def _weak_categories(baseline):
    rows = ((baseline or {}).get("must") or {}).get("rows") or []
    return {
        row["key"] for row in rows
        if row.get("accuracy") is not None and row["accuracy"] < 0.85
    }


def recognize_prototypes(source, source_key=None, baseline=None):
    source = str(source or "weak").lower()
    if source not in RECOGNIZE_SOURCES:
        raise ValueError("认题卡来源只能是 %s" % " / ".join(RECOGNIZE_SOURCES))
    if source == "prototype":
        key = (source_key or "").strip().upper()
        if key not in _PROTOTYPE_BY_KEY or key not in PROTOTYPE_RANGES:
            raise ValueError("这个原型没有题号区间：%s" % (key or "空"))
        return [key]
    if source == "all":
        return [row["key"] for row in _RANGE_ENTRIES]
    if source == "core":
        return [row["key"] for row in _RANGE_ENTRIES if row["core"]]
    weak = _weak_categories(baseline)
    picked = [row["key"] for row in _RANGE_ENTRIES
              if row["category"] in weak]
    if not picked:
        picked = [row["key"] for row in _RANGE_ENTRIES if row["core"]]
    return picked


def recognize_queue(source="weak", source_key=None,
                    count=DEFAULT_RECOGNIZE_QUESTIONS, baseline=None,
                    store=None, exists=None):
    """Round-robin over prototype ranges so one card covers several types."""
    count = max(MIN_QUESTIONS, min(int(count or DEFAULT_RECOGNIZE_QUESTIONS),
                                   MAX_QUESTIONS))
    keys = recognize_prototypes(source, source_key, baseline)
    history = set()
    if store:
        try:
            history = store.drill_history_qids(
                "recognize", str(source or "weak").lower(),
                (source_key or "").strip().upper() or None,
            )
        except Exception:  # noqa: BLE001
            history = set()
    buckets = []
    for key in keys:
        qids = _range_qids(key, exists)
        if not qids:
            continue
        unseen = [qid for qid in qids if qid not in history]
        buckets.append(unseen or qids)
    if not buckets:
        raise ValueError("这些原型没有可用题目")
    picked = []
    index = 0
    while len(picked) < count:
        added = False
        for bucket in buckets:
            if index < len(bucket):
                qid = bucket[index]
                if qid not in picked:
                    picked.append(qid)
                    added = True
                if len(picked) >= count:
                    break
        if not added:
            break
        index += 1
    if len(picked) < MIN_QUESTIONS:
        raise ValueError("可用题目不足 %d 道，换一个原型范围" % MIN_QUESTIONS)
    labels = {key: _PROTOTYPE_BY_KEY[key]["label"] for key in keys}
    return {
        "qids": picked,
        "items": {qid: _prototype_meta(qid) for qid in picked},
        "hint": "覆盖 %d 个原型：%s" % (
            len({prototype_for(qid)["key"] for qid in picked}),
            "、".join(sorted({prototype_for(qid)["key"] for qid in picked})),
        ),
        "prototypes": labels,
    }


def start(store, payload, baseline=None, exists=None, now=None):
    mode = str(payload.get("mode") or "timed").lower()
    if mode not in MODES:
        raise ValueError("训练模式只能是 timed 或 recognize")
    count = payload.get("count") or (
        DEFAULT_RECOGNIZE_QUESTIONS if mode == "recognize"
        else DEFAULT_TIMED_QUESTIONS)
    source_key = payload.get("source_key")
    if mode == "timed":
        selection = timed_queue(
            payload.get("source") or "mixed", source_key, count,
            baseline=baseline, store=store, exists=exists,
        )
        if len(selection["qids"]) < MIN_QUESTIONS:
            raise ValueError(
                "这个来源只有 %d 道可用题，至少需要 %d 道；"
                "先补基线逐题状态或换一个来源"
                % (len(selection["qids"]), MIN_QUESTIONS)
            )
        minutes = payload.get("minutes_per_question") or DEFAULT_MINUTES
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ValueError("每题限时必须是整数")
        if not 5 <= minutes <= 15:
            raise ValueError("限时拆解每题 5–15 分钟；默认 8 分钟")
        session = store.start_drill(
            day=_today(now), mode="timed",
            source=str(payload.get("source") or "mixed").lower(),
            source_key=source_key,
            minutes_per_question=minutes,
            queue=selection["qids"],
            note=selection.get("hint") or "",
        )
    else:
        selection = recognize_queue(
            payload.get("source") or "weak", source_key, count,
            baseline=baseline, store=store, exists=exists,
        )
        session = store.start_drill(
            day=_today(now), mode="recognize",
            source=str(payload.get("source") or "weak").lower(),
            source_key=source_key,
            minutes_per_question=0,
            queue=selection["qids"],
            note=selection.get("hint") or "",
        )
    state = build_state(session)
    state["started"] = True
    state["selection"] = selection
    return state


def summarize(session):
    items = session.get("items") or []
    mode = session.get("mode")
    out = {"mode": mode, "total": len(items), "recorded": len(items)}
    seconds = [row["seconds"] for row in items if row.get("seconds") is not None]
    out["avg_seconds"] = round(sum(seconds) / len(seconds), 1) if seconds else None
    if mode == "timed":
        counts = {key: 0 for key in RESULT_LABELS}
        for row in items:
            if row.get("result") in counts:
                counts[row["result"]] += 1
        attempted = counts["correct"] + counts["wrong"] + counts["overtime"]
        first_step = sum(1 for row in items if (row.get("first_step") or "").strip())
        abandoned_with_step = sum(
            1 for row in items
            if row.get("result") == "abandoned"
            and (row.get("first_step") or "").strip()
        )
        out.update({
            "counts": counts,
            "attempted": attempted,
            "accuracy": (round(counts["correct"] / attempted, 4)
                         if attempted else None),
            "first_step": first_step,
            "first_step_ratio": (
                round(first_step / len(items), 4) if items else None),
            "abandoned_with_step": abandoned_with_step,
        })
        if not items:
            out["headline"] = "还没有记录。"
        elif counts["correct"] == 0 and attempted:
            out["headline"] = "这一组还没有做对；先回原型卡，别继续刷同型题。"
        elif counts["overtime"] > counts["correct"]:
            out["headline"] = (
                "超时 %d 题，多于做对 %d 题；问题在速度，不在知识。"
                % (counts["overtime"], counts["correct"]))
        elif first_step < len(items):
            out["headline"] = (
                "首步 %d/%d；下一组每题先写两行，再动笔算。"
                % (first_step, len(items)))
        else:
            out["headline"] = "首步 %d/%d 全覆盖；保持 8 分钟硬上限。" % (
                first_step, len(items))
    else:
        proto_tried = [row for row in items if row.get("prototype_ok") is not None]
        proto_ok = sum(1 for row in proto_tried if row["prototype_ok"])
        std_tried = [row for row in items if row.get("standard_ok") is not None]
        std_ok = sum(1 for row in std_tried if row["standard_ok"])
        identified = [row for row in items if row.get("identify_ok") is not None]
        out.update({
            "prototype_tried": len(proto_tried),
            "prototype_ok": proto_ok,
            "prototype_accuracy": (
                round(proto_ok / len(proto_tried), 4) if proto_tried else None),
            "standard_tried": len(std_tried),
            "standard_ok": std_ok,
            "standard_accuracy": (
                round(std_ok / len(std_tried), 4) if std_tried else None),
            "identified": sum(1 for row in identified if row["identify_ok"]),
            "identify_accuracy": (
                round(sum(1 for row in identified if row["identify_ok"])
                      / len(identified), 4) if identified else None),
        })
        if not items:
            out["headline"] = "还没有记录。"
        elif proto_tried and proto_ok / len(proto_tried) < 0.5:
            out["headline"] = "原型识别低于一半；先看原型全图，再做题。"
        elif std_tried and std_ok / len(std_tried) < 0.6:
            out["headline"] = "原型认得，规范定位弱；先把七本核心规范贴书签。"
        elif proto_tried and proto_ok == len(proto_tried):
            out["headline"] = "原型全对；下一组只练规范定位，压缩到 20 秒。"
        else:
            out["headline"] = "识别训练正常；错的那几张就是下一组原型卡。"
    return out


def build_state(session):
    recorded = {row["qid"] for row in session.get("items") or []}
    next_qid = next(
        (qid for qid in session.get("queue") or [] if qid not in recorded),
        None,
    )
    return {
        "session": session,
        "progress": {
            "done": len(recorded),
            "total": len(session.get("queue") or []),
        },
        "next_qid": next_qid,
        "finished": bool(session.get("finished_at")),
        "summary": summarize(session),
        "labels": {
            "mode": MODES.get(session.get("mode"), session.get("mode")),
            "results": RESULT_LABELS,
            "sources": (TIMED_SOURCES if session.get("mode") == "timed"
                        else RECOGNIZE_SOURCES),
        },
    }


def _category_for(qid, exam_tags=None):
    return category_key(qid, exam_tags)


def _finish(store, session_id, exam_tags=None):
    session = store.get_drill(session_id)
    if not session:
        raise ValueError("训练不存在")
    if session.get("finished_at"):
        return build_state(session)
    items = session.get("items") or []
    if not items:
        raise ValueError("还没有记录任何题，不能结束")
    mode = session["mode"]
    if mode == "recognize":
        # Recognition cards do not inflate the weekly question ledger.
        finished = store.finish_drill(
            session_id, attempted=0, correct=0, abandoned=0, overtime=0,
            minutes=0, category="mixed", note="认题卡（不计入题量）",
            ledger=False,
        )
        return build_state(finished)
    categories = {
        _category_for(row["qid"], exam_tags) for row in items
    } - {None}
    category = categories.pop() if len(categories) == 1 else "mixed"
    correct = sum(1 for row in items if row.get("result") == "correct")
    overtime = sum(1 for row in items if row.get("result") == "overtime")
    abandoned = sum(1 for row in items if row.get("result") == "abandoned")
    attempted = correct + sum(1 for row in items
                              if row.get("result") == "wrong") + overtime
    seconds = sum(row.get("seconds") or 0 for row in items)
    minutes = max(1, int(math.ceil(seconds / 60.0))) if items else 0
    label = TIMED_SOURCES.get(session["source"], session["source"])
    finished = store.finish_drill(
        session_id, attempted=attempted, correct=correct,
        abandoned=abandoned, overtime=overtime, minutes=minutes,
        category=category,
        note="限时拆解 · %s · %d 题" % (label, len(items)),
    )
    return build_state(finished)


def record(store, session_id, payload, exam_tags=None):
    session = store.get_drill(session_id)
    if not session:
        raise ValueError("训练不存在")
    if session.get("finished_at"):
        # A retried request after auto-finish must not turn into an error.
        return build_state(session)
    qid = str(payload.get("qid") or "").strip().upper()
    if qid not in (session.get("queue") or []):
        raise ValueError("题号不在本次训练队列里")
    mode = session["mode"]
    seconds = payload.get("seconds")
    if seconds is not None:
        try:
            seconds = round(float(seconds), 1)
        except (TypeError, ValueError):
            raise ValueError("用时必须是数字")
        if not 0 <= seconds <= 7200:
            raise ValueError("用时应在 0–7200 秒之间")
    category = _category_for(qid, exam_tags)
    item = {
        "qid": qid,
        "category": category or "",
        "seconds": seconds,
    }
    if mode == "timed":
        result = str(payload.get("result") or "").strip().lower()
        if result not in RESULT_LABELS:
            raise ValueError("训练结果只能是做对/做错/超时/放弃")
        first_step = _clean(payload.get("first_step"))
        if result == "abandoned" and not first_step:
            raise ValueError("放弃也要写首步：两行过程分从这里来")
        info = prototype_for(qid)
        item.update({
            "result": result,
            "first_step": first_step,
            "prototype": info["key"] if info else None,
        })
    else:
        guess_proto = str(payload.get("guess_prototype") or "").strip().upper()
        guess_std = str(payload.get("guess_standard") or "").strip()
        if guess_proto and guess_proto not in _PROTOTYPE_BY_KEY:
            raise ValueError("不认识的原型：%s" % guess_proto)
        codes = {row["code"] for row in standard_options()}
        if guess_std and guess_std not in codes:
            raise ValueError("不认识的规范：%s" % guess_std)
        info = prototype_for(qid)
        if not info:
            raise ValueError("这道题没有原型真值：%s" % qid)
        proto_ok = guess_proto in info["keys"] if guess_proto else False
        expected_std = info["standard"]
        if expected_std is None:
            std_ok = None
        else:
            std_ok = guess_std == expected_std["code"]
        item.update({
            "result": None,
            "prototype": info["key"],
            "guess_prototype": guess_proto or None,
            "guess_standard": guess_std or None,
            "correct_prototype": info["key"],
            "correct_standard": (expected_std or {}).get("code"),
            "prototype_ok": proto_ok,
            "standard_ok": std_ok,
            "identify_ok": bool(proto_ok and std_ok is not False),
        })
    store.record_drill_item(session_id, item)
    updated = store.get_drill(session_id)
    if all(qid_ in {row["qid"] for row in updated["items"]}
           for qid_ in updated["queue"]):
        updated = _finish(store, session_id, exam_tags)["session"]
    state = build_state(updated)
    state["item"] = next(
        (row for row in updated["items"] if row["qid"] == qid), None)
    return state


def finish(store, session_id, exam_tags=None):
    return _finish(store, session_id, exam_tags)


def get_state(store, session_id):
    session = store.get_drill(session_id)
    if not session:
        raise ValueError("训练不存在")
    return build_state(session)


def overview(store, baseline=None, exists=None, now=None):
    buckets = _baseline_buckets(baseline)
    weak = _weak_categories(baseline)
    active = {
        mode: (store.active_drill(mode) is not None)
        for mode in ("timed", "recognize")
    }
    active_ids = {
        mode: (store.active_drill(mode) or {}).get("id")
        for mode in ("timed", "recognize")
    }
    timed_last = None
    try:
        rows = store.list_drills(limit=1, mode="timed")
        if rows:
            timed_last = summarize(rows[0])
            timed_last["id"] = rows[0]["id"]
            timed_last["day"] = rows[0]["day"]
    except Exception:  # noqa: BLE001
        pass
    recog_last = None
    try:
        rows = store.list_drills(limit=1, mode="recognize")
        if rows:
            recog_last = summarize(rows[0])
            recog_last["id"] = rows[0]["id"]
            recog_last["day"] = rows[0]["day"]
    except Exception:  # noqa: BLE001
        pass
    weak_rows = [
        row for row in prototype_options()
        if row["category"] in weak
    ]
    return {
        "modes": MODES,
        "results": RESULT_LABELS,
        "timed": {
            "minutes_per_question": DEFAULT_MINUTES,
            "min": MIN_QUESTIONS,
            "max": MAX_QUESTIONS,
            "default_count": DEFAULT_TIMED_QUESTIONS,
            "sources": TIMED_SOURCES,
            "counts": {
                "slow": len(buckets["slow"]),
                "skipped": len(buckets["skipped"]),
                "wrong": len(buckets["wrong"]),
                "untriaged": len(buckets["untriaged"]),
            },
            "active_id": active_ids["timed"],
            "last": timed_last,
        },
        "recognize": {
            "seconds": RECOGNIZE_SECONDS,
            "min": MIN_QUESTIONS,
            "max": MAX_QUESTIONS,
            "default_count": DEFAULT_RECOGNIZE_QUESTIONS,
            "sources": RECOGNIZE_SOURCES,
            "weak_categories": sorted(weak),
            "weak_count": len(weak_rows),
            "core_count": sum(1 for row in _RANGE_ENTRIES if row["core"]),
            "all_count": len(_RANGE_ENTRIES),
            "active_id": active_ids["recognize"],
            "last": recog_last,
        },
        "prototypes": prototype_options(),
        "standards": standard_options(),
    }
