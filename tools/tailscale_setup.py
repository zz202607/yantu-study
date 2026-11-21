"""One-time Tailscale setup for the phone entry.

    py -3 tools/tailscale_setup.py --status
    py -3 tools/tailscale_setup.py --login      # interactive sign-in
    py -3 tools/tailscale_setup.py --serve      # https://<pc>.<tailnet>.ts.net -> localhost:8000
    py -3 tools/tailscale_setup.py --reset      # remove the serve mapping

The app probes Tailscale on every QR refresh and only offers an entry that
actually answers, so this script is optional: without it the LAN QR still
works.  Run --serve once after logging in to unlock the fixed HTTPS address
(secure context: a real PWA install and the in-page microphone).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.net import lan_ip  # noqa: E402
from app.phone import find_cli, payload, reachable, read_status  # noqa: E402

PAGE_HINT = "页面里的「手机」按钮 →「重新检测」会刷新状态。"


def show_status(port):
    cli = find_cli()
    if not cli:
        print("没找到 Tailscale：https://tailscale.com/download/windows")
        print("不装也可以，同一 Wi-Fi 下的局域网二维码照常可用。")
        return 1
    info = read_status(cli)
    print("Tailscale：%s" % cli)
    print("状态：%s" % info["state"])
    if info.get("dns_name"):
        print("MagicDNS：%s" % info["dns_name"])
    if info.get("ips"):
        print("Tailscale IP：%s" % ", ".join(info["ips"]))
    print("局域网：http://%s:%d/" % (lan_ip(), port))
    data = payload(port, lan_ip(), "", force=True)
    print("固定入口：%s" % (data["tailscale"]["url"] or "不可用"))
    print("提示：%s" % data["tailscale"]["hint"])
    if info["state"] == "needs_login":
        print("下一步：py -3 tools/tailscale_setup.py --login")
    elif info["state"] == "running" and not data["tailscale"]["https"]:
        print("下一步：py -3 tools/tailscale_setup.py --serve")
    elif data["tailscale"]["https"]:
        print("下一步：手机装 Tailscale 并登录同一账号，扫页面里的 Tailscale 码。")
    print(PAGE_HINT)
    return 0


def login(cli):
    print("正在打开 Tailscale 登录；浏览器授权完成后这个命令会返回。")
    proc = subprocess.run([cli, "login"])
    return proc.returncode


def _output(value):
    """Captured subprocess text, tolerating bytes."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore").strip()
    return str(value).strip()


def serve(cli, port):
    info = read_status(cli)
    if info["state"] != "running":
        print("Tailscale 未连接（%s）：先 --login，托盘显示 Connected 再回来。" % info["state"])
        return 1
    dns_name = info.get("dns_name") or ""
    if not dns_name:
        print("没有 MagicDNS 名称：先在 Tailscale 管理台打开 MagicDNS。")
        return 1
    if not reachable("http://127.0.0.1:%d/" % port):
        print("本机 %d 端口没有服务：先双击 start.bat，再运行 --serve。" % port)
        return 1
    try:
        proc = subprocess.run(
            [cli, "serve", "--bg", str(port)],
            text=True, capture_output=True, timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        # A fresh tailnet waits for Serve to be enabled in the admin console;
        # the command prints the enable link and then blocks, so surface it.
        output = _output(exc.stdout) or _output(exc.stderr)
        if output:
            print(output)
        print("Serve 还没在 tailnet 启用：打开上面的链接启用 HTTPS，再重跑 --serve。")
        print("也可以在管理台 DNS → HTTPS Certificates 打开。")
        return 2
    output = _output(proc.stdout) or _output(proc.stderr)
    if output:
        print(output)
    if proc.returncode != 0:
        return proc.returncode
    https_url = "https://%s/" % dns_name
    print("已发布：%s  →  http://127.0.0.1:%d" % (https_url, port))
    print("等待证书和代理就绪（最多约 20 秒）…")
    for _ in range(10):
        if reachable(https_url):
            print("验证通过：%s" % https_url)
            print("下一步：手机装 Tailscale 并登录同一账号，扫页面里的 Tailscale 码。")
            return 0
        time.sleep(1)
    print("还没验证通过：证书可能还在签发；稍后到页面点「重新检测」。")
    print("排查：tailscale serve status")
    return 2


def reset(cli):
    proc = subprocess.run([cli, "serve", "reset"], text=True, capture_output=True, timeout=60)
    print(_output(proc.stdout) or _output(proc.stderr) or "serve 配置已清空。")
    return proc.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--status", action="store_true", help="show the current state (default)")
    parser.add_argument("--login", action="store_true", help="interactive Tailscale sign-in")
    parser.add_argument("--serve", action="store_true", help="publish HTTPS for this app")
    parser.add_argument(
        "--reset", action="store_true", help="remove all serve mappings on this machine"
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)

    cli = find_cli()
    if not cli:
        print("没找到 Tailscale：https://tailscale.com/download/windows")
        return 1
    if args.login:
        return login(cli)
    if args.serve:
        return serve(cli, args.port)
    if args.reset:
        return reset(cli)
    return show_status(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
