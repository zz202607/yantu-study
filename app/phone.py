"""Phone-entry URLs: the LAN address and, when available, a Tailscale name.

The app is opened by a phone browser, so the QR card needs a URL the phone can
reach.  The LAN address comes from `app.net`; Tailscale -- when the PC is
logged in and serving -- gives a stable MagicDNS name that survives Wi-Fi
changes and, over HTTPS, makes the page a secure context, which the installed
PWA and the in-page microphone need.

URL building is pure so CI tests run without FastAPI or a Tailscale install.
Only `runtime()` touches the machine; it caches for a few seconds because the
QR card is opened by hand, not polled.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

CACHE_SECONDS = 10.0
PROBE_TIMEOUT = 1.0

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The Windows installer does not add Tailscale to PATH for every user.
_WINDOWS_CLI = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)

# BackendState strings, reduced to the states the card explains.
_STATES = {
    "running": "running",
    "starting": "starting",
    "needslogin": "needs_login",
    "needsmachineauth": "needs_login",
    "stopped": "stopped",
    "nostate": "stopped",
}

_STATE_HINTS = {
    "missing": "这台电脑没装 Tailscale；现在只能用同一 Wi-Fi 扫码。",
    "needs_login": "Tailscale 已安装但未登录：登录后点「重新检测」，会出现固定入口。",
    "starting": "Tailscale 正在连接：稍后点「重新检测」。",
    "stopped": "Tailscale 没有运行：打开 Tailscale 后点「重新检测」。",
    "unknown": "读不到 Tailscale 状态；局域网扫码仍然可用。",
}

_CACHE = {}
_LOCK = threading.Lock()


def with_version(url, build):
    """Append ?v=<build> so WeChat/browser snapshots see a new address."""
    if not build:
        return url
    separator = "&" if "?" in url else "?"
    return "%s%sv=%s" % (url, separator, build)


def normalize_dns_name(value):
    name = str(value or "").strip().rstrip(".")
    return name.lower() if name else ""


def normalize_state(value):
    raw = str(value or "").strip().lower().replace("-", "").replace("_", "")
    return _STATES.get(raw, "unknown")


def parse_status(data):
    """Pure: Tailscale's `status --json` -> state, MagicDNS name and IPv4s."""
    data = data if isinstance(data, dict) else {}
    self_info = data.get("Self") or {}
    dns_name = normalize_dns_name(self_info.get("DNSName"))
    if not dns_name:
        host = normalize_dns_name(self_info.get("HostName"))
        suffix = normalize_dns_name(data.get("MagicDNSSuffix"))
        if host and suffix:
            dns_name = host + "." + suffix
    ips = [
        str(ip) for ip in (self_info.get("TailscaleIPs") or [])
        if ":" not in str(ip)
    ]
    return {
        "state": normalize_state(data.get("BackendState")),
        "dns_name": dns_name,
        "ips": ips,
    }


def find_cli():
    """The Tailscale executable, or None when it is not installed."""
    override = str(os.environ.get("YANTU_TAILSCALE") or "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("tailscale")
    if found:
        return found
    for path in _WINDOWS_CLI:
        if Path(path).is_file():
            return path
    return None


def read_status(cli=None):
    """Ask Tailscale for its state; every failure is a state the card explains."""
    cli = cli or find_cli()
    if not cli:
        return {"state": "missing", "dns_name": "", "ips": []}
    try:
        proc = subprocess.run(
            [cli, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=4.0,
            creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"state": "unknown", "dns_name": "", "ips": []}
    return parse_status(data)


def tailscale_candidates(info, port):
    """URLs worth probing, best first: HTTPS name, HTTP name, then raw IP."""
    if not info or info.get("state") != "running":
        return []
    urls = []
    dns_name = info.get("dns_name") or ""
    if dns_name:
        urls.append("https://%s/" % dns_name)
        urls.append("http://%s:%d/" % (dns_name, port))
    for ip in info.get("ips") or []:
        urls.append("http://%s:%d/" % (ip, port))
    return list(dict.fromkeys(urls))


def reachable(url, timeout=PROBE_TIMEOUT):
    """True only when that exact URL answers with this app's version JSON."""
    api = url.rstrip("/") + "/api/version"
    # Bypass Clash/system proxies: a tailnet name must be dialled directly.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(api, timeout=timeout) as res:
            data = json.loads(res.read(8192).decode("utf-8", "ignore"))
        return data.get("build") is not None
    except (OSError, ValueError, http.client.HTTPException):
        return False


def runtime(port, force=False):
    """Tailscale state plus the first reachable URL; cached briefly per port."""
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(port)
        if cached and not force and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        info = read_status()
        url = ""
        probed = []
        for candidate in tailscale_candidates(info, port):
            probed.append(candidate)
            if reachable(candidate):
                url = candidate
                break
        value = {"info": info, "url": url, "probed": probed}
        _CACHE[port] = (now, value)
        return value


def tailscale_hint(info, url):
    """One line explaining what the permanent entry needs next."""
    state = (info or {}).get("state") or "unknown"
    if state == "running":
        if url.startswith("https://"):
            return "固定入口已就绪：手机装 Tailscale 登录后，扫一次即可长期使用。"
        if url:
            return "Tailscale 能用，但还没开 HTTPS：运行 tools/tailscale_setup.py --serve。"
        if not (info or {}).get("dns_name"):
            return "Tailscale 已连接，但没有 MagicDNS 名称：在管理台打开 MagicDNS。"
        return "Tailscale 已连接，但固定入口不可达：运行 tools/tailscale_setup.py --serve。"
    return _STATE_HINTS.get(state, _STATE_HINTS["unknown"])


def build_payload(port, build, lan, tailscale):
    """Pure: the JSON the QR card renders.

    Ordering: a working HTTPS Tailscale URL first (it is the permanent, secure
    entry), then the LAN URL (zero setup), then a Tailscale URL that only works
    over HTTP (stable, but not a secure context).
    """
    info = (tailscale or {}).get("info") or {}
    ts_url = (tailscale or {}).get("url") or ""
    ts_target = None
    if ts_url:
        secure = ts_url.startswith("https://")
        ts_target = {
            "id": "tailscale",
            "label": "Tailscale",
            "url": with_version(ts_url, build),
            "note": "固定地址；手机装 Tailscale 后出门 4G 也能用"
            if secure else "固定地址；手机需要装 Tailscale",
        }
    lan_target = None
    if lan and lan != "127.0.0.1":
        lan_target = {
            "id": "lan",
            "label": "局域网",
            "url": with_version("http://%s:%d/" % (lan, port), build),
            "note": "同一 Wi-Fi 直接扫，手机不用装东西",
        }
    targets = []
    if ts_target and ts_url.startswith("https://"):
        targets.append(ts_target)
    if lan_target:
        targets.append(lan_target)
    if ts_target and not ts_url.startswith("https://"):
        targets.append(ts_target)
    return {
        "build": str(build or ""),
        "port": port,
        "targets": targets,
        "preferred": targets[0]["id"] if targets else "",
        "tailscale": {
            "state": info.get("state") or "unknown",
            "dns_name": info.get("dns_name") or "",
            "https": ts_url.startswith("https://"),
            "url": ts_url,
            "hint": tailscale_hint(info, ts_url),
        },
    }


def payload(port, lan, build, force=False):
    return build_payload(port, build, lan, runtime(port, force=force))
