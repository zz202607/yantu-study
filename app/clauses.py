"""Per-question clause hints (M11) and the reverse index (M12).

The question detail already has a category-level "规范定位" button.  This
module answers the narrower question: **where in which standard does this
question live?**  Three sources, strongest first:

    answer     citations already cached by M4 (exact code + clause)
    prototype  the question's prototype -> curated reading stops -> clauses
    keyword    chapter/section/exam-text terms -> curated stop keywords

Only the first source is exact; the other two are exam-curated suggestions
and are labelled as such in the UI.  No OCR runs here: the suggestion panel
warms the M4 cache, and this endpoint stays fast.  Every page comes from the
local standards index; a citation that cannot be resolved is reported as
`unresolved` instead of guessed.

`reverse()` turns the same evidence around: clause -> wrong-book questions.
Exact citations and suggestions stay in separate buckets, one row per
standard section, so the pre-exam checklist never passes a prototype guess
off as a citation.
"""

import re
from pathlib import Path

from app import answer_ocr, curriculum, reading, standards
from app.catalog import Catalog
from app.plan import PROTOTYPES, PROTOTYPE_RANGES

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"
ANSWER_OCR_CACHE = ROOT / ".cache" / "answer_ocr"

MAX_HINTS = 5
MAX_ANSWER_CLAUSES = 3
MAX_PROTOTYPE_HINTS = 4
MAX_KEYWORD_HINTS = 2

# Cross-category links that are worth keeping: 滑坡 lives in GB 50330, and
# 检测 questions often land on the loading-test clause in GB 50021.
CROSS_CATEGORY_GUIDES = {
    "C8": {"GB50330-2013"},
    "C10": {"GB50021-2001", "GB50330-2013"},
}

SOURCE_ORDER = {"answer": 0, "prototype": 1, "keyword": 2}
SOURCE_CONFIDENCE = {"answer": "high", "prototype": "medium", "keyword": "low"}
LEVEL_ORDER = {"must": 0, "key": 1, "later": 2}

_PROTOTYPE_LABEL = {row[0]: row[1] for row in PROTOTYPES}
_CATALOG = Catalog(INDEX)

