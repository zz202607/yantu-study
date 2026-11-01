"""Resolve a book name to the PDF that is actually on disk.

The 12 question books were re-encoded into `compressed/` and the originals were
deleted; the handbook was never compressed (its text layer and bookmarks would
not survive re-encoding) and still sits at the project root. So: compressed copy
wins, root copy is the fallback, and the handbook resolves the second way
without any special case.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPRESSED = os.path.join(ROOT, "compressed")
# The one book that must keep living at the root.
ROOT_ONLY = {"工程地质手册（第五版）.pdf"}


def book_pdf(name):
    """Return the best on-disk path for a book file name or bare stem."""
    if os.path.isabs(name):
        return name
    filename = name if name.lower().endswith(".pdf") else name + ".pdf"
    candidate = os.path.join(COMPRESSED, filename)
    if os.path.exists(candidate):
        return candidate
    return os.path.join(ROOT, filename)


def book_names():
    """Every book name available, compressed copies shadowing root copies."""
    names = set()
    for folder in (COMPRESSED, ROOT):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.lower().endswith(".pdf"):
                names.add(name)
    return sorted(names)
