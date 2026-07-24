import bluetooth
import subprocess
import threading
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


def build_remote_bluetooth_list():
    """
    Build remote bluetooth.list payload entries:
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


def list_paired_devices_with_status():
    """Return paired devices with normalized addresses and live connection status."""
    paired_result = list_paired_devices()
    if not isinstance(paired_result, dict) or paired_result.get("error"):
        return []
    devices = paired_result.get("devices")
    if not isinstance(devices, list):
        return []

    out = []
    seen = set()
    for device in devices:
        if not isinstance(device, dict):
            continue
        address = _normalize_bt_address(device.get("address"))
        if not address or address in seen:
            continue
        seen.add(address)
        out.append(
            {
                "address": address,
                "name": str(device.get("name") or "").strip(),
                "status": get_connection_status(address),
            }
        )
    return out


def list_connected_paired_devices():
    """
    Paired (bonded) devices that are currently connected.
    Returns a list of {"address": "<UPPER MAC>", "name": "<str>"}, unique by address.
    """
    paired_result = list_paired_devices()
    if not isinstance(paired_result, dict) or paired_result.get("error"):
        return []
    devices = paired_result.get("devices")
    if not isinstance(devices, list):
        return []

    out = []
    seen = set()
    for device in devices:
        if not isinstance(device, dict):
            continue
        address = _normalize_bt_address(device.get("address"))
        if not address or address in seen:
            continue
        if get_connection_status(address) != "connected":
            continue
        seen.add(address)
        out.append(
            {
                "address": address,
                "name": str(device.get("name") or "").strip(),
            }
        )
    return out


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
        return handle_exception(
            "bluetooth_remove", e, "Failed to remove Bluetooth device", address=address
        )

def _normalize_bt_address(address):
    """Canonical MAC for bluetoothctl (uppercase segments)."""
    if not address:
        return ""
    return str(address).strip().upper()


def _bluetoothctl_open():
    """Single bluetoothctl session: merge stderr into stdout for reliable parsing."""
    return subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _start_btctl_stdout_drain(process):
    """Avoid filling the pipe (bluetoothctl blocks when stdout buffer is full)."""

    def _run():
        try:
            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _bt_info_text(address):
    """Snapshot from bluetoothctl info (authoritative for Paired/Trusted/Connected)."""
    if not address:
        return ""
    try:
        result = subprocess.run(
            ["bluetoothctl", "info", str(address)],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        if result.returncode == 0:
            return result.stdout or ""
    except Exception:
        pass
    return ""


def _info_flag_yes(address, flag_prefix):
    """flag_prefix e.g. 'paired', 'trusted', 'connected' -> matches 'Paired: yes' etc."""
    out = _bt_info_text(address).lower()
    return f"{flag_prefix.lower()}: yes" in out


def _remove_device_best_effort(address):
    """Clear stale bond before re-pairing (ignore errors if device is unknown)."""
    if not address:
        return
    try:
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        process.stdin.write(f"disconnect {address}\nremove {address}\nexit\n")
        process.stdin.close()
        process.wait(timeout=20)
    except Exception:
        pass


def _poll_until(predicate, timeout_sec, interval=0.4):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _scan_until_device_seen(address, scan_timeout=20):
    """
    Run scan in a dedicated bluetoothctl process and exit.
    Must not share a session with pair/trust/connect: leftover scan output in the
    pipe would otherwise be consumed by the pair loop and hide real pair results
    (especially on second pairing after unpair).
    """
    addr_u = _normalize_bt_address(address)
    process = _bluetoothctl_open()
    try:
        process.stdin.write("power on\n")
        process.stdin.write("scan on\n")
        process.stdin.flush()

        start_time = time.time()
        while time.time() - start_time < scan_timeout:
            line = process.stdout.readline()
            if not line:
                break
            if addr_u and addr_u.lower() in line.lower():
                process.stdin.write("scan off\n")
                process.stdin.flush()
                return True

        process.stdin.write("scan off\n")
        process.stdin.flush()
        return False
    finally:
        try:
            process.stdin.write("exit\n")
            process.stdin.flush()
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            pass


def pair_and_connect_device(address):
    """
    Scans (if not already bonded), pairs, trusts, and connects using bluetoothctl.
    Verifies each step with ``bluetoothctl info`` because pair/trust success lines
    on stdout are inconsistent and a full stdout buffer can block bluetoothctl.
    If the device is already paired, scanning is skipped so reconnect works when
    the device is not discoverable.
    """
    address = _normalize_bt_address(address)
    if not address:
        return handle_bluetooth_error(
            "bluetooth_connect",
            ErrorCode.BLUETOOTH_CONNECT_FAILED,
            "Missing Bluetooth address",
            address=address,
        )

    process = None
    try:
        already_paired = _info_flag_yes(address, "paired")
        if not already_paired:
            if not _scan_until_device_seen(address):
                return handle_bluetooth_error(
                    "bluetooth_connect",
                    ErrorCode.BLUETOOTH_DEVICE_NOT_FOUND,
                    "Bluetooth device not found during scan",
                    address=address,
                )

        process = _bluetoothctl_open()
        _start_btctl_stdout_drain(process)
        process.stdin.write("power on\n")
        process.stdin.write("agent on\n")
        process.stdin.write("default-agent\n")
        process.stdin.flush()

        if not already_paired:
            process.stdin.write(f"pair {address}\n")
            process.stdin.flush()
            paired_ok = _poll_until(
                lambda: _info_flag_yes(address, "paired"),
                timeout_sec=30,
            )
            if not paired_ok:
                _remove_device_best_effort(address)
                time.sleep(0.6)
                process.stdin.write(f"pair {address}\n")
                process.stdin.flush()
                paired_ok = _poll_until(
                    lambda: _info_flag_yes(address, "paired"),
                    timeout_sec=30,
                )
            if not paired_ok:
                return handle_bluetooth_error(
                    "bluetooth_connect",
                    ErrorCode.BLUETOOTH_PAIRING_FAILED,
                    "Unable to pair with Bluetooth device",
                    address=address,
                )

        process.stdin.write(f"trust {address}\n")
        process.stdin.flush()
        if not _poll_until(
            lambda: _info_flag_yes(address, "trusted"),
            timeout_sec=20,
        ):
            return handle_bluetooth_error(
                "bluetooth_connect",
                ErrorCode.BLUETOOTH_TRUST_FAILED,
                "Unable to trust Bluetooth device",
                address=address,
            )

        process.stdin.write(f"connect {address}\n")
        process.stdin.flush()
        if not _poll_until(
            lambda: _info_flag_yes(address, "connected"),
            timeout_sec=30,
        ):
            return handle_bluetooth_error(
                "bluetooth_connect",
                ErrorCode.BLUETOOTH_CONNECTION_FAILED,
                "Unable to connect to Bluetooth device",
                address=address,
            )

        try:
            process.stdin.write("exit\n")
            process.stdin.flush()
            process.wait(timeout=5)
        except Exception:
            pass
        return {"action": "bluetooth_connect", "address": address, "message": "Success"}

    except Exception as e:
        return handle_exception(
            "bluetooth_connect",
            e,
            "Failed to connect Bluetooth device",
            address=address,
        )
    finally:
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                pass


def connect_device_for_remote(address):
    """
    Remote-oriented connection helper.
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