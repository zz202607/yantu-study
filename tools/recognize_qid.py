"""Recognise printed question numbers in a phone photo of a workbook page.

The photo may be rotated, skewed, and covered in black/red handwriting.  The
pipeline is:

1. EXIF-correct, then pick the upright rotation by detection density.
2. Whiten red pen strokes -- they sit on top of question numbers often enough
   to break the detector.
3. Detect text lines and recognise the head of every line that starts at the
   column's left edge -- that is where a `<chapter>-<number>` label lives.
4. If no single confident read comes back, slide a narrow window along the
   left margin.  The detector drops a line whose label is circled or written
   over, so a detection-free scan is the only way to see those labels.
5. Keep candidates whose patch text *starts* with a question-number pattern.

Two label shapes are read: workbook labels (`3-11`) and self-describing book
labels (`A2025-A-7`, `K2025-P-68`, `M2-13`).  The latter name their own book,
so they need no family prefix and are validated against the exam/mock index
instead.  Both shapes can appear on one page: a printed wrong-book sheet mixes
case, knowledge and past-exam questions.

Cost is ~20 ms per patch whatever its size, and every channel is a list of
patches, so the pipeline is shaped to send few, cheap patches: heads are cut
narrow (wide ones get squeezed by the recogniser's fixed input), strip windows
are sized to its cheapest aspect, blank windows are skipped, and a confident
head read skips the strip scan entirely.

Only question numbers are recognised -- stems and figures stay as page images
(decision D2).  Workbook family prefixes are not decided here; that is the
app's job (decision D1: current book first).

Usage:
    py -3 tools/recognize_qid.py "拍照错题/3-11.jpg"
    py -3 tools/recognize_qid.py --json "拍照错题/4-12 4-13错.jpg"
"""

import argparse
import json
import os
import re
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index")

# `3-11` at the start of the line.  OCR often turns the hyphen into a dash,
# a CJK `一`, or drops it entirely; surrounding spaces and a bracket are
# tolerated.  `DASH` is shared by the looser patterns below.
DASH = "-—–_·一−"
ANCHOR_RE = re.compile(
    r"^\s*[\(\[（【'\"“]?\s*(\d{1,2})\s*[%s]\s*(\d{1,3})(?!\d)" % DASH
)
# A number pair anywhere in the leading slice: handwriting often bleeds into
# the patch, so `p=143-2 某…` still carries the printed `3-2`.  Decimals are
# deliberately NOT separators -- `4.5`, `0.85m` and `10.5` would otherwise read
# as question keys.
LOOSE_RE = re.compile(r"(\d{1,4})\s*([%s])\s*(\d{1,3})(?!\d)" % DASH)
# Hyphen dropped entirely, e.g. `(412)`: only trusted in front of a bracket
# and only when the book's index resolves the split unambiguously.
BARE_RE = re.compile(r"(?<![\d.])(\d{2,4})(?=[\)\](）】])")
# Printed label with the hyphen lost and handwriting glued to its left:
# `20419某…` is `4-19` behind a stray `20`.  Only trusted when the run leads
# straight into a question stem (`某`) and the index resolves one suffix.
BARE_STEM_RE = re.compile(r"(?<![\d.])(\d{3,5})(?=某)")
# A date written at the top of a page is not a question label.  OCR often
# reads `.` / `/` as `-`, so `2024-11-14` would otherwise resolve to the
# valid-looking key `4-11` (the tail of the year plus the month).  Every
# candidate whose match lies inside one of these spans is discarded.
DATE_SEP = "-—–_./·一"
# Past-exam and mock labels already carry their own book: `A2025-A-7` is the
# 2025 morning case paper, question 7.  OCR turns hyphens into dash-like
# glyphs, splits them with spaces, or drops them outright, so both separators
# are optional -- but a read is only kept when the index holds the exact key,
# so a dropped dash cannot invent a question.  The trailing `(?!\d)` guard
# stops `A2025-A-70` from being read as question 7.
EXAM_SEP = r"[\s\-—–_·−一./]?"
_EXAM_BODY = (
    r"([AK])\s*(\d{4})\s*%s\s*([AP])\s*%s\s*(\d{1,3})(?!\d)"
    % (EXAM_SEP, EXAM_SEP)
)
EXAM_RE = re.compile(
    r"^\s*[\(\[（【'\"“]?\s*" + _EXAM_BODY, re.IGNORECASE
)
# A label with a stray mark or handwriting glued to its left still carries the
# key; only the first few characters are searched so page text stays silent.
EXAM_ANY_RE = re.compile(_EXAM_BODY, re.IGNORECASE)
MOCK_RE = re.compile(
    r"^\s*[\(\[（【'\"“]?\s*M\s*(\d{1,2})\s*[%s]\s*(\d{1,3})(?!\d)" % DASH,
    re.IGNORECASE,
)
MOCK_BARE_RE = re.compile(
    r"^\s*[\(\[（【'\"“]?\s*M\s*(\d{2,4})(?!\d)", re.IGNORECASE
)
DATE_RES = (
    re.compile(
        r"(?<!\d)(?:19|20)\d{2}\s*[%s年]\s*(?:0?[1-9]|1[0-2])"
        r"(?:\s*[%s月]\s*(?:0?[1-9]|[12]\d|3[01])\s*日?)?(?!\d)"
        % (DATE_SEP, DATE_SEP)
    ),
    # Two-digit year, e.g. `24-11-14`; both month and day are required so a
    # normal `4-11` label is never swallowed.
    re.compile(
        r"(?<![\d.])\d{2}\s*[%s]\s*(?:0?[1-9]|1[0-2])\s*[%s]\s*"
        r"(?:0?[1-9]|[12]\d|3[01])(?!\d)" % (DATE_SEP, DATE_SEP)
    ),
)
HEAD_CHARS = 16

