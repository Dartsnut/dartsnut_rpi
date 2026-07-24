import copy
import sys
import threading
import types

sys.modules["bluetooth"] = types.SimpleNamespace(
    discover_devices=lambda *_a, **_k: [],
)

from python_websocket import bluetooth_operations
from python_websocket.remote_bluetooth_sync_controller import RemoteBluetoothScanController


def test_controller_class_filter_accepts_only_gamepad_and_joystick():
    assert bluetooth_operations._is_game_controller_class(0x2508) is True
    assert bluetooth_operations._is_game_controller_class(0x2504) is True
    assert bluetooth_operations._is_game_controller_class("0x00002508") is True

    assert bluetooth_operations._is_game_controller_class(0x2540) is False
    assert bluetooth_operations._is_game_controller_class(0x2580) is False
    assert bluetooth_operations._is_game_controller_class(0x250C) is False
    assert bluetooth_operations._is_game_controller_class(0x2404) is False
    assert bluetooth_operations._is_game_controller_class(None) is False
    assert bluetooth_operations._is_game_controller_class("bad") is False


def test_build_remote_bluetooth_list_filters_by_class_and_maps_status(monkeypatch):
    calls = []

    def discover_devices(**kwargs):
        calls.append(kwargs)
        return [
            ("aa:bb:cc:dd:ee:01", "Arbitrary Name", 0x2508),
            ("AA:BB:CC:DD:EE:02", "Joystick", 0x2504),
            ("AA:BB:CC:DD:EE:03", "Controller Speaker", 0x2404),
            ("AA:BB:CC:DD:EE:04", "Controller Keyboard", 0x2540),
            ("AA:BB:CC:DD:EE:05", "Controller Remote", 0x250C),
            ("AA:BB:CC:DD:EE:06", "Missing Class", None),
            ("AA:BB:CC:DD:EE:01", "Duplicate", 0x2508),
            ("malformed",),
        ]

    monkeypatch.setattr(
        bluetooth_operations.bluetooth,
        "discover_devices",
        discover_devices,
    )
    monkeypatch.setattr(
        bluetooth_operations,
        "get_connection_status",
        lambda address: "connected" if address.endswith("01") else "disconnected",
    )

    result = bluetooth_operations.build_remote_bluetooth_list()

    assert calls == [
        {"duration": 8, "lookup_names": True, "lookup_class": True}
    ]
    assert result == [
        {
            "address": "AA:BB:CC:DD:EE:01",
            "name": "Arbitrary Name",
            "status": "connected",
        },
        {
            "address": "AA:BB:CC:DD:EE:02",
            "name": "Joystick",
            "status": "disconnected",
        },
    ]


def test_bluetooth_device_properties_parse_class_and_connected_once(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace(
            returncode=0,
            stdout=(
                "Device AA:BB:CC:DD:EE:FF\n"
                "\tClass: 0x00002508 (9480)\n"
                "\tConnected: yes\n"
            ),
        )

    monkeypatch.setattr(bluetooth_operations.subprocess, "run", run)

    properties = bluetooth_operations._get_bluetooth_device_properties(
        "AA:BB:CC:DD:EE:FF"
    )

    assert properties == {"connected": True, "class": 0x2508}
    assert len(calls) == 1
    assert calls[0][0] == [
        "bluetoothctl",
        "info",
        "AA:BB:CC:DD:EE:FF",
    ]


def test_apply_explicit_remote_lists_updates_controllers_and_scan_results():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:34:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )
    controller._state["controllers"] = [
        {"name": "Keep", "mac": "AA:BB:CC:DD:EE:FF", "status": "connected"}
    ]
    controller._state["scan_results"] = [
        {"name": "Stale", "mac": "11:22:33:44:55:66", "status": "idle"}
    ]

    controller.apply_explicit_remote_lists(
        {
            "controllers": [],
            "scan_results": [{"mac": "11:22:33:44:55:66", "name": "Found", "status": "idle"}],
        }
    )
    assert controller._state["controllers"] == []
    assert controller._state["scan_results"] == [
        {"name": "Found", "mac": "11:22:33:44:55:66", "status": "idle"}
    ]
    assert published == []


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

    assert published[0] == {
        "bluetooth": {
            "is_scan": True,
            "controllers": [],
            "scan_results": [],
            "last_scan_at": "",
        }
    }
    assert published[1] == {
        "bluetooth": {
            "is_scan": False,
            "controllers": [],
            "scan_results": [
                {"name": "Pad", "mac": "AA", "status": "connected"},
            ],
            "last_scan_at": "2026-03-23T12:34:56+00:00",
        }
    }


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
    for _ in range(200):
        if len(published) >= 2:
            break
        threading.Event().wait(0.01)

    assert len(published) == 2
    assert published[-1]["bluetooth"]["is_scan"] is False


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

    assert published[-1] == {
        "bluetooth": {
            "is_scan": False,
            "controllers": [],
            "scan_results": [],
            "last_scan_at": "2026-03-23T12:36:56+00:00",
        }
    }