# Curated terms per reading stop.  Kept here instead of derived from the TOC:
# the exam talks about 换填/预压/液化, while the TOC says 垫层法/预压地基/
# 液化和软土地基.  Terms must be specific enough to survive a cross-category
# question; the question category adds a bonus, it is not a hard filter.
STOP_KEYWORDS = {
    "gb50011-3-1": ("设防分类", "设防烈度", "提高一度", "抗震措施"),
    "gb50011-4-1": ("场地类别", "剪切波速", "等效剪切波速", "覆盖层厚度"),
    "gb50011-4-2": ("抗震承载力", "地基抗震", "ζa", "天然地基抗震"),
    "gb50011-4-3": ("液化", "标准贯入", "锤击数", "液化指数"),
    "gb50011-4-4": ("桩基抗震", "预制桩", "液化土中桩"),
    "gb50011-5-1": ("时程分析", "偶然偏心", "地震作用计算"),
    "gb50011-5-2": ("地震影响系数", "反应谱", "底部剪力", "振型分解",
                    "特征周期", "地震作用效应"),
    "gb50025-4-3": ("湿陷系数", "浸水载荷"),
    "gb50025-4-4": ("湿陷性判别", "自重湿陷", "湿陷等级", "湿陷起始压力",
                    "湿陷性黄土"),
    "gb50025-5-6": ("黄土承载力", "黄土变形"),
    "gb50025-6-1": ("剩余湿陷量", "处理深度", "湿陷性处理"),
    "gb50025-6-2": ("垫层法", "灰土垫层"),
    "gb50025-6-3": ("强夯",),
    "gb50025-6-5": ("预浸水",),
    "gb50112-4-2": ("自由膨胀率", "膨胀率", "收缩系数", "膨胀力"),
    "gb50112-4-3": ("膨胀潜势", "膨胀土场地", "大气影响深度", "膨胀土"),
    "gb50112-5-2": ("膨胀土地基计算",),
    "gb50112-5-4": ("坡地挡土墙",),
    "jgj79-3": ("地基处理原则",),
    "jgj79-4-2": ("换填", "垫层厚度", "压实系数"),
    "jgj79-5-2": ("预压", "固结度", "排水板", "堆载"),
    "jgj79-7-1": ("复合地基", "置换率", "桩间土"),
    "jgj79-7-3": ("搅拌桩", "水泥土搅拌"),
    "jgj79-7-7": ("CFG", "粉煤灰碎石桩", "褥垫层"),
    "jgj79-10": ("载荷试验", "检验监测"),
    "gb50007-4-1": ("塑性指数", "液性指数", "粉土", "黏性土"),
    "gb50007-5-2": ("承载力", "深宽修正", "基底压力", "持力层",
                    "软弱下卧层", "压力扩散角"),
    "gb50007-5-3": ("沉降", "工后沉降", "变形计算", "压缩模量",
                    "计算深度", "e~p曲线", "e-p曲线"),
    "gb50007-5-4": ("抗浮", "抗滑", "稳定性验算"),
    "gb50007-6-7": ("土压力", "重力式挡墙"),
    "gb50007-8-1": ("台阶宽高比", "冲切", "剪切", "基础高度", "轴心抗拉"),
    "gb50007-9-3": ("基坑土压力", "地下水控制", "抗渗流"),
    "gb50007-app-k": ("附加应力", "角点法", "附加应力系数"),
    "jgj120-3-1": ("支护安全等级", "支护选型", "变形控制"),
    "jgj120-3-4": ("土压力", "水压力", "水土分算", "水土压力", "地面超载"),
    "jgj120-4-1": ("弹性支点法", "支点刚度", "被动土压力", "支挡",
                   "排桩", "地下连续墙", "内支撑"),
    "jgj120-4-2": ("抗倾覆", "抗隆起", "抗渗流", "整体稳定", "锚杆"),
    "jgj120-5-1": ("土钉墙", "土钉"),
    "jgj120-6-1": ("水泥土墙",),
    "jgj120-7-3": ("降水", "涌水量", "井点", "地下水控制"),
    "gb50330-3-2": ("安全等级", "稳定安全系数"),
    "gb50330-5-2": ("圆弧滑动", "折线滑动", "直线滑动", "传递系数",
                    "边坡稳定", "岩质边坡", "结构面", "滑面"),
    "gb50330-6-2": ("朗肯", "库仑", "侧向土压力", "岩土压力",
                    "土压力", "水土分算"),
    "gb50330-8-2": ("锚杆", "锚索", "锚固长度"),
    "gb50330-11": ("重力式挡墙", "重力式挡土墙", "挡土墙", "抗滑稳定",
                   "抗倾覆稳定"),
    "gb50330-12": ("悬臂式挡墙", "扶壁式挡墙"),
    "gb50330-17": ("滑坡", "崩塌"),
    "gb50330-app-a": ("传递系数法",),
    "gb50021-3-1": ("勘察等级", "工程重要性", "场地复杂程度"),
    "gb50021-3-3": ("土的分类", "土的定名", "塑性指数", "液性指数"),
    "gb50021-4-1": ("勘探点间距", "勘探孔深度", "取样", "原位测试"),
    "gb50021-5-2": ("滑坡勘察", "滑坡"),
    "gb50021-6-1": ("湿陷性判别",),
    "gb50021-7-2": ("抽水试验", "注水试验", "压水试验", "渗透系数", "抽水"),
    "gb50021-10-2": ("载荷试验", "平板载荷", "比例界限", "极限荷载",
                     "原位测试"),
    "gb50021-14-2": ("变异系数", "标准值", "统计修正系数", "参数统计"),
    "jgj94-3-3": ("桩距", "布桩", "桩型"),
    "jgj94-5-1": ("桩顶作用", "群桩", "偏心荷载"),
    "jgj94-5-3": ("单桩竖向", "经验参数法", "大直径桩", "嵌岩桩", "后注浆",
                  "桩基", "桩端", "灌注桩", "预制桩"),
    "jgj94-5-4": ("负摩阻力", "抗拔", "软弱下卧层", "液化土"),
    "jgj94-5-5": ("桩基沉降", "等效作用分层总和"),
    "jgj94-5-7": ("水平承载力", "桩顶位移"),
    "jgj94-5-9": ("承台", "冲切", "局部承压"),
}