MIN_LINE_SCORE = 0.4     # detection confidence for a line-like box
MIN_PATCH_SCORE = 0.55   # recognition confidence for a candidate
ORIENT_WIDTH = 640       # orientation search works on this width
WORK_WIDTH = 3000        # patches are cut from this width
UPSCALE_TO = 64          # patches shorter than this are upscaled
INK_FRAC = 0.003         # strip windows with less dark ink than this are skipped
DET_MAX_SIDE = 2000      # detector input cap on the long side (cost grows with px)

# Recogniser cost is ~20 ms per patch regardless of size, with a steep penalty
# once a patch's width/height ratio passes ~7 (the tensor is 48 px tall, so the
# ratio sets its width).  Both knobs below keep patches numerous-but-cheap.
# Reaching into the margin for the line patch looked right but read worse:
# the margin holds pen marks, and the label is already inside the box.  At 0
# the benchmark gained two auto-accepts and lost none.
HEAD_LEFT = 0.0          # line-head patch: reach left of the box, in line heights
HEAD_RIGHT = 6.0         # ... and this far right of the box start
HEAD_MAX_INDENT = 3.0    # only lines starting this close to the margin can hold a label
STRIP_LEFT = 2.4         # strip reach left of the margin, in line heights
STRIP_WIN = 1.3          # strip window height, in line heights
STRIP_STEP = 0.8         # strip slide step, in line heights (below STRIP_WIN so no
                         # label can fall between two windows)
EARLY_EXIT = True        # stop after the line channel when one clean read wins

_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def engine():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                from rapidocr import RapidOCR

                _ENGINE = RapidOCR(
                    params={
                        "Global.log_level": "warning",
                        "Rec.rec_batch_num": 32,
                        "Global.max_side_len": DET_MAX_SIDE,
                    }
                )
    return _ENGINE


def as_list(value):
    return [] if value is None else [v for v in value]


_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９－．（）【】ＡＫＰＭａｋｐｍ",
    "0123456789-.()[]AKPMakpm",
)


def normalize_text(text):
    return text.translate(_FULLWIDTH)


def load_valid(family):
    """Question keys of a book family from index/lookup.json, without prefix."""
    lookup = json.load(open(os.path.join(INDEX, "lookup.json"), encoding="utf-8"))
    return {key[1:] for key in lookup["chapter"] if key.startswith(family)}


