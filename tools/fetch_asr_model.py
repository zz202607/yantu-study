"""Download the SenseVoice model used by the "hold to talk" button.

The 230 MB int8 model lives under `.cache/asr/sensevoice/` (gitignored).
Run once per machine; the rest of the app does not need it.

    py -3 tools/fetch_asr_model.py           # download missing files
    py -3 tools/fetch_asr_model.py --check   # only report status
    py -3 tools/fetch_asr_model.py --force   # re-download everything

Uses hf-mirror.com because HuggingFace is not reachable from this network.
No API key, no account, no per-use cost.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".cache" / "asr" / "sensevoice"
BASE = ("https://hf-mirror.com/csukuangfj/"
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/")
FILES = ("model.int8.onnx", "tokens.txt", "test_wavs/zh.wav")

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def remote_size(url):
    request = urllib.request.Request(url, method="HEAD")
    with OPENER.open(request, timeout=30) as res:
        return int(res.headers.get("Content-Length") or 0)


def fetch(name, force=False):
    dest = DEST / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = BASE + name
    try:
        expected = remote_size(url)
    except Exception as exc:  # noqa: BLE001 - the hint below is the real answer
        raise SystemExit("连不上 hf-mirror：%s" % exc)
    if dest.exists() and not force and (not expected or dest.stat().st_size == expected):
        print("%-22s 已有 (%s)" % (name, human(dest.stat().st_size)))
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.exists() and not force else 0
    if force and part.exists():
        part.unlink()
        offset = 0
    request = urllib.request.Request(url)
    if offset:
        request.add_header("Range", "bytes=%d-" % offset)
    print("%-22s 下载中%s" % (name, "（续传 %s）" % human(offset) if offset else ""))
    started = time.time()
    with OPENER.open(request, timeout=60) as res, open(part, "ab") as out:
        while True:
            chunk = res.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            offset += len(chunk)
            if expected:
                pct = offset * 100 / expected
                sys.stdout.write("\r  %5.1f%%  %s" % (pct, human(offset)))
                sys.stdout.flush()
    print()
    if expected and part.stat().st_size != expected:
        raise SystemExit("下载不完整：%s/%s；重跑本脚本续传"
                         % (human(part.stat().st_size), human(expected)))
    if dest.exists():
        dest.unlink()
    part.replace(dest)
    print("  完成 %s，用时 %.0f 秒" % (human(dest.stat().st_size),
                                       time.time() - started))
    return dest


def human(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.0f %s" % (size, unit) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024


def main():
    parser = argparse.ArgumentParser(description="下载本机语音识别模型")
    parser.add_argument("--check", action="store_true", help="只检查，不下载")
    parser.add_argument("--force", action="store_true", help="重新下载")
    args = parser.parse_args()
    if args.check:
        missing = [name for name in FILES
                   if not (DEST / name).exists()]
        print("模型目录：%s" % DEST)
        print("状态：" + ("齐全" if not missing else "缺 " + "、".join(missing)))
        print("按住说话：" + ("可用" if not missing else "不可用"))
        return
    for name in FILES:
        fetch(name, force=args.force)
    print("完成。重启服务后，问 AI 面板的「按住说话」即可使用。")


if __name__ == "__main__":
    main()
