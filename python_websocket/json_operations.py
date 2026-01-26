from pathlib import Path
import json
import os
import base64
import subprocess
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_file_not_found,
)

APPS_DIR = "apps"  # Update this to your desired save directory
HOME_DIR = ""


def read_json_file(file_path):
    try:
        if os.path.isabs(file_path):
            file_path = file_path.lstrip("/")
        full_save_path = os.path.join(os.getcwd(), APPS_DIR, file_path)

        if not Path(full_save_path).is_file():
            return handle_file_not_found("read_json", file_path)

        with open(full_save_path, "r") as file:
            # if any field in the json is a string and longer than 100 characters, strip it to 100 characters
            data = json.load(file)
            for key, value in data.items():
                if isinstance(value, str) and len(value) > 100:
                    data[key] = value[:100]
            return {
                "action": "read_json",
                "file_path": file_path,
                "content": base64.b64encode(json.dumps(data).encode("utf-8")).decode(
                    "utf-8"
                ),
            }
    except json.JSONDecodeError as e:
        return handle_exception(
            "read_json", e, "Failed to decode JSON file", file_path=file_path
        )
    except PermissionError:
        return handle_exception(
            "read_json", PermissionError(), "Failed to read file", file_path=file_path
        )
    except Exception as e:
        return handle_exception(
            "read_json", e, "Failed to read JSON file", file_path=file_path
        )


def write_json_file(file_path, data):
    try:
        if os.path.isabs(file_path):
            file_path = file_path.lstrip("/")
        full_save_path = os.path.join(os.getcwd(), APPS_DIR, file_path)

        # Decode the data with base64 before writing
        if isinstance(data, str):
            try:
                data = json.loads(base64.b64decode(data).decode("utf-8"))
            except Exception as e:
                return handle_exception(
                    "write_json", e, "Failed to decode base64 data", file_path=file_path
                )

        with open(full_save_path, "w") as file:
            json.dump(data, file)

        return {"action": "write_json", "file_path": file_path, "message": "Success"}
    except PermissionError:
        return handle_exception(
            "write_json", PermissionError(), "Failed to write file", file_path=file_path
        )
    except Exception as e:
        return handle_exception(
            "write_json", e, "Failed to write JSON file", file_path=file_path
        )


def get_device_info():
    device_info = {}
    try:
        # Read the device.json file
        with open(os.path.join(os.getcwd(), HOME_DIR, "device.json"), "r") as file:
            device_info = json.load(file)

        # Get the MAC address of the device
        with open("/sys/class/net/wlan0/address", "r") as file:
            mac_address = file.read().strip()
            device_info["mac_address"] = mac_address

        # Get the wifi ssid of the current connection
        ssid = ""
        try:
            # Check connection status
            result = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True,
                text=True,
                check=True,
            )
            connected_info = [
                line for line in result.stdout.splitlines() if line.startswith("yes:")
            ]
            if connected_info:
                _, ssid = connected_info[0].split(":")
            else:
                ssid = ""
        except Exception as e:
            ssid = ""
        device_info["ssid"] = ssid

        return {"action": "get_device_info", "device_info": device_info}
    except FileNotFoundError as e:
        return handle_exception("get_device_info", e, "Device info file not found")
    except json.JSONDecodeError as e:
        return handle_exception(
            "get_device_info", e, "Failed to decode JSON from device info file"
        )
    except Exception as e:
        return handle_exception(
            "get_device_info", e, "An error occurred while getting device info"
        )


def set_device_name(name):
    device_info_path = os.path.join(os.getcwd(), HOME_DIR, "device.json")
    try:
        # Read the existing device info
        with open(device_info_path, "r") as file:
            device_info = json.load(file)

        # Update the device name
        device_info["name"] = name

        # Write the updated info back to the file
        with open(device_info_path, "w") as file:
            json.dump(device_info, file)

        return {"action": "set_device_name", "device_name": name, "message": "Success"}

    except FileNotFoundError as e:
        return handle_exception(
            "set_device_name", e, "Device info file not found", device_name=name
        )
    except json.JSONDecodeError as e:
        return handle_exception(
            "set_device_name",
            e,
            "Failed to decode JSON from device info file",
            device_name=name,
        )
    except Exception as e:
        return handle_exception(
            "set_device_name",
            e,
            "An error occurred while setting device name",
            device_name=name,
        )
