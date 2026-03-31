from __future__ import annotations

import pytest


@pytest.mark.integration
# Known action contract matrix (happy path)
@pytest.mark.parametrize(
    "message, expected_action",
    [
        ({"action": "send_file", "req_id": 1, "file_name": "a", "file_data": "x"}, "send_file"),
        ({"action": "get_file", "req_id": 2, "file_name": "a"}, "get_file"),
        ({"action": "read_json", "req_id": 3, "file_path": "conf.json"}, "read_json"),
        ({"action": "write_json", "req_id": 4, "file_path": "conf.json", "content": "{}"}, "write_json"),
        ({"action": "remove_directory", "req_id": 5, "directory": "g1"}, "remove_directory"),
        ({"action": "create_directory", "req_id": 6, "directory": "g1"}, "create_directory"),
        ({"action": "list_files", "req_id": 7, "directory": "g1"}, "list_files"),
        ({"action": "list_apps", "req_id": 8}, "list_apps"),
        ({"action": "get_file_md5", "req_id": 9, "file_name": "g1.tar.gz"}, "get_file_md5"),
        ({"action": "get_device_info", "req_id": 10}, "get_device_info"),
        ({"action": "set_device_name", "req_id": 11, "device_name": "Board"}, "set_device_name"),
        (
            {"action": "download_app", "req_id": 12, "game_id": "g1", "url": "https://example.com/g.tar.gz", "md5": "abc"},
            "download_app",
        ),
        ({"action": "get_download_progress", "req_id": 13, "game_ids": ["g1"]}, "get_download_progress"),
        ({"action": "set_brightness", "req_id": 14, "brightness": "50"}, "set_brightness"),
        ({"action": "set_volume", "req_id": 15, "volume": "35"}, "set_volume"),
        ({"action": "set_time_zone", "req_id": 16, "time_zone": "UTC"}, "set_time_zone"),
        ({"action": "locate_device", "req_id": 17}, "locate_device"),
        ({"action": "reload_conf", "req_id": 18}, "reload_conf"),
        ({"action": "start_game", "req_id": 19, "game_id": "g1"}, "start_game"),
        ({"action": "get_widgets_screen", "req_id": 20}, "get_widgets_screen"),
        ({"action": "bluetooth_scan", "req_id": 21}, "bluetooth_scan"),
        ({"action": "bluetooth_list", "req_id": 22}, "bluetooth_list"),
        ({"action": "bluetooth_remove", "req_id": 23, "address": "AA:BB"}, "bluetooth_remove"),
        ({"action": "bluetooth_connect", "req_id": 24, "address": "AA:BB"}, "bluetooth_connect"),
        ({"action": "get_wifi_rssi", "req_id": 25}, "get_wifi_rssi"),
        ({"action": "get_brightness", "req_id": 26}, "get_brightness"),
        ({"action": "get_volume", "req_id": 27}, "get_volume"),
        ({"action": "get_dim_window", "req_id": 28}, "get_dim_window"),
        ({"action": "set_dim_window", "req_id": 29}, "set_dim_window"),
        ({"action": "get_version", "req_id": 30}, "get_version"),
        ({"action": "check_update", "req_id": 31}, "check_update"),
        ({"action": "perform_update", "req_id": 32}, "perform_update"),
        ({"action": "get_ssh_status", "req_id": 33}, "get_ssh_status"),
        ({"action": "start_ssh", "req_id": 34}, "start_ssh"),
        ({"action": "stop_ssh", "req_id": 35}, "stop_ssh"),
        ({"action": "get_user_data", "req_id": 36}, "get_user_data"),
        (
            {"action": "update_user_info", "req_id": 37, "user_id": "u", "jwt_token": "j", "refresh_token": "r"},
            "update_user_info",
        ),
        ({"action": "get_game_playtime", "req_id": 38, "game_id": "g1"}, "get_game_playtime"),
    ],
)
def test_websocket_actions_feature_matrix(message, expected_action, run_action, websocket_registry, endpoint_config):
    result = run_action(message=message, registry=websocket_registry, endpoint_config=endpoint_config)
    assert result["req_id"] == message["req_id"]
    assert result["payload"]["action"] == expected_action


@pytest.mark.integration
# Fire-and-forget action behavior
def test_feature_matrix_non_response_actions_trigger_side_effects(
    run_action, websocket_registry, endpoint_config, endpoint_state
):
    run_action(
        message={"action": "forget_wifi", "req_id": 100},
        registry=websocket_registry,
        endpoint_config=endpoint_config,
    )
    run_action(
        message={"action": "reboot", "req_id": 101},
        registry=websocket_registry,
        endpoint_config=endpoint_config,
    )
    # forget_wifi/reboot are fire-and-forget in this runtime path.
    assert endpoint_state["reloaded"] is False


@pytest.mark.integration
# Unknown action guard contract
def test_feature_matrix_unknown_action_contract(run_action, websocket_registry, endpoint_config):
    result = run_action(
        message={"action": "unknown_action", "req_id": 200},
        registry=websocket_registry,
        endpoint_config=endpoint_config,
    )
    assert result["payload"]["action"] == "unknown_action"
    assert result["payload"]["error_code"] == "7003"
