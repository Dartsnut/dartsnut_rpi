from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import base64
from PIL import Image
from io import BytesIO

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

screen_buffer = bytearray(128*160*3)
preview_buffer = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
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
            elif action == "get_device_info":
                await send_response(req_id, get_device_info())
            elif action == "set_device_name":
                await send_response(req_id, set_device_name(message.get("device_name")))
            elif action == "locate_device":
                websocket_endpoint.locate_device() if websocket_endpoint.locate_device else None
                await send_response(req_id, {"action": "locate_device", "message": "Success"})
            elif action == "reload_conf":
                websocket_endpoint.reload_conf() if websocket_endpoint.reload_conf else None
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
            elif action == "capture_screen":
                img = Image.frombytes('RGB', (128, 160), bytes(screen_buffer))
                # main screen
                main_img = img.crop((0, 0, 128, 128))
                main_img_buffer = BytesIO()
                main_img.save(main_img_buffer, format='JPEG')
                main_img_bytes = main_img_buffer.getvalue()
                main_img_base64_str = base64.b64encode(main_img_bytes).decode('utf-8')
                # secondary screen
                second_img = img.crop((0, 128, 64, 160))
                second_img_buffer = BytesIO()
                second_img.save(second_img_buffer, format='JPEG')
                second_img_bytes = second_img_buffer.getvalue()
                second_img_base64_str = base64.b64encode(second_img_bytes).decode('utf-8')
                await send_response(req_id, {
                    "action": "capture_screen",
                    "main_screen": main_img_base64_str,
                    "second_screen": second_img_base64_str,
                    "req_id": req_id
                })
            else:
                await send_response(req_id, {"action": action, "error": "Unknown action", "req_id": req_id})

        except Exception as e:
            pass
        finally:
            pass
            # await websocket.close()

def start_websocket_server(set_brightness=None, locate_device=None, reload_conf=None):
    websocket_endpoint.set_brightness = set_brightness if set_brightness else None
    websocket_endpoint.locate_device = locate_device if locate_device else None
    websocket_endpoint.reload_conf = reload_conf if reload_conf else None
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9251)

if __name__ == "__main__":
    from file_operations import receive_file, send_file, remove_directory, get_file_md5, get_file_list, create_directory, download_app, get_app_list
    from json_operations import read_json_file, write_json_file, get_device_info, set_device_name
    from bluetooth_operations import scan_bluetooth_devices, list_paired_devices, disconnect_and_unpair_device, pair_and_connect_device
    start_websocket_server()
else:
    from python_websocket.file_operations import receive_file, send_file, remove_directory, get_file_md5, get_file_list, create_directory, download_app, get_app_list
    from python_websocket.json_operations import read_json_file, write_json_file, get_device_info, set_device_name
    from python_websocket.bluetooth_operations import scan_bluetooth_devices, list_paired_devices, disconnect_and_unpair_device, pair_and_connect_device
