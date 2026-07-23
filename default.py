"""Default widget: show the machine's dynamic Bluetooth connection QR."""
import json
import os
import time

from pydartsnut import Dartsnut

from runtime.bluetooth_qr import create_bluetooth_qr_for_device


def _load_device_info() -> dict:
    try:
        device_path = os.path.join(os.getcwd(), "device.json")
        with open(device_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _display_connection_qr(dartsnut) -> None:
    while True:
        try:
            qr_image = create_bluetooth_qr_for_device(_load_device_info())
            if qr_image is not None:
                dartsnut.update_frame_buffer(qr_image)
                return
        except Exception:
            pass
        time.sleep(1)


def main() -> None:
    dartsnut = Dartsnut()
    _display_connection_qr(dartsnut)
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("default widget exiting...")


if __name__ == "__main__":
    main()