_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()


def load_self_describing():
    """Full keys of the exam and mock books, loaded once per process.

    Unlike chapter keys these need no family prefix -- `A2025-A-7` names its
    own book -- so they are validated as-is.  Loading is deferred until the
    first photo and cached: one photo can mix every family, and the sets are
    small next to the workbook key map the server already holds.
    """
    if not _REGISTRY:
        with _REGISTRY_LOCK:
            if not _REGISTRY:
                with open(
                    os.path.join(INDEX, "lookup.json"), encoding="utf-8"
                ) as fh:
                    lookup = json.load(fh)
                _REGISTRY["exam"] = frozenset(lookup.get("exam") or ())
                _REGISTRY["mock"] = frozenset(lookup.get("mock") or ())
    return _REGISTRY["exam"], _REGISTRY["mock"]


def _split_qids(digits, number):
    """Every plausible chapter split of a digits-hyphen-digits read.

    OCR glues handwriting onto the printed label (`p=143-2`), so the chapter
    can be a suffix of the digit run, not necessarily all of it.
    """
    out = []
    for i in range(max(0, len(digits) - 2), len(digits)):
        chapter = digits[i:]
        if 1 <= len(chapter) <= 2 and len(number) <= 3:
            out.append("%d-%d" % (int(chapter), int(number)))
    return out


def _split_bare(digits):
    """Split a hyphen-less run such as `412` into `4-12` / `41-2`."""
    out = []
    for cut in (1, 2):
        if 1 <= len(digits) - cut <= 3:
            out.append("%d-%d" % (int(digits[:cut]), int(digits[cut:])))
    return out


