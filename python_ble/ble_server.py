# Bluezero modules
import asyncio
import json
import logging
import subprocess
import threading
import time

from bluezero import adapter
from bluezero import device
from bluezero import peripheral

from python_websocket.error_handler import (
    ErrorCode,
    create_error_response,
    handle_exception,
)
from network_utils import get_primary_ipv4
from runtime.bluetooth_identity import (
    build_bluetooth_local_name,
    get_bluetooth_adapter_address,
)

_log = logging.getLogger(__name__)

# constants
UART_SERVICE = '6E400001-B5A3-F393-E0A9-E50E24DCCA9E'
RX_CHARACTERISTIC = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'
TX_CHARACTERISTIC = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'

_WIFI_PASSWORD_ERROR_PATTERNS = (
    "invalid 802.11i/wpa passphrase",
    "invalid password",
    "bad password",
    "wrong password",
    "no valid secrets",
    "secrets were required",
)

_WIFI_TIMEOUT_ERROR_PATTERNS = (
    "timed out",
    "timeout",
    "activation took too long",
)


def _command_output_text(exc: subprocess.CalledProcessError) -> str:
    return " ".join(
        str(value or "")
        for value in (getattr(exc, "stdout", ""), getattr(exc, "stderr", ""))
    ).lower()


def _ble_error_response(command: str, error_code: ErrorCode, message: str, **kwargs):
    response = create_error_response(command, error_code, message, **kwargs)
    response["command"] = command
    return response


def _connect_wifi_error_response(exc: subprocess.CalledProcessError):
    output = _command_output_text(exc)
    if exc.returncode == 255:
        return _ble_error_response(
            "connect_wifi",
            ErrorCode.WIFI_ALREADY_CONNECTED,
            "Already connected to the given network",
        )
    if any(pattern in output for pattern in _WIFI_TIMEOUT_ERROR_PATTERNS):
        return _ble_error_response(
            "connect_wifi",
            ErrorCode.NETWORK_ERROR,
            "Unable to connect to the network",
        )
    if exc.returncode == 4 and any(
        pattern in output for pattern in _WIFI_PASSWORD_ERROR_PATTERNS
    ):
        return _ble_error_response(
            "connect_wifi",
            ErrorCode.WIFI_PASSWORD_WRONG,
            "The WiFi password is incorrect",
        )

    response = handle_exception("connect_wifi", exc, "Failed to connect to WiFi")
    response["command"] = "connect_wifi"
    return response