def test_remote_scan_start_preserves_paired_controllers_clears_only_scan_results():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [{"address": "BB", "name": "Found", "status": "idle"}],
        timestamp_factory=lambda: "2026-03-23T12:38:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    assert controller.start_connect_if_requested("AA:BB:CC:DD:EE:01", "controllers") is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)
    published.clear()

    assert controller.start_scan_if_requested() is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published[0]["bluetooth"]["scan_results"] == []
    assert published[0]["bluetooth"]["controllers"] == [
        {"name": "", "mac": "AA:BB:CC:DD:EE:01", "status": "connected"}
    ]


def test_remote_scan_controller_clears_then_republishes_scan_results_each_scan():
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
        if len(published) >= 2:
            break
        threading.Event().wait(0.01)

    assert controller.start_scan_if_requested() is True
    for _ in range(40):
        if len(published) >= 4:
            break
        threading.Event().wait(0.01)

    assert len(published) == 4
    assert published[0]["bluetooth"]["scan_results"] == []
    assert published[1] == {
        "bluetooth": {
            "is_scan": False,
            "controllers": [],
            "scan_results": [{"name": "Pad", "mac": "AA", "status": "connected"}],
            "last_scan_at": "2026-03-23T12:37:56+00:00",
        }
    }
    assert published[2]["bluetooth"]["scan_results"] == []
    assert published[3]["bluetooth"]["last_scan_at"] == "2026-03-23T12:37:56+00:00"


def test_remote_connect_controller_success_sets_connected_and_clears_connect():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:40:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
    )

    assert controller.start_connect_if_requested("AA:BB:CC:DD:EE:FF", "scan_results") is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published[-1]["bluetooth"]["scan_results"] == []
    assert published[-1]["bluetooth"]["controllers"] == [
        {"name": "", "mac": "AA:BB:CC:DD:EE:FF", "status": "connected"}
    ]


def test_remote_connect_preserves_scan_result_name_when_upserting_controller():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:42:56+00:00",
        publish_update=lambda payload: published.append(copy.deepcopy(payload)),
        connect_device=lambda address: (True, ""),
    )

    controller._state["scan_results"] = [
        {
            "name": "Xbox Wireless Controller",
            "mac": "AA:BB:CC:DD:EE:FF",
            "status": "idle",
        }
    ]

    assert controller.start_connect_if_requested("AA:BB:CC:DD:EE:FF", "scan_results") is True

    assert len(published) >= 1
    assert published[0]["bluetooth"]["controllers"] == [
        {
            "name": "Xbox Wireless Controller",
            "mac": "AA:BB:CC:DD:EE:FF",
            "status": "connecting",
        }
    ]
    assert published[0]["bluetooth"]["scan_results"] == []


def test_list_paired_devices_filters_by_class_and_queries_once(monkeypatch):
    monkeypatch.setattr(
        bluetooth_operations,
        "_list_paired_devices_raw",
        lambda: {
            "action": "bluetooth_list",
            "devices": [
                {"address": "aa:bb:cc:dd:ee:01", "name": "Pad"},
                {"address": "AA:BB:CC:DD:EE:01", "name": "Duplicate"},
                {"address": "11:22:33:44:55:66", "name": "Speaker"},
            ],
        },
    )
    calls = []

    def properties(address):
        calls.append(address)
        return {
            "AA:BB:CC:DD:EE:01": {"connected": False, "class": 0x2508},
            "11:22:33:44:55:66": {"connected": True, "class": 0x2404},
        }[address]

    monkeypatch.setattr(
        bluetooth_operations,
        "_get_bluetooth_device_properties",
        properties,
    )

    assert bluetooth_operations.list_paired_devices() == {
        "action": "bluetooth_list",
        "devices": [
            {"address": "AA:BB:CC:DD:EE:01", "name": "Pad"},
        ],
    }
    assert calls == ["AA:BB:CC:DD:EE:01", "11:22:33:44:55:66"]