def _date_spans(text):
    """Character ranges occupied by dates such as `2024-11-14`."""
    spans = []
    for pattern in DATE_RES:
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def qid_candidates(text, valid, exam_keys=None, mock_keys=None):
    """Pull question-number candidates out of one recognised line.

    Returns (qid, confidence multiplier, kind) tuples.  With `valid` (the
    current book's keys) even damaged reads like `p=143-2` or `(412)` resolve;
    without it only the conservative anchored read is accepted.

    `exam_keys` / `mock_keys` are the full keys of the past-exam and mock
    books.  A label like `A2025-A-7` is self-describing, so it is emitted as
    a full key; `None` means "accept a well-formed read" (benchmark/test use
    without an index), while a set restricts the read to real questions.
    """
    out = []
    t = normalize_text(text).strip()
    if not t:
        return out
    dates = _date_spans(t)

    def unit_follows(match):
        end = match.end()
        return end < len(t) and t[end].isascii() and t[end].isalpha()

    def inside_date(match):
        return any(match.start() < end and match.end() > start
                   for start, end in dates)

    # A self-describing label contains digit-hyphen-digit runs of its own
    # (`M2-13` holds `2-13`).  Those characters belong to the label, not to
    # the current workbook, so chapter reads inside the span are suppressed.
    label_spans = []

    def inside_label(match):
        return any(match.start() < end and match.end() > start
                   for start, end in label_spans)

    def exam_key(match):
        return "%s%s-%s-%d" % (
            match.group(1).upper(), match.group(2),
            match.group(3).upper(), int(match.group(4)),
        )

    # Self-describing labels first: they cannot be mistaken for chapter pairs,
    # and on a mixed printed sheet they are what a photo most often shows.
    emitted = set()

    def emit_self(key, score, kind, span):
        if key in emitted:
            return
        emitted.add(key)
        label_spans.append(span)
        out.append((key, score, kind))

    match = EXAM_RE.match(t)
    if match and not unit_follows(match):
        key = exam_key(match)
        if exam_keys is None or key in exam_keys:
            emit_self(key, 1.0, "start", match.span())
    # Scan a little past HEAD_CHARS so a spaced-out key is not cut in half;
    # only the match's start position is constrained.
    for match in EXAM_ANY_RE.finditer(t[:HEAD_CHARS + 20]):
        if match.start() > 6 or match.start() == 0:
            continue
        if unit_follows(match):
            continue
        key = exam_key(match)
        if exam_keys is not None and key not in exam_keys:
            continue
        emit_self(key, 0.93 if match.start() <= 2 else 0.88, "loose",
                  match.span())

    match = MOCK_RE.match(t)
    if match and not unit_follows(match):
        key = "M%d-%d" % (int(match.group(1)), int(match.group(2)))
        if mock_keys is None or key in mock_keys:
            emit_self(key, 1.0, "start", match.span())
    elif mock_keys is not None:
        # Dash dropped by OCR (`M213`): only a split that the mock index
        # resolves uniquely counts, and it never auto-opens.
        match = MOCK_BARE_RE.match(t)
        if match:
            digits = match.group(1)
            found = set()
            for cut in (1, 2):
                if cut < len(digits):
                    key = "M%d-%d" % (int(digits[:cut]), int(digits[cut:]))
                    if key in mock_keys:
                        found.add(key)
            if len(found) == 1:
                emit_self(found.pop(), 0.8, "bare", match.span())

    match = ANCHOR_RE.match(t)
    if match and not unit_follows(match) and not inside_date(match):
        qid = "%d-%d" % (int(match.group(1)), int(match.group(2)))
        if valid is None or qid in valid:
            out.append((qid, 1.0, "start"))

    head = t[:HEAD_CHARS]
    for match in LOOSE_RE.finditer(head):
        if inside_date(match) or inside_label(match):
            continue
        digits, number = match.group(1), match.group(3)
        if unit_follows(match):
            continue
        if match.start() > 6:
            continue
        if valid is None and (match.start() > 2 or len(digits) > 2):
            continue
        penalty = 0.93 if match.start() <= 2 else 0.88
        for qid in _split_qids(digits, number):
            if valid is None or qid in valid:
                out.append((qid, penalty, "loose"))

    if valid is not None:
        for match in BARE_RE.finditer(t[:10]):
            if inside_date(match) or inside_label(match):
                continue
            found = {q for q in _split_bare(match.group(1)) if q in valid}
            if len(found) == 1:
                out.append((found.pop(), 0.8, "bare"))

        for match in BARE_STEM_RE.finditer(t[:14]):
            if inside_date(match) or inside_label(match):
                continue
            digits = match.group(1)
            found = {}
            for length in range(2, len(digits) + 1):
                suffix = digits[-length:]
                cands = {q for q in _split_bare(suffix) if q in valid}
                if cands:
                    found = cands
            if len(found) == 1:
                out.append((found.pop(), 0.8, "suffix"))
    return out


def load_photo(path):
    """EXIF-corrected RGB image."""
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def whiten_red(arr):
    """Turn red ink white so it stops splitting printed text lines."""
    a = arr.astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mask = (r - np.maximum(g, b) > 35) & (r > 80)
    a[mask] = 255
    return a.astype(np.uint8)


def _detect(engine, arr):
    """Detect text lines.  Boxes come back in the detection model's resized
    space, so they are mapped to the input image with the preprocess ratios --
    cropping the original image with raw box coordinates lands on the wrong
    line."""
    pre, op = engine.preprocess_img(np.ascontiguousarray(arr))
    crops, det = engine.detect_and_crop(pre, op)
    boxes = as_list(det.boxes)
    scores = as_list(det.scores)
    ratios = op.get("preprocess", {}) if isinstance(op, dict) else {}
    ratio_w = ratios.get("ratio_w", 1.0)
    ratio_h = ratios.get("ratio_h", 1.0)
    out = []
    for i, box in enumerate(boxes):
        xs = [p[0] * ratio_w for p in box]
        ys = [p[1] * ratio_h for p in box]
        out.append(
            {
                "box": (min(xs), min(ys), max(xs), max(ys)),
                "score": float(scores[i]) if i < len(scores) else 0.0,
            }
        )
    return out, crops