class UARTDevice:
    tx_obj = None
    callback = None
    device_info = None
    locate_device = None

    @classmethod
    def on_connect(cls, ble_device: device.Device):
        _log.info("ble: central connected address=%s", ble_device.address)

    @classmethod
    def on_disconnect(cls, adapter_address, device_address):
        _log.info("ble: central disconnected address=%s", device_address)

    @classmethod
    def uart_notify(cls, notifying, characteristic):
        if notifying:
            cls.tx_obj = characteristic
        else:
            cls.tx_obj = None

    @classmethod
    def send_data(cls, value):
        _log.debug("ble: tx %s", value)
        if cls.tx_obj:
            cls.tx_obj.set_value(json.dumps(value).encode('utf-8'))

    @classmethod
    def _scan_wifi_task(cls):
        # Use a system command to scan for WiFi networks
        try:
            subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'], capture_output=True, text=True, check=True)
            # Use nmcli for scanning instead of iwlist (more reliable and easier to parse)
            result = subprocess.run(['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'], capture_output=True, text=True, check=True)
            networks = []
            seen_ssids = set()
            
            for line in result.stdout.splitlines():
                parts = line.split(':')
                if len(parts) >= 1:
                    ssid = parts[0].replace(r'\:', ':')
                    if ssid and ssid not in seen_ssids:
                        networks.append(ssid)
                        seen_ssids.add(ssid)
                        
                        total_bytes = sum(len(n.encode('utf-8')) for n in networks)
                        if total_bytes > 64:
                            cls.send_data({"command": "scan_wifi", "networks": networks})
                            networks = []
                            
            # After the loop, send any remaining SSIDs
            if networks:
                cls.send_data({"command": "scan_wifi", "networks": networks, "end": True})
            else:
                cls.send_data({"command": "scan_wifi", "networks": [], "end": True})
        except subprocess.CalledProcessError as e:
            _log.warning("Failed to scan WiFi networks: %s", e)
            error_response = handle_exception("scan_wifi", e, "Failed to scan WiFi networks")
            error_response["command"] = "scan_wifi"
            cls.send_data(error_response)

    @classmethod
    def _connect_wifi_task(cls, ssid, password):
        try:
            # Use nmcli to connect to the WiFi network
            # Try connecting with WPA-PSK first (most common)
            try:
                result = subprocess.run(['nmcli', 'dev', 'wifi', 'connect', ssid, 'password', password], capture_output=True, text=True, check=True)
                # Get IP address
                ip_address = get_primary_ipv4()
                cls.send_data({"command": "connect_wifi", "status": "success", "ip_address": ip_address})
            except subprocess.CalledProcessError as inner_e:
                # Check for specific error codes that shouldn't trigger fallback
                error_response = _connect_wifi_error_response(inner_e)
                if error_response.get("error_code") in {
                    ErrorCode.WIFI_ALREADY_CONNECTED.value,
                    ErrorCode.WIFI_PASSWORD_WRONG.value,
                    ErrorCode.NETWORK_ERROR.value,
                }:
                    cls.send_data(error_response)
                    return
                # If that fails with other error, try specifying WPA-PSK explicitly (sometimes needed for nmcli)
                # Or try without specifying security if it's open (though password implies security)
                # This fallback attempts to delete any existing connection profile first to avoid conflicts
                # Delete existing connection if any (ignore errors if it doesn't exist)
                subprocess.run(['nmcli', 'connection', 'delete', ssid], capture_output=True, check=False)
                # Create new connection profile manually
                subprocess.run(['nmcli', 'con', 'add', 'type', 'wifi', 'ifname', 'wlan0', 'con-name', ssid, 'ssid', ssid], capture_output=True, check=False)
                subprocess.run(['nmcli', 'con', 'modify', ssid, 'wifi-sec.key-mgmt', 'wpa-psk'], capture_output=True, check=False)
                subprocess.run(['nmcli', 'con', 'modify', ssid, 'wifi-sec.psk', password], capture_output=True, check=False)
                # Bring up the connection
                result = subprocess.run(['nmcli', 'con', 'up', ssid], capture_output=True, text=True, check=True)
                # Get IP address
                ip_address = get_primary_ipv4()
                cls.send_data({"command": "connect_wifi", "ip_address": ip_address, "status": "success"})
        except subprocess.CalledProcessError as e:
            _log.warning("Failed to connect to WiFi: %s", e)
            cls.send_data(_connect_wifi_error_response(e))

    @classmethod
    def _reconnect_wifi_task(cls):
        try:
            # Disconnect
            subprocess.run(['nmcli', 'dev', 'disconnect', 'wlan0'], capture_output=True, check=False)
            time.sleep(2)
            # Reconnect (bring up the device, nmcli usually auto-connects to known networks)
            subprocess.run(['nmcli', 'dev', 'connect', 'wlan0'], capture_output=True, text=True, check=True)
            time.sleep(2)
            # Get IP address
            ip_address = get_primary_ipv4()
            cls.send_data({"command": "reconnect_wifi", "ip_address": ip_address, "status": "success"})
        except subprocess.CalledProcessError as e:
            _log.warning("Failed to reconnect WiFi: %s", e)
            error_response = handle_exception("reconnect_wifi", e, "Failed to reconnect WiFi")
            error_response["command"] = "reconnect_wifi"
            cls.send_data(error_response)

    @classmethod
    def uart_write(cls, value, options):
        try:
            data = json.loads(value.decode("utf-8"))
            command = data.get("command")
            _log.debug("ble: rx command=%s", command)
            if command is None:
                _log.warning("ble: uart JSON missing command")
                return
            elif (command == "scan_wifi"):
                threading.Thread(target=cls._scan_wifi_task, daemon=True).start()
            elif (command == "enable_wifi"):
                # Enable WiFi using nmcli
                try:
                    result = subprocess.run(['nmcli', 'radio', 'wifi', 'on'], capture_output=True, text=True, check=True)
                    cls.send_data({"command": "enable_wifi", "status": "success"})
                except subprocess.CalledProcessError as e:
                    _log.warning("Failed to enable WiFi: %s", e)
                    error_response = handle_exception("enable_wifi", e, "Failed to enable WiFi")
                    error_response["command"] = "enable_wifi"
                    cls.send_data(error_response)
            elif (command == "wifi_status"):
                # Check WiFi status
                try:
                    # Check if WiFi is enabled
                    result = subprocess.run(['nmcli', 'radio', 'wifi'], capture_output=True, text=True, check=True)
                    wifi_enabled = result.stdout.strip() == "enabled"
                    if wifi_enabled:
                        # Check connection status
                        result = subprocess.run(['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'], capture_output=True, text=True, check=True)
                        connected_info = [line for line in result.stdout.splitlines() if line.startswith("yes:")]
                        if connected_info:
                            _, ssid = connected_info[0].split(':')
                            # Get the IP address of the connected WiFi
                            ip_address = get_primary_ipv4()
                            cls.send_data({"command": "wifi_status", "wifi_enabled": True, "connected": True, "ssid": ssid, "ip_address": ip_address})
                        else:
                            cls.send_data({"command": "wifi_status", "wifi_enabled": True, "connected": False})
                    else:
                        cls.send_data({"command": "wifi_status", "wifi_enabled": False})
                except subprocess.CalledProcessError as e:
                    _log.warning("Failed to get WiFi status: %s", e)
                    error_response = handle_exception("wifi_status", e, "Failed to get WiFi status")
                    error_response["command"] = "wifi_status"
                    cls.send_data(error_response)
            elif (command == "connect_wifi"):
                # Connect to a WiFi network
                ssid = data.get("ssid")
                password = data.get("password")
                if ssid and password:
                    threading.Thread(target=cls._connect_wifi_task, args=(ssid, password), daemon=True).start()
                else:
                    error_response = create_error_response("connect_wifi", ErrorCode.MISSING_PARAMETER, "SSID or password missing")
                    error_response["command"] = "connect_wifi"
                    cls.send_data(error_response)
            elif (command == "reconnect_wifi"):
                threading.Thread(target=cls._reconnect_wifi_task, daemon=True).start()
            elif (command == "device_info"):
                try:
                    # Get the MAC address of the device
                    with open('/sys/class/net/wlan0/address', 'r') as f:
                        mac_address = f.read().strip()
                    UARTDevice.device_info["mac_address"] = mac_address
                    cls.send_data({"command": "device_info", "info": UARTDevice.device_info})
                except Exception as e:
                    _log.warning("Failed to get device info: %s", e)
                    error_response = handle_exception("device_info", e, "Failed to get device info")
                    error_response["command"] = "device_info"
                    cls.send_data(error_response)
            elif (command == "locate_device"):
                if UARTDevice.locate_device:
                    threading.Thread(target=UARTDevice.locate_device, daemon=True).start()
                cls.send_data({"command": "locate_device", "status": "success"})
            else:
                error_response = create_error_response("unknown", ErrorCode.UNKNOWN_ACTION, "Unknown command")
                error_response["command"] = command if command else "unknown"
                cls.send_data(error_response)
                
        except json.JSONDecodeError as e:
            error_response = create_error_response("unknown", ErrorCode.INVALID_JSON, "Failed to decode JSON")
            error_response["command"] = "unknown"
            cls.send_data(error_response)


