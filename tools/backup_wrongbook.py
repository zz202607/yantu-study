"""Back the wrong book up, or restore it from a backup zip.

    py -3 tools/backup_wrongbook.py backup [--out PATH]
    py -3 tools/backup_wrongbook.py restore BACKUP.zip --yes

Restore replaces the live database and attachments.  It always snapshots the
current data first, and refuses to run without --yes.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.store import Store, now_iso  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backup", "restore"])
    parser.add_argument("archive", nargs="?", help="backup zip for restore")
    parser.add_argument("--out", help="output path for backup")
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--yes", action="store_true", help="confirm a restore")
    args = parser.parse_args()

    if args.command == "backup":
        store = Store(args.data)
        path = store.backup(args.out)
        stats = store.stats()
        print("备份完成：%s" % path)
        print("  错题 %d 题 / 照片目录 %s" % (stats["total"], Path(args.data) / "attachments"))
        return

    if not args.archive:
        parser.error("restore 需要一个备份 zip 路径")
    if not args.yes:
        print("恢复会覆盖当前错题本；确认后加 --yes 重跑。")
        return
    # Deliberately do not open the store first: on a fresh target that would
    # create an empty database and force a pointless safety backup.
    report = Store.restore_from_zip(args.archive, Path(args.data))
    print(
        "恢复完成（%s）：错题 %d 题 / 照片 %d 张"
        % (now_iso(), report["attempts"], report["photos"])
    )
    if report["safety_backup"]:
        print("恢复前的旧数据已备份到：%s" % report["safety_backup"])


if __name__ == "__main__":
    main()
