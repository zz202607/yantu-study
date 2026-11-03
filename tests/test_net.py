"""Choosing the address that goes into the phone QR code."""

from app.net import pick_lan_ip


def test_home_range_beats_vpn_and_loopback():
    candidates = ["127.0.0.1", "169.254.7.9", "198.18.0.2", "192.168.1.3"]
    assert pick_lan_ip(candidates) == "192.168.1.3"


def test_ten_range_beats_docker_range():
    assert pick_lan_ip(["172.17.0.1", "10.0.0.5"]) == "10.0.0.5"


def test_tailscale_and_public_addresses_are_not_lan():
    # A Tailscale 100.x address only works when the phone runs Tailscale too,
    # and a public address is never reachable from the home Wi-Fi.
    assert pick_lan_ip(["100.100.1.1", "8.8.8.8"]) is None


def test_override_wins_verbatim():
    assert pick_lan_ip(["192.168.1.3"], "192.168.1.9") == "192.168.1.9"
    assert pick_lan_ip(["192.168.1.3"], "yantu.local") == "yantu.local"


def test_invalid_values_are_ignored():
    assert pick_lan_ip(["nope", "", None, "192.168.1.3"]) == "192.168.1.3"
    assert pick_lan_ip([]) is None
