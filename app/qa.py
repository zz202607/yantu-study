"""Ask AI about one question, grounded in its solution and local clauses.

The question and its official solution are OCR'd locally (the scans have no
text layer), the matched clauses are read from the local standard PDFs, and
only those excerpts plus the student's own question leave the machine.  The
API key lives in `data/llm.json`, never in the repo and never in a backup.

The model is a plain OpenAI-compatible chat endpoint.  DeepSeek is the
default; pointing `base_url`/`model` elsewhere is enough for another vendor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from app import answer_ocr, standards

ROOT = Path(__file__).resolve().parent.parent
CONTEXT_VERSION = 1
PROMPT_VERSION = 2
CACHE_TTL_DAYS = 30
MAX_QUESTION = 500
MAX_HINTS = 5
MAX_CLAUSE_CHARS = 1600
MAX_TOTAL_CLAUSE_CHARS = 6400
DEFAULT_LIMIT = 20
DEFAULT_MAX_TOKENS = 900
DEFAULT_TIMEOUT = 90
MAX_HISTORY = 6

SYSTEM_PROMPT = """你是注册岩土工程师案例考试的辅导老师。学生会给你一道题的题干、官方解析、本机规范条文摘录，以及他自己的疑问。

回答规则：
1. 先直接回答学生的疑点（2-4 句），不要复述整道题。
2. 再给依据：写清是"解析的哪一步"还是"《规范名》第X条"；只有材料里有的条文才写条号。
3. 再指出学生理解容易偏差的地方（如果问题暴露了误解）。
4. 最后用一两句回到本题的取法。
5. 题干和解析来自 OCR，可能有错字或公式识别错误；不要因为 OCR 噪声改变题目条件，也不要把没出现的数字当成已知。
6. 材料没有覆盖的内容，直接说"材料里没写"；如果必须用通用知识补充，先注明"通用解释（非本题依据）"。
7. 不重新判分，不因为选项字母改答案；案例题按过程讲。
8. 解析中已经写出的判断（例如"不满足承台下有1m非液化土要求"）是本题的既定取法；解释时直接采用，不要重新从 OCR 题干图推断层厚、尺寸或条件。若你的推断与解析冲突，以解析为准。
9. 引用材料中的条文时，优先使用材料列出的条号；补充的条号必须是真实存在的规范条文，不确定就不写条号。
10. 中文，简洁，尽量不超过 500 字；可以用短列表。"""


class QAError(RuntimeError):
    """A user-facing Q&A failure (config, network, quota, bad response)."""


class QANotConfigured(QAError):
    pass


class QALimit(QAError):
    pass


# --- configuration ---------------------------------------------------------

def load_config(data_dir):
    """Read data/llm.json with env overrides; the key never leaves the PC."""
    config = {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "",
        "daily_limit": DEFAULT_LIMIT,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "timeout": DEFAULT_TIMEOUT,
    }
    path = Path(data_dir) / "llm.json"
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise QAError("data/llm.json 读不了：%s" % exc)
        if isinstance(loaded, dict):
            config.update({k: v for k, v in loaded.items() if v is not None})
    for env, key in (
        ("YANTU_LLM_KEY", "api_key"),
        ("YANTU_LLM_BASE", "base_url"),
        ("YANTU_LLM_MODEL", "model"),
        ("YANTU_LLM_LIMIT", "daily_limit"),
    ):
        if os.environ.get(env):
            config[key] = os.environ[env]
    for key, fallback in (("daily_limit", DEFAULT_LIMIT),
                          ("max_tokens", DEFAULT_MAX_TOKENS),
                          ("timeout", DEFAULT_TIMEOUT)):
        try:
            config[key] = int(config.get(key) or fallback)
        except (TypeError, ValueError):
            config[key] = fallback
    config["daily_limit"] = max(1, min(config["daily_limit"], 100))
    config["max_tokens"] = max(200, min(config["max_tokens"], 2000))
    config["timeout"] = max(10, min(config["timeout"], 300))
    config["provider"] = str(config.get("provider") or "custom").strip()
    config["model"] = str(config.get("model") or "").strip()
    config["base_url"] = str(config.get("base_url") or "").strip()
    config["api_key"] = str(config.get("api_key") or "").strip()
    return config


def configured(config):
    return bool(config and config.get("api_key") and config.get("model"))


def public_config(config):
    """Config safe to return to the phone: no key, no headers."""
    return {
        "provider": config.get("provider", ""),
        "model": config.get("model", ""),
        "daily_limit": int(config.get("daily_limit") or DEFAULT_LIMIT),
        "configured": configured(config),
    }


# --- small helpers ---------------------------------------------------------

def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "")) or "q"


def _file_signature(path):
    stat = os.stat(str(path))
    return {"size": stat.st_size, "mtime": int(stat.st_mtime)}


def _first_clause(value):
    text = str(value or "").strip()
    text = re.split(r"[~～至]", text)[0]
    return standards.normalize_clause(text)


def _ocr_text(path, engine=None, ocr_lock=None):
    if ocr_lock is None:
        lines, seconds = answer_ocr.ocr_lines(str(path), engine=engine)
    else:
        with ocr_lock:
            lines, seconds = answer_ocr.ocr_lines(str(path), engine=engine)
    return answer_ocr.join_lines(lines), seconds


def _clean_answer(text):
    return re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", str(text or "")).strip()


# --- clause text -----------------------------------------------------------

def clause_text(code, clause, max_pages=2, max_chars=MAX_CLAUSE_CHARS):
    """Locate one clause and return its text plus page metadata.

    `locate_clause` deliberately resolves to the section start (a 30-second
    lookup).  For Q&A the exact clause matters, so search the text layer
    forward from that page for the clause number and read from the hit page.
    Scanned standards return an empty text and keep the page reference.
    """
    loc = standards.locate_clause(code, clause)
    if not loc:
        return None
    row = dict(loc)
    row["clause"] = standards.normalize_clause(clause)
    row["text"] = ""
    row["exact"] = False
    path = standards.file_path(loc.get("name"))
    if not path:
        return row
    try:
        import fitz
    except ImportError:  # pragma: no cover - pymupdf is a hard requirement
        return row
    try:
        doc = fitz.open(str(path))
    except Exception:  # noqa: BLE001 - a broken PDF must not break Q&A
        return row
    try:
        page_no = max(1, int(loc.get("pdf_page") or 1))
        wanted = _first_clause(clause)
        if wanted:
            pattern = re.compile(r"(?<!\d)" + re.escape(wanted) + r"(?!\d)")
            for number in range(page_no, min(doc.page_count, page_no + 30) + 1):
                text = doc[number - 1].get_text()
                if text and pattern.search(text):
                    page_no = number
                    row["exact"] = True
                    break
        chunks = []
        for number in range(page_no, min(doc.page_count, page_no + max_pages - 1) + 1):
            chunks.append(doc[number - 1].get_text())
        text = "\n".join(chunks)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        row["pdf_page"] = page_no
        row["text"] = text[:max_chars]
        return row
    finally:
        doc.close()


def _merge_hints(hints, answer_text):
    """Add exact citations found in the OCR'd solution to the M4 hints."""
    rows = [dict(row) for row in (hints or []) if row.get("code")]
    seen = set()
    for row in rows:
        for clause in row.get("clauses") or ():
            seen.add((standards.code_key(row["code"]),
                      standards.normalize_clause(clause)))
    extra = 0
    try:
        citations = answer_ocr.extract_citations(answer_text or "")
    except Exception:  # noqa: BLE001 - hints must survive OCR noise
        citations = []
    for cite in citations:
        if extra >= 3:
            break
        std = standards.match_standard(cite.get("title"), cite.get("code"))
        if not std:
            continue
        for raw_clause in (cite.get("clauses") or ())[:6]:
            clause = standards.normalize_clause(raw_clause)
            key = (standards.code_key(std.get("code")), clause)
            if not clause or key in seen:
                continue
            loc = standards.locate_clause(std.get("code"), clause)
            if not loc:
                continue
            seen.add(key)
            rows.append({
                "code": std.get("code"),
                "title": std.get("title"),
                "code_key": standards.code_key(std.get("code")),
                "section": loc.get("matched"),
                "section_title": loc.get("section_title"),
                "clauses": [clause],
                "pdf_page": loc.get("pdf_page"),
                "printed_page": loc.get("printed_page"),
                "page_count": loc.get("page_count"),
                "estimated": loc.get("estimated", False),
                "source": "answer",
                "source_label": "解析依据",
                "confidence": "high",
                "reason": "解析直接引用 %s" % clause,
            })
            extra += 1
    rows.sort(key=lambda row: (
        0 if row.get("source") == "answer" else 1,
        0 if row.get("confidence") == "high" else 1,
    ))
    return rows[:MAX_HINTS]


