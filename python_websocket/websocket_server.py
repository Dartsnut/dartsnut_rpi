from python_websocket.file_operations import receive_file, send_file, remove_directory, get_file_md5, get_file_list, create_directory, download_app, get_app_list
from python_websocket.json_operations import read_json_file, write_json_file, get_device_info, set_device_name
from python_websocket.bluetooth_operations import scan_bluetooth_devices, list_paired_devices, disconnect_and_unpair_device, pair_and_connect_device
from python_websocket.git_operations import check_update, perform_update, get_version
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import base64
from PIL import Image
from io import BytesIO
import uvicorn

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
                await  send_response(req_id, json.dumps(get_file_md5(websocket, message.get("file_name"))))
            elif action == "set_brightness":
                brightness = int(message.get("brightness", "0"))
                if 10 <= brightness <= 100:
                    websocket_endpoint.set_brightness(brightness) if websocket_endpoint.set_brightness else None
                    await send_response(req_id, {"action": "set_brightness", "message": "Success"})
                else:
                    await send_response(req_id, {"action": "set_brightness", "error": "Brightness must be between 10 and 100"})
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
                await send_response(req_id, download_app(message.get("url"), message.get("md5")))
            elif action == "get_widgets_screen":
                if websocket_endpoint.get_widgets_framebuffer:
                    framebuffers = websocket_endpoint.get_widgets_framebuffer()
                    await send_response(req_id, {"action": "get_widgets_screen", "framebuffers": framebuffers})
                else:
                    await send_response(req_id, {"action": "get_widgets_screen", "error": "Function not available"})
            elif action == "forget_wifi":
                subprocess.run("nmcli -t -f NAME,TYPE connection show | grep 802-11-wireless | cut -d: -f1 | xargs -r -n1 nmcli connection delete", shell=True)
                subprocess.run("nmcli radio wifi off && nmcli radio wifi on", shell=True)
            elif action == "get_version":
                await send_response(req_id, get_version())
            elif action == "check_update":
                await send_response(req_id, check_update())
            elif action == "perform_update":
                await send_response(req_id, perform_update())
            elif action == "start_game":
                if websocket_endpoint.start_game_process:
                    if websocket_endpoint.start_game_process(message.get("game_id")):
                        await send_response(req_id, {"action": "start_game", "message": "Game started"})
                    else:
                        await send_response(req_id, {"action": "start_game", "error": "Game start failed"})
                else:
                    await send_response(req_id, {"action": "start_game", "error": "Function not available"})
            elif action == "reboot":
                os.system("sudo reboot")
            else:
                await send_response(req_id, {"action": action, "error": "Unknown action"})

        except Exception as e:
            print(f"error: {e}")
            if websocket.client_state.name == "DISCONNECTED":
                break
        finally:
            pass
            # await websocket.close()

def start_websocket_server(set_brightness=None, locate_device=None, reload_config=None, set_time_zone=None, get_widgets_framebuffer=None, start_game_process=None):
    websocket_endpoint.set_brightness = set_brightness if set_brightness else None
    websocket_endpoint.locate_device = locate_device if locate_device else None
    websocket_endpoint.reload_config = reload_config if reload_config else None
    websocket_endpoint.set_time_zone = set_time_zone if set_time_zone else None
    websocket_endpoint.get_widgets_framebuffer = get_widgets_framebuffer if get_widgets_framebuffer else None
    websocket_endpoint.start_game_process = start_game_process if start_game_process else None
    uvicorn.run(app, host="0.0.0.0", port=9251)
