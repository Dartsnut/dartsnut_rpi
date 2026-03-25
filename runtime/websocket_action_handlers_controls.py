from __future__ import annotations
from typing import Any, Awaitable, Callable

from runtime.machine_action_services import (
    get_widgets_screen_service,
    locate_device_service,
    reload_conf_service,
    set_brightness_service,
    set_time_zone_service,
    set_volume_service,
    start_game_service,
)
from runtime.machine_result import to_websocket_payload
from runtime.websocket_ports import WebsocketEndpointConfig

SendResponse = Callable[[Any, dict], Awaitable[None]]
ActionHandler = Callable[..., Awaitable[None]]


async def _handle_set_brightness(*, req_id, message, endpoint_config, send_response):
    result = await set_brightness_service(message=message, endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


async def _handle_set_volume(*, req_id, message, endpoint_config, send_response):
    result = await set_volume_service(message=message, endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


async def _handle_set_time_zone(*, req_id, message, endpoint_config, send_response):
    result = await set_time_zone_service(message=message, endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


async def _handle_locate_device(*, req_id, message, endpoint_config, send_response):
    result = await locate_device_service(endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


async def _handle_reload_conf(*, req_id, message, endpoint_config, send_response):
    result = await reload_conf_service(endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


async def _handle_start_game(*, req_id, message, endpoint_config, send_response):
    result = await start_game_service(message=message, endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


async def _handle_get_widgets_screen(*, req_id, message, endpoint_config, send_response):
    result = await get_widgets_screen_service(endpoint_config=endpoint_config)
    await send_response(req_id, to_websocket_payload(result))


CONTROL_ACTION_HANDLERS: dict[str, ActionHandler] = {
    "set_brightness": _handle_set_brightness,
    "set_volume": _handle_set_volume,
    "set_time_zone": _handle_set_time_zone,
    "locate_device": _handle_locate_device,
    "reload_conf": _handle_reload_conf,
    "start_game": _handle_start_game,
    "get_widgets_screen": _handle_get_widgets_screen,
}


async def try_handle_control_actions(
    *,
    action: str | None,
    req_id: Any,
    message: dict,
    endpoint_config: WebsocketEndpointConfig,
    send_response: SendResponse,
) -> bool:
    if action in CONTROL_ACTION_HANDLERS:
        await CONTROL_ACTION_HANDLERS[action](
            req_id=req_id,
            message=message,
            endpoint_config=endpoint_config,
            send_response=send_response,
        )
        return True

    return False
