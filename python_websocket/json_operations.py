from pathlib import Path
import json
import os
import base64
import subprocess

APPS_DIR = "apps"  # Update this to your desired save directory
HOME_DIR = ""

def read_json_file(file_path):
    if os.path.isabs(file_path):
        file_path = file_path.lstrip("/")
    full_save_path = os.path.join(os.getcwd(), APPS_DIR, file_path)

    if not Path(full_save_path).is_file():
        return {"action": "read_json", "file_path": file_path, "error": "File not found"}
    
    with open(full_save_path, 'r') as file:
        return {"action": "read_json", "file_path": file_path, "content": base64.b64encode(json.dumps(json.load(file)).encode('utf-8')).decode('utf-8')}
        

def write_json_file(file_path, data):
    if os.path.isabs(file_path):
        file_path = file_path.lstrip("/")
    full_save_path = os.path.join(os.getcwd(), APPS_DIR, file_path)

    # Decode the data with base64 before writing
    if isinstance(data, str):
        try:
            data = json.loads(base64.b64decode(data).decode('utf-8'))
        except Exception as e:
            return {"action": "write_json", "file_path": file_path, "error":f"Failed to decode base64 data: {e}"}

    with open(full_save_path, 'w') as file:
        json.dump(data, file)

    return {"action": "write_json", "file_path": file_path, "message": "Success"}

def get_device_info():
    device_info = {}
    try:
        # Read the device.json file
        with open(os.path.join(os.getcwd(), HOME_DIR, "device.json"), 'r') as file:
            device_info = json.load(file)
        
        # Get the MAC address of the device
        with open('/sys/class/net/wlan0/address', 'r') as file:
            mac_address = file.read().strip()
            device_info["mac_address"] = mac_address

        # Get the wifi ssid of the current connection
        ssid = ""
        try:
            # Check connection status
            result = subprocess.run(['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'], capture_output=True, text=True, check=True)
            connected_info = [line for line in result.stdout.splitlines() if line.startswith("yes:")]
            if connected_info:
                _, ssid = connected_info[0].split(':')
            else:
                ssid = ""
        except Exception as e:
            ssid = ""
        device_info["ssid"] = ssid
        
        return {"action": "get_device_info", "device_info": device_info}
    except FileNotFoundError as e:
        return {"action": "get_device_info", "error": "File not found"}
    except json.JSONDecodeError as e:
        return {"action": "get_device_info", "error": f"Failed to decode JSON from device info file: {e}"}
    except Exception as e:
        return {"action": "get_device_info", "error": f"An error occurred while getting device info: {e}"}

def set_device_name(name):
    device_info_path = os.path.join(os.getcwd(), HOME_DIR, "device.json")
    try:
        # Read the existing device info
        with open(device_info_path, 'r') as file:
            device_info = json.load(file)
        
        # Update the device name
        device_info['name'] = name
        
        # Write the updated info back to the file
        with open(device_info_path, 'w') as file:
            json.dump(device_info, file)

        return {"action": "set_device_name", "device_name": name, "message": "Success"}

    except FileNotFoundError:
        return {"action": "set_device_name", "device_name": name, "error": "File not found"}
    except json.JSONDecodeError:
        return {"action": "set_device_name", "device_name": name, "error": f"Failed to decode JSON from device info file: {e}"}
    except Exception as e:
        return  {"action": "set_device_name", "device_name": name, "error": f"An error occurred while getting device info: {e}"}