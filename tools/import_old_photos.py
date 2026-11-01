"""Import the legacy photo set (`拍照错题/`) into the wrong book.

The old photos carry the question id in the filename; that is the only place
the filename is trusted.  Run a preview first, then `--apply`:

    py -3 tools/import_old_photos.py            # preview, writes nothing
    py -3 tools/import_old_photos.py --apply    # create the attempts
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.catalog import Catalog  # noqa: E402
from app.photo_import import import_photos, scan_photos  # noqa: E402
from app.store import Store  # noqa: E402

STATUS = {"new": "新", "dup": "已在库", "unknown": "题库无此号"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photo-dir", default=str(ROOT / "拍照错题"))
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--family", default="C")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    store = Store(args.data)
    catalog = Catalog(ROOT / "index")
    items = scan_photos(store, catalog, args.photo_dir, args.family)

    new = dup = unknown = 0
    for item in items:
        marks = []
        for q in item["qids"]:
            marks.append("%s(%s)" % (q["qid"], STATUS[q["status"]]))
            if q["status"] == "new":
                new += 1
            elif q["status"] == "dup":
                dup += 1
            else:
                unknown += 1
        detail = "  ".join(marks) if marks else item["error"] or "文件里没有题号"
        print("%-24s %s" % (item["file"], detail))

    print(
        "\n共 %d 张照片：新增 %d 题 / 已在库 %d / 无法识别 %d"
        % (len(items), new, dup, unknown)
    )
    if not args.apply:
        print("预览模式，未写入。确认无误后加 --apply。")
        return

    result = import_photos(store, catalog, args.photo_dir, args.family)
    print(
        "已导入 %d 张照片 → 新增 %d 题，跳过 %d 题"
        % (result["photos"], len(result["created"]), len(result["skipped"]))
    )
    for item in result.get("failed", []):
        print("  失败 %s：%s" % (item.get("qid"), item.get("detail")))
    print("错题本现有 %d 题" % store.stats()["total"])


if __name__ == "__main__":
    main()
