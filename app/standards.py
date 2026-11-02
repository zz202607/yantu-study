"""Standards and handbook linkage.

Two read-only bridges from a question to the source material:

    categories -> core/companion standard PDFs in the local 2024-outline library
    handbook   -> full-text search + page rendering of the one book that has a
                  text layer (工程地质手册 第五版)

The standards library is outside the repo (`E:` by default) and is never
copied or modified.  Files are served by basename only, after resolving the
path back into that directory, so a crafted name cannot escape it.
"""

import json
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

try:  # PyMuPDF 1.24+ exposes the modern package name
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older environments
    import fitz

from app.curriculum import category_rows

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STANDARDS_DIR = Path(
    r"E:\其他文件\2025YT\3注册岩土全套电子版规范（2024大纲）"
)
STANDARDS_DIR = Path(os.environ.get("YANTU_STANDARDS_DIR", DEFAULT_STANDARDS_DIR))
HANDBOOK = Path(os.environ.get("YANTU_HANDBOOK", ROOT / "工程地质手册（第五版）.pdf"))
CACHE = ROOT / ".cache"
INDEX_PATH = CACHE / "handbook_index.json"
PAGE_CACHE = CACHE / "handbook_pages"
STD_INDEX_PATH = ROOT / "index" / "standards_index.json"
STD_TEXT_CACHE = CACHE / "standards_text"
STD_PAGE_CACHE = CACHE / "standards_pages"
_STD_INDEX_CACHE = {"stamp": None, "data": None}
_STD_INDEX_LOCK = threading.Lock()

ALLOWED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
_LIBRARY_CACHE = {"stamp": 0.0, "dir": None, "data": None}
_LIBRARY_LOCK = threading.Lock()
_INDEX_LOCK = threading.Lock()
_PAGE_LOCK = threading.Lock()
_INDEX_CACHE = {"stamp": 0.0, "data": None}

_PREFIX_RE = re.compile(r"^\s*\d+\s*[.、．]*\s*")
_CODE_RE = re.compile(
    r"((?:GB|JGJ|JTG|JTS|TB|NB|SL|DL|CJJ|JC|YS|HG|SH|CECS)\s*(?:[/∕／]?T)?"
    r"\s*\d+(?:\.\d+)?\s*[-—–]\s*\d{4}(?:\s*\(\s*\d{4}\s*年版?\s*\))?)",
    re.IGNORECASE,
)
_CODE_KEY_RE = re.compile(r"[^A-Z0-9/-]")


# ---------------------------------------------------------------- library

def code_key(code):
    """Normalise GB 50007-2011, GB/T 50942-2014 and JGJT 87-2012 variants."""
    text = (code or "").upper()
    text = text.replace("∕", "/").replace("／", "/")
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"\(\s*\d{4}\s*年版?\s*\)", "", text)
    text = text.replace("/T", "T")
    text = _CODE_KEY_RE.sub("", text)
    return text


def title_key(title):
    return re.sub(r"[\s《》（）()【】\[\]·—–_\-]", "", title or "").upper()


def _decode_name(name):
    stem = Path(name).stem
    stem = _PREFIX_RE.sub("", stem)
    match = _CODE_RE.search(stem)
    code = match.group(1) if match else ""
    title = stem.replace(code, " ") if code else stem
    title = re.sub(r"[（(][^）)]*[）)]", " ", title)
    title = re.sub(r"[《》（）()【】\[\]]", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" ._-")
    return {"code": code.strip(), "title": title or stem}


