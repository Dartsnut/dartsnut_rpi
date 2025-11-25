import subprocess
import socket
import time
import json
import os

def get_ip_address():
    try:
        return subprocess.check_output("hostname -I", shell=True).decode('utf-8').strip()
    except Exception:
        return "0.0.0.0"

def get_mac_address():
    try:
        return subprocess.check_output("cat /sys/class/net/wlan0/address", shell=True).decode('utf-8').strip()
    except Exception:
        return "00:00:00:00:00:00"

def get_device_info():
    with open(os.path.join(os.getcwd(), "device.json"), 'r') as file:
        return json.load(file)

def udp_broadcast():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    device_info = get_device_info()
    
    while True:
        try:
            ip = get_ip_address()
            mac = get_mac_address()
            message = json.dumps({"ip": ip, "mac": mac, "device_info": device_info})
            udp_socket.sendto(message.encode(), ('<broadcast>', 9252))
            time.sleep(3)
        except Exception as e:
            print(f"UDP broadcast error: {e}")
            time.sleep(3)