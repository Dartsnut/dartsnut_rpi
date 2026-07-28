"""Default widget: show the machine's dynamic Bluetooth connection QR."""
import json
import os
import time

from pydartsnut import Dartsnut

from runtime.bluetooth_identity import (
    resolve_bluetooth_device_id,
    resolve_bluetooth_local_name,
)
from runtime.bluetooth_qr import connection_qr_payload, create_qr_surface
from runtime.qr_status import read_qr_status


def _load_device_info() -> dict:
    try:
        device_path = os.path.join(os.getcwd(), "device.json")
        with open(device_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _desired_connection_payload(device_info: dict) -> str | None:
    status = read_qr_status()
    return connection_qr_payload(
        resolve_bluetooth_local_name(device_info),
        supabase_connected=status["supabase_connected"],
        device_id=resolve_bluetooth_device_id(device_info) or "",
    )


def _display_connection_qr(dartsnut) -> str:
    """Retry until an identity is available, render it, and return its payload."""
    while True:
        try:
            payload = _desired_connection_payload(_load_device_info())
            if payload is not None:
                dartsnut.update_frame_buffer(create_qr_surface(payload))
                return payload
        except Exception:
            pass
        time.sleep(1)


def _refresh_connection_qr_loop(dartsnut, current_payload: str) -> None:
    """Keep the default widget QR synchronized with Supabase connectivity."""
    while True:
        time.sleep(1)
        try:
            payload = _desired_connection_payload(_load_device_info())
            if payload is not None and payload != current_payload:
                dartsnut.update_frame_buffer(create_qr_surface(payload))
                current_payload = payload
        except Exception:
            pass


def main() -> None:
    dartsnut = Dartsnut()
    current_payload = _display_connection_qr(dartsnut)
    try:
        _refresh_connection_qr_loop(dartsnut, current_payload)
    except KeyboardInterrupt:
        print("default widget exiting...")


if __name__ == "__main__":
    main()
