import json
import os
import socket
import threading
from typing import Any, Callable, Dict, Optional


SOCKET_PATH = "/tmp/dartsnut-firestore-sync.sock"


class _SyncClient:
    def __init__(self, socket_path: str, reload_config: Callable[[], None]) -> None:
        self._socket_path = socket_path
        self._reload_config = reload_config
        self._conn: Optional[socket.socket] = None
        self._conn_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None

    def start_server(self, on_config_updated: Callable[[Dict[str, Any]], None]) -> None:
        def _server() -> None:
            try:
                if os.path.exists(self._socket_path):
                    os.remove(self._socket_path)
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                srv.bind(self._socket_path)
                srv.listen(1)
                conn, _ = srv.accept()
                with self._conn_lock:
                    self._conn = conn
                with conn, srv:
                    buf = b""
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        buf += data
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line.decode("utf-8"))
                            except Exception:
                                continue
                            kind = msg.get("kind")
                            payload = msg.get("payload")
                            if kind == "config" and isinstance(payload, dict):
                                try:
                                    on_config_updated(payload)
                                except Exception:
                                    continue
                                try:
                                    self._reload_config()
                                except Exception:
                                    continue
            except Exception:
                return

        self._reader_thread = threading.Thread(target=_server, daemon=True)
        self._reader_thread.start()

    def send_state(self, payload: Dict[str, Any], *, full: bool = False) -> None:
        msg = {
            "kind": "initial_state" if full else "device_state",
            "payload": payload,
        }
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._conn_lock:
            conn = self._conn
        if not conn:
            return
        try:
            conn.sendall(data)
        except Exception:
            return


_client: Optional[_SyncClient] = None


def _build_initial_state(device_info: Dict[str, Any]) -> Dict[str, Any]:
    brightness_raw = device_info.get("brightness")
    volume_raw = device_info.get("volume")
    try:
        brightness = int(brightness_raw)
    except Exception:
        brightness = 0
    try:
        volume = int(volume_raw)
    except Exception:
        volume = 0

    dim_window = {
        "dim_window_enabled": bool(device_info.get("dim_window_enabled", False)),
        "dim_window_start": device_info.get("dim_window_start", ""),
        "dim_window_end": device_info.get("dim_window_end", ""),
        "dim_level": device_info.get("dim_level", 0),
        "dim_restore_seconds": device_info.get("dim_restore_seconds", 0),
    }

    device_meta = {
        "sn": device_info.get("serial", ""),
        "model": device_info.get("model", ""),
        "name": device_info.get("name", ""),
    }

    pages_config: Dict[str, Any] = {}
    try:
        if os.path.isfile("./apps/conf.json"):
            with open("./apps/conf.json", "r") as f:
                pages_config = json.load(f)
    except Exception:
        pages_config = {}

    cfg: Dict[str, Any] = {
        "games": [],
        "brightness": brightness,
        "pages": pages_config.get("pages", []),
        "time_zone": device_info.get("time_zone", ""),
        "device_info": device_meta,
        "ip_address": device_info.get("ip_address", ""),
        "dim_window": dim_window,
        "volume": volume,
    }
    return cfg


def start_firestore_sync_if_available(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    global _client
    ble_suffix = device_info.get("device_ble_mac_suffix") or device_info.get(
        "ble_mac_suffix"
    )
    if not ble_suffix:
        return

    executable_path = os.environ.get(
        "DARTSNUT_FIRESTORE_SYNC_BINARY", "./dartsnut_firestore_sync"
    )
    if not os.path.isfile(executable_path) or not os.access(executable_path, os.X_OK):
        return

    _client = _SyncClient(SOCKET_PATH, reload_config)
    _client.start_server(on_config_updated)

    def _launch() -> None:
        args = [
            executable_path,
            f"--device-ble-mac={ble_suffix}",
            f"--socket-path={SOCKET_PATH}",
        ]
        try:
            subprocess = __import__("subprocess")
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return

    threading.Thread(target=_launch, daemon=True).start()

    initial_state = _build_initial_state(device_info)
    _client.send_state(initial_state, full=True)


def notify_device_state_update(partial_state: Dict[str, Any]) -> None:
    if not isinstance(partial_state, dict):
        return
    if not partial_state:
        return
    global _client
    if _client is None:
        return
    _client.send_state(partial_state, full=False)
