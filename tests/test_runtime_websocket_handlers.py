import asyncio

from runtime.websocket_action_handlers_controls import try_handle_control_actions
from runtime.websocket_action_handlers_file_json import try_handle_file_json_actions
from runtime.websocket_action_handlers_ops import try_handle_ops_actions
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


def _registry():
    return WebsocketServiceRegistry(
        file_ops=FileOpsPort(
            receive_file=lambda *_a, **_k: {"action": "send_file", "message": "ok"},
            send_file=lambda *_a, **_k: {"action": "get_file", "message": "ok"},
            remove_directory=lambda *_a, **_k: {"action": "remove_directory", "message": "ok"},
            get_file_md5=lambda *_a, **_k: {"action": "get_file_md5", "message": "ok"},
            get_file_list=lambda *_a, **_k: {"action": "list_files", "message": "ok"},
            create_directory=lambda *_a, **_k: {"action": "create_directory", "message": "ok"},
            download_app=lambda *_a, **_k: {"action": "download_app", "message": "ok"},
            get_app_list=lambda *_a, **_k: {"action": "list_apps", "message": "ok"},
            start_game_download_async=lambda *_a, **_k: {"action": "download_app", "message": "ok"},
            start_game_download_async_with_url=lambda *_a, **_k: {"action": "download_app", "message": "ok"},
            get_download_progress=lambda *_a, **_k: {"action": "get_download_progress", "message": "ok"},
        ),
        json_ops=JsonOpsPort(
            read_json_file=lambda *_a, **_k: {"action": "read_json", "message": "ok"},
            write_json_file=lambda *_a, **_k: {"action": "write_json", "message": "ok"},
            get_device_info=lambda *_a, **_k: {"action": "get_device_info", "message": "ok"},
            set_device_name=lambda *_a, **_k: {"action": "set_device_name", "message": "ok"},
        ),
        bluetooth_ops=BluetoothOpsPort(
            scan_bluetooth_devices=lambda *_a, **_k: {"action": "bluetooth_scan", "message": "ok"},
            list_paired_devices=lambda *_a, **_k: {"action": "bluetooth_list", "message": "ok"},
            disconnect_and_unpair_device=lambda *_a, **_k: {"action": "bluetooth_remove", "message": "ok"},
            pair_and_connect_device=lambda *_a, **_k: {"action": "bluetooth_connect", "message": "ok"},
        ),
        git_ops=GitOpsPort(
            check_update=lambda *_a, **_k: {"action": "check_update", "message": "ok"},
            perform_update=lambda *_a, **_k: {"action": "perform_update", "message": "ok"},
            get_version=lambda *_a, **_k: {"action": "get_version", "message": "ok"},
        ),
        device_ops=DeviceOpsPort(
            get_wifi_rssi=lambda *_a, **_k: {"action": "get_wifi_rssi", "message": "ok"},
            forget_wifi=lambda *_a, **_k: None,
            reboot=lambda *_a, **_k: None,
            get_ssh_status=lambda *_a, **_k: {"action": "get_ssh_status", "message": "ok"},
            start_ssh=lambda *_a, **_k: {"action": "start_ssh", "message": "ok"},
            stop_ssh=lambda *_a, **_k: {"action": "stop_ssh", "message": "ok"},
            get_brightness=lambda *_a, **_k: {"action": "get_brightness", "message": "ok"},
            get_volume=lambda *_a, **_k: {"action": "get_volume", "message": "ok"},
            get_dim_window=lambda *_a, **_k: {"action": "get_dim_window", "message": "ok"},
            set_dim_window=lambda *_a, **_k: {"action": "set_dim_window", "message": "Success"},
        ),
        user_data_ops=UserDataOpsPort(
            get_user_data=lambda *_a, **_k: {"action": "get_user_data", "message": "ok"},
            update_user_info=lambda *_a, **_k: {"action": "update_user_info", "message": "ok"},
            get_game_playtime=lambda *_a, **_k: {"action": "get_game_playtime", "message": "ok"},
        ),
    )


def test_try_handle_file_json_covers_special_paths():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    reg = _registry()
    handled_missing = asyncio.run(
        try_handle_file_json_actions(
            action="download_app",
            req_id=1,
            message={"action": "download_app"},
            websocket=object(),
            registry=reg,
            send_response=send_response,
        )
    )
    handled_progress = asyncio.run(
        try_handle_file_json_actions(
            action="get_download_progress",
            req_id=2,
            message={"action": "get_download_progress", "game_id": "g1"},
            websocket=object(),
            registry=reg,
            send_response=send_response,
        )
    )
    assert handled_missing is True
    assert handled_progress is True
    assert sent[0][1]["error_code"] == "3002"
    assert sent[1][1]["action"] == "get_download_progress"


