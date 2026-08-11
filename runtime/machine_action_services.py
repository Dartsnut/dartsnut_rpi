from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from python_websocket.error_handler import ErrorCode, create_error_response, handle_exception
from runtime.machine_result import MachineResult
from runtime.websocket_ports import WebsocketEndpointConfig

_log = logging.getLogger(__name__)


def ok(action: str, **fields: Any) -> MachineResult:
    payload = {"action": action, **fields}
    return MachineResult(payload=payload)


def error(action: str, error_code: ErrorCode | str, message: str) -> MachineResult:
    return MachineResult(payload=create_error_response(action, error_code, message))


def unknown_action(action: str | None) -> MachineResult:
    return error(
        action or "unknown",
        ErrorCode.UNKNOWN_ACTION,
        "The requested action is not recognized",
    )


def from_exception(action: str, exc: Exception, context: str) -> MachineResult:
    return MachineResult(payload=handle_exception(action, exc, context))


async def invoke(action: str, fn: Callable[..., Any], *args: Any, context: str | None = None, **kwargs: Any) -> MachineResult:
    try:
        payload = await asyncio.to_thread(fn, *args, **kwargs)
        return MachineResult(payload=payload)
    except Exception as exc:  # pragma: no cover - defensive boundary
        return from_exception(action, exc, context or f"Failed to {action.replace('_', ' ')}")


async def set_brightness_service(
    *,
    message: dict[str, Any],
    endpoint_config: WebsocketEndpointConfig,
) -> MachineResult:
    action = "set_brightness"
    try:
        brightness = int(message.get("brightness", "-1"))
        if not 0 <= brightness <= 9:
            return error(action, ErrorCode.INVALID_BRIGHTNESS, "Brightness must be between 0 and 9")
        if endpoint_config.set_brightness:
            await asyncio.to_thread(endpoint_config.set_brightness, brightness)
        return ok(action, message="Success")
    except ValueError as exc:
        return from_exception(action, exc, "Invalid brightness value")
    except Exception as exc:  # pragma: no cover - defensive boundary
        return from_exception(action, exc, "Failed to set brightness")


async def set_volume_service(
    *,
    message: dict[str, Any],
    endpoint_config: WebsocketEndpointConfig,
) -> MachineResult:
    action = "set_volume"
    try:
        volume = int(message.get("volume", "0"))
        if not 0 <= volume <= 100:
            return error(action, ErrorCode.INVALID_INPUT, "Volume must be between 0 and 100")
        if endpoint_config.set_volume:
            await asyncio.to_thread(endpoint_config.set_volume, volume)
        return ok(action, message="Success")
    except ValueError as exc:
        return from_exception(action, exc, "Invalid volume value")
    except Exception as exc:  # pragma: no cover - defensive boundary
        return from_exception(action, exc, "Failed to set volume")


async def set_time_zone_service(
    *,
    message: dict[str, Any],
    endpoint_config: WebsocketEndpointConfig,
) -> MachineResult:
    if endpoint_config.set_time_zone:
        await asyncio.to_thread(endpoint_config.set_time_zone, message.get("time_zone", "UTC"))
    return ok("set_time_zone", message="Success")


async def locate_device_service(*, endpoint_config: WebsocketEndpointConfig) -> MachineResult:
    if endpoint_config.locate_device:
        await asyncio.to_thread(endpoint_config.locate_device)
    return ok("locate_device", message="Success")


async def reload_conf_service(*, endpoint_config: WebsocketEndpointConfig) -> MachineResult:
    if endpoint_config.reload_config:
        await asyncio.to_thread(endpoint_config.reload_config)
        _log.info("reload_conf: widget/apps configuration reloaded from disk")
    return ok("reload_conf", message="Success")


async def start_game_service(
    *,
    message: dict[str, Any],
    endpoint_config: WebsocketEndpointConfig,
) -> MachineResult:
    if not endpoint_config.start_game_process:
        return error("start_game", ErrorCode.FUNCTION_NOT_AVAILABLE, "This feature is not available")
    try:
        game_id = message.get("game_id")
        started = await asyncio.to_thread(endpoint_config.start_game_process, game_id)
        if started:
            _log.info("start_game: WebSocket accepted game_id=%s (main loop will launch)", game_id)
            return ok("start_game", message="Game started")
        return error("start_game", ErrorCode.COMMAND_FAILED, "Unable to start the game")
    except Exception as exc:  # pragma: no cover - defensive boundary
        return from_exception("start_game", exc, "Failed to start game")


async def get_widgets_screen_service(*, endpoint_config: WebsocketEndpointConfig) -> MachineResult:
    if not endpoint_config.get_widgets_framebuffer:
        return error(
            "get_widgets_screen",
            ErrorCode.FUNCTION_NOT_AVAILABLE,
            "This feature is not available",
        )
    try:
        framebuffers = await asyncio.to_thread(endpoint_config.get_widgets_framebuffer)
        return ok("get_widgets_screen", framebuffers=framebuffers)
    except Exception as exc:  # pragma: no cover - defensive boundary
        return from_exception("get_widgets_screen", exc, "Failed to get widgets screen")