def _clause_rows(hints):
    """Extract text for the first clauses of each hint, one row per page.

    Several clauses often sit on the same page; sending that page twice only
    burns tokens, so clause numbers are merged and the text is sent once.
    """
    rows, by_page = [], {}
    total = 0
    for hint in hints:
        for raw in (hint.get("clauses") or [])[:2]:
            clause = standards.normalize_clause(raw)
            if not clause:
                continue
            detail = clause_text(hint.get("code"), clause)
            if not detail:
                continue
            page = detail.get("pdf_page")
            code = detail.get("code") or hint.get("code")
            key = (detail.get("code_key") or standards.code_key(code), page)
            if key in by_page:
                row = by_page[key]
                if clause not in row["clauses"]:
                    row["clauses"].append(clause)
                continue
            text = detail.get("text") or ""
            if total + len(text) > MAX_TOTAL_CLAUSE_CHARS:
                text = text[:max(0, MAX_TOTAL_CLAUSE_CHARS - total)]
            total += len(text)
            row = {
                "code": code,
                "title": detail.get("title") or hint.get("title"),
                "clauses": [clause],
                "section": detail.get("matched") or hint.get("section"),
                "section_title": detail.get("section_title")
                                 or hint.get("section_title"),
                "pdf_page": page,
                "printed_page": detail.get("printed_page"),
                "exact": bool(detail.get("exact")),
                "text": text,
            }
            by_page[key] = row
            rows.append(row)
            if total >= MAX_TOTAL_CLAUSE_CHARS:
                return rows
    return rows


