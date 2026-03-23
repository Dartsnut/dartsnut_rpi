import bluetooth
import os
import subprocess
import time
from datetime import datetime, timezone
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_bluetooth_error
)


def _discover_filtered_devices():
    """
    Discover nearby Bluetooth devices and keep likely controller/audio devices.
    Returns a list of {"address": ..., "name": ...} objects.
    """
    audio_keywords = ["headphone", "speaker", "audio", "controller"]
    nearby_devices = bluetooth.discover_devices(duration=8, lookup_names=True)

    filtered_devices = []
    seen = set()
    for addr, name in nearby_devices:
        if not addr or addr in seen:
            continue
        lower_name = name.lower() if isinstance(name, str) else ""
        if any(keyword in lower_name for keyword in audio_keywords):
            filtered_devices.append({"address": addr, "name": name or ""})
            seen.add(addr)
    return filtered_devices


def get_connection_status(address):
    """
    Return Bluetooth connection status for a device address:
    - "connected"
    - "disconnected"
    """
    if not address:
        return "disconnected"
    try:
        result = subprocess.run(
            ["bluetoothctl", "info", str(address)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and "connected: yes" in result.stdout.lower():
            return "connected"
    except Exception:
        pass
    return "disconnected"


def build_firestore_bluetooth_list():
    """
    Build Firestore bluetooth.list payload entries:
    {address, name, status}
    """
    devices = _discover_filtered_devices()
    payload = []
    for device in devices:
        address = device.get("address", "")
        payload.append(
            {
                "address": address,
                "name": device.get("name", "") or "",
                "status": get_connection_status(address),
            }
        )
    return payload


def current_utc_iso_timestamp():
    return datetime.now(timezone.utc).isoformat()

def scan_bluetooth_devices():
    """
    Scans for nearby Bluetooth devices and returns a list of devices
    that are likely controllers, headphones, or Bluetooth speakers.
    """
    try:
        return {"action": "bluetooth_scan", "devices": _discover_filtered_devices()}
    except Exception as e:
        return handle_exception("bluetooth_scan", e, "Bluetooth scan failed")

def list_paired_devices():
    """
    Lists all Bluetooth devices that are already paired (bonded) with the system.
    Returns a list of dictionaries with 'address' and 'name'.
    """
    paired_devices = []
    try:
        # Use bluetoothctl to get the authoritative list of paired devices
        result = subprocess.run(
            ['bluetoothctl', 'devices', 'Paired'],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            # Output format: Device XX:XX:XX:XX:XX:XX Name
            for line in result.stdout.splitlines():
                parts = line.split(" ", 2)
                if len(parts) >= 3 and parts[0] == "Device":
                    paired_devices.append({'address': parts[1], 'name': parts[2]})
        else:
            return handle_bluetooth_error(
                "bluetooth_list",
                ErrorCode.BLUETOOTH_LIST_FAILED,
                "Unable to list paired Bluetooth devices"
            )

    except Exception as e:
        return handle_exception("bluetooth_list", e, "Failed to list paired devices")

    return {"action": "bluetooth_list", "devices": paired_devices}

def disconnect_and_unpair_device(address):
    """
    Disconnects, removes (unpairs), and forgets a Bluetooth device using bluetoothctl.
    Returns True if successful, False otherwise.
    """
    try:
        process = subprocess.Popen(
            ['bluetoothctl'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        commands = [
            f'disconnect {address}\n',
            f'remove {address}\n',
            'exit\n'
        ]
        for cmd in commands:
            process.stdin.write(cmd)
            process.stdin.flush()

        process.communicate()
        return {"action": "bluetooth_remove", "address": address, "message": "Success"}
    except Exception as e:
        return handle_exception("bluetooth_remove", e, "Failed to remove Bluetooth device", address=address)

def pair_and_connect_device(address):
    """
    Scans, waits for the device to appear, then pairs, trusts, and connects to a Bluetooth device using bluetoothctl.
    Returns a status code indicating the result.
    """
    try:
        process = subprocess.Popen(
            ['bluetoothctl'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Start scanning
        process.stdin.write('power on\n')
        process.stdin.write('agent on\n')
        process.stdin.write('default-agent\n')
        process.stdin.write('scan on\n')
        process.stdin.flush()

        found = False
        scan_timeout = 20  # seconds
        start_time = time.time()
        while time.time() - start_time < scan_timeout:
            line = process.stdout.readline()
            if address in line:
                found = True
                process.stdin.write('scan off\n')
                process.stdin.flush()
                break
        if not found:
            process.stdin.write('scan off\n')
            process.stdin.write('exit\n')
            process.stdin.flush()
            process.terminate()
            return handle_bluetooth_error(
                "bluetooth_connect",
                ErrorCode.BLUETOOTH_DEVICE_NOT_FOUND,
                "Bluetooth device not found during scan",
                address=address
            )
            
        # Execute pair, trust, connect one by one
        process.stdin.write(f'pair {address}\n')
        process.stdin.flush()
        # Wait for pairing result
        pair_timeout = 5
        pair_start = time.time()
        paired = False
        while time.time() - pair_start < pair_timeout:
            line = process.stdout.readline()
            if "Pairing successful" in line or "Paired: yes" in line:
                paired = True
                break
            if "Failed to pair" in line or "AuthenticationFailed" in line:
                break
        if not paired:
            process.stdin.write('exit\n')
            process.stdin.flush()
            process.terminate()
            return handle_bluetooth_error(
                "bluetooth_connect",
                ErrorCode.BLUETOOTH_PAIRING_FAILED,
                "Unable to pair with Bluetooth device",
                address=address
            )

        process.stdin.write(f'trust {address}\n')
        process.stdin.flush()
        # Wait for trust result
        trust_timeout = 5
        trust_start = time.time()
        trusted = False
        while time.time() - trust_start < trust_timeout:
            line = process.stdout.readline()
            if "trust succeeded" in line or "Trusted: yes" in line:
                trusted = True
                break
        if not trusted:
            process.stdin.write('exit\n')
            process.stdin.flush()
            process.terminate()
            return handle_bluetooth_error(
                "bluetooth_connect",
                ErrorCode.BLUETOOTH_TRUST_FAILED,
                "Unable to trust Bluetooth device",
                address=address
            )

        process.stdin.write(f'connect {address}\n')
        process.stdin.flush()
        # Wait for connection confirmation before exiting
        connect_timeout = 5  # seconds
        connect_start = time.time()
        while time.time() - connect_start < connect_timeout:
            line = process.stdout.readline()
            if "Connection successful" in line or f"Device {address} connected" in line:
                break
            if "Failed to connect" in line or "AuthenticationFailed" in line:
                return handle_bluetooth_error(
                    "bluetooth_connect",
                    ErrorCode.BLUETOOTH_CONNECTION_FAILED,
                    "Unable to connect to Bluetooth device",
                    address=address
                )

        process.stdin.write('exit\n')
        process.stdin.flush()
        return {"action": "bluetooth_connect", "address": address, "message": "Success"}

    except Exception as e:
        return handle_exception("bluetooth_connect", e, "Failed to connect Bluetooth device", address=address)


def connect_device_for_firestore(address):
    """
    Firestore-oriented connection helper.
    Returns (success: bool, error_message: str).
    """
    if not address:
        return False, "Missing address"
    try:
        result = pair_and_connect_device(address)
        if isinstance(result, dict) and not result.get("error"):
            return True, ""
        if isinstance(result, dict):
            err = str(result.get("error") or "").strip()
            if err:
                short = err.split("(")[0].strip()
                return False, short[:120]
        return False, "Connection failed"
    except Exception:
        return False, "Connection failed"

# Example usage:
if __name__ == "__main__":
    # print(json.dumps(scan_bluetooth_devices()))
    # pair_and_connect_device("58:10:31:2D:12:52")
    # print(list_paired_devices())
    # disconnect_and_unpair_device("58:10:31:2D:12:52")
    pass