import asyncio

from runtime.machine_action_services import (
    get_widgets_screen_service,
    locate_device_service,
    reload_conf_service,
    set_brightness_service,
    set_time_zone_service,
    set_volume_service,
    start_game_service,
)
from runtime.websocket_ports import WebsocketEndpointConfig


def test_set_brightness_invalid_range_returns_error():
    result = asyncio.run(
        set_brightness_service(
            message={"brightness": "9"},
            endpoint_config=WebsocketEndpointConfig(),
        )
    )
    assert result.payload["action"] == "set_brightness"
    assert result.payload["error_code"] == "3005"


def test_set_volume_invalid_value_returns_error():
    result = asyncio.run(
        set_volume_service(
            message={"volume": "nope"},
            endpoint_config=WebsocketEndpointConfig(),
        )
    )
    assert result.payload["action"] == "set_volume"
    assert result.payload["error_code"] == "3001"


def test_set_time_zone_and_locate_reload_success_callbacks_called():
    calls = []
    cfg = WebsocketEndpointConfig(
        set_time_zone=lambda tz: calls.append(("tz", tz)),
        locate_device=lambda: calls.append(("locate", True)),
        reload_config=lambda: calls.append(("reload", True)),
    )
    tz_result = asyncio.run(set_time_zone_service(message={"time_zone": "UTC"}, endpoint_config=cfg))
    locate_result = asyncio.run(locate_device_service(endpoint_config=cfg))
    reload_result = asyncio.run(reload_conf_service(endpoint_config=cfg))
    assert tz_result.payload["message"] == "Success"
    assert locate_result.payload["message"] == "Success"
    assert reload_result.payload["message"] == "Success"
    assert calls == [("tz", "UTC"), ("locate", True), ("reload", True)]


def test_start_game_not_available_and_failed():
    missing = asyncio.run(
        start_game_service(
            message={"game_id": "g1"},
            endpoint_config=WebsocketEndpointConfig(),
        )
    )
    failed = asyncio.run(
        start_game_service(
            message={"game_id": "g1"},
            endpoint_config=WebsocketEndpointConfig(start_game_process=lambda _gid: False),
        )
    )
    assert missing.payload["error_code"] == "7002"
    assert failed.payload["error_code"] == "4001"


def test_get_widgets_screen_success_and_unavailable():
    unavailable = asyncio.run(
        get_widgets_screen_service(endpoint_config=WebsocketEndpointConfig())
    )
    success = asyncio.run(
        get_widgets_screen_service(
            endpoint_config=WebsocketEndpointConfig(
                get_widgets_framebuffer=lambda: [{"id": "a"}]
            )
        )
    )
    assert unavailable.payload["error_code"] == "7002"
    assert success.payload["framebuffers"] == [{"id": "a"}]
