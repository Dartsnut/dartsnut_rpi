import bluetooth
import os
import subprocess
import time

def scan_bluetooth_devices():
    """
    Scans for nearby Bluetooth devices and returns a list of devices
    that are likely controllers, headphones, or Bluetooth speakers.
    """
    # Common keywords for audio devices and controllers
    audio_keywords = ['headphone', 'speaker', 'audio', 'controller']

    nearby_devices = bluetooth.discover_devices(duration=8, lookup_names=True)
    
    filtered_devices = []

    for addr, name in nearby_devices:
        lower_name = name.lower() if name else ""
        if any(keyword in lower_name for keyword in audio_keywords):
            filtered_devices.append({'address': addr, 'name': name})

    return {"action" : "bluetooth_scan", "devices": filtered_devices}

def list_paired_devices():
    """
    Lists all Bluetooth devices that are already paired (bonded) with the system.
    Returns a list of dictionaries with 'address' and 'name'.
    """

    paired_devices = []
    # This path is typical for BlueZ on Linux
    base_path = "/var/lib/bluetooth"
    if not os.path.exists(base_path):
        return {"action": "bluetooth_list", "error": "Bluetooth device not found"}

    for adapter in os.listdir(base_path):
        adapter_path = os.path.join(base_path, adapter)
        if not os.path.isdir(adapter_path):
            continue
        for device in os.listdir(adapter_path):
            device_path = os.path.join(adapter_path, device)
            info_file = os.path.join(device_path, "info")
            if os.path.isfile(info_file):
                try:
                    with open(info_file, "r") as f:
                        lines = f.readlines()
                        name = None
                        for line in lines:
                            if line.startswith("Name"):
                                name = line.strip().split("=", 1)[1]
                                break
                        if name:
                            paired_devices.append({'address': device, 'name': name})
                except Exception as e:
                    continue
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
        # stdout, stderr = process.communicate(timeout=10)
        # if "Device has been removed" in stdout or "Device has been removed" in stderr:
        #     print(f"Successfully disconnected and removed {address}")
        #     return True
        # else:
        #     print(f"Failed to remove {address}. Output: {stdout} {stderr}")
        #     return False
    except Exception as e:
        return {"action": "bluetooth_remove", "address": address, "error": f"Failed to remove device: {str(e)}"}

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
            return {"action": "bluetooth_connect", "address": address, "error": "Device not found during scan"}
            
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
            return {"action": "bluetooth_connect", "address": address, "error": "Pairing failed"}

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
            return {"action": "bluetooth_connect", "address": address, "error": "Trusting failed"}

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
                return {"action": "bluetooth_connect", "address": address, "error": "Connecting failed"}

        process.stdin.write('exit\n')
        process.stdin.flush()
        return {"action": "bluetooth_connect", "address": address, "message": "Success"}

    except Exception as e:
        return {"action": "bluetooth_connect", "address": address, "error": f"Failed to connect device: {str(e)}"}

# Example usage:
if __name__ == "__main__":
    # print(json.dumps(scan_bluetooth_devices()))
    # pair_and_connect_device("58:10:31:2D:12:52")
    # print(list_paired_devices())
    # disconnect_and_unpair_device("58:10:31:2D:12:52")
    pass