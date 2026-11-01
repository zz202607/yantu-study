"""Read-only question catalog helpers.

The wrong book stores only question ids.  Everything a screen needs to *show*
about a question -- family, volume, chapter, section, short book name -- is
derived here from the generated index, never copied into the user database
(decision D5: the catalog stays rebuildable, user data stays independent).
"""

import json
import re
from pathlib import Path

QID_RE = re.compile(r"^(?:[CZ]\d{1,2}-\d{1,3}|[AK]\d{4}-[A-Z]-\d{1,3}|M\d{1,2}-\d{1,3})$")
CHAPTER_RE = re.compile(r"^([CZ])(\d{1,2})-(\d{1,3})$")
EXAM_RE = re.compile(r"^([AK])(\d{4})-([A-Z])-(\d{1,3})$")
MOCK_RE = re.compile(r"^M(\d{1,2})-(\d{1,3})$")
BARE_RE = re.compile(r"^(\d{1,2})-(\d{1,3})$")

FAMILY_NAME = {
    "C": "案例",
    "Z": "知识",
    "A": "案例真题",
    "K": "知识真题",
    "M": "模拟",
}

SESSION_NAME = {"A": "上午卷", "P": "下午卷"}


def normalize_qid(text, family="C"):
    """Accept `3-11`, `C3-11`, `A2015-P-19`, `M2-13` and return the key."""
    t = (text or "").strip().upper()
    t = t.replace("－", "-").replace("—", "-").replace("–", "-").replace(" ", "")
    if not t:
        return None
    if QID_RE.match(t):
        return t
    bare = BARE_RE.match(t)
    if bare:
        return "%s%s-%s" % ((family or "C").upper(), bare.group(1), bare.group(2))
    return None


class Catalog:
    """Thin façade over lookup.json / chapters.json / books.json."""

    def __init__(self, index_dir):
        self.index_dir = Path(index_dir)
        self._lookup = None
        self._chapters = None
        self._short = {}
        self._sections = {}  # "C3" -> [(first, last, name), ...]

    @property
    def lookup(self):
        if self._lookup is None:
            with open(self.index_dir / "lookup.json", encoding="utf-8") as fh:
                self._lookup = json.load(fh)
        return self._lookup

    @property
    def chapters(self):
        if self._chapters is None:
            with open(self.index_dir / "chapters.json", encoding="utf-8") as fh:
                self._chapters = json.load(fh)
            for key, chap in self._chapters.items():
                spans = []
                for sec in chap.get("sections", []):
                    first = int(sec["first"].split("-")[1])
                    last = int(sec["last"].split("-")[1])
                    spans.append((first, last, sec["name"]))
                self._sections[key] = spans
        return self._chapters

    def _short_names(self):
        if self._short:
            return self._short
        path = self.index_dir / "books.json"
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data if isinstance(data, list) else next(iter(data.values()))
            for row in rows:
                name = row.get("file", "")
                self._short[name] = row.get("short", Path(name).stem)
                self._short[Path(name).stem] = row.get("short", Path(name).stem)
        return self._short

    def short_book(self, file_name):
        if not file_name:
            return ""
        names = self._short_names()
        return names.get(file_name, Path(file_name).stem)

    def family(self, qid):
        return (qid or "")[:1].upper()

    def exists(self, qid):
        if not qid:
            return False
        fam = qid[0]
        if fam in "CZ":
            return qid in self.lookup["chapter"]
        if fam in "AK":
            return qid in self.lookup["exam"]
        if fam == "M":
            return qid in self.lookup["mock"]
        return False

    def describe(self, qid):
        """Return display fields for one qid; unknown qids yield blanks."""
        fam = self.family(qid)
        info = {
            "qid": qid,
            "family": fam,
            "family_name": FAMILY_NAME.get(fam, fam),
            "chapter": "",
            "section": "",
            "book": "",
        }
        if fam in "CZ":
            chap = CHAPTER_RE.match(qid)
            if not chap:
                return info
            number = int(chap.group(2))
            entry = self.lookup["chapter"].get(qid) or {}
            if fam == "C":
                page = self.chapters.get("C%d" % number)
                if page:
                    info["chapter"] = page.get("title", "")
                    local = int(chap.group(3))
                    for first, last, name in self._sections.get("C%d" % number, []):
                        if first <= local <= last:
                            info["section"] = name
                            break
            elif fam == "Z":
                info["chapter"] = "第%d章" % number
            # Prefer the blank workbook for C: that is where the question lives.
            books = list((entry.get("books") or {}).keys())
            if books:
                blanks = [b for b in books if "空白" in b]
                pick = blanks[0] if (fam == "C" and blanks) else books[0]
                info["book"] = self.short_book(pick)
            return info

        if fam in "AK":
            m = EXAM_RE.match(qid)
            if not m:
                return info
            year, letter = m.group(2), m.group(3)
            label = "案例真题" if fam == "A" else "知识真题"
            session = SESSION_NAME.get(letter, "%s卷" % letter)
            info["chapter"] = "%s %s · %s" % (year, label, session)
            entry = self.lookup["exam"].get(qid) or {}
            info["book"] = self.short_book(entry.get("book", ""))
            return info

        if fam == "M":
            m = MOCK_RE.match(qid)
            if m:
                info["chapter"] = "模拟第%d套" % int(m.group(1))
            entry = self.lookup["mock"].get(qid) or {}
            info["book"] = self.short_book(entry.get("book", ""))
        return info

    def chapter_keys(self):
        """C-family volumes, for the chapter filter on the wrong-book screen."""
        self.chapters
        return [
            {"key": key, "title": chap.get("title", key)}
            for key, chap in sorted(
                self.chapters.items(), key=lambda kv: int(kv[0][1:])
            )
        ]
