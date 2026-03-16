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
from machine_state_service import get_machine_state_service

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

        # If this targets root apps/conf.json, let MachineStateService own pages.
        try:
            svc = get_machine_state_service()
            if (
                svc is not None
                and os.path.normpath(full_save_path)
                == os.path.normpath(os.path.join(os.getcwd(), APPS_DIR, "conf.json"))
            ):
                pages = data.get("pages", [])
                if isinstance(pages, list):
                    svc.set_pages(pages)
        except Exception as e:
            print(f"Error syncing pages after write_json conf.json: {e}")

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

        # Get the BLE MAC address of the device using the same adapter-based
        # approach as python_ble (no sysfs fallback, to keep behavior consistent).
        try:
            from bluezero import adapter  # type: ignore

            adapters = list(adapter.Adapter.available())
            if adapters:
                ble_mac = adapters[0].address
                device_info["mac_address"] = ble_mac
        except Exception:
            pass

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
        except Exception:
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
    try:
        svc = get_machine_state_service()
        if svc is None:
            raise RuntimeError("MachineStateService not initialized")
        svc.set_device_name(name)
        return {"action": "set_device_name", "device_name": name, "message": "Success"}

    except Exception as e:
        return handle_exception(
            "set_device_name",
            e,
            "An error occurred while setting device name",
            device_name=name,
        )
