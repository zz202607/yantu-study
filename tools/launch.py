"""One-click launcher: start the local server, open the desktop browser, and
show a QR code for the phone.  Running it twice reuses the running server.

    py -3 tools/launch.py            # start (if needed), open browser, show QR
    py -3 tools/launch.py --no-open  # no browser / image window (for scripts)
    py -3 tools/launch.py --stop     # stop the process listening on the port
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
sys.path.insert(0, str(ROOT))

from app.net import lan_ip  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


def port_in_use(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def is_ours(port):
    """The root page is ours, not some other service squatting on the port."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1.5) as res:
            body = res.read(4096)
    except OSError:
        return False
    return "岩土" in body.decode("utf-8", "ignore")


def wait_ready(port, timeout=40.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_ours(port):
            return True
        time.sleep(0.3)
    return False


def start_server(port):
    CACHE.mkdir(exist_ok=True)
    log_path = CACHE / "server.log"
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [sys.executable, str(ROOT / "app" / "server.py")],
            cwd=str(ROOT),
            env={**os.environ, "PORT": str(port)},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    return log_path


def stop_server(port):
    if os.name != "nt":
        print("stop is only wired for Windows; kill the uvicorn process yourself.")
        return 1
    script = (
        "$pids = Get-NetTCPConnection -LocalPort %d -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; "
        "if (-not $pids) { Write-Output 'not-running'; exit 0 }; "
        "$pids | ForEach-Object { Stop-Process -Id $_ -Force }; "
        "Write-Output ('stopped ' + ($pids -join ','))" % port
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    print((out.stdout or out.stderr).strip())
    return out.returncode


def show_qr(url, open_image=True):
    """Save a scannable PNG and (optionally) open it in the default viewer."""
    try:
        import segno
    except ImportError:
        print("未安装 segno，无法生成二维码：py -3 -m pip install segno==1.6.6")
        return
    path = CACHE / "phone_access.png"
    segno.make(url, error="m").save(path, scale=12, border=3)
    print("二维码：%s" % path)
    if open_image and os.name == "nt":
        os.startfile(path)  # noqa: S606 - open the generated PNG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--no-open", action="store_true", help="do not open browser or image")
    parser.add_argument("--stop", action="store_true", help="stop the server on this port")
    args = parser.parse_args()
    port = args.port

    if args.stop:
        return stop_server(port)

    if port_in_use(port) and not is_ours(port):
        print("端口 %d 已被其它程序占用；换端口：py -3 tools/launch.py --port 8001" % port)
        return 1
    if is_ours(port):
        print("服务已在运行。")
    else:
        log_path = start_server(port)
        if not wait_ready(port):
            print("服务启动失败，日志：%s" % log_path)
            return 1
        print("服务已启动（后台运行）。")

    local = "http://localhost:%d" % port
    lan = lan_ip()
    print("电脑：%s" % local)
    if lan == "127.0.0.1":
        print("未找到手机可用的局域网地址；请检查 Wi-Fi，或用 YANTU_HOST 指定。")
    else:
        phone = "http://%s:%d" % (lan, port)
        print("手机：%s" % phone)
        show_qr(phone, open_image=not args.no_open)
    print("换网络后重新运行 start.bat 即可生成新二维码。")

    if not args.no_open:
        webbrowser.open(local)
    print("停止服务：stop.bat（或 py -3 tools/launch.py --stop）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
