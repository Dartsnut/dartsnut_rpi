import importlib
import subprocess
import sys
import types


def _load_ble_server():
    adapter = types.ModuleType("bluezero.adapter")
    device = types.ModuleType("bluezero.device")
    peripheral = types.ModuleType("bluezero.peripheral")
    device.Device = type("Device", (), {})
    bluezero = types.ModuleType("bluezero")
    bluezero.adapter = adapter
    bluezero.device = device
    bluezero.peripheral = peripheral
    sys.modules.setdefault("bluezero", bluezero)
    sys.modules.setdefault("bluezero.adapter", adapter)
    sys.modules.setdefault("bluezero.device", device)
    sys.modules.setdefault("bluezero.peripheral", peripheral)
    return importlib.import_module("python_ble.ble_server")


def _nmcli_error(returncode, stderr="", stdout=""):
    return subprocess.CalledProcessError(
        returncode,
        ["nmcli", "dev", "wifi", "connect", "ssid", "password", "secret"],
        output=stdout,
        stderr=stderr,
    )


def test_connect_wifi_timeout_is_not_reported_as_invalid_password():
    ble_server = _load_ble_server()
    response = ble_server._connect_wifi_error_response(
        _nmcli_error(4, stderr="Error: Connection activation failed: Activation took too long")
    )
    assert response["command"] == "connect_wifi"
    assert response["error_code"] == "2001"
    assert "Unable to connect to the network" in response["error"]
    assert "password" not in response["error"].lower()


def test_connect_wifi_password_error_requires_password_signal():
    ble_server = _load_ble_server()
    response = ble_server._connect_wifi_error_response(
        _nmcli_error(4, stderr="Error: Connection activation failed: Secrets were required, but not provided")
    )
    assert response["error_code"] == "2008"
    assert "password" in response["error"].lower()


def test_connect_wifi_activation_failure_without_specific_signal_is_generic():
    ble_server = _load_ble_server()
    response = ble_server._connect_wifi_error_response(
        _nmcli_error(4, stderr="Error: Connection activation failed")
    )
    assert response["error_code"] == "4001"
    assert "password" not in response["error"].lower()