_STOP_ROWS = None


def _stop_rows():
    """Flatten the nine guides into page-resolved rows (static data)."""
    global _STOP_ROWS
    if _STOP_ROWS is not None:
        return _STOP_ROWS
    rows = []
    for guide in reading.load_guides()["standards"]:
        detail = reading.detail(guide["code_key"], {})
        toc = standards.standard_toc(guide["code"]) or {}
        page_count = int(toc.get("page_count") or 0)
        for stop in detail["stops"]:
            page = int(stop.get("pdf_page") or 0)
            if page <= 0:
                continue
            rows.append({
                "guide": guide,
                "stop": stop,
                "code_key": guide["code_key"],
                "code": guide["code"],
                "title": guide["name"],
                "category_key": guide.get("category_key"),
                "pdf_page": page,
                "page_count": page_count,
                "level": stop.get("level"),
            })
    rows.sort(key=lambda row: (
        LEVEL_ORDER.get(row.get("level"), 9),
        row["guide"].get("order", 99),
        row["stop"].get("section") or "",
    ))
    _STOP_ROWS = rows
    return rows


def _prototype_keys(qid):
    """Prototypes whose question range contains this C-family qid."""
    match = re.match(r"^C(\d{1,2})-(\d{1,3})$", (qid or "").upper())
    if not match:
        return []
    prefix = "C%d" % int(match.group(1))
    number = int(match.group(2))
    found = []
    for key, (start, end) in PROTOTYPE_RANGES.items():
        if start.split("-")[0] != prefix:
            continue
        first = int(start.split("-")[1])
        last = int(end.split("-")[1])
        if first <= number <= last:
            found.append((last - first, key))
    found.sort()
    return [key for _span, key in found]


def _clause_prefix(value):
    return standards.normalize_clause(value)


def _stop_for_clause(code, clause):
    """Find the reading stop that owns a clause when the TOC cannot."""
    wanted = _clause_prefix(clause)
    if not wanted:
        return None
    key = standards.code_key(code)
    for row in _stop_rows():
        if key and row["code_key"] != key:
            continue
        clauses = row["stop"].get("clauses") or []
        if wanted in clauses:
            return row
        section = str(row["stop"].get("section") or "").split("/")[0]
        if section and wanted.startswith(section + "."):
            return row
    return None


def _row_from_stop(row, source, reason, clauses=None, confidence=None):
    stop = row["stop"]
    return {
        "code": row["code"],
        "title": row["title"],
        "section": stop.get("section") or "",
        "section_title": stop.get("title") or "",
        "clauses": list(clauses or stop.get("clauses") or []),
        "pdf_page": row["pdf_page"],
        "printed_page": stop.get("page"),
        "page_count": row["page_count"],
        "estimated": False,
        "source": source,
        "source_label": {
            "answer": "解析依据",
            "prototype": "原型重点",
            "keyword": "章节匹配",
        }[source],
        "confidence": confidence or SOURCE_CONFIDENCE[source],
        "reason": reason,
    }


def _answer_rows(qid, cache_dir=None):
    """Exact citations from the M4 cache; unresolved ones are reported."""
    cached = answer_ocr.load_cached(qid, cache_dir or ANSWER_OCR_CACHE)
    if not cached:
        return [], []
    rows = []
    unresolved = []
    for citation in cached.get("citations") or []:
        title = citation.get("title") or ""
        code = citation.get("code") or ""
        clauses = [c for c in (citation.get("clauses") or []) if c]
        if not clauses:
            continue
        lookup_code = code or title
        for clause in clauses[:MAX_ANSWER_CLAUSES]:
            located = standards.locate_clause(lookup_code, clause)
            if not located and title and lookup_code != title:
                located = standards.locate_clause(title, clause)
            if not located:
                stop_row = _stop_for_clause(lookup_code, clause) \
                    or _stop_for_clause(title, clause)
                if stop_row:
                    rows.append(_row_from_stop(
                        stop_row, "answer",
                        "解析直接引用 %s" % clause,
                        clauses=[clause],
                    ))
                else:
                    unresolved.append({
                        "title": title,
                        "code": code,
                        "clause": clause,
                    })
                continue
            stop_row = _stop_for_clause(
                located.get("code") or lookup_code, clause
            )
            rows.append({
                "code": located.get("code") or code or title,
                "title": located.get("title") or title,
                "section": located.get("matched") or "",
                # The scan TOC sometimes carries English/OCR titles; the
                # curated reading stop is the better label when both exist.
                "section_title": (stop_row["stop"].get("title") if stop_row else "")
                or located.get("section_title") or "",
                "clauses": [clause],
                "pdf_page": located.get("pdf_page"),
                "printed_page": located.get("printed_page"),
                "page_count": located.get("page_count"),
                "estimated": bool(located.get("estimated")),
                "source": "answer",
                "source_label": "解析依据",
                "confidence": "high",
                "reason": "解析直接引用 %s" % clause,
            })
    return rows, unresolved


