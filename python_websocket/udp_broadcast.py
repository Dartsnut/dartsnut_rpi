import subprocess
import socket
import time
import json
import os
from network_utils import get_wifi_ipv4

def get_ip_address():
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
            message = json.dumps({"ip": ip, "mac": wlan_mac, "ble_mac": ble_mac, "device_info": device_info})
            
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