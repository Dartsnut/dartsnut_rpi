import subprocess
import socket
import time
import json
import os

def get_ip_address():
    try:
        return subprocess.check_output(["hostname", "-I"]).decode('utf-8').strip()
    except Exception:
        return "0.0.0.0"

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

def udp_broadcast():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    wlan_mac = get_mac_address()
    ble_mac = get_ble_mac()

    while True:
        try:
            ip = get_ip_address()
            device_info = get_device_info()
            message = json.dumps({"ip": ip, "mac": wlan_mac, "ble_mac": ble_mac, "device_info": device_info})
            udp_socket.sendto(message.encode(), ('<broadcast>', 9252))
            time.sleep(3)
        except Exception as e:
            print(f"UDP broadcast error: {e}")
            time.sleep(3)