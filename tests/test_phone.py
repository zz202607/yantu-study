"""The phone QR card: which URLs it offers and in what order."""

from app.phone import (
    build_payload,
    normalize_dns_name,
    parse_status,
    tailscale_candidates,
    tailscale_hint,
    with_version,
)


def test_version_param_keeps_the_wechat_snapshot_escape():
    assert with_version("http://192.168.1.3:8000/", "123") == (
        "http://192.168.1.3:8000/?v=123"
    )
    assert with_version("http://host:8000/?a=1", "123") == (
        "http://host:8000/?a=1&v=123"
    )
    assert with_version("http://host:8000/", "") == "http://host:8000/"


def test_dns_name_is_lowercase_without_the_root_dot():
    assert normalize_dns_name("ZGM.Example.TS.NET.") == "zgm.example.ts.net"
    assert normalize_dns_name("") == ""


def test_running_status_gives_magicdns_name_and_ipv4s():
    status = parse_status(
        {
            "BackendState": "Running",
            "Self": {
                "DNSName": "zgm.tail1234.ts.net.",
                "TailscaleIPs": ["100.88.1.2", "fd7a::1"],
            },
            "MagicDNSSuffix": "tail1234.ts.net",
        }
    )
    assert status["state"] == "running"
    assert status["dns_name"] == "zgm.tail1234.ts.net"
    assert status["ips"] == ["100.88.1.2"]


def test_host_name_and_suffix_are_the_fallback():
    status = parse_status(
        {
            "BackendState": "running",
            "Self": {"HostName": "ZGM"},
            "MagicDNSSuffix": "tail1234.ts.net.",
        }
    )
    assert status["dns_name"] == "zgm.tail1234.ts.net"


def test_logged_out_status_offers_no_tailscale_url():
    status = parse_status(
        {
            "BackendState": "NeedsLogin",
            "Self": {"DNSName": "", "TailscaleIPs": None},
            "MagicDNSSuffix": "",
        }
    )
    assert status["state"] == "needs_login"
    assert tailscale_candidates(status, 8000) == []


def test_candidates_prefer_https_then_http_then_raw_ip():
    status = {"state": "running", "dns_name": "zgm.ts.net", "ips": ["100.88.1.2"]}
    assert tailscale_candidates(status, 8000) == [
        "https://zgm.ts.net/",
        "http://zgm.ts.net:8000/",
        "http://100.88.1.2:8000/",
    ]


def test_https_tailscale_is_the_preferred_entry():
    data = build_payload(
        8000,
        "123",
        "192.168.1.3",
        {
            "info": {"state": "running", "dns_name": "zgm.ts.net", "ips": []},
            "url": "https://zgm.ts.net/",
        },
    )
    assert [target["id"] for target in data["targets"]] == ["tailscale", "lan"]
    assert data["preferred"] == "tailscale"
    assert data["targets"][0]["url"] == "https://zgm.ts.net/?v=123"
    assert data["targets"][1]["url"] == "http://192.168.1.3:8000/?v=123"
    assert data["tailscale"]["https"] is True


def test_lan_comes_first_when_tailscale_has_no_https():
    data = build_payload(
        8000,
        "123",
        "192.168.1.3",
        {
            "info": {"state": "running", "dns_name": "zgm.ts.net", "ips": []},
            "url": "http://zgm.ts.net:8000/",
        },
    )
    assert [target["id"] for target in data["targets"]] == ["lan", "tailscale"]
    assert data["preferred"] == "lan"


def test_no_lan_address_still_offers_tailscale():
    data = build_payload(
        8000,
        "123",
        "127.0.0.1",
        {"info": {"state": "running", "dns_name": "zgm.ts.net", "ips": []},
         "url": "https://zgm.ts.net/"},
    )
    assert [target["id"] for target in data["targets"]] == ["tailscale"]


def test_logged_out_tailscale_still_leaves_the_lan_qr():
    data = build_payload(
        8000,
        "123",
        "192.168.1.3",
        {"info": {"state": "needs_login", "dns_name": "", "ips": []}, "url": ""},
    )
    assert [target["id"] for target in data["targets"]] == ["lan"]
    assert data["preferred"] == "lan"
    assert "未登录" in data["tailscale"]["hint"]


def test_no_phone_route_at_all():
    data = build_payload(
        8000,
        "123",
        "127.0.0.1",
        {"info": {"state": "missing", "dns_name": "", "ips": []}, "url": ""},
    )
    assert data["targets"] == []
    assert data["preferred"] == ""
    assert "没装 Tailscale" in data["tailscale"]["hint"]


def test_hint_explains_running_but_unserved():
    hint = tailscale_hint(
        {"state": "running", "dns_name": "zgm.ts.net", "ips": []}, ""
    )
    assert "--serve" in hint
