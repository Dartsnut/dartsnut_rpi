import json
import subprocess

from python_websocket.error_handler import (
    ErrorCode,
    create_error_response,
    handle_command_error,
    handle_exception,
)


def test_create_error_response_formats_code():
    result = create_error_response("act", ErrorCode.INVALID_INPUT, "bad input")
    assert result["action"] == "act"
    assert result["error_code"] == "3001"
    assert result["error"].endswith("(30-01)")


def test_handle_exception_called_process_error_uses_command_mapping():
    exc = subprocess.CalledProcessError(1, ["systemctl", "start", "ssh"])
    result = handle_exception("start_ssh", exc, "Failed to start SSH service")
    assert result["action"] == "start_ssh"
    assert result["error_code"] == "4001"


def test_handle_exception_json_decode_and_key_error():
    decode_exc = json.JSONDecodeError("bad", "{}", 1)
    decode_result = handle_exception("read_json", decode_exc, "oops")
    key_result = handle_exception("x", KeyError("k"), None)
    assert decode_result["error_code"] == "3003"
    assert key_result["error_code"] == "3002"


def test_handle_command_error_uses_standard_shape():
    result = handle_command_error("wifi", "nmcli radio wifi")
    assert result["action"] == "wifi"
    assert result["error_code"] == "4001"
