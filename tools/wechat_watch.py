"""Watch the clipboard / inbox and auto-import WeChat answer pages.

Why not a browser extension: the article page is behind a WeChat captcha and
the embedded WeChat browser does not run extensions.  The reliable path is the
image the user already sees: copy it in WeChat (right-click -> copy) or save it
into the inbox folder; this watcher stores it under data/wechat_inbox/ and
posts it to the local app as an answer page.  The answer page contains the
question too, so one image is enough.

Run:  py -3 tools/wechat_watch.py
Stop: Ctrl+C
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "data" / "wechat_inbox"
FAILED = INBOX / "failed"
SEEN_PATH = INBOX / "seen.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SERVER = os.environ.get("YANTU_URL", "http://127.0.0.1:8000").rstrip("/")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def say(message):
    print(message, flush=True)


def load_seen():
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        return set(data.get("sha") or [])
    except (OSError, ValueError):
        return set()


def save_seen(seen):
    INBOX.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(
        json.dumps({"sha": sorted(seen)[-1000:]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def server_up():
    try:
        with OPENER.open(SERVER + "/api/version", timeout=1.5) as res:
            return res.status == 200
    except (OSError, urllib.error.URLError):
        return False


def upload(path):
    boundary = "----yantu" + uuid.uuid4().hex
    suffix = path.suffix.lower() if path.suffix.lower() in IMAGE_SUFFIXES else ".png"
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    body = b"".join([
        b"--" + boundary.encode() + b"\r\n",
        b'Content-Disposition: form-data; name="answer"; filename="answer'
        + suffix.encode() + b'"\r\n',
        b"Content-Type: " + mime.encode() + b"\r\n\r\n",
        path.read_bytes(),
        b"\r\n--" + boundary.encode() + b"--\r\n",
    ])
    request = urllib.request.Request(
        SERVER + "/api/ext/import", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    with OPENER.open(request, timeout=180) as res:
        return json.loads(res.read().decode("utf-8"))


def move_to(folder, path):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        target = folder / (path.stem + "-" + uuid.uuid4().hex[:5] + path.suffix)
    shutil.move(str(path), str(target))
    return target


def fail_file(path, reason):
    target = move_to(FAILED, path)
    say("未入库：%s（%s）" % (reason, target))


def process(path, seen):
    """Import one saved answer page; returns True when it left the inbox."""
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        say("读图失败：%s（%s）" % (path, exc))
        return False
    if digest in seen:
        return True
    if not server_up():
        say("服务没在运行：图片已存到 %s；先双击 start.bat，再回来。" % INBOX)
        return False
    say("识别中：%s" % path.name)
    try:
        data = upload(path)
        item = data.get("item") or {}
        say("已入库：%s · %s" % (item.get("day", ""), item.get("title", "")))
        seen.add(digest)
        save_seen(seen)
        if path.parent == INBOX:
            moved = move_to(INBOX / "imported", path)
            say("  原图留在：%s" % moved)
        return True
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:  # noqa: BLE001 - keep the status text
            detail = exc.reason
        fail_file(path, detail or ("HTTP %s" % exc.code))
        seen.add(digest)
        save_seen(seen)
        return False
    except Exception as exc:  # noqa: BLE001 - a bad upload must not stop the watcher
        say("上传失败：%s" % exc)
        return False


def clipboard_image():
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    try:
        clip = ImageGrab.grabclipboard()
    except Exception:  # noqa: BLE001 - some environments have no clipboard
        return None
    if isinstance(clip, list):
        return [Path(name) for name in clip
                if Path(name).suffix.lower() in IMAGE_SUFFIXES]
    return clip


def save_clipboard(image, seen, last_hash):
    buffer = BytesIO()
    image.convert("RGB").save(buffer, "PNG")
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    if digest == last_hash or digest in seen:
        return last_hash, None
    INBOX.mkdir(parents=True, exist_ok=True)
    name = "clip-%s-%s.png" % (
        time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4]
    )
    path = INBOX / name
    path.write_bytes(data)
    say("已保存：%s" % path.name)
    return digest, path


def main():
    parser = argparse.ArgumentParser(
        description="监听微信剪贴板/收件箱，自动把答案页存入公众号题外题库"
    )
    parser.add_argument("--save-only", action="store_true",
                        help="只保存到 data/wechat_inbox，不自动入库")
    args = parser.parse_args()
    INBOX.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    last_hash = None
    warned_offline = False
    say("微信公众号题 · 图片监听")
    say("在微信里打开「每日作业」的答案页，右键图片 → 复制；")
    say("或者把图片另存到：%s" % INBOX)
    say("图片会自动入库；Ctrl+C 停止。")
    if args.save_only:
        say("当前是只保存模式（--save-only）。")
    while True:
        clip = clipboard_image()
        if clip is not None and not isinstance(clip, list):
            last_hash, path = save_clipboard(clip, seen, last_hash)
            if path is not None and not args.save_only:
                process(path, seen)
        elif isinstance(clip, list):
            for name in clip:
                if name.is_file():
                    # Copy first: a copied file must never be moved/deleted
                    # from the user's own folder.
                    target = INBOX / (time.strftime("%Y%m%d-%H%M%S") + "-"
                                      + uuid.uuid4().hex[:4] + name.suffix)
                    try:
                        shutil.copy2(name, target)
                        process(target, seen)
                    except OSError as exc:
                        say("复制失败：%s（%s）" % (name, exc))
        if not args.save_only:
            if not server_up():
                if not warned_offline:
                    say("服务没在运行；先双击 start.bat，监听器会继续等。")
                    warned_offline = True
            else:
                if warned_offline:
                    say("服务已连上。")
                    warned_offline = False
                for path in sorted(INBOX.iterdir()):
                    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                        process(path, seen)
        time.sleep(1.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n已停止。")
