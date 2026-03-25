from __future__ import annotations

from typing import Any, Awaitable, Callable

from runtime.websocket_action_handlers_controls import try_handle_control_actions
from runtime.websocket_action_handlers_file_json import try_handle_file_json_actions
from runtime.websocket_action_handlers_ops import try_handle_ops_actions
from runtime.websocket_ports import WebsocketEndpointConfig, WebsocketServiceRegistry

SendResponse = Callable[[Any, dict], Awaitable[None]]


async def handle_action_message(
    *,
    websocket: Any,
    message: dict,
    registry: WebsocketServiceRegistry,
    endpoint_config: WebsocketEndpointConfig,
    send_response: SendResponse,
) -> None:
    action = message.get("action")
    req_id = message.get("req_id")
    if await try_handle_file_json_actions(
        action=action,
        req_id=req_id,
        message=message,
        websocket=websocket,
        registry=registry,
        send_response=send_response,
    ):
        return

    if await try_handle_control_actions(
        action=action,
        req_id=req_id,
        message=message,
        endpoint_config=endpoint_config,
        send_response=send_response,
    ):
        return

    await try_handle_ops_actions(
        action=action,
        req_id=req_id,
        message=message,
        registry=registry,
        endpoint_config=endpoint_config,
        send_response=send_response,
    )