def _line_like(item, width, height):
    x0, y0, x1, y1 = item["box"]
    w, h = x1 - x0, y1 - y0
    return (
        item["score"] >= MIN_LINE_SCORE
        and w >= width * 0.02
        and height * 0.004 <= h <= height * 0.06
    )


def auto_qid(candidates):
    """The one candidate safe to open without asking.

    One clean anchored read, well clear of every other candidate.  The server,
    the page and the benchmark all use this single rule.
    """
    starts = [c for c in candidates if c["kind"] == "start" and c["score"] >= 0.90]
    if len(starts) != 1:
        return None
    top = starts[0]
    rival = max((c["score"] for c in candidates if c is not top), default=0.0)
    return top["qid"] if rival <= top["score"] - 0.05 else None


NOISE_CUT = 0.55     # a looser read below this is not worth a tap target
NOISE_LIMIT = 8      # how many loose reads may ride along with the questions
MAX_TOTAL = 16


def pack_candidates(candidates, noise_cut=NOISE_CUT, noise_limit=NOISE_LIMIT,
                    max_total=MAX_TOTAL):
    """Candidates as the page shows them: questions first, then page order.

    A whole-page shot carries several question labels.  Every `start` read is
    an anchored label validated against the book index, so those *are* the
    page's questions and always survive -- dropping one loses a real question.
    Looser reads are only useful as manual picks, so they fill the remaining
    slots by score.  Ordering by the label's y makes the list read
    top-to-bottom the way the paper does (the app's multi-question picker and
    its batch viewer both rely on this order).
    """
    best = {}
    for c in candidates:
        prev = best.get(c["qid"])
        if prev is None or c["score"] > prev["score"]:
            best[c["qid"]] = c

    starts = sorted(
        (c for c in best.values() if c["kind"] == "start"),
        key=lambda c: -c["score"],
    )[:max_total]
    noise = sorted(
        (c for c in best.values() if c["kind"] != "start" and c["score"] >= noise_cut),
        key=lambda c: -c["score"],
    )[:noise_limit]
    rows = sorted(starts + noise, key=lambda c: (c["box"][1], c["box"][0]))
    return [
        {
            "qid": c["qid"],
            "score": c["score"],
            "kind": c["kind"],
            "start": c["kind"] == "start",
            "text": c["text"],
            "y": round(c["box"][1], 1),
        }
        for c in rows
    ]


def detect_orientation(im):
    """Pick the rotation whose text lines look most like text lines."""
    small = im.copy()
    small.thumbnail((ORIENT_WIDTH, ORIENT_WIDTH), Image.LANCZOS)

    def metric(rot):
        test = small.rotate(rot, expand=True) if rot else small
        arr = np.ascontiguousarray(np.array(test))
        rows, _ = _detect(engine(), arr)
        good = [r for r in rows if _line_like(r, test.width, test.height)]
        return sum((r["box"][2] - r["box"][0]) * r["score"] for r in good), len(good)

    best_rot, best_metric = 0, metric(0)
    # Upright photos are the common case; only search when 0 looks wrong.
    if best_metric[1] < 8:
        for rot in (90, 180, 270):
            m = metric(rot)
            if m > best_metric:
                best_rot, best_metric = rot, m
    return best_rot, best_metric[1]


def _head_patch(arr, item):
    """Crop a line's head: the margin-reaching left end plus a few characters.

    The label sits at the very start of the line, so the tail is dead weight --
    dropping it keeps the patch narrow enough to stay at the recogniser's
    cheapest input width, and stops long lines from squeezing the label out.
    """
    h_img, w_img = arr.shape[:2]
    x0, y0, x1, y1 = item["box"]
    h = max(1.0, y1 - y0)
    left = max(0, int(x0 - h * HEAD_LEFT))
    right = min(w_img, int(min(x1, x0 + h * HEAD_RIGHT)))
    top = max(0, int(y0 - h * 0.18))
    bottom = min(h_img, int(y1 + h * 0.18))
    return arr[top:bottom, left:right]


