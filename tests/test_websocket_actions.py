import pytest
import sys
import types
import asyncio

from runtime.websocket_actions import handle_action_message
from runtime.websocket_ports import (
    BluetoothOpsPort,
    DeviceOpsPort,
    FileOpsPort,
    GitOpsPort,
    JsonOpsPort,
    UserDataOpsPort,
    WebsocketEndpointConfig,
    WebsocketServiceRegistry,
)


def _registry(calls):
    return WebsocketServiceRegistry(
        file_ops=FileOpsPort(
            receive_file=lambda *_a, **_k: {"action": "send_file", "message": "ok"},
            send_file=lambda *_a, **_k: {"action": "get_file", "message": "ok"},
            remove_directory=lambda *_a, **_k: {"message": "ok"},
            get_file_md5=lambda *_a, **_k: {"message": "ok"},
            get_file_list=lambda *_a, **_k: {"message": "ok"},
            create_directory=lambda *_a, **_k: {"message": "ok"},
            download_app=lambda *_a, **_k: {"message": "ok"},
            get_app_list=lambda *_a, **_k: {"message": "ok"},
            start_game_download_async=lambda *_a, **_k: {"message": "ok"},
            start_game_download_async_with_url=lambda *_a, **_k: {"message": "ok"},
            get_download_progress=lambda *_a, **_k: {"message": "ok"},
        ),
        json_ops=JsonOpsPort(
            read_json_file=lambda path: calls.append(("read_json", path))
            or {"action": "read_json", "message": "ok"},
            write_json_file=lambda *_a, **_k: {"message": "ok"},
            get_device_info=lambda: {"action": "get_device_info", "message": "ok"},
            set_device_name=lambda *_a, **_k: {"message": "ok"},
        ),
        bluetooth_ops=BluetoothOpsPort(
            scan_bluetooth_devices=lambda *_a, **_k: {"message": "ok"},
            list_paired_devices=lambda *_a, **_k: {"message": "ok"},
            disconnect_and_unpair_device=lambda *_a, **_k: {"message": "ok"},
            pair_and_connect_device=lambda *_a, **_k: {"message": "ok"},
        ),
        git_ops=GitOpsPort(
            check_update=lambda *_a, **_k: {"message": "ok"},
            perform_update=lambda *_a, **_k: {"message": "ok"},
            get_version=lambda *_a, **_k: {"message": "ok"},
        ),
        device_ops=DeviceOpsPort(
            get_wifi_rssi=lambda *_a, **_k: {"message": "ok"},
            forget_wifi=lambda *_a, **_k: None,
            reboot=lambda *_a, **_k: None,
            get_ssh_status=lambda *_a, **_k: {"message": "ok"},
            start_ssh=lambda *_a, **_k: {"message": "ok"},
            stop_ssh=lambda *_a, **_k: {"message": "ok"},
            get_brightness=lambda *_a, **_k: {"message": "ok"},
            get_volume=lambda *_a, **_k: {"message": "ok"},
            get_dim_window=lambda *_a, **_k: {"message": "ok"},
            set_dim_window=lambda *_a, **_k: {"message": "ok"},
        ),
        user_data_ops=UserDataOpsPort(
            get_user_data=lambda *_a, **_k: {"message": "ok"},
            update_user_info=lambda *_a, **_k: {"message": "ok"},
            get_game_playtime=lambda *_a, **_k: {"message": "ok"},
        ),
    )


def test_handle_action_routes_read_json():
    calls = []
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    asyncio.run(
        handle_action_message(
            websocket=object(),
            message={"action": "read_json", "req_id": 7, "file_path": "conf.json"},
            registry=_registry(calls),
            endpoint_config=WebsocketEndpointConfig(),
            send_response=send_response,
        )
    )

    assert calls == [("read_json", "conf.json")]
    assert sent[0][0] == 7
    assert sent[0][1]["action"] == "read_json"


def test_handle_action_validates_brightness_and_calls_callback():
    bright = []
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    asyncio.run(
        handle_action_message(
            websocket=object(),
            message={"action": "set_brightness", "req_id": 3, "brightness": "42"},
            registry=_registry([]),
            endpoint_config=WebsocketEndpointConfig(
                set_brightness=lambda v: bright.append(v)
            ),
            send_response=send_response,
        )
    )

    assert bright == [42]
    assert sent[0][1]["message"] == "Success"


def test_handle_action_invalid_brightness_returns_expected_error_shape():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    asyncio.run(
        handle_action_message(
            websocket=object(),
            message={"action": "set_brightness", "req_id": 4, "brightness": "105"},
            registry=_registry([]),
            endpoint_config=WebsocketEndpointConfig(),
            send_response=send_response,
        )
    )

    assert sent[0][0] == 4
    assert sent[0][1]["action"] == "set_brightness"
    assert sent[0][1]["error_code"] == "3005"
    assert "Brightness must be between 10 and 100" in sent[0][1]["error"]


def test_handle_action_unknown_action_returns_error():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    asyncio.run(
        handle_action_message(
            websocket=object(),
            message={"action": "not_real", "req_id": 9},
            registry=_registry([]),
            endpoint_config=WebsocketEndpointConfig(),
            send_response=send_response,
        )
    )

    assert sent[0][0] == 9
    assert sent[0][1]["action"] == "not_real"
    assert "error_code" in sent[0][1]


def test_handle_action_wraps_exceptions_with_compatible_error():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    reg = _registry([])
    reg = WebsocketServiceRegistry(
        file_ops=reg.file_ops,
        json_ops=JsonOpsPort(
            read_json_file=lambda *_a, **_k: (_ for _ in ()).throw(ValueError("boom")),
            write_json_file=reg.json_ops.write_json_file,
            get_device_info=reg.json_ops.get_device_info,
            set_device_name=reg.json_ops.set_device_name,
        ),
        bluetooth_ops=reg.bluetooth_ops,
        git_ops=reg.git_ops,
        device_ops=reg.device_ops,
        user_data_ops=reg.user_data_ops,
    )

    asyncio.run(
        handle_action_message(
            websocket=object(),
            message={"action": "read_json", "req_id": 10, "file_path": "conf.json"},
            registry=reg,
            endpoint_config=WebsocketEndpointConfig(),
            send_response=send_response,
        )
    )

    assert sent[0][0] == 10
    assert sent[0][1]["action"] == "read_json"
    assert sent[0][1]["error_code"] == "3001"


def test_build_default_registry_smoke():
    sys.modules.setdefault("bluetooth", types.SimpleNamespace())
    from runtime.websocket_service_registry import build_default_websocket_registry

    reg = build_default_websocket_registry()
    assert reg.file_ops is not None
    assert reg.device_ops is not None
