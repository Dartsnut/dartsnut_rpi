from python_websocket.file_operations import (
    receive_file,
    send_file,
    remove_directory,
    get_file_md5,
    get_file_list,
    create_directory,
    download_app,
    get_app_list,
    start_game_download_async,
    start_game_download_async_with_url,
    get_download_progress as get_download_progress_status,
)
from python_websocket.json_operations import read_json_file, write_json_file, get_device_info, set_device_name
from python_websocket.bluetooth_operations import scan_bluetooth_devices, list_paired_devices, disconnect_and_unpair_device, pair_and_connect_device
from python_websocket.git_operations import check_update, perform_update, get_version
from python_websocket.udp_broadcast import udp_broadcast
from python_websocket.device_operations import get_wifi_rssi, forget_wifi, reboot, get_ssh_status, start_ssh, stop_ssh, get_brightness, get_volume, get_dim_window, set_dim_window
from python_websocket.user_data_operations import (
    get_user_data,
    update_user_info,
    get_game_playtime
)
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    create_error_response
)
from firestore_sync_bridge import is_firestore_bridge_active
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import uvicorn
import threading

app = FastAPI()

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
    await asyncio.to_thread(websocket_endpoint.reload_config) if websocket_endpoint.reload_config else None
    action = None
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
        response = data
        response["req_id"] = req_id
        print("response: ", response)
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
            print("action: ", action)

            try:
                if action == "send_file":
                    result = await asyncio.to_thread(receive_file, websocket, message)
                    await send_response(req_id, result)
                elif action == "get_file":
                    result = await asyncio.to_thread(send_file, websocket, message)
                    await send_response(req_id, result)
                elif action == "read_json":
                    result = await asyncio.to_thread(read_json_file, message.get("file_path"))
                    await send_response(req_id, result)
                elif action == "write_json":
                    result = await asyncio.to_thread(write_json_file, message.get("file_path"), message.get("content"))
                    await send_response(req_id, result)
                elif action == "remove_directory":
                    result = await asyncio.to_thread(remove_directory, websocket, message.get("directory"))
                    await send_response(req_id, result)
                elif action == "create_directory":
                    result = await asyncio.to_thread(create_directory, websocket, message.get("directory"))
                    await send_response(req_id, result)
                elif action == "list_files":
                    result = await asyncio.to_thread(get_file_list, message.get("directory"))
                    await send_response(req_id, result)
                elif action == "list_apps":
                    result = await asyncio.to_thread(get_app_list)
                    await send_response(req_id, result)
                elif action == "get_file_md5":
                    result = await asyncio.to_thread(get_file_md5, websocket, message.get("file_name"))
                    await send_response(req_id, result)
                elif action == "set_brightness":
                    try:
                        brightness = int(message.get("brightness", "0"))
                        if 10 <= brightness <= 100:
                            if websocket_endpoint.set_brightness:
                                await asyncio.to_thread(websocket_endpoint.set_brightness, brightness)
                            await send_response(req_id, {"action": "set_brightness", "message": "Success"})
                        else:
                            await send_response(req_id, create_error_response(
                                "set_brightness",
                                ErrorCode.INVALID_BRIGHTNESS,
                                "Brightness must be between 10 and 100"
                            ))
                    except ValueError as e:
                        await send_response(req_id, handle_exception("set_brightness", e, "Invalid brightness value"))
                    except Exception as e:
                        await send_response(req_id, handle_exception("set_brightness", e, "Failed to set brightness"))
                elif action == "set_volume":
                    try:
                        volume = int(message.get("volume", "0"))
                        if 0 <= volume <= 100:
                            if websocket_endpoint.set_volume:
                                await asyncio.to_thread(websocket_endpoint.set_volume, volume)
                            await send_response(req_id, {"action": "set_volume", "message": "Success"})
                        else:
                            await send_response(req_id, create_error_response(
                                "set_volume",
                                ErrorCode.INVALID_INPUT,
                                "Volume must be between 0 and 100"
                            ))
                    except ValueError as e:
                        await send_response(req_id, handle_exception("set_volume", e, "Invalid volume value"))
                    except Exception as e:
                        await send_response(req_id, handle_exception("set_volume", e, "Failed to set volume"))
                elif action == "set_time_zone":
                    time_zone = message.get("time_zone", "UTC")
                    if websocket_endpoint.set_time_zone:
                        await asyncio.to_thread(websocket_endpoint.set_time_zone, time_zone)
                    await send_response(req_id, {"action": "set_time_zone", "message": "Success"})
                elif action == "get_device_info":
                    result = await asyncio.to_thread(get_device_info)
                    await send_response(req_id, result)
                elif action == "set_device_name":
                    device_name = message.get("device_name")
                    result = await asyncio.to_thread(set_device_name, device_name)
                    await send_response(req_id, result)
                elif action == "locate_device":
                    if websocket_endpoint.locate_device:
                        await asyncio.to_thread(websocket_endpoint.locate_device)
                    await send_response(req_id, {"action": "locate_device", "message": "Success"})
                elif action == "reload_conf":
                    if websocket_endpoint.reload_config:
                        await asyncio.to_thread(websocket_endpoint.reload_config)
                    await send_response(req_id, {"action": "reload_conf", "message": "Success"})
                elif action == "bluetooth_scan":
                    result = await asyncio.to_thread(scan_bluetooth_devices)
                    await send_response(req_id, result)
                elif action == "bluetooth_list":
                    result = await asyncio.to_thread(list_paired_devices)
                    await send_response(req_id, result)
                elif action == "bluetooth_remove":
                    result = await asyncio.to_thread(disconnect_and_unpair_device, message.get("address"))
                    await send_response(req_id, result)
                elif action == "bluetooth_connect":
                    result = await asyncio.to_thread(pair_and_connect_device, message.get("address"))
                    await send_response(req_id, result)
                elif action == "download_app":
                    url = message.get("url")
                    md5 = message.get("md5")
                    game_id = message.get("game_id")

                    if not url or not md5:
                        await send_response(req_id, create_error_response(
                            "download_app",
                            ErrorCode.MISSING_PARAMETER,
                            "Required information is missing"
                        ))
                    elif game_id:
                        # Async download with progress tracking by game_id
                        await send_response(req_id, start_game_download_async_with_url(game_id, url, md5))
                    else:
                        # Sync download when game_id not provided: run in a task so the consumer
                        # can process get_download_progress and other requests while wget runs.
                        async def run_sync_download_then_respond():
                            try:
                                result = await asyncio.to_thread(download_app, url, md5)
                                await send_response(req_id, result)
                            except Exception as e:
                                try:
                                    err = handle_exception("download_app", e, "Download failed")
                                    err["req_id"] = req_id
                                    await websocket.send_text(json.dumps(err))
                                except Exception:
                                    pass

                        asyncio.create_task(run_sync_download_then_respond())
                        continue
                elif action == "start_game":
                    if websocket_endpoint.start_game_process:
                        try:
                            started = await asyncio.to_thread(websocket_endpoint.start_game_process, message.get("game_id"))
                            if started:
                                await send_response(req_id, {"action": "start_game", "message": "Game started"})
                            else:
                                await send_response(req_id, create_error_response(
                                    "start_game",
                                    ErrorCode.COMMAND_FAILED,
                                    "Unable to start the game"
                                ))
                        except Exception as e:
                            await send_response(req_id, handle_exception("start_game", e, "Failed to start game"))
                    else:
                        await send_response(req_id, create_error_response(
                            "start_game",
                            ErrorCode.FUNCTION_NOT_AVAILABLE,
                            "This feature is not available"
                        ))
                elif action == "get_widgets_screen":
                    if websocket_endpoint.get_widgets_framebuffer:
                        try:
                            framebuffers = await asyncio.to_thread(websocket_endpoint.get_widgets_framebuffer)
                            await send_response(req_id, {"action": "get_widgets_screen", "framebuffers": framebuffers})
                        except Exception as e:
                            await send_response(req_id, handle_exception("get_widgets_screen", e, "Failed to get widgets screen"))
                    else:
                        await send_response(req_id, create_error_response(
                            "get_widgets_screen",
                            ErrorCode.FUNCTION_NOT_AVAILABLE,
                            "This feature is not available"
                        ))
                elif action == "get_wifi_rssi":
                    result = await asyncio.to_thread(get_wifi_rssi)
                    await send_response(req_id, result)
                elif action == "get_brightness":
                    result = await asyncio.to_thread(get_brightness)
                    await send_response(req_id, result)
                elif action == "get_volume":
                    result = await asyncio.to_thread(get_volume)
                    await send_response(req_id, result)
                elif action == "get_dim_window":
                    result = await asyncio.to_thread(get_dim_window)
                    await send_response(req_id, result)
                elif action == "set_dim_window":
                    dim_window_payload = {
                        "dim_window_start": message.get("dim_window_start"),
                        "dim_window_end": message.get("dim_window_end"),
                        "dim_level": message.get("dim_level"),
                        "dim_restore_seconds": message.get("dim_restore_seconds"),
                        "dim_window_enabled": message.get("dim_window_enabled"),
                    }
                    result = await asyncio.to_thread(
                        set_dim_window,
                        message.get("dim_window_start"),
                        message.get("dim_window_end"),
                        message.get("dim_level"),
                        message.get("dim_restore_seconds"),
                        message.get("dim_window_enabled"),
                    )
                    await send_response(req_id, result)
                    if result.get("message") == "Success" and websocket_endpoint.trigger_dim_check:
                        await asyncio.to_thread(websocket_endpoint.trigger_dim_check)
                elif action == "forget_wifi":
                    await asyncio.to_thread(forget_wifi)
                elif action == "get_version":
                    result = await asyncio.to_thread(get_version)
                    await send_response(req_id, result)
                elif action == "check_update":
                    result = await asyncio.to_thread(check_update)
                    await send_response(req_id, result)
                elif action == "perform_update":
                    result = await asyncio.to_thread(perform_update)
                    await send_response(req_id, result)
                elif action == "get_download_progress":
                    game_ids = message.get("game_ids")
                    if game_ids is None:
                        # Handle backward compatibility: check for single game_id
                        game_id = message.get("game_id")
                        if game_id is not None:
                            game_ids = [game_id]
                        else:
                            await send_response(req_id, create_error_response(
                                "get_download_progress",
                                ErrorCode.MISSING_PARAMETER,
                                "game_ids or game_id parameter is required"
                            ))
                            continue
                    result = await asyncio.to_thread(get_download_progress_status, game_ids)
                    await send_response(req_id, result)
                elif action == "reboot":
                    await asyncio.to_thread(reboot)
                elif action == "get_ssh_status":
                    result = await asyncio.to_thread(get_ssh_status)
                    await send_response(req_id, result)
                elif action == "start_ssh":
                    result = await asyncio.to_thread(start_ssh)
                    await send_response(req_id, result)
                elif action == "stop_ssh":
                    result = await asyncio.to_thread(stop_ssh)
                    await send_response(req_id, result)
                elif action == "get_user_data":
                    result = await asyncio.to_thread(get_user_data)
                    await send_response(req_id, result)
                elif action == "update_user_info":
                    result = await asyncio.to_thread(
                        update_user_info,
                        user_id=message.get("user_id"),
                        jwt_token=message.get("jwt_token"),
                        refresh_token=message.get("refresh_token")
                    )
                    await send_response(req_id, result)
                elif action == "get_game_playtime":
                    result = await asyncio.to_thread(get_game_playtime, message.get("game_id"))
                    await send_response(req_id, result)
                else:
                    await send_response(req_id, create_error_response(
                        action or "unknown",
                        ErrorCode.UNKNOWN_ACTION,
                        "The requested action is not recognized"
                    ))

            except Exception as e:
                print(f"error: {e}")
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

def start_websocket_server(set_brightness=None, locate_device=None, reload_config=None, set_time_zone=None, get_widgets_framebuffer=None, start_game_process=None, set_volume=None, trigger_dim_check=None):
    # Start UDP broadcast thread
    udp_thread = threading.Thread(target=udp_broadcast, daemon=True)
    udp_thread.start()
    # Start WebSocket server
    websocket_endpoint.set_brightness = set_brightness if set_brightness else None
    websocket_endpoint.locate_device = locate_device if locate_device else None
    websocket_endpoint.reload_config = reload_config if reload_config else None
    websocket_endpoint.set_time_zone = set_time_zone if set_time_zone else None
    websocket_endpoint.get_widgets_framebuffer = get_widgets_framebuffer if get_widgets_framebuffer else None
    websocket_endpoint.start_game_process = start_game_process if start_game_process else None
    websocket_endpoint.set_volume = set_volume if set_volume else None
    websocket_endpoint.trigger_dim_check = trigger_dim_check if trigger_dim_check else None
    uvicorn.run(app, host="0.0.0.0", port=9251)