def test_list_paired_devices_with_status_filters_by_class_and_queries_once(monkeypatch):
    monkeypatch.setattr(
        bluetooth_operations,
        "_list_paired_devices_raw",
        lambda: {
            "action": "bluetooth_list",
            "devices": [
                {"address": "aa:bb:cc:dd:ee:01", "name": "Pad A"},
                {"address": "AA:BB:CC:DD:EE:01", "name": "Duplicate"},
                {"address": "11:22:33:44:55:66", "name": "Speaker"},
                {"address": "22:33:44:55:66:77", "name": "Joystick"},
            ],
        },
    )
    calls = []

    def properties(address):
        calls.append(address)
        return {
            "AA:BB:CC:DD:EE:01": {"connected": True, "class": 0x2508},
            "11:22:33:44:55:66": {"connected": True, "class": 0x2404},
            "22:33:44:55:66:77": {"connected": False, "class": 0x2504},
        }[address]

    monkeypatch.setattr(
        bluetooth_operations,
        "_get_bluetooth_device_properties",
        properties,
    )

    assert bluetooth_operations.list_paired_devices_with_status() == [
        {
            "address": "AA:BB:CC:DD:EE:01",
            "name": "Pad A",
            "status": "connected",
        },
        {
            "address": "22:33:44:55:66:77",
            "name": "Joystick",
            "status": "disconnected",
        },
    ]
    assert calls == [
        "AA:BB:CC:DD:EE:01",
        "11:22:33:44:55:66",
        "22:33:44:55:66:77",
    ]


def test_list_connected_paired_devices_filters_class_status_and_dedupes(monkeypatch):
    monkeypatch.setattr(
        bluetooth_operations,
        "_list_paired_devices_raw",
        lambda: {
            "action": "bluetooth_list",
            "devices": [
                {"address": "aa:bb:cc:dd:ee:01", "name": "Pad A"},
                {"address": "AA:BB:CC:DD:EE:01", "name": "Pad A dup"},
                {"address": "11:22:33:44:55:66", "name": "Speaker"},
                {"address": "22:33:44:55:66:77", "name": "Idle Pad"},
            ],
        },
    )
    calls = []

    def properties(address):
        calls.append(address)
        return {
            "AA:BB:CC:DD:EE:01": {"connected": True, "class": 0x2508},
            "11:22:33:44:55:66": {"connected": True, "class": 0x2404},
            "22:33:44:55:66:77": {"connected": False, "class": 0x2508},
        }[address]

    monkeypatch.setattr(
        bluetooth_operations,
        "_get_bluetooth_device_properties",
        properties,
    )

    assert bluetooth_operations.list_connected_paired_devices() == [
        {"address": "AA:BB:CC:DD:EE:01", "name": "Pad A"},
    ]
    assert calls == [
        "AA:BB:CC:DD:EE:01",
        "11:22:33:44:55:66",
        "22:33:44:55:66:77",
    ]


def test_start_scan_adds_missing_connected_controller_when_list_empty():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:50:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
        connected_controllers_provider=lambda: [
            {"address": "AA:BB:CC:DD:EE:FF", "name": "Game Pad"}
        ],
    )

    assert controller.start_scan_if_requested(sync_connected_controllers=True) is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published[0]["bluetooth"]["is_scan"] is True
    assert published[0]["bluetooth"]["controllers"] == [
        {"name": "Game Pad", "mac": "AA:BB:CC:DD:EE:FF", "status": "connected"}
    ]


def test_start_scan_adds_missing_connected_without_touching_existing():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:51:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
        connected_controllers_provider=lambda: [
            {"address": "AA:BB:CC:DD:EE:01", "name": "New name"},
            {"address": "BB:CC:DD:EE:FF:00", "name": "Pad B"},
        ],
    )
    controller._state["controllers"] = [
        {"name": "Old", "mac": "AA:BB:CC:DD:EE:01", "status": "idle"}
    ]

    assert controller.start_scan_if_requested(sync_connected_controllers=True) is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    controllers = published[0]["bluetooth"]["controllers"]
    assert controllers[0] == {
        "name": "Old",
        "mac": "AA:BB:CC:DD:EE:01",
        "status": "idle",
    }
    assert controllers[1] == {
        "name": "Pad B",
        "mac": "BB:CC:DD:EE:FF:00",
        "status": "connected",
    }


