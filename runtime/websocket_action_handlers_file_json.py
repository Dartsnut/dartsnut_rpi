from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from python_websocket.error_handler import ErrorCode
from runtime.machine_action_services import error, invoke
from runtime.machine_result import to_websocket_payload
from runtime.websocket_ports import WebsocketServiceRegistry

SendResponse = Callable[[Any, dict], Awaitable[None]]

ActionHandler = Callable[..., Awaitable[None]]


async def _respond(send_response: SendResponse, req_id: Any, result: Any) -> None:
    await send_response(req_id, to_websocket_payload(result))


async def _handle_send_file(*, req_id, message, websocket, registry, send_response):
    result = await invoke("send_file", registry.file_ops.receive_file, websocket, message)
    await _respond(send_response, req_id, result)


async def _handle_get_file(*, req_id, message, websocket, registry, send_response):
    result = await invoke("get_file", registry.file_ops.send_file, websocket, message)
    await _respond(send_response, req_id, result)


async def _handle_read_json(*, req_id, message, websocket, registry, send_response):
    result = await invoke("read_json", registry.json_ops.read_json_file, message.get("file_path"))
    await _respond(send_response, req_id, result)


async def _handle_write_json(*, req_id, message, websocket, registry, send_response):
    result = await invoke(
        "write_json",
        registry.json_ops.write_json_file,
        message.get("file_path"),
        message.get("content"),
    )
    await _respond(send_response, req_id, result)


async def _handle_remove_directory(*, req_id, message, websocket, registry, send_response):
    result = await invoke(
        "remove_directory",
        registry.file_ops.remove_directory, websocket, message.get("directory")
    )
    await _respond(send_response, req_id, result)


async def _handle_create_directory(*, req_id, message, websocket, registry, send_response):
    result = await invoke(
        "create_directory",
        registry.file_ops.create_directory, websocket, message.get("directory")
    )
    await _respond(send_response, req_id, result)


async def _handle_list_files(*, req_id, message, websocket, registry, send_response):
    result = await invoke("list_files", registry.file_ops.get_file_list, message.get("directory"))
    await _respond(send_response, req_id, result)


async def _handle_list_apps(*, req_id, message, websocket, registry, send_response):
    result = await invoke("list_apps", registry.file_ops.get_app_list)
    await _respond(send_response, req_id, result)


async def _handle_get_file_md5(*, req_id, message, websocket, registry, send_response):
    result = await invoke(
        "get_file_md5",
        registry.file_ops.get_file_md5, websocket, message.get("file_name")
    )
    await _respond(send_response, req_id, result)


async def _handle_get_device_info(*, req_id, message, websocket, registry, send_response):
    result = await invoke("get_device_info", registry.json_ops.get_device_info)
    await _respond(send_response, req_id, result)


async def _handle_set_device_name(*, req_id, message, websocket, registry, send_response):
    result = await invoke("set_device_name", registry.json_ops.set_device_name, message.get("device_name"))
    await _respond(send_response, req_id, result)


FILE_JSON_ACTION_HANDLERS: dict[str, ActionHandler] = {
    "send_file": _handle_send_file,
    "get_file": _handle_get_file,
    "read_json": _handle_read_json,
    "write_json": _handle_write_json,
    "remove_directory": _handle_remove_directory,
    "create_directory": _handle_create_directory,
    "list_files": _handle_list_files,
    "list_apps": _handle_list_apps,
    "get_file_md5": _handle_get_file_md5,
    "get_device_info": _handle_get_device_info,
    "set_device_name": _handle_set_device_name,
}


async def try_handle_file_json_actions(
    *,
    action: str | None,
    req_id: Any,
    message: dict,
    websocket: Any,
    registry: WebsocketServiceRegistry,
    send_response: SendResponse,
) -> bool:
    if action in FILE_JSON_ACTION_HANDLERS:
        await FILE_JSON_ACTION_HANDLERS[action](
            req_id=req_id,
            message=message,
            websocket=websocket,
            registry=registry,
            send_response=send_response,
        )
        return True
    if action == "download_app":
        url = message.get("url")
        md5 = message.get("md5")
        game_id = message.get("game_id")
        if not url or not md5:
            await _respond(
                send_response,
                req_id,
                error("download_app", ErrorCode.MISSING_PARAMETER, "Required information is missing"),
            )
            return True
        if game_id:
            await send_response(
                req_id,
                registry.file_ops.start_game_download_async_with_url(game_id, url, md5),
            )
            return True

        async def run_sync_download_then_respond():
            result = await invoke("download_app", registry.file_ops.download_app, url, md5)
            try:
                await _respond(send_response, req_id, result)
            except Exception:
                pass

        asyncio.create_task(run_sync_download_then_respond())
        return True
    if action == "get_download_progress":
        game_ids = message.get("game_ids")
        if game_ids is None:
            game_id = message.get("game_id")
            if game_id is not None:
                game_ids = [game_id]
            else:
                await send_response(
                    req_id,
                    to_websocket_payload(
                        error(
                            "get_download_progress",
                            ErrorCode.MISSING_PARAMETER,
                            "game_ids or game_id parameter is required",
                        )
                    ),
                )
                return True
        result = await invoke("get_download_progress", registry.file_ops.get_download_progress, game_ids)
        await _respond(send_response, req_id, result)
        return True

    return False