def _prototype_rows(qid):
    rows = []
    seen = set()
    for key in _prototype_keys(qid):
        label = _PROTOTYPE_LABEL.get(key, key)
        for row in _stop_rows():
            if key not in (row["stop"].get("prototypes") or []):
                continue
            dedupe = (row["code_key"], row["stop"].get("section"))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            rows.append(_row_from_stop(
                row, "prototype", "原型 %s %s" % (key, label)
            ))
            if len(rows) >= MAX_PROTOTYPE_HINTS:
                break
        if len(rows) >= MAX_PROTOTYPE_HINTS:
            break
    return rows


def _keyword_text(described, exam_text):
    parts = [
        described.get("section"),
        described.get("chapter"),
        exam_text,
    ]
    return " ".join(part for part in parts if part)


def _keyword_rows(described, category, exam_text):
    if not category:
        return []
    text = _keyword_text(described, exam_text)
    if not text:
        return []
    allowed = CROSS_CATEGORY_GUIDES.get(category, set())
    scored = []
    for row in _stop_rows():
        terms = STOP_KEYWORDS.get(row["stop"].get("id"))
        if not terms:
            continue
        same_category = row.get("category_key") == category
        if not same_category and row["code_key"] not in allowed:
            continue
        hits = [term for term in terms if term.lower() in text.lower()]
        if not hits:
            continue
        score = sum(len(term) for term in hits)
        if same_category:
            score += 3
        scored.append((score, len(hits), row, hits))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]["guide"].get("order", 99)))
    rows = []
    seen = set()
    for score, _count, row, hits in scored:
        dedupe = (row["code_key"], row["stop"].get("section"))
        if dedupe in seen:
            continue
        seen.add(dedupe)
        rows.append(_row_from_stop(
            row, "keyword",
            "题目关键词：%s" % "、".join(hits[:4]),
            confidence="medium" if score >= 8 else "low",
        ))
        if len(rows) >= MAX_KEYWORD_HINTS:
            break
    return rows


def _dedupe(rows):
    """Merge rows that point at the same standard section."""
    merged = {}
    order = []
    for row in rows:
        section = row.get("section") or (row.get("clauses") or [""])[0]
        key = (standards.code_key(row.get("code")), section)
        if key not in merged:
            merged[key] = row
            order.append(key)
            continue
        existing = merged[key]
        clauses = list(existing.get("clauses") or [])
        for clause in row.get("clauses") or []:
            if clause not in clauses:
                clauses.append(clause)
        if SOURCE_ORDER.get(row.get("source"), 9) < SOURCE_ORDER.get(existing.get("source"), 9):
            row["clauses"] = clauses
            merged[key] = row
        else:
            existing["clauses"] = clauses
            if not existing.get("section_title"):
                existing["section_title"] = row.get("section_title") or ""
            if not existing.get("reason"):
                existing["reason"] = row.get("reason") or ""
    return [merged[key] for key in order]


def _row_tokens(row):
    """Exact sections for one answer row: '5.2/5.3' -> ['5.2', '5.3'].

    Only exact citations are split.  Prototype/keyword hints keep the curated
    stop section (often a span such as '5.2/5.3') so the same question is not
    counted under two section rows.
    """
    section = str(row.get("section") or "").strip()
    parts = [part.strip() for part in section.split("/") if part.strip()]
    return parts or [""]


