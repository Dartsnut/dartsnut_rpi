from __future__ import annotations

import threading

from runtime.wifi_controller import WifiController


def test_wifi_controller_suppresses_duplicate_scan_and_records_result():
    release = threading.Event()

    def scan():
        release.wait(1)
        return [{"ssid": "Home", "rssi": 80, "secured": True}]

    controller = WifiController(scan_networks=scan, connect_network=lambda *_args: None)
    assert controller.start_scan_if_requested() is True
    assert controller.start_scan_if_requested() is False
    release.set()

    for _ in range(100):
        snapshot = controller.get_state_snapshot()
        if not snapshot["is_scan"]:
            break
        threading.Event().wait(0.01)

    assert snapshot["networks"][0]["ssid"] == "Home"
    assert snapshot["scan_error"] == ""


def test_wifi_controller_connect_success_and_password_error():
    calls = []

    def connect(ssid, password, secured):
        calls.append((ssid, password, secured))
        if ssid == "Bad":
            raise RuntimeError("Secrets were required, but not provided")

    controller = WifiController(scan_networks=lambda: [], connect_network=connect)
    assert controller.start_connect_if_requested("Good", "pw", True) is True
    for _ in range(100):
        snapshot = controller.get_state_snapshot()
        if snapshot["connection_status"] != "connecting":
            break
        threading.Event().wait(0.01)
    assert snapshot["connection_status"] == "success"

    controller.clear_connection_result()
    assert controller.start_connect_if_requested("Bad", "", True) is True
    for _ in range(100):
        snapshot = controller.get_state_snapshot()
        if snapshot["connection_status"] != "connecting":
            break
        threading.Event().wait(0.01)
    assert snapshot["connection_status"] == "error"
    assert snapshot["connection_error"] == "Incorrect password"
    assert calls == [("Good", "pw", True), ("Bad", "", True)]


def test_wifi_controller_suppresses_duplicate_connect_request():
    release = threading.Event()

    def connect(_ssid, _password, _secured):
        release.wait(1)

    controller = WifiController(scan_networks=lambda: [], connect_network=connect)

    assert controller.start_connect_if_requested("Home", "pw", True) is True
    assert controller.start_connect_if_requested("Other", "pw2", True) is False

    release.set()
    for _ in range(100):
        snapshot = controller.get_state_snapshot()
        if snapshot["connection_status"] != "connecting":
            break
        threading.Event().wait(0.01)

    assert snapshot["connection_status"] == "success"
    assert snapshot["connection_ssid"] == "Home"


def test_wifi_controller_separates_connected_network_from_scan_results():
    controller = WifiController(
        scan_networks=lambda: [
            {"ssid": "Home", "rssi": 90, "secured": True, "connected": True},
            {"ssid": "Guest", "rssi": 70, "secured": False, "connected": False},
            {"ssid": "Home", "rssi": 60, "secured": True, "connected": False},
        ],
        connect_network=lambda *_args: None,
    )

    assert controller.start_scan_if_requested() is True
    for _ in range(100):
        snapshot = controller.get_state_snapshot()
        if not snapshot["is_scan"]:
            break
        threading.Event().wait(0.01)

    assert snapshot["connected_network"]["ssid"] == "Home"
    assert [network["ssid"] for network in snapshot["networks"]] == ["Guest"]
