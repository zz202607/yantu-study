"""One-off import of the legacy photo set whose filenames carry question ids.

Only these old photos use the name as a hint; the live flow recognises the
question number from the image itself.  Import rules:

* a name may hold several questions (`4-12 4-13错.jpg` -> two);
* a qid already in the wrong book is skipped, so re-importing is a no-op;
* a qid missing from the catalog is reported, never invented.
"""

import re
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
NUMBER_RE = re.compile(r"(\d{1,2})-(\d{1,3})(?!\d)")


def parse_qids(name, family="C"):
    """`4-12 4-13错.jpg` -> ['C4-12', 'C4-13'] (deduplicated, in name order)."""
    family = (family or "C").upper()
    qids = []
    for match in NUMBER_RE.finditer(Path(name).stem):
        qid = "%s%d-%d" % (family, int(match.group(1)), int(match.group(2)))
        if qid not in qids:
            qids.append(qid)
    return qids


def scan_photos(store, catalog, photo_dir, family="C"):
    """Preview every importable photo: qids, statuses, thumbnail."""
    photo_dir = Path(photo_dir)
    items = []
    if not photo_dir.exists():
        return items
    for path in sorted(photo_dir.iterdir(), key=lambda p: p.name):
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            continue
        item = {"file": path.name, "sha256": None, "thumb": None,
                "qids": [], "new_count": 0, "error": ""}
        try:
            photo = store.save_photo_file(path)
        except Exception as exc:  # noqa: BLE001 - a broken file must not stop the batch
            item["error"] = str(exc)
            items.append(item)
            continue
        item["sha256"] = photo["sha256"]
        item["thumb"] = photo["thumb"]
        item["taken_at"] = photo["taken_at"]
        for qid in parse_qids(path.name, family):
            if not catalog.exists(qid):
                status = "unknown"
            elif store.get_attempt_by_qid(qid):
                status = "dup"
            else:
                status = "new"
            item["qids"].append({"qid": qid, "status": status})
            if status == "new":
                item["new_count"] += 1
        items.append(item)
    return items


def import_photos(store, catalog, photo_dir, family="C", shas=None, dry_run=False):
    """Create attempts for the selected photos; existing qids are skipped."""
    items = scan_photos(store, catalog, photo_dir, family)
    wanted = set(shas or [])
    rows = []
    picked = []
    for item in items:
        if not item["sha256"]:
            continue
        if wanted and item["sha256"] not in wanted:
            continue
        picked.append(item)
        for q in item["qids"]:
            if q["status"] != "new":
                continue
            rows.append(
                {
                    "qid": q["qid"],
                    "tags": [],
                    "source": "import",
                    "photo_sha": item["sha256"],
                    "at": item.get("taken_at"),
                }
            )
    summary = {
        "photos": len(picked),
        "planned": [row["qid"] for row in rows],
        "errors": [{"file": i["file"], "detail": i["error"]}
                   for i in items if i["error"]],
    }
    if dry_run:
        return summary
    result = store.add_attempts(rows, skip_existing=True)
    summary["created"] = [item["qid"] for item in result["created"]]
    summary["skipped"] = result["skipped"]
    summary["failed"] = result["errors"]
    store.prune_orphan_photos()
    return summary
