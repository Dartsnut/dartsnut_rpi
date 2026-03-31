from __future__ import annotations

from runtime.websocket_ports import JsonOpsPort, WebsocketEndpointConfig, WebsocketServiceRegistry


# WebSocket-driven local actions that publish remote state
def test_device_action_updates_brightness_and_publishes_remote(
    run_action, websocket_registry, fake_remote_sync
):
    local_state = {"brightness": None}

    endpoint_config = WebsocketEndpointConfig(
        set_brightness=lambda v: local_state.__setitem__("brightness", int(v))
        or fake_remote_sync.publish_partial_state({"brightness": int(v)})
    )
    result = run_action(
        message={"action": "set_brightness", "req_id": 1, "brightness": 72},
        registry=websocket_registry,
        endpoint_config=endpoint_config,
    )

    assert result["req_id"] == 1
    assert result["payload"]["action"] == "set_brightness"
    assert result["payload"]["message"] == "Success"
    assert local_state["brightness"] == 72
    assert fake_remote_sync.published[-1] == {"brightness": 72}


def test_device_action_updates_volume_and_publishes_remote(
    run_action, websocket_registry, fake_remote_sync
):
    local_state = {"volume": None}
    endpoint_config = WebsocketEndpointConfig(
        set_volume=lambda v: local_state.__setitem__("volume", int(v))
        or fake_remote_sync.publish_partial_state({"volume": int(v)})
    )
    result = run_action(
        message={"action": "set_volume", "req_id": 2, "volume": 48},
        registry=websocket_registry,
        endpoint_config=endpoint_config,
    )

    assert result["req_id"] == 2
    assert result["payload"]["action"] == "set_volume"
    assert result["payload"]["message"] == "Success"
    assert local_state["volume"] == 48
    assert fake_remote_sync.published[-1] == {"volume": 48}


def test_device_action_set_device_name_publishes_remote(run_action, websocket_registry, fake_remote_sync):
    calls: list[str] = []
    registry = WebsocketServiceRegistry(
        file_ops=websocket_registry.file_ops,
        json_ops=JsonOpsPort(
            read_json_file=websocket_registry.json_ops.read_json_file,
            write_json_file=websocket_registry.json_ops.write_json_file,
            get_device_info=websocket_registry.json_ops.get_device_info,
            set_device_name=lambda name: calls.append(name)
            or fake_remote_sync.publish_partial_state({"device_info": {"name": name}})
            or {"action": "set_device_name", "message": "ok"},
        ),
        bluetooth_ops=websocket_registry.bluetooth_ops,
        git_ops=websocket_registry.git_ops,
        device_ops=websocket_registry.device_ops,
        user_data_ops=websocket_registry.user_data_ops,
    )
    result = run_action(
        message={"action": "set_device_name", "req_id": 3, "device_name": "Kitchen"},
        registry=registry,
        endpoint_config=WebsocketEndpointConfig(),
    )

    assert calls == ["Kitchen"]
    assert result["payload"]["action"] == "set_device_name"
    assert fake_remote_sync.published[-1] == {"device_info": {"name": "Kitchen"}}


# Reset-state payload contract
def test_remote_reset_request_uses_expected_payload(fake_remote_sync):
    fake_remote_sync.request_device_reset_state()
    assert fake_remote_sync.published[-1] == {
        "ip_address": "",
        "ssid": "",
        "pages": [],
        "games": [],
        "dim_window": {"dim_window_enabled": False},
    }