def test_try_handle_control_and_ops_cover_non_response_actions():
    sent = []
    dim_triggered = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    cfg = WebsocketEndpointConfig(
        trigger_dim_check=lambda: dim_triggered.append(True),
        set_brightness=lambda _v: None,
        set_volume=lambda _v: None,
        set_time_zone=lambda _tz: None,
        locate_device=lambda: None,
        reload_config=lambda: None,
    )
    reg = _registry()
    assert asyncio.run(
        try_handle_control_actions(
            action="set_brightness",
            req_id=1,
            message={"brightness": "50"},
            endpoint_config=cfg,
            send_response=send_response,
        )
    )
    assert asyncio.run(
        try_handle_ops_actions(
            action="set_dim_window",
            req_id=2,
            message={},
            registry=reg,
            endpoint_config=cfg,
            send_response=send_response,
        )
    )
    assert asyncio.run(
        try_handle_ops_actions(
            action="forget_wifi",
            req_id=3,
            message={},
            registry=reg,
            endpoint_config=cfg,
            send_response=send_response,
        )
    )
    assert asyncio.run(
        try_handle_ops_actions(
            action="reboot",
            req_id=4,
            message={},
            registry=reg,
            endpoint_config=cfg,
            send_response=send_response,
        )
    )
    assert sent[0][1]["action"] == "set_brightness"
    assert sent[1][1]["action"] == "set_dim_window"
    assert dim_triggered == [True]


def test_try_handle_ops_unknown_and_control_unknown():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    handled_control = asyncio.run(
        try_handle_control_actions(
            action="not_control",
            req_id=9,
            message={},
            endpoint_config=WebsocketEndpointConfig(),
            send_response=send_response,
        )
    )
    handled_ops = asyncio.run(
        try_handle_ops_actions(
            action="not_ops",
            req_id=10,
            message={},
            registry=_registry(),
            endpoint_config=WebsocketEndpointConfig(),
            send_response=send_response,
        )
    )
    assert handled_control is False
    assert handled_ops is True
    assert sent[0][1]["error_code"] == "7003"


def test_try_handle_file_json_dispatch_table_actions():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    reg = _registry()
    action_messages = {
        "send_file": {"file_name": "a", "file_data": "x"},
        "get_file": {"file_name": "a"},
        "read_json": {"file_path": "conf.json"},
        "write_json": {"file_path": "conf.json", "content": "{}"},
        "remove_directory": {"directory": "d"},
        "create_directory": {"directory": "d"},
        "list_files": {"directory": "d"},
        "list_apps": {},
        "get_file_md5": {"file_name": "f"},
        "get_device_info": {},
        "set_device_name": {"device_name": "n"},
    }
    for idx, (action, message) in enumerate(action_messages.items(), start=1):
        handled = asyncio.run(
            try_handle_file_json_actions(
                action=action,
                req_id=idx,
                message=message,
                websocket=object(),
                registry=reg,
                send_response=send_response,
            )
        )
        assert handled is True
    assert len(sent) == len(action_messages)


def test_try_handle_ops_dispatch_table_actions():
    sent = []

    async def send_response(req_id, data):
        sent.append((req_id, data))

    reg = _registry()
    cfg = WebsocketEndpointConfig()
    action_messages = {
        "bluetooth_scan": {},
        "bluetooth_list": {},
        "bluetooth_remove": {"address": "aa"},
        "bluetooth_connect": {"address": "aa"},
        "get_wifi_rssi": {},
        "get_brightness": {},
        "get_volume": {},
        "get_dim_window": {},
        "get_version": {},
        "check_update": {},
        "perform_update": {},
        "get_ssh_status": {},
        "start_ssh": {},
        "stop_ssh": {},
        "get_user_data": {},
        "update_user_info": {"user_id": "u", "jwt_token": "j", "refresh_token": "r"},
        "get_game_playtime": {"game_id": "g"},
    }
    for idx, (action, message) in enumerate(action_messages.items(), start=1):
        handled = asyncio.run(
            try_handle_ops_actions(
                action=action,
                req_id=idx,
                message=message,
                registry=reg,
                endpoint_config=cfg,
                send_response=send_response,
            )
        )
        assert handled is True
    assert len(sent) == len(action_messages)
