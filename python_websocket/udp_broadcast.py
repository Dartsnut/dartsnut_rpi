import json
import logging
import os
import socket
import subprocess
import time
from typing import Optional
from network_utils import get_wifi_ipv4

_log = logging.getLogger(__name__)


def normalize_ip(ip: str) -> Optional[str]:
    """
    Normalize an IP string and classify invalid/local cases.

    Current UI behavior treats missing/failed lookups as "0.0.0.0". To align
    remote sync with that behavior, we consider the following invalid and return None:
    - Empty/whitespace-only strings
    - Literal "0.0.0.0"

    All other values (including private/local addresses) are treated as valid;
    they are returned unchanged so that the UI and remote sync stay consistent.
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
    IPs where \"blank\" means \"do not populate the field\" in remote sync.
    """
    if ssid is None:
        return None
    s = str(ssid).strip()
    if not s:
        return None
    return s


def get_ip_address() -> str:
    return get_wifi_ipv4()

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
    
    _log.info("UDP discovery broadcast started (port 9252)")

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
            _log.warning("UDP broadcast error: %s", e)
        finally:
            if udp_socket:
                try:
                    udp_socket.close()
                except Exception:
                    pass
        
        time.sleep(3)