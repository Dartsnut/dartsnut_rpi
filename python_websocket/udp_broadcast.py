import subprocess
import socket
import time
import json
import os
from typing import Optional

from firestore_sync_bridge import is_firestore_bridge_active, notify_device_state_update


_last_published_ip: Optional[str] = None
_last_published_ssid: Optional[str] = None


def normalize_ip(ip: str) -> Optional[str]:
    """
    Normalize an IP string and classify invalid/local cases.

    Current UI behavior treats missing/failed lookups as "0.0.0.0". To align
    Firestore with that behavior, we consider the following invalid and return None:
    - Empty/whitespace-only strings
    - Literal "0.0.0.0"

    All other values (including private/local addresses) are treated as valid;
    they are returned unchanged so that the UI and Firestore stay consistent.
    """
    if ip is None:
        return None
    s = str(ip).strip()
    if not s:
        return None
    if s == "0.0.0.0":
        return None
    return s


def normalize_ssid(ssid: str) -> Optional[str]:
    """
    Normalize SSID string.

    Treat empty/whitespace-only SSIDs as invalid (None). Any non-empty string is
    considered valid and returned stripped. This matches the pattern used for
    IPs where \"blank\" means \"do not populate the field\" in Firestore.
    """
    if ssid is None:
        return None
    s = str(ssid).strip()
    if not s:
        return None
    return s


def get_ip_address() -> str:
    try:
        ips = (
            subprocess.check_output(["hostname", "-I"])
            .decode("utf-8")
            .strip()
            .split()
        )
        raw = ips[0] if ips else "0.0.0.0"
    except Exception:
        raw = "0.0.0.0"
    normalized = normalize_ip(raw)
    return normalized or "0.0.0.0"

def get_mac_address():
    try:
        with open("/sys/class/net/wlan0/address", "r") as f:
            return f.read().strip().lower()
    except Exception:
        return "00:00:00:00:00:00"

def get_device_info():
    # Caching mechanism
    if not hasattr(get_device_info, "_last_mtime"):
        get_device_info._last_mtime = 0
        get_device_info._cached_device_info = None
    # Read device.json file
    file_path = os.path.join(os.getcwd(), "device.json")
    try:
        current_mtime = os.path.getmtime(file_path)
        if current_mtime != get_device_info._last_mtime or get_device_info._cached_device_info is None:
            with open(file_path, 'r') as file:
                get_device_info._cached_device_info = json.load(file)
            get_device_info._last_mtime = current_mtime
        return get_device_info._cached_device_info
    except Exception:
        return {}


def get_current_ssid() -> str:
    """
    Read the current WiFi SSID from NetworkManager (nmcli).

    Returns an empty string when not connected or on error.
    """
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("yes:"):
                # Format is 'yes:<ssid>'
                parts = line.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip()
        return ""
    except Exception:
        return ""

def get_ble_mac():
    if hasattr(get_ble_mac, "_cached_mac"):
        return get_ble_mac._cached_mac
        
    try:
        output = subprocess.check_output(["hcitool", "dev"]).decode('utf-8')
        for line in output.split('\n'):
            if "hci0" in line:
                mac = line.split()[1].lower()
                get_ble_mac._cached_mac = mac
                return mac
        return "00:00:00:00:00:00"
    except Exception:
        return "00:00:00:00:00:00"

def get_broadcast_addresses():
    addresses = set()
    try:
        output = subprocess.check_output(["ip", "-4", "addr"]).decode('utf-8')
        for line in output.split('\n'):
            if "inet" in line and "brd" in line:
                parts = line.split()
                try:
                    brd_idx = parts.index("brd")
                    addresses.add(parts[brd_idx + 1])
                except ValueError:
                    pass
    except Exception:
        pass
    
    # Only use generic broadcast if no specific subnet broadcast was found
    if not addresses:
        addresses.add("255.255.255.255")
        
    return list(addresses)

def udp_broadcast():
    wlan_mac = get_mac_address()
    ble_mac = get_ble_mac()
    
    print("UDP Broadcast started on port 9252")

    while True:
        udp_socket = None
        try:
            # Create a new socket for each broadcast to ensure it uses the current network state
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            
            # Bind to 0.0.0.0 to ensure we use the network stack properly
            try:
                udp_socket.bind(('', 0))
            except Exception:
                pass

            ip = get_ip_address()
            device_info = get_device_info()

            # Update Firestore devices/{ble_mac} when bridge is active.
            try:
                if is_firestore_bridge_active():
                    updates: dict = {}

                    # IP address: represent invalid/local IPs as blank in Firestore.
                    normalized_ip = normalize_ip(ip)
                    payload_ip = normalized_ip or ""
                    global _last_published_ip
                    if payload_ip != _last_published_ip:
                        updates["ip_address"] = payload_ip
                        _last_published_ip = payload_ip

                    # SSID: fetch live from nmcli; mirror same \"blank when invalid\" convention.
                    raw_ssid = get_current_ssid()
                    normalized_ssid = normalize_ssid(raw_ssid)
                    payload_ssid = normalized_ssid or ""
                    global _last_published_ssid
                    if payload_ssid != _last_published_ssid:
                        updates["ssid"] = payload_ssid
                        _last_published_ssid = payload_ssid

                    if updates:
                        notify_device_state_update(updates)
            except Exception:
                # Firestore updates are best-effort; don't break UDP broadcast loop.
                pass

            message = json.dumps(
                {
                    "ip": ip,
                    "mac": wlan_mac,
                    "ble_mac": ble_mac,
                    "device_info": device_info,
                }
            )
            
            # Send to all broadcast addresses
            for addr in get_broadcast_addresses():
                try:
                    udp_socket.sendto(message.encode(), (addr, 9252))
                except Exception:
                    pass
            
        except Exception as e:
            print(f"UDP broadcast error: {e}")
        finally:
            if udp_socket:
                try:
                    udp_socket.close()
                except Exception:
                    pass
        
        time.sleep(3)