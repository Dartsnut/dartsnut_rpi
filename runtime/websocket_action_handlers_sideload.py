"""WebSocket transport adapter for local sideload sessions."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Awaitable, Callable

from runtime.sideload_session import SideloadError
from runtime.websocket_ports import WebsocketEndpointConfig

SendResponse = Callable[[Any, dict], Awaitable[None]]

SIDELOAD_ACTIONS = {
    "sideload_capabilities",
    "sideload_upload",
    "sideload_start",
    "sideload_heartbeat",
    "sideload_stop",
    "sideload_logs",
    "sideload_frame",
    "sideload_update_params",
}


def _event_emitter(
    loop: asyncio.AbstractEventLoop,
    send_response: SendResponse,
) -> Callable[[dict], None]:
    def emit(payload: dict) -> None:
        future = asyncio.run_coroutine_threadsafe(send_response(None, payload), loop)
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    return emit


async def try_handle_sideload_actions(
    *,
    action: str | None,
    req_id: Any,
    message: dict,
    endpoint_config: WebsocketEndpointConfig,
    send_response: SendResponse,
) -> bool:
    if action not in SIDELOAD_ACTIONS:
        return False
    manager = endpoint_config.sideload_manager
    if manager is None:
        await send_response(
            req_id,
            {
                "action": action,
                "error_code": "7002",
                "error": "Safe sideload sessions are unavailable",
            },
        )
        return True
    try:
        if action == "sideload_capabilities":
            result = manager.capabilities()
        elif action == "sideload_upload":
            try:
                data = base64.b64decode(message.get("file_data") or "", validate=True)
            except Exception:
                raise SideloadError("file_data must be valid base64") from None
            result = await asyncio.to_thread(
                manager.upload_file,
                message.get("session_id"),
                message.get("app_id"),
                message.get("relative_path"),
                data,
            )
        elif action == "sideload_start":
            emit = _event_emitter(asyncio.get_running_loop(), send_response)
            result = await asyncio.to_thread(
                manager.start,
                session_id=message.get("session_id"),
                app_id=message.get("app_id"),
                size=message.get("size"),
                params=message.get("params"),
                emit=emit,
            )
        elif action == "sideload_heartbeat":
            result = await asyncio.to_thread(manager.heartbeat, message.get("session_id"))
        elif action == "sideload_stop":
            result = await asyncio.to_thread(manager.stop, message.get("session_id"))
        elif action == "sideload_frame":
            result = await asyncio.to_thread(
                manager.frame,
                message.get("session_id"),
                raw=bool(message.get("raw", False)),
            )
        elif action == "sideload_update_params":
            result = await asyncio.to_thread(
                manager.update_params,
                message.get("session_id"),
                message.get("params"),
            )
        else:
            emit = _event_emitter(asyncio.get_running_loop(), send_response)
            result = await asyncio.to_thread(manager.logs, message.get("session_id"), emit)
        await send_response(req_id, result)
    except SideloadError as exc:
        await send_response(
            req_id,
            {"action": action, "error_code": "3001", "error": str(exc)},
        )
    return True