def _clauses_for_token(clauses, token):
    if not token:
        return list(clauses)
    picked = [
        clause for clause in clauses
        if clause == token
        or clause.startswith(token + ".")
        or (token.startswith("附录") and clause.startswith(token))
    ]
    return picked or list(clauses)


def _question_entry(item, hint):
    return {
        "id": item.get("id"),
        "qid": str(item.get("qid") or "").upper(),
        "status": item.get("status") or "active",
        "times": int(item.get("times") or 0),
        "source": hint.get("source") or "keyword",
        "source_label": hint.get("source_label") or "",
        "reason": hint.get("reason") or "",
    }


def _finish_row(group):
    questions = list(group["questions"].values())
    questions.sort(key=lambda q: (q["status"] == "mastered", q["qid"]))
    counts = {"answer": 0, "prototype": 0, "keyword": 0}
    for question in questions:
        counts[question["source"]] = counts.get(question["source"], 0) + 1
    strongest = min(
        (question["source"] for question in questions),
        key=lambda source: SOURCE_ORDER.get(source, 9),
        default=group.get("source") or "keyword",
    )
    mastered = sum(1 for question in questions if question["status"] == "mastered")
    return {
        "code": group["code"],
        "title": group["title"],
        "code_key": group["code_key"],
        "section": group["section"],
        "section_title": group["section_title"],
        "clauses": group["clauses"],
        "pdf_page": group["pdf_page"],
        "printed_page": group["printed_page"],
        "page_count": group["page_count"],
        "estimated": group["estimated"],
        "source": strongest,
        "source_label": {
            "answer": "解析依据",
            "prototype": "原型建议",
            "keyword": "关键词建议",
        }[strongest],
        "sources": counts,
        "questions": questions,
        "active_count": len(questions) - mastered,
        "mastered_count": mastered,
    }


