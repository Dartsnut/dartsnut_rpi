import json
import logging
import os
import re
import subprocess
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_command_error,
    create_error_response
)
from machine_state_service import get_machine_state_service

_log = logging.getLogger(__name__)


def _device_info_path() -> str:
    return os.path.join(os.getcwd(), "device.json")


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
                # Prevent reconnection races while reset flow is running.
                subprocess.run(
                    ["nmcli", "connection", "modify", name, "connection.autoconnect", "no"],
                    check=False,
                )
                subprocess.run(["nmcli", "connection", "delete", name], check=False)

        # Ensure currently-active Wi-Fi device is dropped immediately.
        subprocess.run(["nmcli", "device", "disconnect", "wlan0"], check=False)
        # Refresh Wi-Fi stack state after profile deletion/disconnect.
        subprocess.run(["nmcli", "radio", "wifi", "off"], check=False)
        subprocess.run(["nmcli", "radio", "wifi", "on"], check=False)
    except Exception as e:
        _log.warning("Error forgetting wifi: %s", e)

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
        with open(_device_info_path(), "r") as file:
            device_info = json.load(file)
        
        # Extract brightness value, default to 50 if missing
        brightness = int(device_info.get("brightness", "50"))
        return {"action": "get_brightness", "brightness": brightness}
    except FileNotFoundError as e:
        return handle_exception("get_brightness", e, "Device info file not found")
    except (json.JSONDecodeError, ValueError) as e:
        return handle_exception("get_brightness", e, "Failed to decode brightness value")
    except Exception as e:
        return handle_exception("get_brightness", e, "An error occurred while getting brightness")

def get_volume():
    try:
        with open(_device_info_path(), "r") as file:
            device_info = json.load(file)
        
        # Extract volume value, default to 50 if missing
        volume = int(device_info.get("volume", "50"))
        return {"action": "get_volume", "volume": volume}
    except FileNotFoundError as e:
        return handle_exception("get_volume", e, "Device info file not found")
    except (json.JSONDecodeError, ValueError) as e:
        return handle_exception("get_volume", e, "Failed to decode volume value")
    except Exception as e:
        return handle_exception("get_volume", e, "An error occurred while getting volume")


def _parse_hhmm(s):
    """Parse 'HH:MM' string into (h, m). Returns None if invalid. h in 0-23, m in 0-59."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    parts = s.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)
    except (ValueError, TypeError):
        pass
    return None


def _dim_enabled_to_bool(x):
    """Convert dim_window_enabled to bool. None stays None; True/1/'true'->True; else False."""
    if x is None:
        return None
    return str(x).lower() in ("true", "1")


def get_dim_window():
    try:
        with open(_device_info_path(), "r") as file:
            device_info = json.load(file)
        return {
            "action": "get_dim_window",
            "dim_window_start": device_info.get("dim_window_start", ""),
            "dim_window_end": device_info.get("dim_window_end", ""),
            "dim_level": int(device_info.get("dim_level", 10)),
            "dim_restore_seconds": int(device_info.get("dim_restore_seconds", 30)),
            "dim_window_enabled": str(device_info.get("dim_window_enabled", "false")).lower() == "true",
        }
    except FileNotFoundError as e:
        return handle_exception("get_dim_window", e, "Device info file not found")
    except (json.JSONDecodeError, ValueError) as e:
        return handle_exception("get_dim_window", e, "Failed to decode device info")
    except Exception as e:
        return handle_exception("get_dim_window", e, "An error occurred while getting dim window")


def set_dim_window(dim_window_start, dim_window_end, dim_level=None, dim_restore_seconds=None, dim_window_enabled=None):
    start_s = (dim_window_start or "").strip() if dim_window_start is not None else ""
    end_s = (dim_window_end or "").strip() if dim_window_end is not None else ""

    # Both start and end empty
    if not start_s and not end_s:
        # Only toggle enable flag or clear config when both times are empty.
        try:
            svc = get_machine_state_service()
            if svc is None:
                raise RuntimeError("MachineStateService not initialized")
            cfg = {}
            if dim_window_enabled is not None:
                cfg["dim_window_enabled"] = _dim_enabled_to_bool(dim_window_enabled)
            else:
                # Clearing all keys: set to defaults/empty.
                cfg = {
                    "dim_window_enabled": False,
                    "dim_window_start": "",
                    "dim_window_end": "",
                    "dim_level": 10,
                    "dim_restore_seconds": 30,
                }
            svc.set_dim_window(cfg)
            return {"action": "set_dim_window", "message": "Success"}
        except Exception as e:
            return handle_exception("set_dim_window", e, "Failed to set dim window")

    # Enable: both required
    if not start_s or not end_s:
        return create_error_response(
            "set_dim_window",
            ErrorCode.INVALID_INPUT,
            "dim_window_start and dim_window_end must both be provided to enable, or both empty to disable"
        )

    # Validate HH:MM
    if _parse_hhmm(start_s) is None or _parse_hhmm(end_s) is None:
        return create_error_response(
            "set_dim_window",
            ErrorCode.INVALID_INPUT,
            "dim_window_start and dim_window_end must be in HH:MM format (00-23:00-59)"
        )

    # dim_level: 1-100, default 10
    try:
        level = int(dim_level) if dim_level is not None else 10
    except (TypeError, ValueError):
        return create_error_response(
            "set_dim_window",
            ErrorCode.INVALID_INPUT,
            "dim_level must be between 1 and 100"
        )
    if not (1 <= level <= 100):
        return create_error_response(
            "set_dim_window",
            ErrorCode.INVALID_INPUT,
            "dim_level must be between 1 and 100"
        )

    # dim_restore_seconds: 5-300, default 30
    try:
        secs = int(dim_restore_seconds) if dim_restore_seconds is not None else 30
    except (TypeError, ValueError):
        return create_error_response(
            "set_dim_window",
            ErrorCode.INVALID_INPUT,
            "dim_restore_seconds must be between 5 and 300"
        )
    if not (5 <= secs <= 300):
        return create_error_response(
            "set_dim_window",
            ErrorCode.INVALID_INPUT,
            "dim_restore_seconds must be between 5 and 300"
        )

    enabled = _dim_enabled_to_bool(dim_window_enabled) if dim_window_enabled is not None else True

    try:
        svc = get_machine_state_service()
        if svc is None:
            raise RuntimeError("MachineStateService not initialized")
        cfg = {
            "dim_window_start": start_s,
            "dim_window_end": end_s,
            "dim_level": level,
            "dim_restore_seconds": secs,
            "dim_window_enabled": enabled,
        }
        svc.set_dim_window(cfg)
        return {"action": "set_dim_window", "message": "Success"}
    except Exception as e:
        return handle_exception("set_dim_window", e, "Failed to set dim window")