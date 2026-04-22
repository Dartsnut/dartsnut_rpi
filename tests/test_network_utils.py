import subprocess

from network_utils import get_primary_ipv4


def test_get_primary_ipv4_prefers_wlan0_then_eth0(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        interface = cmd[-1]
        if interface == "wlan0":
            return subprocess.CompletedProcess(cmd, 0, stdout="2: wlan0    inet 192.168.1.20/24 brd 192.168.1.255")
        if interface == "eth0":
            return subprocess.CompletedProcess(cmd, 0, stdout="2: eth0    inet 10.0.0.11/24 brd 10.0.0.255")
        raise AssertionError(f"unexpected interface {interface}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_primary_ipv4() == "192.168.1.20"


def test_get_primary_ipv4_uses_eth0_when_wlan0_missing(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        interface = cmd[-1]
        if interface == "wlan0":
            raise subprocess.CalledProcessError(1, cmd)
        if interface == "eth0":
            return subprocess.CompletedProcess(cmd, 0, stdout="2: eth0    inet 10.0.0.11/24 brd 10.0.0.255")
        raise AssertionError(f"unexpected interface {interface}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_primary_ipv4() == "10.0.0.11"


def test_get_primary_ipv4_returns_fallback_when_no_interfaces_available(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_primary_ipv4(fallback="0.0.0.0") == "0.0.0.0"
