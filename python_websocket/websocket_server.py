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
from python_websocket.device_operations import get_wifi_rssi, forget_wifi, reboot, get_ssh_status, start_ssh, stop_ssh, get_brightness, get_volume
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
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
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
    websocket_endpoint.reload_config() if websocket_endpoint.reload_config else None
    action = None
    while True:
        try:
            data = await websocket.receive_text()
            message = json.loads(data)

            action = message.get("action")
            req_id = message.get("req_id")
            print("action: ",action)
            
            async def send_response(req_id, data):
                response = data
                response["req_id"] = req_id
                await websocket.send_text(json.dumps(response))

            if action == "send_file":
                await send_response(req_id, receive_file(websocket, message))
            elif action == "get_file":
                await send_response(req_id, send_file(websocket, message))
            elif action == "read_json":
                await send_response(req_id, read_json_file(message.get("file_path")))
            elif action == "write_json":
                await send_response(req_id, write_json_file(message.get("file_path"), message.get("content")))
            elif action == "remove_directory":
                await send_response(req_id, remove_directory(websocket, message.get("directory")))
            elif action == "create_directory":
                await send_response(req_id, create_directory(websocket, message.get("directory")))
            elif action == "list_files":
                await send_response(req_id, get_file_list(message.get("directory")))
            elif action == "list_apps":
                await send_response(req_id, get_app_list())
            elif action == "get_file_md5":
                await send_response(req_id, get_file_md5(websocket, message.get("file_name")))
            elif action == "set_brightness":
                try:
                    brightness = int(message.get("brightness", "0"))
                    if 10 <= brightness <= 100:
                        websocket_endpoint.set_brightness(brightness) if websocket_endpoint.set_brightness else None
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
                        websocket_endpoint.set_volume(volume) if websocket_endpoint.set_volume else None
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
                websocket_endpoint.set_time_zone(time_zone) if websocket_endpoint.set_time_zone else None
                await send_response(req_id, {"action": "set_time_zone", "message": "Success"})
            elif action == "get_device_info":
                await send_response(req_id, get_device_info())
            elif action == "set_device_name":
                await send_response(req_id, set_device_name(message.get("device_name")))
            elif action == "locate_device":
                websocket_endpoint.locate_device() if websocket_endpoint.locate_device else None
                await send_response(req_id, {"action": "locate_device", "message": "Success"})
            elif action == "reload_conf":
                websocket_endpoint.reload_config() if websocket_endpoint.reload_config else None
                await send_response(req_id, {"action": "reload_conf", "message": "Success"})
            elif action == "bluetooth_scan":
                await send_response(req_id, scan_bluetooth_devices())
            elif action == "bluetooth_list":
                await send_response(req_id, list_paired_devices())
            elif action == "bluetooth_remove":
                await send_response(req_id, disconnect_and_unpair_device(message.get("address")))
            elif action == "bluetooth_connect":
                await send_response(req_id, pair_and_connect_device(message.get("address")))
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
                    # Sync download when game_id not provided
                    await send_response(req_id, download_app(url, md5))
            elif action == "start_game":
                if websocket_endpoint.start_game_process:
                    try:
                        if websocket_endpoint.start_game_process(message.get("game_id")):
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
                        framebuffers = websocket_endpoint.get_widgets_framebuffer()
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
               await send_response(req_id, get_wifi_rssi())
            elif action == "get_brightness":
                await send_response(req_id, get_brightness())
            elif action == "get_volume":
                await send_response(req_id, get_volume())
            elif action == "forget_wifi":
                forget_wifi()
            elif action == "get_version":
                await send_response(req_id, get_version())
            elif action == "check_update":
                await send_response(req_id, check_update())
            elif action == "perform_update":
                await send_response(req_id, perform_update())
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
                await send_response(req_id, get_download_progress_status(game_ids))
            elif action == "reboot":
                reboot()
            elif action == "get_ssh_status":
                await send_response(req_id, get_ssh_status())
            elif action == "start_ssh":
                await send_response(req_id, start_ssh())
            elif action == "stop_ssh":
                await send_response(req_id, stop_ssh())
            elif action == "get_user_data":
                await send_response(req_id, get_user_data())
            elif action == "update_user_info":
                await send_response(req_id, update_user_info(
                    user_id=message.get("user_id"),
                    jwt_token=message.get("jwt_token"),
                    refresh_token=message.get("refresh_token")
                ))
            elif action == "get_game_playtime":
                await send_response(req_id, get_game_playtime(message.get("game_id")))
            else:
                await send_response(req_id, create_error_response(
                    action or "unknown",
                    ErrorCode.UNKNOWN_ACTION,
                    "The requested action is not recognized"
                ))

        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            try:
                error_response = handle_exception(action or "unknown", e, "Invalid JSON in request")
                error_response["req_id"] = message.get("req_id") if 'message' in locals() else None
                await websocket.send_text(json.dumps(error_response))
            except:
                pass
            if websocket.client_state.name == "DISCONNECTED":
                break
        except Exception as e:
            print(f"error: {e}")
            try:
                error_response = handle_exception(action or "unknown", e, "An unexpected error occurred")
                error_response["req_id"] = message.get("req_id") if 'message' in locals() else None
                await websocket.send_text(json.dumps(error_response))
            except:
                pass
            if websocket.client_state.name == "DISCONNECTED":
                break
        finally:
            pass
            # await websocket.close()

def start_websocket_server(set_brightness=None, locate_device=None, reload_config=None, set_time_zone=None, get_widgets_framebuffer=None, start_game_process=None, set_volume=None):
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
    uvicorn.run(app, host="0.0.0.0", port=9251)
