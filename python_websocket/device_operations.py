import subprocess
import re

def get_wifi_rssi():
    try:
        # Run the command to get signal level
        result = subprocess.check_output(["iwconfig", "wlan0"]).decode('utf-8')
        # Extract the signal level value (e.g., -56)
        match = re.search(r'Signal level=(-\d+)', result)
        if match:
            rssi = match.group(1)
            return {"action": "get_wifi_rssi", "rssi": rssi}
        return {"action": "get_wifi_rssi", "error": "Signal level not found"}
    except Exception as e:
        return {"action": "get_wifi_rssi", "error": str(e)}

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
        return {"action": "get_ssh_status", "error": str(e)}

def start_ssh():
    try:
        subprocess.run(['sudo', 'systemctl', 'start', 'ssh'], check=True)
        return {"action": "start_ssh", "message": "SSH started successfully"}
    except subprocess.CalledProcessError as e:
        return {"action": "start_ssh", "error": str(e)}

def stop_ssh():
    try:
        subprocess.run(['sudo', 'systemctl', 'stop', 'ssh'], check=True)
        return {"action": "stop_ssh", "message": "SSH stopped successfully"}
    except subprocess.CalledProcessError as e:
        return {"action": "stop_ssh", "error": str(e)}