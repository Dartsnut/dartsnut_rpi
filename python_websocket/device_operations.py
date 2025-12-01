import subprocess

def get_wifi_rssi():
    try:
        # Run the command to get signal level
        result = subprocess.check_output("iwconfig wlan0 | grep -i --color=never 'Signal level'", shell=True).decode('utf-8')
        # Extract the signal level value (e.g., -56)
        rssi = result.split("Signal level=")[1].split(" ")[0]
        return {"action": "get_wifi_rssi", "rssi": rssi}
    except Exception as e:
        return {"action": "get_wifi_rssi", "error": str(e)}

def forget_wifi():
    subprocess.run("nmcli -t -f NAME,TYPE connection show | grep 802-11-wireless | cut -d: -f1 | xargs -r -n1 nmcli connection delete", shell=True)
    subprocess.run("nmcli radio wifi off && nmcli radio wifi on", shell=True)

def reboot():
    os.system("sudo reboot")

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