def start_ble_server(locate_device=None):
    UARTDevice.locate_device = locate_device if locate_device else None
    with open("device.json", 'r') as file:
        UARTDevice.device_info = json.load(file)

    # Use the shared identity helper so the UI QR and BLE advertisement match.
    adapter_address = get_bluetooth_adapter_address()
    if not adapter_address:
        raise RuntimeError("No Bluetooth adapter available")
    local_name = build_bluetooth_local_name(UARTDevice.device_info, adapter_address)
    UARTDevice.device_info["ble_mac"] = adapter_address
    # Ensure Bluetooth is unblocked and powered on
    try:
        subprocess.run(['rfkill', 'unblock', 'bluetooth'], check=False, capture_output=True)
    except Exception as e:
        _log.warning("rfkill unblock failed: %s", e)
        
    # Power up bt adapter
    try:
        bt_adapter = adapter.Adapter(adapter_address)
        if not bt_adapter.powered:
            bt_adapter.powered = True

        for _ in range(10):
            if bt_adapter.powered:
                break
            time.sleep(0.2)
    except Exception as e:
        _log.warning("Failed to power Bluetooth adapter: %s", e)

    # Set the adapter alias to match the local name
    try:
        bt_adapter.alias = local_name
    except Exception as e:
        _log.warning("Failed to set adapter alias: %s", e)

    ble_uart = peripheral.Peripheral(adapter_address, local_name=local_name)
    ble_uart.add_service(srv_id=1, uuid=UART_SERVICE, primary=True)
    ble_uart.add_characteristic(srv_id=1, chr_id=1, uuid=RX_CHARACTERISTIC,
                                value=[], notifying=False,
                                flags=['write', 'write-without-response'],
                                write_callback=UARTDevice.uart_write,
                                read_callback=None,
                                notify_callback=None)
    ble_uart.add_characteristic(srv_id=1, chr_id=2, uuid=TX_CHARACTERISTIC,
                                value=[], notifying=False,
                                flags=['notify'],
                                notify_callback=UARTDevice.uart_notify,
                                read_callback=None,
                                write_callback=None)

    ble_uart.on_connect = UARTDevice.on_connect
    ble_uart.on_disconnect = UARTDevice.on_disconnect

    from runtime.logging_config import tune_third_party_logging

    tune_third_party_logging()
    _log.info("ble: publishing GATT peripheral name=%s", local_name)
    ble_uart.publish()

if __name__ == '__main__':
    server_thread = threading.Thread(target=start_ble_server, daemon=True)
    server_thread.start()

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        _log.info("ble_server exiting...")
