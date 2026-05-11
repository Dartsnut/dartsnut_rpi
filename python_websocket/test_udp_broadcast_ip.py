import json

from python_websocket.udp_broadcast import normalize_ip


def test_normalize_ip_invalid_values():
    assert normalize_ip("") is None
    assert normalize_ip("   ") is None
    assert normalize_ip("0.0.0.0") is None
    assert normalize_ip(None) is None  # type: ignore[arg-type]


def test_normalize_ip_valid_values():
    assert normalize_ip("192.168.1.10") == "192.168.1.10"
    assert normalize_ip(" 192.168.1.10 ") == "192.168.1.10"
    assert normalize_ip("10.0.0.5") == "10.0.0.5"

