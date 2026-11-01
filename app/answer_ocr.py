"""Answer-page OCR -> standard citations and error-cause candidates (M4).

v1 concept F8: only questions already in the wrong book are read, the OCR runs
on demand and never blocks the main flow.  The output is *candidates*: every
tag carries the sentence that triggered it, and only the user presses save.

Three layers, all pure functions except the OCR call:

    join_lines        OCR rows -> one whitespace-free string (titles wrap)
    extract_citations 《规范名》 + code + clause numbers
    suggest_tags      evidence sentences -> at most three of the four tags

Everything is regex/rule based on purpose: no cloud AI, no generated text.
"""

import json
import os
import re
import time

from app.store import TAGS

CACHE_VERSION = 3

# A standard title never spans more than a handful of OCR rows; joining all
# rows into one string is what makes wrapped titles (`《建筑地基基` + `础设计
# 规范》`) visible.  Whitespace is dropped for the same reason.
TITLE_RE = re.compile(r"[《〈]([^》〉]{2,40})[》〉]")
CODE_RE = re.compile(
    r"(?<![A-Z])([A-Z]{2,4}(?:/[A-Z])?(?:\d{2,6})(?:[-—–]\d{2,4})?)"
)
CLAUSE_TOKEN = re.compile(
    r"(?:第)?(\d{1,3}(?:\.\d{1,3}){1,3})(?:条|款)?"
    r"|(附录\s*[A-Z](?:\.\d{1,3}){0,3})"
)
# Between two clause tokens only separators and these filler words are allowed.
GAP_RE = re.compile(r"(?:[\s、，,；;：:及和至~～\-—–]|条文说明|第\d+款)*")
LEAD_GAP_RE = re.compile(r"[\s、，,；;：:]*")
# The handbooks are cited by page: `《工程地质手册》第五版p294页` or
# `《…规范》p81页第14.7.4.5条` -- skip the page part, keep the clause.
PAGE_RE = re.compile(r"(?:第[一二三四五六七八九十\d]+版)?[pP]\s*\d+\s*页(?:[，,；;、]?第)?")

COMMENT_MARKERS = ("【华山论剑培训点评】", "【点评】", "【解析】", "【解答】")
CLAUSE_SPLIT = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩；;。]")

# Caution words only.  Plain `计算` is everywhere in a solution, so the calc
# rules stay narrow; `单位` is dropped because question text says `尺寸单位`.
CAUTION_RULES = (
    ("misread", (
        "注意", "仔细", "看清", "审题", "题意", "忽略", "漏掉", "漏算",
        "误用", "误解", "误判", "看错", "陷阱", "容易错", "别忘了",
    )),
    ("calc", (
        "计算量", "细心计算", "换算", "代入", "四舍五入", "精度", "算错",
        "粗心", "取小值计算", "计算过程",
    )),
    ("concept", ("概念", "定义", "原理", "本质", "含义")),
)

# Tie-break order: an explicit caution note beats a citation, because every
# solution cites something but only a minority carries a warning.
PRIORITY = {"misread": 0, "calc": 1, "concept": 2, "standard": 3}
MAX_CANDIDATES = 3
MAX_EVIDENCE = 2


def join_lines(lines):
    """OCR rows -> one string without whitespace."""
    return re.sub(r"\s+", "", "".join(str(line or "") for line in lines))


def _clauses_after(window):
    """Clause tokens that directly follow a title (or its standard code)."""
    opener = re.match(r"[\s（(【\[]*", window)
    pos = opener.end()
    code_match = CODE_RE.match(window[pos:])
    if code_match:
        pos += code_match.end()
        closer = re.match(r"[）)】\]]*", window[pos:])
        pos += closer.end()
    lead = LEAD_GAP_RE.match(window[pos:])
    pos += lead.end()
    page = PAGE_RE.match(window[pos:])
    if page:
        pos += page.end()
    clauses = []
    merge_next = False
    while len(clauses) < 8:
        rest = window[pos:]
        token = CLAUSE_TOKEN.match(rest)
        if not token:
            break
        value = re.sub(r"\s+", "", token.group(1) or token.group(2))
        pos += token.end()
        if merge_next and clauses:
            # `第14.2.2~14.2.4条` is one range, not two separate clauses.
            clauses[-1] = clauses[-1] + "~" + value
        else:
            clauses.append(value)
        gap = GAP_RE.match(window[pos:])
        pos += gap.end()
        merge_next = "~" in gap.group(0) or "至" in gap.group(0)
        # A token must be followed by a separator/filler word, otherwise the
        # next number belongs to prose (e.g. `3.95m`), not to this citation.
        if not gap.group(0):
            break
    return clauses


def extract_citations(text):
    """Return unique《规范》citations with optional code and clause numbers."""
    found = {}
    order = []
    for match in TITLE_RE.finditer(text):
        title = match.group(1).strip()
        if not title:
            continue
        window = text[match.end():match.end() + 90]
        code = ""
        code_match = CODE_RE.match(re.sub(r"^[\s（(【\[]+", "", window))
        if code_match:
            code = code_match.group(1)
        clauses = _clauses_after(window)
        # The same standard is often cited twice: once code-only in the stem,
        # once clause-only in the 点评.  Merge on the title.
        key = title
        if key not in found:
            found[key] = {
                "title": title,
                "code": "",
                "clauses": [],
                "raw": text[match.start():match.end()],
            }
            order.append(key)
        entry = found[key]
        if code and not entry["code"]:
            entry["code"] = code
        for clause in clauses:
            if clause not in entry["clauses"]:
                entry["clauses"].append(clause)
    citations = [found[key] for key in order]
    # Strongest first: explicit clause/code beats a bare title mention.
    citations.sort(key=lambda c: (not (c["code"] or c["clauses"]),))
    return citations