def reverse(attempts, include_mastered=False, cache_dir=None):
    """Clause -> wrong-book questions (M12).

    Exact citations from the cached solution OCR form `exact`; prototype and
    keyword suggestions form `suggested`.  A question with no hint at all is
    listed in `unmatched` instead of being silently dropped, and citations
    whose page cannot be resolved stay in `unresolved`.
    """
    attempts = list(attempts or [])
    groups = {}
    order = []
    unmatched = []
    unresolved = []
    errors = []
    included = 0
    indexed = 0
    question_sources = {"answer": 0, "prototype": 0, "keyword": 0}

    for item in attempts:
        status = item.get("status") or "active"
        if status == "mastered" and not include_mastered:
            continue
        included += 1
        qid = str(item.get("qid") or "").strip().upper()
        try:
            data = hints(qid, cache_dir=cache_dir)
        except Exception as exc:  # noqa: BLE001 - one bad qid must not blank the list
            errors.append({"qid": qid, "error": str(exc)})
            unmatched.append({"id": item.get("id"), "qid": qid, "status": status})
            continue
        rows = data.get("hints") or []
        for row in data.get("unresolved") or []:
            unresolved.append(dict(row, qid=qid))
        if not rows:
            unmatched.append({"id": item.get("id"), "qid": qid, "status": status})
            continue
        exact_codes = {
            standards.code_key(row.get("code")) or row.get("code")
            for row in rows if row.get("source") == "answer"
        }
        used_sources = set()
        for hint in rows:
            code = hint.get("code") or ""
            code_key = standards.code_key(code) or code
            # A suggestion never repeats a standard that already has an exact
            # citation for this question: exact evidence wins, suggestions
            # only fill the standards that are still dark.
            if hint.get("source") != "answer" and code_key in exact_codes:
                continue
            used_sources.add(hint.get("source"))
            page = int(hint.get("pdf_page") or 0)
            clauses = list(hint.get("clauses") or [])
            tokens = _row_tokens(hint) if hint.get("source") == "answer" \
                else [str(hint.get("section") or "").strip()]
            for token in tokens:
                # One row per standard section; an empty section groups by page.
                key = (code_key, token, page if not token else 0)
                group = groups.get(key)
                if group is None:
                    group = {
                        "code": code,
                        "title": hint.get("title") or "",
                        "code_key": code_key,
                        "section": token,
                        "section_title": hint.get("section_title")
                        if token and hint.get("section") == token else "",
                        "clauses": [],
                        "pdf_page": page,
                        "printed_page": hint.get("printed_page"),
                        "page_count": int(hint.get("page_count") or 0),
                        "estimated": bool(hint.get("estimated")),
                        "source": hint.get("source") or "keyword",
                        "questions": {},
                    }
                    groups[key] = group
                    order.append(key)
                if not group["title"] and hint.get("title"):
                    group["title"] = hint.get("title")
                if page and not group["pdf_page"]:
                    group["pdf_page"] = page
                if not group["section_title"] and hint.get("section_title"):
                    group["section_title"] = hint.get("section_title") or ""
                page_count = int(hint.get("page_count") or 0)
                if page_count > group["page_count"]:
                    group["page_count"] = page_count
                better = SOURCE_ORDER.get(hint.get("source"), 9) < \
                    SOURCE_ORDER.get(group["source"], 9)
                if better:
                    group["source"] = hint.get("source") or group["source"]
                    if page:
                        group["pdf_page"] = page
                    group["printed_page"] = hint.get("printed_page") or group["printed_page"]
                    group["estimated"] = bool(hint.get("estimated"))
                    if token and hint.get("section") == token:
                        group["section_title"] = hint.get("section_title") \
                            or group["section_title"]
                for clause in _clauses_for_token(clauses, token):
                    if clause not in group["clauses"]:
                        group["clauses"].append(clause)
                entry = _question_entry(item, hint)
                current = group["questions"].get(qid)
                if current is None or SOURCE_ORDER.get(entry["source"], 9) < \
                        SOURCE_ORDER.get(current["source"], 9):
                    group["questions"][qid] = entry
                elif not current.get("reason") and entry.get("reason"):
                    current["reason"] = entry["reason"]
        if used_sources:
            indexed += 1
        for source in used_sources:
            if source in question_sources:
                question_sources[source] += 1

    rows = [_finish_row(groups[key]) for key in order]
    exact = [row for row in rows if row["source"] == "answer"]
    suggested = [row for row in rows if row["source"] != "answer"]
    for bucket in (exact, suggested):
        bucket.sort(key=lambda row: (
            -row["active_count"],
            -len(row["questions"]),
            SOURCE_ORDER.get(row["source"], 9),
            row["code"],
            row["pdf_page"],
        ))
    return {
        "summary": {
            "attempts": len(attempts),
            "included": included,
            "mastered_hidden": len(attempts) - included,
            "indexed": indexed,
            "unmatched": len(unmatched),
            "exact_rows": len(exact),
            "suggested_rows": len(suggested),
            "clauses": len(exact) + len(suggested),
            "questions_by_source": question_sources,
            "unresolved": len(unresolved),
        },
        "exact": exact,
        "suggested": suggested,
        "unmatched": sorted(unmatched, key=lambda row: row["qid"]),
        "unresolved": unresolved,
        "errors": errors,
    }


def hints(qid, cache_dir=None):
    """Clause hints for one question, strongest source first."""
    qid = (qid or "").strip().upper()
    if not qid:
        return {"qid": "", "hints": [], "unresolved": [], "coverage": {}}
    tags = curriculum.load_exam_tags(INDEX)
    exam = tags.get(qid) or {}
    category = curriculum.category_key(qid, tags)
    described = _CATALOG.describe(qid)
    answer_rows, unresolved = _answer_rows(qid, cache_dir=cache_dir)
    rows = list(answer_rows)
    rows.extend(_prototype_rows(qid))
    rows.extend(_keyword_rows(described, category, exam.get("text") or ""))
    rows = _dedupe(rows)
    rows.sort(key=lambda row: (
        SOURCE_ORDER.get(row.get("source"), 9),
        {"high": 0, "medium": 1, "low": 2}.get(row.get("confidence"), 3),
    ))
    counts = {"answer": 0, "prototype": 0, "keyword": 0}
    for row in rows:
        counts[row.get("source")] = counts.get(row.get("source"), 0) + 1
    return {
        "qid": qid,
        "category_key": category,
        "category_name": (curriculum.CASE_BY_KEY.get(category) or {}).get("name", ""),
        "chapter": described.get("chapter", ""),
        "section": described.get("section", ""),
        "hints": rows[:MAX_HINTS],
        "unresolved": unresolved[:3],
        "coverage": counts,
    }
