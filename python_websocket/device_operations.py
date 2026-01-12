import subprocess
import re
import json
import os
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_command_error,
    create_error_response
)

def get_wifi_rssi():
    try:
        # Run the command to get signal level
        result = subprocess.check_output(["iwconfig", "wlan0"]).decode('utf-8')
        # Extract the signal level value (e.g., -56)
        match = re.search(r'Signal level=(-\d+)', result)
        if match:
            rssi = match.group(1)
            return {"action": "get_wifi_rssi", "rssi": rssi}
        return create_error_response(
            "get_wifi_rssi",
            ErrorCode.SIGNAL_LEVEL_NOT_FOUND,
            "Unable to retrieve WiFi signal strength"
        )
    except subprocess.CalledProcessError as e:
        return handle_command_error("get_wifi_rssi", "iwconfig", e.returncode, e.stderr)
    except Exception as e:
        return handle_exception("get_wifi_rssi", e, "Failed to get WiFi signal level")

def forget_wifi():
    try:
        # Get list of connections
        result = subprocess.check_output(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], text=True)
        for line in result.splitlines():
            if "802-11-wireless" in line:
                name = line.split(":")[0]
                subprocess.run(["nmcli", "connection", "delete", name])
        
        subprocess.run(["nmcli", "radio", "wifi", "off"])
        subprocess.run(["nmcli", "radio", "wifi", "on"])
    except Exception as e:
        print(f"Error forgetting wifi: {e}")

def reboot():
    subprocess.run(["sudo", "reboot"])

def get_ssh_status():
    try:
        result = subprocess.run(['systemctl', 'is-active', 'ssh'], capture_output=True, text=True, check=False)
        status = result.stdout.strip()
        return {"action": "get_ssh_status", "status": status}
    except Exception as e:
        return handle_exception("get_ssh_status", e, "Failed to get SSH status")

def start_ssh():
    try:
        subprocess.run(['sudo', 'systemctl', 'start', 'ssh'], check=True)
        return {"action": "start_ssh", "message": "SSH started successfully"}
    except subprocess.CalledProcessError as e:
        return handle_command_error("start_ssh", "systemctl start ssh", e.returncode, e.stderr)
    except Exception as e:
        return handle_exception("start_ssh", e, "Failed to start SSH service")

def stop_ssh():
    try:
        subprocess.run(['sudo', 'systemctl', 'stop', 'ssh'], check=True)
        return {"action": "stop_ssh", "message": "SSH stopped successfully"}
    except subprocess.CalledProcessError as e:
        return handle_command_error("stop_ssh", "systemctl stop ssh", e.returncode, e.stderr)
    except Exception as e:
        return handle_exception("stop_ssh", e, "Failed to stop SSH service")

def get_brightness():
    try:
        # Read the device.json file
        device_info_path = os.path.join(os.getcwd(), "device.json")
        with open(device_info_path, 'r') as file:
            device_info = json.load(file)
        
        # Extract brightness value, default to 50 if missing
        brightness = int(device_info.get('brightness', '50'))
        return {"action": "get_brightness", "brightness": brightness}
    except FileNotFoundError as e:
        return handle_exception("get_brightness", e, "Device info file not found")
    except (json.JSONDecodeError, ValueError) as e:
        return handle_exception("get_brightness", e, "Failed to decode brightness value")
    except Exception as e:
        return handle_exception("get_brightness", e, "An error occurred while getting brightness")

def get_volume():
    try:
        # Read the device.json file
        device_info_path = os.path.join(os.getcwd(), "device.json")
        with open(device_info_path, 'r') as file:
            device_info = json.load(file)
        
        # Extract volume value, default to 50 if missing
        volume = int(device_info.get('volume', '50'))
        return {"action": "get_volume", "volume": volume}
    except FileNotFoundError as e:
        return handle_exception("get_volume", e, "Device info file not found")
    except (json.JSONDecodeError, ValueError) as e:
        return handle_exception("get_volume", e, "Failed to decode volume value")
    except Exception as e:
        return handle_exception("get_volume", e, "An error occurred while getting volume")