def citation_label(citation):
    """`《规范名》GB50007-2011 第5.2.6条、附录J` for display and evidence."""
    parts = ["《%s》" % citation["title"]]
    if citation["code"]:
        parts.append(citation["code"])
    clauses = citation["clauses"]
    if clauses:
        numbers = [c for c in clauses if not c.startswith("附录")]
        appendices = [c for c in clauses if c.startswith("附录")]
        if numbers:
            parts.append("第%s条" % "、".join(numbers))
        parts.extend(appendices)
    return " ".join(parts)


def commentary(text):
    """The note part of a solution: 点评 if present, else after 解析/解答."""
    positions = [(text.rfind(marker), marker) for marker in COMMENT_MARKERS]
    positions = [(pos, marker) for pos, marker in positions if pos >= 0]
    if not positions:
        return text
    pos, marker = max(positions, key=lambda item: item[0])
    if "点评" in marker:
        return text[pos + len(marker):]
    # `【解析】`/`【解答】` start the whole working; keep only its tail where
    # the hand-written notes live, so formulas do not trigger calc rules.
    body = text[pos + len(marker):]
    return body[int(len(body) * 0.55):]


def _snippets(text, words, limit=MAX_EVIDENCE):
    """Clauses containing any caution word, in reading order."""
    picked = []
    for clause in CLAUSE_SPLIT.split(text):
        clause = clause.strip(" ，,、：:")
        if len(clause) < 4 or len(clause) > 90:
            continue
        if not any(word in clause for word in words):
            continue
        if clause in picked:
            continue
        picked.append(clause)
        if len(picked) >= limit:
            break
    return picked


def suggest_tags(text, citations):
    """Evidence-backed candidates among the four error tags.

    `standard` is backed by the extracted citations; the other three need an
    explicit caution sentence, so silence means "no suggestion", never a guess.
    """
    candidates = []
    notes = commentary(text)
    for tag, words in CAUTION_RULES:
        snippets = _snippets(notes, words)
        if not snippets:
            continue
        reason = {
            "misread": "点评提示：%s" % snippets[0],
            "calc": "点评提到计算细节：%s" % snippets[0],
            "concept": "解析强调：%s" % snippets[0],
        }[tag]
        candidates.append({
            "tag": tag,
            "label": TAGS[tag],
            "reason": reason,
            "evidence": snippets,
            # A caution sentence is stronger evidence than one more citation.
            "score": 2 + len(snippets),
        })

    # Nearly every solution cites one standard, so a lone citation says little
    # about *why* this question was missed.  Offer `standard` when two or more
    # standards are cited (cross-standard questions are a real trap), or when
    # the citation is the only evidence at all.
    # A bare《标准名》mention ("未指定《土工试验方法标准》时…") is a remark,
    # not a basis; only citations that carry a code or a clause count here.
    strong = [c for c in citations if c["code"] or c["clauses"]]
    if strong and (len(strong) >= 2 or not candidates):
        evidence = [citation_label(c) for c in strong[:MAX_EVIDENCE]]
        count = len(strong)
        reason = "解析引用了 %d 部规范/标准" % count if count > 1 \
            else "解析依据 %s" % evidence[0]
        candidates.append({
            "tag": "standard",
            "label": TAGS["standard"],
            "reason": reason,
            "evidence": evidence,
            "score": min(3, count),
        })

    candidates.sort(key=lambda c: (-c["score"], PRIORITY[c["tag"]]))
    return candidates[:MAX_CANDIDATES]


def analyze_lines(lines):
    text = join_lines(lines)
    citations = extract_citations(text)
    return {
        "citations": citations,
        "candidates": suggest_tags(text, citations),
        "chars": len(text),
    }


# --- OCR + disk cache ------------------------------------------------------

def ocr_lines(path, engine=None):
    """OCR one answer crop; returns (lines, seconds)."""
    import numpy as np
    from PIL import Image

    started = time.time()
    image = Image.open(path).convert("RGB")
    array = np.ascontiguousarray(np.array(image)[:, :, ::-1])
    if engine is None:
        from rapidocr import RapidOCR

        engine = RapidOCR(
            params={"Global.log_level": "warning", "Rec.rec_batch_num": 32}
        )
    result = engine(array)
    lines = list(result.txts) if getattr(result, "txts", None) is not None else []
    return lines, time.time() - started


def _signature(path):
    stat = os.stat(path)
    return {"path": str(path), "size": stat.st_size, "mtime": int(stat.st_mtime)}


def cache_file(cache_dir, qid):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", qid)
    return os.path.join(str(cache_dir), safe + ".json")


def analyze_crop(qid, path, cache_dir, engine=None):
    """OCR one crop, using `.cache/answer_ocr/<qid>.json` when still fresh.

    Returns the analysis plus `cached` and `seconds`.  A stale cache (crop
    rebuilt, module version bumped) is silently replaced.
    """
    signature = _signature(path)
    cached_path = cache_file(cache_dir, qid)
    try:
        with open(cached_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("version") == CACHE_VERSION \
                and payload.get("source") == signature:
            result = dict(payload["result"])
            result.update({"cached": True, "seconds": 0.0})
            return result
    except (OSError, ValueError, KeyError):
        pass

    lines, seconds = ocr_lines(path, engine=engine)
    result = analyze_lines(lines)
    os.makedirs(str(cache_dir), exist_ok=True)
    tmp = cached_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": CACHE_VERSION,
                "source": signature,
                "result": result,
                "lines": lines,
            },
            fh,
            ensure_ascii=False,
        )
    os.replace(tmp, cached_path)
    out = dict(result)
    out.update({"cached": False, "seconds": round(seconds, 2)})
    return out
