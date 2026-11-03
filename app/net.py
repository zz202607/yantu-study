"""Pick a LAN IPv4 address a phone on the same Wi-Fi can actually reach.

A VPN or container adapter often takes the default route, so the classic
"connect a UDP socket and read the source address" trick can return a
198.18.x.x TUN address that no phone can open.  Prefer the usual home ranges,
and let `YANTU_HOST` override the choice when the PC has several adapters.
"""

from __future__ import annotations

import ipaddress
import os
import socket

# Addresses that are private on paper but useless for a phone on the same Wi-Fi:
# 198.18.0.0/15 is the benchmark range VPN TUN adapters like Clash use,
# 169.254.0.0/16 is link-local, 100.64.0.0/10 is CGNAT (Tailscale lives here;
# reachable only when the phone also runs Tailscale).
_SKIPPED = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)

# Home routers overwhelmingly hand out 192.168.x.x; 10.x is the next best bet.
# 172.16/12 is last because Docker/WSL also use it.
_PREFERRED = (
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
)


def _usable(value):
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if ip.version != 4 or not ip.is_private:
        return None
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return None
    if any(ip in net for net in _SKIPPED):
        return None
    return ip


def _rank(ip):
    for index, net in enumerate(_PREFERRED):
        if ip in net:
            return index
    return len(_PREFERRED)


def pick_lan_ip(candidates, override=None):
    """Return the best LAN IPv4 string, or None when nothing is reachable.

    An explicit override is trusted verbatim (it may be a hostname).
    """
    if override and str(override).strip():
        return str(override).strip()
    usable = [ip for ip in (_usable(value) for value in candidates) if ip is not None]
    if not usable:
        return None
    usable.sort(key=lambda ip: (_rank(ip), ip.packed))
    return str(usable[0])


def lan_candidates():
    """Default-route address first, then every IPv4 the hostname resolves to."""
    values = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        values.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            values.append(info[4][0])
    except OSError:
        pass
    return list(dict.fromkeys(value for value in values if value))


def lan_ip():
    """The address to show on the phone access screen."""
    return pick_lan_ip(lan_candidates(), os.environ.get("YANTU_HOST")) or "127.0.0.1"