def _upscale(patch):
    h, w = patch.shape[:2]
    if h < UPSCALE_TO and h > 0:
        factor = min(2.6, UPSCALE_TO / float(h))
        im = Image.fromarray(patch)
        im = im.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.LANCZOS)
        return np.ascontiguousarray(np.array(im))
    return np.ascontiguousarray(patch)


def _line_metrics(rows, width, height):
    """Typical text-line height and the left margin of the text column.

    Merged boxes (a line fused with handwriting) inflate the median, so the
    25th percentile is used for the height.  The margin is the 5th percentile
    of every line-like box's left edge: restricting it to long lines leaves
    too small a sample on sparse pages, and the percentile then swings with
    the detection width.  Erring left costs a strip a little width but never
    puts the margin outside the window.
    """
    lines = [r for r in rows if _line_like(r, width, height)]
    heights = [r["box"][3] - r["box"][1] for r in lines]
    lefts = [r["box"][0] for r in lines if r["box"][0] > 0.01 * width]
    h = float(np.percentile(heights, 25)) if heights else 70.0
    h = max(24.0, min(h, height * 0.05))
    margin = float(np.percentile(lefts, 5)) if lefts else width * 0.1
    return h, margin


def _margin_windows(arr, h, margin, right=8.0, left=None, step_scale=None, upscale=True):
    """Overlapping windows along the left margin, for detection-free OCR.
    Returns (patches, pseudo rows) with rows carrying the patch box.

    Two widths are used: a narrow window keeps the label from being squeezed
    away by the recogniser's fixed input width, a wide one survives labels
    printed further right than the estimated margin.  Both are kept a few
    line heights tall so their width/height ratio stays where the recogniser
    is cheapest -- a taller window shows the label no smaller in the tensor
    and covers the page with fewer steps.
    """
    left = STRIP_LEFT if left is None else left
    step_scale = STRIP_STEP if step_scale is None else step_scale
    height, width = arr.shape[:2]
    x0 = max(0, int(margin - left * h))
    x1 = min(width, int(margin + right * h))
    if x1 - x0 < 60:
        return [], []
    win_h = max(24, int(h * STRIP_WIN))
    step = max(4, int(h * step_scale))
    # Blank windows cost a full recognition pass and can only ever return
    # noise, so the column's dark-pixel count is accumulated once and windows
    # under the ink floor are dropped before OCR.
    dark = (arr[:, x0:x1].min(axis=2) < 110).sum(axis=1)
    cum = np.concatenate(([0], np.cumsum(dark)))
    min_ink = INK_FRAC * (x1 - x0) * win_h
    patches, rows = [], []
    for y in range(0, max(1, height - win_h // 2), step):
        bottom = min(height, y + win_h)
        if cum[bottom] - cum[y] < min_ink:
            continue
        patch = arr[y:bottom, x0:x1]
        patches.append(_upscale(patch) if upscale else np.ascontiguousarray(patch))
        rows.append({"box": (x0, y, x1, bottom)})
    return patches, rows


def recognize(photo, work_width=WORK_WIDTH, debug=False, valid=None,
              exam_keys=None, mock_keys=None, on_partial=None):
    """Return orientation, candidates and timing for one photo.

    `valid` is the current book's question-key set (see load_valid); passing
    it lets damaged reads (`p=143-2`, `(412)`) resolve against the index.
    `exam_keys` / `mock_keys` are the self-describing key sets; when omitted
    they are loaded once from the index, so past-exam labels are always read.

    `on_partial` is called with the candidates found by the line channel
    before the slower margin scan starts, so a caller can show something
    early.  Its argument has `candidates`, `auto` and `lines` keys.
    """
    if exam_keys is None or mock_keys is None:
        default_exam, default_mock = load_self_describing()
        exam_keys = default_exam if exam_keys is None else exam_keys
        mock_keys = default_mock if mock_keys is None else mock_keys
    started = time.time()
    im = load_photo(photo) if isinstance(photo, str) else photo
    rot, orient_lines = detect_orientation(im)
    if rot:
        im = im.rotate(rot, expand=True)

    scale = work_width / float(im.width)
    if scale < 1:
        im = im.resize((work_width, max(1, round(im.height * scale))), Image.LANCZOS)
    arr = whiten_red(np.array(im))

    # The detector caps its own input at ~2000 px on the long side, so feeding
    # it the page at work width costs nothing extra and keeps box coordinates
    # in the same space as the patches cut below.
    rows, _ = _detect(engine(), arr)
    width, height = arr.shape[1], arr.shape[0]
    h_line, margin = _line_metrics(rows, width, height)
    order = sorted(range(len(rows)), key=lambda i: (rows[i]["box"][1], rows[i]["box"][0]))
    line_rows, heads = [], []
    for i in order:
        item = rows[i]
        if not _line_like(item, width, height):
            continue
        # A question label sits at the column's left edge; a line indented
        # well past it is a continuation and costs a patch for nothing.
        if item["box"][0] > margin + HEAD_MAX_INDENT * h_line:
            continue
        patch = _head_patch(arr, item)
        if patch.size == 0:
            continue
        line_rows.append(item)
        heads.append(_upscale(patch))
    if not heads:
        return {"orientation": rot, "candidates": [], "lines": 0, "auto": None,
                "seconds": round(time.time() - started, 2)}

    best = {}
    debug_texts = []

    def absorb(name, rows_i, crops_i, weight=1.0):
        rec = engine().recognize_txt(crops_i)
        texts = as_list(rec.txts)
        scores = as_list(rec.scores)
        for j, text in enumerate(texts):
            score = float(scores[j]) if j < len(scores) else 0.0
            if debug:
                debug_texts.append(
                    {"source": name, "text": text[:80], "score": round(score, 3)}
                )
            if score < MIN_PATCH_SCORE:
                continue
            for qid, penalty, kind in qid_candidates(
                text, valid, exam_keys, mock_keys
            ):
                cand = {
                    "qid": qid,
                    "score": round(score * penalty * weight, 3),
                    "ocr_score": round(score, 3),
                    "kind": kind,
                    "text": text[:60],
                    "box": [round(float(v), 1) for v in rows_i[j]["box"]],
                    "source": name,
                }
                prev = best.get(qid)
                if prev is None or cand["score"] > prev["score"]:
                    best[qid] = cand

    def snapshot():
        cands = sorted(best.values(), key=lambda c: (-c["score"], c["qid"]))
        return {"candidates": cands, "auto": auto_qid(cands), "lines": len(line_rows)}

    absorb("line", line_rows, heads)

    # Slide along the left margin.  The detector drops the question's first
    # line often enough (red circle, pen stroke across it) that a
    # detection-free scan is the only reliable way to see the label -- but it
    # is also the expensive part, so a confident line read ends the search.
    stop_early = EARLY_EXIT and auto_qid(list(best.values()))
    if on_partial and not stop_early:
        on_partial(snapshot())
    if not stop_early:
        for right, name in ((6.0, "strip"), (9.0, "strip-wide")):
            strip_windows, strip_rows = _margin_windows(
                arr, h_line, margin, right=right, step_scale=STRIP_STEP
            )
            if strip_windows:
                absorb(name, strip_rows, strip_windows, weight=0.95)

    result = {
        "orientation": rot,
        "orient_lines": orient_lines,
        "seconds": round(time.time() - started, 2),
        **snapshot(),
    }
    if debug:
        result["texts"] = debug_texts
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("photo", help="image file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--width", type=int, default=WORK_WIDTH)
    parser.add_argument("--family", default="", help="C or Z: validate keys against the index")
    args = parser.parse_args()

    valid = load_valid(args.family) if args.family else None
    result = recognize(args.photo, work_width=args.width, debug=args.debug, valid=valid)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(
        "%s | rot=%d | %d lines | %.2fs | auto=%s"
        % (
            os.path.basename(args.photo),
            result["orientation"],
            result["lines"],
            result["seconds"],
            result["auto"] or "-",
        )
    )
    for cand in result["candidates"]:
        print("  %-10s score=%.2f  %s" % (cand["qid"], cand["score"], cand["text"]))


if __name__ == "__main__":
    main()
