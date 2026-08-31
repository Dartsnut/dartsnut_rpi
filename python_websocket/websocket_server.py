import asyncio
import json
import logging

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from python_websocket.error_handler import (
    handle_exception,
)
from runtime.machine_result import to_websocket_payload
from runtime.websocket_actions import handle_action_message
from runtime.websocket_ports import WebsocketEndpointConfig, WebsocketServiceRegistry
from runtime.websocket_service_registry import build_default_websocket_registry

_log = logging.getLogger(__name__)

app = FastAPI()
_service_registry: WebsocketServiceRegistry = build_default_websocket_registry()
_endpoint_config = WebsocketEndpointConfig()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    if _endpoint_config.reload_config:
        await asyncio.to_thread(_endpoint_config.reload_config)
    queue = asyncio.Queue()

    async def receive_loop():
        while True:
            try:
                data = await websocket.receive_text()
            except Exception:
                await queue.put(("stop", None))
                return
            try:
                message = json.loads(data)
                await queue.put(("msg", message))
            except json.JSONDecodeError as e:
                await queue.put(("json_error", {"e": e, "req_id": None}))

    recv_task = asyncio.create_task(receive_loop())

    async def send_response(req_id, data):
        response = dict(to_websocket_payload(data))
        response["req_id"] = req_id
        _log.debug("websocket response: %s", response)
        await websocket.send_text(json.dumps(response))

    try:
        while True:
            kind, payload = await queue.get()
            if kind == "stop":
                break
            if kind == "json_error":
                try:
                    err = handle_exception("unknown", payload["e"], "Invalid JSON in request")
                    err["req_id"] = payload.get("req_id")
                    await websocket.send_text(json.dumps(err))
                except Exception:
                    pass
                continue

            message = payload
            action = message.get("action")
            req_id = message.get("req_id")
            _log.debug("websocket action: %s", action)

            try:
                await handle_action_message(
                    websocket=websocket,
                    message=message,
                    registry=_service_registry,
                    endpoint_config=_endpoint_config,
                    send_response=send_response,
                )

            except Exception as e:
                _log.warning("websocket action %s failed: %s", action, e)
                try:
                    error_response = handle_exception(action or "unknown", e, "An unexpected error occurred")
                    error_response["req_id"] = req_id
                    await websocket.send_text(json.dumps(error_response))
                except Exception:
                    pass
                if websocket.client_state.name == "DISCONNECTED":
                    break
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

def start_websocket_server(
    set_brightness=None,
    locate_device=None,
    reload_config=None,
    set_time_zone=None,
    get_widgets_framebuffer=None,
    start_game_process=None,
    set_volume=None,
    trigger_dim_check=None,
    service_registry: WebsocketServiceRegistry | None = None,
    endpoint_config: WebsocketEndpointConfig | None = None,
    sideload_manager=None,
):
    global _service_registry, _endpoint_config
    _service_registry = service_registry or build_default_websocket_registry()
    _endpoint_config = endpoint_config or WebsocketEndpointConfig(
        set_brightness=set_brightness,
        locate_device=locate_device,
        reload_config=reload_config,
        set_time_zone=set_time_zone,
        get_widgets_framebuffer=get_widgets_framebuffer,
        start_game_process=start_game_process,
        set_volume=set_volume,
        trigger_dim_check=trigger_dim_check,
        sideload_manager=sideload_manager,
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9251,
        log_level="warning",
        access_log=False,
    )
