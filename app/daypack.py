"""Turn a checked day sheet into grades, wrong-book entries and practice totals.

The print already knows which questions were review and which were new, so the
user only marks the exceptions ("this one was wrong, cause = calc").  This
module owns the derivation:

* review items run through the normal scheduling path (7/15/30 ladder);
* new wrong items become wrong-book rows with their tags and photo;
* new answers become one practice row, so the four totals are never typed;
* everything not flagged is treated as done correctly.
"""

RESULTS = ("correct", "wrong", "skipped", "overtime")
VALID_TAGS = ("concept", "calc", "standard", "misread")


def _index_results(pack, results):
    """Validate the submitted rows and default every other item to correct."""
    catalog = {item["qid"]: item["role"] for item in pack.get("items") or []}
    submitted = {}
    for row in results or ():
        qid = str(row.get("qid") or "").strip().upper()
        if qid not in catalog:
            raise ValueError("题 %s 不在这张卷里" % (qid or "?"))
        result = str(row.get("result") or "").strip().lower()
        if result not in RESULTS:
            raise ValueError("未知结果：%s" % result)
        tags = [str(tag).strip().lower() for tag in row.get("tags") or ()]
        bad = [tag for tag in tags if tag not in VALID_TAGS]
        if bad:
            raise ValueError("未知错因：%s" % bad[0])
        submitted[qid] = {
            "result": result,
            "tags": list(dict.fromkeys(tags)),
        }
    merged = []
    for item in pack.get("items") or []:
        hit = submitted.get(item["qid"], {})
        merged.append({
            "qid": item["qid"],
            "role": item["role"],
            "result": hit.get("result", "correct"),
            "tags": hit.get("tags", []),
        })
    return merged


def apply_results(store, pack, results, photo_sha=None, now=None):
    """Apply a checked sheet once; returns the closed pack and a summary."""
    if not pack:
        raise ValueError("今日卷不存在")
    if pack.get("closed"):
        raise ValueError("这张卷已经归档")
    photo = None
    if photo_sha:
        photo = store.photo(str(photo_sha))
        if not photo:
            raise ValueError("照片不存在")
    merged = _index_results(pack, results)
    # Grade in the frozen session's queue order.  The review cursor finishes
    # a session when it walks off the end, so grading a later card first would
    # end the round before the earlier cards are recorded.
    session_queue = []
    if pack.get("session_id"):
        session_queue = (
            store.session(pack["session_id"]).get("queue") or []
        )
    order = {qid: index for index, qid in enumerate(session_queue)}
    merged = sorted(
        merged,
        key=lambda item: (
            0 if item["role"] == "review" else 1,
            order.get(item["qid"], len(order)),
        ),
    )
    summary = {
        "review": {"total": 0, "correct": 0, "wrong": 0, "skipped": 0},
        "new": {"total": 0, "correct": 0, "wrong": 0, "skipped": 0,
                "overtime": 0},
        "wrongs": [],
        "warnings": [],
        "photo": photo["sha256"] if photo else "",
        "practice_id": None,
    }

    for item in merged:
        qid, role, result = item["qid"], item["role"], item["result"]
        if role == "review":
            summary["review"]["total"] += 1
            if result == "skipped":
                summary["review"]["skipped"] += 1
                continue
            grade = "correct" if result in ("correct", "overtime") else "wrong"
            summary["review"]["correct" if grade == "correct" else "wrong"] += 1
            session_id = pack.get("session_id")
            if not session_id:
                summary["warnings"].append(
                    "%s 没有可用的复习会话，未改排期" % qid)
                continue
            try:
                store.grade(
                    session_id, qid, grade,
                    tags=item["tags"] if grade == "wrong" else None,
                    process_photo_sha=(
                        photo["sha256"] if (grade == "wrong" and photo) else None
                    ),
                    now=now,
                )
            except ValueError as exc:
                summary["warnings"].append("%s：%s" % (qid, exc))
            continue

        summary["new"]["total"] += 1
        if result == "skipped":
            summary["new"]["skipped"] += 1
        elif result == "correct":
            summary["new"]["correct"] += 1
        elif result == "overtime":
            summary["new"]["correct"] += 1
            summary["new"]["overtime"] += 1
        else:
            summary["new"]["wrong"] += 1
            summary["wrongs"].append(qid)

    wrong_new = [
        {"qid": item["qid"], "family": item["qid"][0], "source": "paper",
         "tags": item["tags"], "photo_sha": photo["sha256"] if photo else None}
        for item in merged
        if item["role"] == "new" and item["result"] == "wrong"
    ]
    if wrong_new:
        result = store.add_attempts(wrong_new)
        for row in result.get("errors") or ():
            summary["warnings"].append(
                "%s：%s" % (row.get("qid"), row.get("detail")))

    new = summary["new"]
    attempted = new["total"] - new["skipped"]
    if attempted > 0:
        practice = store.add_practice_session(
            day=pack.get("day"),
            source="paper",
            category="mixed",
            attempted=attempted,
            correct=new["correct"],
            abandoned=new["skipped"],
            overtime=new["overtime"],
            minutes=0,
            note="今日卷自动归档",
        )
        summary["practice_id"] = practice["id"]

    closed = store.close_day_pack(
        pack["id"], results=merged,
        photo_id=photo["id"] if photo else None,
        summary=summary,
    )
    return {"pack": closed, "summary": summary}