def load_library(directory=None, force=False):
    """List PDFs/images in the standards folder; cached for a minute."""
    folder = Path(directory or STANDARDS_DIR)
    now = time.time()
    with _LIBRARY_LOCK:
        cached = _LIBRARY_CACHE
        if (not force and cached["data"] is not None
                and cached["dir"] == str(folder)
                and now - cached["stamp"] < 60):
            return cached["data"]
        if not folder.exists():
            data = {
                "enabled": False,
                "dir": str(folder),
                "count": 0,
                "files": [],
                "error": "规范库目录不在：%s" % folder,
            }
        else:
            files = []
            try:
                names = sorted(folder.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                names = []
                data = {
                    "enabled": False,
                    "dir": str(folder),
                    "count": 0,
                    "files": [],
                    "error": "无法读取规范库：%s" % exc,
                }
                cached.update(stamp=now, dir=str(folder), data=data)
                return data
            for path in names:
                if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                parsed = _decode_name(path.name)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                files.append({
                    "name": path.name,
                    "title": parsed["title"],
                    "code": parsed["code"],
                    "ext": path.suffix.lower(),
                    "size": size,
                })
            data = {
                "enabled": True,
                "dir": str(folder),
                "count": len(files),
                "files": files,
                "error": None,
            }
        cached.update(stamp=now, dir=str(folder), data=data)
        return data


def _file_url(name):
    from urllib.parse import quote
    return "/api/standards/file?name=" + quote(name)


def match_standard(title, code, files=None, directory=None):
    """Find the local PDF for a citation, by code first and title second."""
    if files is None:
        files = load_library(directory)["files"]
    if not files:
        return None
    wanted = code_key(code)
    by_code = {}
    for entry in files:
        if entry["code"]:
            by_code.setdefault(code_key(entry["code"]), entry)
    if wanted:
        if wanted in by_code:
            found = by_code[wanted]
            return {**found, "url": _file_url(found["name"])}
        # OCR sometimes drops the year or trailing digits; accept a unique
        # prefix of at least six characters rather than guessing among many.
        if len(wanted) >= 6:
            candidates = [
                entry for key, entry in by_code.items()
                if key.startswith(wanted) or wanted.startswith(key)
            ]
            if len(candidates) == 1:
                found = candidates[0]
                return {**found, "url": _file_url(found["name"])}
    wanted_title = title_key(title)
    if len(wanted_title) < 4:
        return None
    scored = []
    for entry in files:
        candidate = title_key(entry["title"])
        if len(candidate) < 4:
            continue
        if wanted_title in candidate or candidate in wanted_title:
            scored.append((min(len(wanted_title), len(candidate)), entry))
    if not scored:
        return None
    scored.sort(key=lambda row: -row[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous: do not open the wrong standard
    found = scored[0][1]
    return {**found, "url": _file_url(found["name"])}


def standard_map(directory=None):
    """Categories with core/companion entries enriched with local files."""
    library = load_library(directory)
    files = library["files"]
    rows = category_rows()
    for row in rows:
        for key in ("core", "companions"):
            enriched = []
            for item in row[key]:
                match = match_standard(item["title"], item["code"], files)
                enriched.append({**item, "file": match})
            row[key] = enriched
    return {"categories": rows, "library": library}


def file_path(name, directory=None):
    """Resolve one library file, refusing traversal and unknown names."""
    folder = Path(directory or STANDARDS_DIR)
    base = Path(name or "")
    if not name or base.name != name or base.suffix.lower() not in ALLOWED_SUFFIXES:
        return None
    try:
        root = folder.resolve()
        target = (root / name).resolve()
    except OSError:
        return None
    if root not in target.parents:
        return None
    if not target.is_file():
        return None
    return target


# ---------------------------------------------------------------- handbook

def _index_is_fresh(index_path, handbook):
    if not index_path.exists() or not handbook.exists():
        return False
    try:
        return index_path.stat().st_mtime >= handbook.stat().st_mtime
    except OSError:
        return False


def build_index(handbook=None, index_path=None, force=False):
    """Extract page text and the bookmark tree once; store as one JSON."""
    handbook = Path(handbook or HANDBOOK)
    index_path = Path(index_path or INDEX_PATH)
    if not handbook.exists():
        raise FileNotFoundError("手册文件不在：%s" % handbook)
    if not force and _index_is_fresh(index_path, handbook):
        return load_index(index_path)
    with _INDEX_LOCK:
        if not force and _index_is_fresh(index_path, handbook):
            return load_index(index_path)
        doc = fitz.open(str(handbook))
        try:
            pages = []
            for page in doc:
                text = page.get_text("text")
                text = re.sub(r"[ \t\u3000]+", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                pages.append(text)
            toc = [
                {"level": int(level), "title": str(title), "page": int(page)}
                for level, title, page in doc.get_toc(simple=True)
                if int(page) >= 1
            ]
            page_count = doc.page_count
        finally:
            doc.close()
        data = {
            "page_count": page_count,
            "toc": toc,
            "pages": pages,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = index_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, index_path)
        return data


def load_index(index_path=None):
    index_path = Path(index_path or INDEX_PATH)
    now = time.time()
    if (_INDEX_CACHE["data"] is not None
            and now - _INDEX_CACHE["stamp"] < 30
            and _INDEX_CACHE.get("path") == str(index_path)):
        return _INDEX_CACHE["data"]
    with open(index_path, encoding="utf-8") as fh:
        data = json.load(fh)
    _INDEX_CACHE.update(stamp=now, data=data, path=str(index_path))
    return data


def ensure_index(handbook=None, index_path=None):
    index_path = Path(index_path or INDEX_PATH)
    handbook = Path(handbook or HANDBOOK)
    if _index_is_fresh(index_path, handbook):
        return load_index(index_path)
    return build_index(handbook, index_path)


def _bookmark_map(toc, page_count):
    """page -> the last bookmark at or before that page."""
    marks = {}
    current = None
    by_page = {}
    for entry in sorted(toc, key=lambda row: (row["page"], row["level"])):
        if 1 <= entry["page"] <= page_count:
            by_page.setdefault(entry["page"], []).append(entry)
    for page in range(1, page_count + 1):
        if page in by_page:
            current = by_page[page][-1]
        marks[page] = current
    return marks


def _snippet(text, position, width_before=60, width_after=110):
    start = max(0, position - width_before)
    end = min(len(text), position + width_after)
    piece = re.sub(r"\s+", " ", text[start:end]).strip()
    return ("…" if start else "") + piece + ("…" if end < len(text) else "")


def search(query, limit=20, handbook=None, index_path=None):
    """Substring search over the OCR text layer, top pages first.

    Every whitespace-separated token must appear on the page.  Bookmarks are
    matched separately and ranked first, so a chapter name jump works even
    when its page text is noisy.
    """
    query = (query or "").strip()
    result = {"query": query, "tokens": [], "total": 0, "results": [],
              "index_ready": False}
    if not query:
        return result
    index = ensure_index(handbook, index_path)
    result["index_ready"] = True
    tokens = [token.lower() for token in re.split(r"\s+", query) if token]
    result["tokens"] = tokens
    page_count = index["page_count"]
    marks = _bookmark_map(index["toc"], page_count)
    hits = {}
    for entry in index["toc"]:
        if not 1 <= entry["page"] <= page_count:
            continue
        title = entry["title"]
        low = title.lower()
        if all(token in low for token in tokens):
            hits[entry["page"]] = {
                "page": entry["page"],
                "kind": "toc",
                "title": title,
                "bookmark": {"title": title, "page": entry["page"],
                             "level": entry["level"]},
                "snippet": title,
                "score": 10000 - entry["level"] * 10 + len(title),
            }
    for number, text in enumerate(index["pages"], start=1):
        if not text:
            continue
        low = text.lower()
        positions = []
        ok = True
        total = 0
        for token in tokens:
            position = low.find(token)
            if position < 0:
                ok = False
                break
            positions.append(position)
            total += low.count(token)
        if not ok:
            continue
        if number in hits:
            hits[number]["score"] += total
            continue
        first = min(positions)
        hits[number] = {
            "page": number,
            "kind": "page",
            "title": "",
            "bookmark": marks.get(number),
            "snippet": _snippet(text, first),
            "score": total * 10 - first / 10000.0,
        }
    rows = sorted(hits.values(), key=lambda row: (-row["score"], row["page"]))
    result["total"] = len(rows)
    result["results"] = rows[:max(1, min(int(limit or 20), 50))]
    return result


def page_image(page, dpi=110, handbook=None, cache_dir=None):
    """Render one handbook page to PNG, cached on disk."""
    handbook = Path(handbook or HANDBOOK)
    cache_dir = Path(cache_dir or PAGE_CACHE)
    if not handbook.exists():
        raise FileNotFoundError("手册文件不在：%s" % handbook)
    doc = fitz.open(str(handbook))
    try:
        page_count = doc.page_count
        number = int(page)
        if not 1 <= number <= page_count:
            raise ValueError("页码应在 1–%d 之间" % page_count)
        dpi = max(72, min(int(dpi or 110), 160))
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / ("%04d_%03d.png" % (number, dpi))
        if not path.exists():
            with _PAGE_LOCK:
                if not path.exists():
                    zoom = dpi / 72
                    pix = doc[number - 1].get_pixmap(
                        matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB
                    )
                    pix.save(str(path))
        return path, page_count
    finally:
        doc.close()


def meta(handbook=None, index_path=None):
    handbook = Path(handbook or HANDBOOK)
    index_path = Path(index_path or INDEX_PATH)
    info = {
        "file": str(handbook),
        "exists": handbook.exists(),
        "index_ready": _index_is_fresh(index_path, handbook),
        "page_count": None,
        "toc_count": None,
        "error": None,
    }
    if not handbook.exists():
        info["error"] = "手册文件不在：%s" % handbook
        return info
    if info["index_ready"]:
        try:
            index = load_index(index_path)
            info["page_count"] = index["page_count"]
            info["toc_count"] = len(index["toc"])
        except (OSError, ValueError) as exc:
            info["error"] = "手册索引读取失败：%s" % exc
    else:
        try:
            doc = fitz.open(str(handbook))
            try:
                info["page_count"] = doc.page_count
                info["toc_count"] = len(doc.get_toc(simple=True))
            finally:
                doc.close()
        except Exception as exc:  # noqa: BLE001 - metadata is a nicety
            info["error"] = "手册读取失败：%s" % exc
    return info


def toc(index_path=None):
    index = ensure_index(index_path=index_path)
    return {"toc": index["toc"], "page_count": index["page_count"]}


# ---------------------------------------------------------------- chapter index

def load_standard_index(index_path=None):
    """Load index/standards_index.json; cached by mtime."""
    path = Path(index_path or STD_INDEX_PATH)
    if not path.exists():
        return {"standards": [], "built_at": None, "folder": None}
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = None
    if _STD_INDEX_CACHE["data"] is not None and _STD_INDEX_CACHE["stamp"] == stamp:
        return _STD_INDEX_CACHE["data"]
    with _STD_INDEX_LOCK:
        if _STD_INDEX_CACHE["data"] is not None and _STD_INDEX_CACHE["stamp"] == stamp:
            return _STD_INDEX_CACHE["data"]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {"standards": [], "built_at": None, "folder": None}
        _STD_INDEX_CACHE.update(stamp=stamp, data=data)
        return data


def standard_summaries(index_path=None):
    """Light list for the standards selector (no chapter arrays)."""
    rows = []
    for row in load_standard_index(index_path).get("standards", []):
        rows.append({
            "name": row.get("name"),
            "code": row.get("code"),
            "code_key": row.get("code_key"),
            "title": row.get("title"),
            "page_count": row.get("page_count"),
            "source": row.get("source"),
            "confidence": row.get("confidence"),
            "has_text": row.get("has_text", False),
            "toc_count": len(row.get("toc") or []),
        })
    rows.sort(key=lambda row: (
        0 if (row.get("code_key") or "").startswith(("GB50007", "GB50021",
                                                    "JGJ94", "JGJ79",
                                                    "GB50330", "JGJ120",
                                                    "GB50011")) else 1,
        row.get("code_key") or "",
    ))
    return rows


def _find_standard(code, index_path=None):
    wanted = code_key(code)
    rows = load_standard_index(index_path).get("standards", [])
    if wanted:
        for row in rows:
            if row.get("code_key") == wanted or code_key(row.get("code")) == wanted:
                return row
        for row in rows:
            key = row.get("code_key") or code_key(row.get("code"))
            if key and (key.startswith(wanted) or wanted.startswith(key)):
                return row
    text = title_key(code)
    if len(text) >= 4:
        for row in rows:
            if text in title_key(row.get("title")):
                return row
    return None


def normalize_clause(value):
    """'第5.2.4条' -> '5.2.4'; '附录A.0.1' -> '附录A'."""
    text = str(value or "").replace("．", ".").replace("　", " ").strip()
    appendix = re.search(r"附录\s*([A-Z])", text, re.IGNORECASE)
    if appendix:
        return "附录" + appendix.group(1).upper()
    match = re.search(r"(\d+(?:\.\d+){0,4})", text)
    return match.group(1) if match else ""


def locate_clause(code, clause, index_path=None):
    """Find the section page for a clause number.

    A clause like 5.2.4 has no TOC entry of its own; it resolves to the
    longest available prefix (5.2, then 5).  The page is the start of that
    section, which is what a 30-second lookup needs.
    """
    standard = _find_standard(code, index_path)
    if not standard:
        return None
    number = normalize_clause(clause)
    if not number:
        return None
    entries = standard.get("toc") or []
    candidate = number
    while candidate:
        for entry in entries:
            if str(entry.get("number") or "") == candidate:
                return {
                    "code": standard.get("code"),
                    "name": standard.get("name"),
                    "title": standard.get("title"),
                    "clause": number,
                    "matched": candidate,
                    "section_title": entry.get("title"),
                    "printed_page": entry.get("printed_page"),
                    "pdf_page": entry.get("pdf_page"),
                    "estimated": bool(entry.get("estimated")),
                    "page_count": standard.get("page_count"),
                    "source": standard.get("source"),
                }
        if "." in candidate:
            candidate = candidate.rsplit(".", 1)[0]
        else:
            break
    return None


def standard_toc(code, index_path=None):
    standard = _find_standard(code, index_path)
    if not standard:
        return None
    return {
        "code": standard.get("code"),
        "name": standard.get("name"),
        "title": standard.get("title"),
        "page_count": standard.get("page_count"),
        "source": standard.get("source"),
        "confidence": standard.get("confidence"),
        "offset": standard.get("offset"),
        "has_text": standard.get("has_text", False),
        "toc": standard.get("toc") or [],
    }


def search_standard(code, query, limit=20, index_path=None, text_cache=None):
    """Search a text-layer standard; scanned standards return available=False."""
    standard = _find_standard(code, index_path)
    if not standard:
        return {"available": False, "results": [], "total": 0}
    key = standard.get("code_key") or code_key(standard.get("code"))
    cache = Path(text_cache or STD_TEXT_CACHE) / ("%s.json" % key)
    if not cache.exists():
        return {"available": False, "results": [], "total": 0}
    try:
        with open(cache, encoding="utf-8") as fh:
            pages = json.load(fh).get("pages", [])
    except (OSError, ValueError):
        return {"available": False, "results": [], "total": 0}
    tokens = [part.lower() for part in re.split(r"\s+", (query or "").strip()) if part]
    if not tokens:
        return {"available": True, "results": [], "total": 0}
    hits = []
    for number, text in enumerate(pages, start=1):
        low = text.lower()
        positions = []
        ok = True
        count = 0
        for token in tokens:
            position = low.find(token)
            if position < 0:
                ok = False
                break
            positions.append(position)
            count += low.count(token)
        if not ok:
            continue
        first = min(positions)
        start = max(0, first - 50)
        end = min(len(text), first + 110)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        hits.append({
            "page": number,
            "snippet": ("…" if start else "") + snippet
                       + ("…" if end < len(text) else ""),
            "score": count,
        })
    hits.sort(key=lambda row: (-row["score"], row["page"]))
    return {"available": True, "total": len(hits),
            "results": hits[:max(1, min(int(limit or 20), 50))],
            "code": standard.get("code")}


def standard_page(code, page, dpi=110, index_path=None, library_dir=None,
                  cache_dir=None):
    """Render one standard page to PNG for the in-app viewer."""
    standard = _find_standard(code, index_path)
    if not standard:
        raise FileNotFoundError("规范索引里没有：%s" % code)
    path = file_path(standard.get("name"), library_dir)
    if not path:
        raise FileNotFoundError("规范文件不在：%s" % standard.get("name"))
    cache = Path(cache_dir or STD_PAGE_CACHE)
    doc = fitz.open(str(path))
    try:
        page_count = doc.page_count
        number = int(page)
        if not 1 <= number <= page_count:
            raise ValueError("页码应在 1–%d 之间" % page_count)
        dpi = max(72, min(int(dpi or 110), 160))
        cache.mkdir(parents=True, exist_ok=True)
        key = standard.get("code_key") or code_key(standard.get("code"))
        out = cache / ("%s_%04d_%03d.png" % (key, number, dpi))
        if not out.exists():
            zoom = dpi / 72
            pix = doc[number - 1].get_pixmap(
                matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB
            )
            pix.save(str(out))
        return out, page_count, standard
    finally:
        doc.close()
