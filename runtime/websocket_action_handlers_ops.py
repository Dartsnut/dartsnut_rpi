from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from runtime.machine_action_services import invoke, unknown_action
from runtime.machine_result import to_websocket_payload
from runtime.websocket_ports import WebsocketEndpointConfig, WebsocketServiceRegistry

SendResponse = Callable[[Any, dict], Awaitable[None]]
ActionHandler = Callable[..., Awaitable[None]]


async def _respond(send_response: SendResponse, req_id: Any, result: Any) -> None:
    await send_response(req_id, to_websocket_payload(result))


async def _handle_bluetooth_scan(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("bluetooth_scan", registry.bluetooth_ops.scan_bluetooth_devices)
    await _respond(send_response, req_id, result)


async def _handle_bluetooth_list(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("bluetooth_list", registry.bluetooth_ops.list_paired_devices)
    await _respond(send_response, req_id, result)


async def _handle_bluetooth_remove(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke(
        "bluetooth_remove",
        registry.bluetooth_ops.disconnect_and_unpair_device, message.get("address")
    )
    await _respond(send_response, req_id, result)


async def _handle_bluetooth_connect(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke(
        "bluetooth_connect",
        registry.bluetooth_ops.pair_and_connect_device, message.get("address")
    )
    await _respond(send_response, req_id, result)


async def _handle_get_wifi_rssi(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_wifi_rssi", registry.device_ops.get_wifi_rssi)
    await _respond(send_response, req_id, result)


async def _handle_get_brightness(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_brightness", registry.device_ops.get_brightness)
    await _respond(send_response, req_id, result)


async def _handle_get_volume(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_volume", registry.device_ops.get_volume)
    await _respond(send_response, req_id, result)


async def _handle_get_dim_window(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_dim_window", registry.device_ops.get_dim_window)
    await _respond(send_response, req_id, result)


async def _handle_set_dim_window(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke(
        "set_dim_window",
        registry.device_ops.set_dim_window,
        message.get("dim_window_start"),
        message.get("dim_window_end"),
        message.get("dim_level"),
        message.get("dim_restore_seconds"),
        message.get("dim_window_enabled"),
    )
    payload = to_websocket_payload(result)
    await send_response(req_id, payload)
    if payload.get("message") == "Success" and endpoint_config.trigger_dim_check:
        await asyncio.to_thread(endpoint_config.trigger_dim_check)


async def _handle_forget_wifi(*, req_id, message, registry, endpoint_config, send_response):
    await asyncio.to_thread(registry.device_ops.forget_wifi)


async def _handle_get_version(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_version", registry.git_ops.get_version)
    await _respond(send_response, req_id, result)


async def _handle_check_update(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("check_update", registry.git_ops.check_update)
    await _respond(send_response, req_id, result)


async def _handle_perform_update(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("perform_update", registry.git_ops.perform_update)
    await _respond(send_response, req_id, result)


async def _handle_reboot(*, req_id, message, registry, endpoint_config, send_response):
    await asyncio.to_thread(registry.device_ops.reboot)


async def _handle_get_ssh_status(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_ssh_status", registry.device_ops.get_ssh_status)
    await _respond(send_response, req_id, result)


async def _handle_start_ssh(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("start_ssh", registry.device_ops.start_ssh)
    await _respond(send_response, req_id, result)


async def _handle_stop_ssh(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("stop_ssh", registry.device_ops.stop_ssh)
    await _respond(send_response, req_id, result)


async def _handle_get_user_data(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke("get_user_data", registry.user_data_ops.get_user_data)
    await _respond(send_response, req_id, result)


async def _handle_update_user_info(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke(
        "update_user_info",
        registry.user_data_ops.update_user_info,
        user_id=message.get("user_id"),
        jwt_token=message.get("jwt_token"),
        refresh_token=message.get("refresh_token"),
    )
    await _respond(send_response, req_id, result)


async def _handle_get_game_playtime(*, req_id, message, registry, endpoint_config, send_response):
    result = await invoke(
        "get_game_playtime",
        registry.user_data_ops.get_game_playtime, message.get("game_id")
    )
    await _respond(send_response, req_id, result)


OPS_ACTION_HANDLERS: dict[str, ActionHandler] = {
    "bluetooth_scan": _handle_bluetooth_scan,
    "bluetooth_list": _handle_bluetooth_list,
    "bluetooth_remove": _handle_bluetooth_remove,
    "bluetooth_connect": _handle_bluetooth_connect,
    "get_wifi_rssi": _handle_get_wifi_rssi,
    "get_brightness": _handle_get_brightness,
    "get_volume": _handle_get_volume,
    "get_dim_window": _handle_get_dim_window,
    "set_dim_window": _handle_set_dim_window,
    "forget_wifi": _handle_forget_wifi,
    "get_version": _handle_get_version,
    "check_update": _handle_check_update,
    "perform_update": _handle_perform_update,
    "reboot": _handle_reboot,
    "get_ssh_status": _handle_get_ssh_status,
    "start_ssh": _handle_start_ssh,
    "stop_ssh": _handle_stop_ssh,
    "get_user_data": _handle_get_user_data,
    "update_user_info": _handle_update_user_info,
    "get_game_playtime": _handle_get_game_playtime,
}


async def try_handle_ops_actions(
    *,
    action: str | None,
    req_id: Any,
    message: dict,
    registry: WebsocketServiceRegistry,
    endpoint_config: WebsocketEndpointConfig,
    send_response: SendResponse,
) -> bool:
    if action in OPS_ACTION_HANDLERS:
        await OPS_ACTION_HANDLERS[action](
            req_id=req_id,
            message=message,
            registry=registry,
            endpoint_config=endpoint_config,
            send_response=send_response,
        )
        return True

    await send_response(
        req_id,
        to_websocket_payload(unknown_action(action)),
    )
    return True