def test_start_scan_merge_dedupes_by_mac():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:52:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
        connected_controllers_provider=lambda: [
            {"address": "AA:BB:CC:DD:EE:FF", "name": "One"},
            {"address": "AA:BB:CC:DD:EE:FF", "name": "Two"},
        ],
    )

    assert controller.start_scan_if_requested(sync_connected_controllers=True) is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert len(published[0]["bluetooth"]["controllers"]) == 1
    assert published[0]["bluetooth"]["controllers"][0]["mac"] == "AA:BB:CC:DD:EE:FF"


def test_start_scan_skips_os_merge_when_flag_false():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:53:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
        connected_controllers_provider=lambda: [
            {"address": "AA:BB:CC:DD:EE:FF", "name": "Game Pad"}
        ],
    )

    assert controller.start_scan_if_requested(sync_connected_controllers=False) is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published[0]["bluetooth"]["controllers"] == []


def test_remote_connect_controller_failure_sets_error_and_clears_connect():
    published = []

    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-03-23T12:41:56+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (False, "Auth failed"),
    )

    assert controller.start_connect_if_requested("11:22:33:44:55:66", "controllers") is True
    for _ in range(40):
        if published:
            break
        threading.Event().wait(0.01)

    assert published[-1]["bluetooth"]["controllers"] == [
        {
            "name": "",
            "mac": "11:22:33:44:55:66",
            "status": "error",
            "last_error": "Auth failed",
        }
    ]


def test_state_snapshot_is_detached_from_controller_state():
    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-07-24T00:00:00+00:00",
        publish_update=lambda payload: None,
        connect_device=lambda address: (True, ""),
    )
    controller._state["controllers"] = [
        {"name": "Pad", "mac": "AA:BB", "status": "idle"}
    ]

    snapshot = controller.get_state_snapshot()
    snapshot["controllers"][0]["status"] = "error"

    assert controller.get_state_snapshot()["controllers"][0]["status"] == "idle"


def test_async_remembered_refresh_merges_status_and_preserves_error():
    published = []
    controller = RemoteBluetoothScanController(
        scan_builder=lambda: [],
        timestamp_factory=lambda: "2026-07-24T00:00:00+00:00",
        publish_update=lambda payload: published.append(payload),
        connect_device=lambda address: (True, ""),
        remembered_controllers_provider=lambda: [
            {"address": "AA:BB", "name": "Connected", "status": "connected"},
            {"address": "CC:DD", "name": "Retry", "status": "idle"},
        ],
    )
    controller._state["controllers"] = [
        {"name": "Retry", "mac": "CC:DD", "status": "error", "last_error": "No"}
    ]

    assert controller.refresh_remembered_if_requested() is True
    for _ in range(100):
        if published:
            break
        threading.Event().wait(0.01)

    snapshot = controller.get_state_snapshot()
    assert snapshot["controllers"][0]["status"] == "error"
    assert snapshot["controllers"][0]["last_error"] == "No"
    assert snapshot["controllers"][1] == {
        "name": "Connected",
        "mac": "AA:BB",
        "status": "connected",
    }


def test_scan_refreshes_remembered_devices_without_blocking_scan_start():
    scan_gate = threading.Event()
    published = []

    def scan_builder():
        scan_gate.wait(timeout=1)
        return [{"address": "EE:FF", "name": "Found", "status": "idle"}]

    controller = RemoteBluetoothScanController(
        scan_builder=scan_builder,
        timestamp_factory=lambda: "2026-07-24T00:00:00+00:00",
        publish_update=lambda payload: published.append(copy.deepcopy(payload)),
        connect_device=lambda address: (True, ""),
        remembered_controllers_provider=lambda: [
            {"address": "AA:BB", "name": "Paired", "status": "connected"}
        ],
    )

    assert controller.start_scan_if_requested() is True
    assert published[0]["bluetooth"]["is_scan"] is True
    assert published[0]["bluetooth"]["scan_results"] == []
    scan_gate.set()
    for _ in range(100):
        if len(published) >= 2:
            break
        threading.Event().wait(0.01)

    assert published[-1]["bluetooth"]["controllers"] == [
        {"name": "Paired", "mac": "AA:BB", "status": "connected"}
    ]
    assert published[-1]["bluetooth"]["scan_results"] == [
        {"name": "Found", "mac": "EE:FF", "status": "idle"}
    ]