# --- context ---------------------------------------------------------------

def build_context(qid, question_path, answer_path, engine=None, ocr_lock=None,
                  cache_dir=None, hints=None, ocr_fn=None):
    """OCR the question + solution and attach the matched clause text.

    Cached under `.cache/qa/<qid>.json`, keyed by crop size/mtime and the
    hint list.  `ocr_fn(path)->str` is injectable for tests.
    """
    qid = str(qid or "").strip().upper()
    cache_dir = Path(cache_dir or (ROOT / ".cache" / "qa"))
    if hints is None:
        try:
            from app import clauses
            hints = clauses.hints(qid).get("hints") or []
        except Exception:  # noqa: BLE001 - clause hints are a bonus
            hints = []
    signature = {
        "version": CONTEXT_VERSION,
        "q": _file_signature(question_path),
        "a": _file_signature(answer_path),
        "hints": _hash(hints),
    }
    cache = cache_dir / (_safe_name(qid) + ".json")
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if payload and payload.get("signature") == signature:
            return payload.get("context")

    started = time.time()
    if ocr_fn is not None:
        question_text = str(ocr_fn(question_path) or "")
        answer_text = str(ocr_fn(answer_path) or "")
        seconds = 0.0
    else:
        question_text, q_seconds = _ocr_text(question_path, engine, ocr_lock)
        answer_text, a_seconds = _ocr_text(answer_path, engine, ocr_lock)
        seconds = round(q_seconds + a_seconds, 2)
    merged = _merge_hints(hints, answer_text)
    clauses_rows = _clause_rows(merged)
    context = {
        "qid": qid,
        "question": question_text,
        "answer": answer_text,
        "hints": merged,
        "clauses": clauses_rows,
        "ocr_seconds": seconds,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    context["hash"] = _hash({
        "qid": qid,
        "question": question_text,
        "answer": answer_text,
        "clauses": clauses_rows,
        "hints": merged,
    })
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(".tmp")
        temp.write_text(
            json.dumps({"signature": signature, "context": context},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        temp.replace(cache)
    except OSError:
        pass
    return context


def _context_text(context):
    parts = ["【题目】%s" % context.get("qid", "")]
    if context.get("question"):
        parts.append("【题干（本地 OCR，可能有错字）】\n" + context["question"])
    if context.get("answer"):
        parts.append("【官方解析（本地 OCR，可能有错字）】\n" + context["answer"])
    sources = context.get("hints") or []
    if sources:
        lines = ["【本题相关的规范位置】"]
        for row in sources:
            label = "《%s》%s" % (row.get("title", ""), row.get("code", ""))
            numbers = "、".join(row.get("clauses") or [])
            if numbers:
                label += " 第%s条" % numbers
            label += "（PDF p%s）" % row.get("pdf_page", "?")
            if row.get("source_label"):
                label += " · %s" % row["source_label"]
            lines.append(label)
        parts.append("\n".join(lines))
    clause_rows = [row for row in context.get("clauses") or [] if row.get("text")]
    if clause_rows:
        parts.append("【规范条文摘录（本机规范库）】")
        for row in clause_rows:
            head = "《%s》%s 第%s条（PDF p%s%s）" % (
                row.get("title", ""), row.get("code", ""),
                "、".join(row.get("clauses") or []), row.get("pdf_page", "?"),
                "" if row.get("exact") else "，条文原文可能不在本页",
            )
            parts.append(head + "\n" + row.get("text", ""))
    return "\n\n".join(parts)


def build_messages(context, question, history=None):
    messages = [{
        "role": "system",
        "content": SYSTEM_PROMPT + "\n\n以下是本题材料：\n\n" + _context_text(context),
    }]
    for row in (history or [])[-MAX_HISTORY:]:
        role = row.get("role")
        content = str(row.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:1500]})
    messages.append({"role": "user", "content": question})
    return messages


def answer_cache_key(context, question, model):
    """Same question + same source material + same model -> same answer.

    Conversation history is deliberately excluded: asking the same question
    again in the same thread deserves the same grounded answer, and a cache
    hit must stay free even after the daily quota is used up.
    """
    return _hash({
        "qid": context.get("qid"),
        "context": context.get("hash"),
        "question": question,
        "model": model,
        "prompt": PROMPT_VERSION,
    })


# --- model call ------------------------------------------------------------

def chat(config, messages):
    """One non-streaming OpenAI-compatible chat call -> (content, usage)."""
    if not configured(config):
        raise QANotConfigured("还没有配置 DeepSeek key（data/llm.json）")
    base = (config.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = {
        "model": config.get("model"),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": int(config.get("max_tokens") or DEFAULT_MAX_TOKENS),
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + config.get("api_key", ""),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
                request, timeout=config.get("timeout") or DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = _clean_answer(
            exc.read().decode("utf-8", "ignore")[:500]) if exc.fp else ""
        if exc.code == 401:
            raise QAError("DeepSeek 鉴权失败（401）；检查 data/llm.json 的 key")
        if exc.code == 402:
            raise QAError("DeepSeek 余额不足（402）；充值后再问")
        if exc.code == 429:
            raise QAError("DeepSeek 限流（429）；稍后再问")
        raise QAError("DeepSeek 返回 %s%s" % (
            exc.code, ("：" + detail) if detail else ""))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QAError("连不上 DeepSeek：%s" % exc)
    try:
        data = json.loads(raw)
    except ValueError:
        raise QAError("DeepSeek 返回的不是 JSON")
    choices = data.get("choices") or []
    content = ""
    if choices:
        content = str((choices[0].get("message") or {}).get("content") or "")
    content = content.strip()
    if not content:
        raise QAError("模型没返回内容；再试一次")
    usage = data.get("usage") or {}
    return content, {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


# --- citation verification -------------------------------------------------

_CITE_TITLE = re.compile(
    r"《([^》]{2,40})》\s*"
    r"(?:([A-Z]{2,4}\s?\d{2,6}(?:[-—–]\d{2,4})?)\s*)?"
    r"(?:第\s*)?(\d{1,3}(?:\.\d{1,3}){1,3})(?!\d)\s*条?"
)
_CITE_CODE = re.compile(
    r"([A-Z]{2,4}\s?\d{2,6}(?:[-—–]\d{2,4})?)\s*"
    r"(?:第\s*)?(\d{1,3}(?:\.\d{1,3}){1,3})(?!\d)\s*条?"
)


def verify_citations(answer, hints):
    """Resolve clause numbers in the answer against the local index.

    Returns (cited rows, warnings).  A resolved citation that was not among
    the provided hints is still real; the UI can label it as AI-supplied.
    Unresolved numbers are counted, never silently dropped.
    """
    known = set()
    for row in hints or []:
        for clause in row.get("clauses") or ():
            known.add((standards.code_key(row.get("code")),
                       standards.normalize_clause(clause)))
    found, seen, unresolved = [], set(), 0
    for match in _CITE_TITLE.finditer(answer or ""):
        title, code, clause = match.group(1), match.group(2), match.group(3)
        std = standards.match_standard(title, code)
        if not std:
            unresolved += 1
            continue
        loc = standards.locate_clause(std.get("code"), clause)
        key = (standards.code_key(std.get("code")),
               standards.normalize_clause(clause))
        if key in seen:
            continue
        seen.add(key)
        if not loc:
            unresolved += 1
            continue
        found.append({
            "code": std.get("code"),
            "title": std.get("title"),
            "clause": standards.normalize_clause(clause),
            "pdf_page": loc.get("pdf_page"),
            "page_count": loc.get("page_count"),
            "section_title": loc.get("section_title"),
            "in_hints": key in known,
        })
    for match in _CITE_CODE.finditer(answer or ""):
        code, clause = match.group(1), match.group(2)
        key = (standards.code_key(code), standards.normalize_clause(clause))
        if key in seen:
            continue
        loc = standards.locate_clause(code, clause)
        seen.add(key)
        if not loc:
            unresolved += 1
            continue
        found.append({
            "code": loc.get("code"),
            "title": loc.get("title"),
            "clause": standards.normalize_clause(clause),
            "pdf_page": loc.get("pdf_page"),
            "page_count": loc.get("page_count"),
            "section_title": loc.get("section_title"),
            "in_hints": key in known,
        })
    warnings = []
    if unresolved:
        warnings.append("%d 处条文引用未在本地规范索引中核对" % unresolved)
    return found[:8], warnings


# --- the ask flow ----------------------------------------------------------

def ask(store, qid, question, context, config, chat_fn=None, now=None):
    """Answer one question; stores the pair and enforces the daily quota."""
    qid = str(qid or "").strip().upper()
    question = str(question or "").strip()
    if not question:
        raise QAError("先写下你的问题")
    if len(question) > MAX_QUESTION:
        raise QAError("问题太长（上限 %d 字）" % MAX_QUESTION)
    if not configured(config):
        raise QANotConfigured("还没有配置 DeepSeek key（data/llm.json）")
    limit = int(config.get("daily_limit") or DEFAULT_LIMIT)
    history = store.qa_history(qid)
    key = answer_cache_key(context, question, config.get("model"))
    hit = store.qa_cached(key, days=CACHE_TTL_DAYS, now=now)
    if not hit:
        used = store.qa_calls_today(now=now)
        if used >= limit:
            raise QALimit("今天的 AI 提问额度已用完（%d 次）" % limit)
    if hit:
        thread = store.qa_add_exchange(
            qid, question, hit["content"], sources=hit.get("sources") or {},
            cache_key=key, cached=True,
            model=hit.get("model") or config.get("model"),
            prompt_tokens=0, completion_tokens=0, now=now,
        )
        sources = hit.get("sources") or {}
        return {
            "qid": qid,
            "answer": hit["content"],
            "cached": True,
            "sources": sources,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0},
            "quota": {"used": store.qa_calls_today(now=now), "limit": limit},
            "thread": thread,
        }

    messages = build_messages(context, question, history)
    content, usage = (chat_fn or chat)(config, messages)
    cited, warnings = verify_citations(content, context.get("hints") or [])
    sources = {
        "hints": context.get("hints") or [],
        "cited": cited,
        "warnings": warnings,
    }
    thread = store.qa_add_exchange(
        qid, question, content, sources=sources, cache_key=key,
        cached=False, model=config.get("model"),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0), now=now,
    )
    return {
        "qid": qid,
        "answer": content,
        "cached": False,
        "sources": sources,
        "usage": usage,
        "quota": {"used": store.qa_calls_today(now=now), "limit": limit},
        "thread": thread,
    }
