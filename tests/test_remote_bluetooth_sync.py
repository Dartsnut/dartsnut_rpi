import threading

from python_websocket import bluetooth_operations
from python_websocket.remote_bluetooth_sync_controller import RemoteBluetoothScanController


def test_build_remote_bluetooth_list_maps_status(monkeypatch):
    monkeypatch.setattr(
        bluetooth_operations.bluetooth,
        "discover_devices",
        lambda duration, lookup_names: [
            ("AA:BB:CC:DD:EE:01", "Game Controller"),
            ("AA:BB:CC:DD:EE:02", "Office Speaker"),
            ("AA:BB:CC:DD:EE:03", "Keyboard"),
        ],
    )

    monkeypatch.setattr(
        bluetooth_operations, "get_connection_status", lambda address: "connected" if address.endswith("01") else "disconnected"
    )

    result = bluetooth_operations.build_remote_bluetooth_list()

    assert result == [
        {
            "address": "AA:BB:CC:DD:EE:01",
            "name": "Game Controller",
            "status": "connected",
        },
        {
            "address": "AA:BB:CC:DD:EE:02",
            "name": "Office Speaker",
            "status": "disconnected",
        },
    ]


def test_remote_scan_controller_publishes_scan_result_and_resets_flag():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [{"address": "AA", "name": "Pad", "status": "connected"}],
        timestamp_factory=lambda: "2026-03-23T12:34:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    started = controller.start_scan_if_requested()
    assert started is True

    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published == [
        {
            "bluetooth": {
                "is_scan": False,
                "list": [{"address": "AA", "name": "Pad", "status": "connected"}],
                "timestamp": "2026-03-23T12:34:56+00:00",
            }
        }
    ]


def test_remote_scan_controller_ignores_reentrant_request_until_done():
    published = []
    gate = threading.Event()

    def slow_scan():
        gate.wait(1)
        return [{"address": "AA", "name": "Pad", "status": "disconnected"}]

    controller = RemoteBluetoothScanController(
        scan_builder=slow_scan,
        timestamp_factory=lambda: "2026-03-23T12:35:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    assert controller.start_scan_if_requested() is True
    assert controller.start_scan_if_requested() is False

    gate.set()
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert len(published) == 1
    assert published[0]["bluetooth"]["is_scan"] is False


def test_remote_scan_controller_handles_scan_failure_with_empty_list():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: (_ for _ in ()).throw(RuntimeError("scan failed")),
        timestamp_factory=lambda: "2026-03-23T12:36:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    assert controller.start_scan_if_requested() is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published == [
        {
            "bluetooth": {
                "is_scan": False,
                "list": [],
                "timestamp": "2026-03-23T12:36:56+00:00",
            }
        }
    ]


def test_remote_scan_controller_skips_unchanged_list_payload():
    published = []
    scan_result = [{"address": "AA", "name": "Pad", "status": "connected"}]

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: scan_result,
        timestamp_factory=lambda: "2026-03-23T12:37:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    assert controller.start_scan_if_requested() is True
    for _ in range(40):
        if len(published) >= 1:
            break
        threading.Event().wait(0.01)

    assert controller.start_scan_if_requested() is True
    for _ in range(40):
        if len(published) >= 2:
            break
        threading.Event().wait(0.01)

    assert len(published) == 2
    assert published[0] == {
        "bluetooth": {
            "is_scan": False,
            "list": scan_result,
            "timestamp": "2026-03-23T12:37:56+00:00",
        }
    }
    assert published[1] == {"bluetooth": {"is_scan": False}}


def test_remote_connect_controller_success_sets_connected_and_clears_connect():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:40:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    assert controller.start_connect_if_requested("AA:BB:CC:DD:EE:FF") is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published == [
        {
            "bluetooth": {
                "connect": "",
                "timestamp": "2026-03-23T12:40:56+00:00",
                "error": "",
                "list": [
                    {
                        "address": "AA:BB:CC:DD:EE:FF",
                        "name": "",
                        "status": "connected",
                    }
                ],
            }
        }
    ]


def test_remote_connect_controller_failure_sets_error_and_clears_connect():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:41:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (False, "Auth failed"),
    )

    assert controller.start_connect_if_requested("11:22:33:44:55:66") is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published == [
        {
            "bluetooth": {
                "connect": "",
                "timestamp": "2026-03-23T12:41:56+00:00",
                "error": "Auth failed",
                "list": [
                    {
                        "address": "11:22:33:44:55:66",
                        "name": "",
                        "status": "disconnected",
                    }
                ],
            }
        }
    ]
