"""
Optional Firestore sync bridge: spawns the Bun Firestore bridge executable and
talks to it over a Unix socket. Python derives deviceId from BLE, sends initial
state and partial updates; receives config pushes and applies them via the
provided callbacks. If the module is missing or the executable is not available,
sync is skipped (WS + BLE only).
"""

import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

try:
    from bluezero import adapter as _ble_adapter  # type: ignore
except Exception:
    _ble_adapter = None


SOCKET_PATH = "/tmp/dartsnut-firestore-sync.sock"
_DEFAULT_BRIDGE_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "firestore_bridge",
    "dist",
    "dartsnut_firestore_bridge",
)

_client: Optional["_SyncClient"] = None
_bridge_proc: Optional[subprocess.Popen] = None
_bridge_lock = threading.Lock()


def _normalize_mac(adapter_address: str) -> str:
    """
    Normalize a MAC address to lowercase colon-separated form
    (e.g. 'AA:BB:CC:DD:EE:FF' -> 'aa:bb:cc:dd:ee:ff').
    """
    try:
        s = adapter_address.strip().lower()
        # If already colon-separated, just normalize case/whitespace
        if ":" in s:
            return s
        # Fallback: insert colons every two hex chars
        if len(s) == 12:
            return ":".join(s[i : i + 2] for i in range(0, 12, 2))
    except Exception:
        pass
    return ""


def _derive_device_id_from_ble() -> Optional[str]:
    """Derive device ID from full BLE MAC for Firestore device document id."""
    if _ble_adapter is None:
        return None
    try:
        adapters = list(_ble_adapter.Adapter.available())
        if not adapters:
            return None
        adapter_address = adapters[0].address
        normalized = _normalize_mac(adapter_address)
        return normalized or adapter_address
    except Exception:
        return None


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

    firmware = {
        "version": device_info.get("firmware_version", ""),
        "update": bool(device_info.get("firmware_update", False)),
    }

    pages = []
    pages_updated_at = ""
    try:
        apps_conf = os.path.join(os.getcwd(), "apps", "conf.json")
        if os.path.isfile(apps_conf):
            with open(apps_conf, "r") as f:
                conf = json.load(f)
            pages = conf.get("pages", [])
            pages_updated_at = conf.get("pages_updated_at", "") or ""
    except Exception:
        pass

    # Lightweight games list derived from local apps/*/conf.json, if available.
    games = []
    try:
        from game_lifecycle import get_games_summary

        games = get_games_summary()
    except Exception:
        games = device_info.get("games", [])

    return {
        "time_zone": device_info.get("time_zone", ""),
        "volume": volume,
        "ip_address": device_info.get("ip_address", ""),
        "brightness": brightness,
        "games": games,
        "dim_window": dim_window,
        "pages": pages,
        "device_updated_at": device_info.get("updated_at", "") or "",
        "pages_updated_at": pages_updated_at,
        "device_info": device_meta,
        "firmware": firmware,
    }


def _parse_iso_ts(value: Any) -> Optional[datetime]:
    """Best-effort parse of an ISO8601-ish timestamp string into a datetime."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value))
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1]
            return datetime.fromisoformat(s)
    except Exception:
        return None
    return None


def _merge_remote_and_local(remote: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge Firestore config with local JSON configuration using timestamps.

    - For device-level fields, compare device_updated_at (remote) vs device.json[updated_at].
    - For pages, compare pages_updated_at (remote) vs apps/conf.json[pages_updated_at].
    - If timestamps are missing or equal, prefer the remote config for backward compatibility.
    """
    # Start with a copy of remote config; we will selectively overwrite from local.
    merged: Dict[str, Any] = dict(remote or {})

    # Load local device.json
    local_device: Dict[str, Any] = {}
    try:
        device_path = os.path.join(os.getcwd(), "device.json")
        with open(device_path, "r") as f:
            local_device = json.load(f)
    except Exception:
        local_device = {}

    # Load local pages + timestamp from apps/conf.json
    local_pages_conf: Dict[str, Any] = {}
    try:
        apps_conf = os.path.join(os.getcwd(), "apps", "conf.json")
        if os.path.isfile(apps_conf):
            with open(apps_conf, "r") as f:
                local_pages_conf = json.load(f)
    except Exception:
        local_pages_conf = {}

    # Compute timestamps
    local_device_ts = _parse_iso_ts(local_device.get("updated_at"))
    remote_device_ts = _parse_iso_ts(
        remote.get("device_updated_at") or remote.get("updated_at")
    )

    local_pages_ts = _parse_iso_ts(local_pages_conf.get("pages_updated_at"))
    remote_pages_ts = _parse_iso_ts(remote.get("pages_updated_at"))

    # Rebuild canonical local device state using existing helper.
    local_initial = _build_initial_state(local_device or {})

    # Decide device source.
    use_local_device = False
    if local_device_ts and remote_device_ts:
        use_local_device = local_device_ts > remote_device_ts
    elif local_device_ts and not remote_device_ts:
        # Local has timestamp, remote doesn't: prefer local.
        use_local_device = True
    else:
        # Missing or equal timestamps: keep remote (backward compatible).
        use_local_device = False

    if use_local_device:
        for key in (
            "time_zone",
            "volume",
            "ip_address",
            "brightness",
            "games",
            "dim_window",
            "device_info",
            "firmware",
        ):
            if key in local_initial:
                merged[key] = local_initial[key]
        merged["device_updated_at"] = local_device.get("updated_at", "") or ""

    # Decide pages source.
    use_local_pages = False
    if local_pages_ts and remote_pages_ts:
        use_local_pages = local_pages_ts > remote_pages_ts
    elif local_pages_ts and not remote_pages_ts:
        use_local_pages = True
    else:
        use_local_pages = False

    if use_local_pages:
        merged["pages"] = local_pages_conf.get("pages", []) or []
        merged["pages_updated_at"] = (
            local_pages_conf.get("pages_updated_at", "") or ""
        )

    return merged


class _SyncClient:
    """Holds the socket server thread and connection to the Bun bridge; sends state, receives config."""

    def __init__(
        self,
        socket_path: str,
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        initial_state: Dict[str, Any],
    ) -> None:
        self._socket_path = socket_path
        self._reload_config = reload_config
        self._on_config_updated = on_config_updated
        self._initial_state = initial_state
        self._conn: Optional[socket.socket] = None
        self._conn_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None

    def start_server(self) -> None:
        """Run the Unix socket server in a daemon thread; accept one connection from the bridge."""

        def _server() -> None:
            try:
                if os.path.exists(self._socket_path):
                    try:
                        os.remove(self._socket_path)
                    except Exception:
                        pass
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                srv.bind(self._socket_path)
                srv.listen(1)
                conn, _ = srv.accept()
                with self._conn_lock:
                    self._conn = conn
                with conn:
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
                            if kind == "ready":
                                self.send_state(self._initial_state, full=True)
                            elif kind in ("config", "config_initial") and isinstance(
                                payload, dict
                            ):
                                cfg = payload
                                if kind == "config_initial":
                                    try:
                                        cfg = _merge_remote_and_local(payload)
                                    except Exception:
                                        cfg = payload
                                    # Push the merged state back to Firestore so it
                                    # becomes the new source of truth.
                                    try:
                                        notify_device_state_update(cfg)
                                    except Exception:
                                        pass
                                try:
                                    self._on_config_updated(cfg)
                                except Exception:
                                    pass
                                try:
                                    self._reload_config()
                                except Exception:
                                    pass
            except Exception:
                pass
            finally:
                with self._conn_lock:
                    self._conn = None

        self._reader_thread = threading.Thread(target=_server, daemon=True)
        self._reader_thread.start()

    def send_state(self, payload: Dict[str, Any], *, full: bool = False) -> None:
        kind = "initial_state" if full else "device_state"
        data = (json.dumps({"kind": kind, "payload": payload}) + "\n").encode("utf-8")
        with self._conn_lock:
            conn = self._conn
        if conn is None:
            return
        try:
            conn.sendall(data)
        except Exception:
            return


def start_firestore_sync_if_available(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    """
    Start Firestore sync by launching the Bun bridge executable and talking over a Unix socket.

    - Derives deviceId from BLE MAC suffix; passes it to the bridge.
    - Python listens on SOCKET_PATH; spawns the bridge with --device-id and --socket-path.
    - Sends initial_state (full device + pages config); receives config pushes and applies via
      on_config_updated + reload_config. Partial local updates go out via notify_device_state_update.
    """
    ensure_firestore_sync_running(device_info, reload_config, on_config_updated)


def notify_device_state_update(partial_state: Dict[str, Any]) -> None:
    """Push local field changes to Firestore via the bridge (merge into devices/{deviceId})."""
    if not isinstance(partial_state, dict) or not partial_state:
        return
    global _client
    if _client is None:
        return
    _client.send_state(partial_state, full=False)


def is_firestore_bridge_active() -> bool:
    """Return True if the Firestore bridge client appears to be connected."""
    return _client is not None


def request_set_brightness(value: int) -> None:
    """Proxy a brightness change request to Firestore when the bridge is active."""
    try:
        notify_device_state_update({"brightness": int(value)})
    except Exception as e:
        print(f"Firestore bridge: failed to request brightness update: {e}")


def request_set_volume(value: int) -> None:
    """Proxy a volume change request to Firestore when the bridge is active."""
    try:
        notify_device_state_update({"volume": int(value)})
    except Exception as e:
        print(f"Firestore bridge: failed to request volume update: {e}")


def request_set_dim_window(config: Dict[str, Any]) -> None:
    """
    Proxy a dim-window config change to Firestore when the bridge is active.
    Config keys should mirror _build_initial_state()['dim_window'].
    """
    if not isinstance(config, dict):
        return
    try:
        notify_device_state_update({"dim_window": dict(config)})
    except Exception as e:
        print(f"Firestore bridge: failed to request dim_window update: {e}")


def request_set_pages(pages: Any) -> None:
    """Proxy a pages config change to Firestore when the bridge is active."""
    if not isinstance(pages, list):
        return
    try:
        notify_device_state_update({"pages": pages})
    except Exception as e:
        print(f"Firestore bridge: failed to request pages update: {e}")


def request_set_device_name(name: str) -> None:
    """Proxy a device name change to Firestore when the bridge is active."""
    try:
        notify_device_state_update({"device_info": {"name": name}})
    except Exception as e:
        print(f"Firestore bridge: failed to request device name update: {e}")


def ensure_firestore_sync_running(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    """
    Idempotently start Firestore sync if prerequisites are met and the bridge
    is not already believed to be running.

    This is safe to call multiple times (e.g. from startup and from a WiFi
    connectivity monitor) and will be a no-op if a bridge process is already
    tracked as running.
    """
    global _client, _bridge_proc

    with _bridge_lock:
        if _bridge_proc is not None and _bridge_proc.poll() is None:
            return

        device_id = _derive_device_id_from_ble()
        if not device_id:
            print("Firestore sync skipped: could not derive device ID from BLE (bluezero or no adapter)")
            return

        executable_path = os.environ.get("DARTSNUT_FIRESTORE_BRIDGE", _DEFAULT_BRIDGE_BIN)
        if not os.path.isfile(executable_path):
            print(f"Firestore sync skipped: bridge binary not found at {executable_path}")
            return
        if not os.access(executable_path, os.X_OK):
            print(f"Firestore sync skipped: bridge binary not executable: {executable_path}")
            return

        socket_path = os.environ.get("DARTSNUT_FIRESTORE_SOCKET", SOCKET_PATH)
        print(f"Firestore sync starting for device id {device_id} (bridge: {executable_path})")
        initial_state = _build_initial_state(device_info)
        _client = _SyncClient(socket_path, reload_config, on_config_updated, initial_state)
        _client.start_server()

        def _launch() -> None:
            global _bridge_proc
            args = [
                executable_path,
                f"--device-id={device_id}",
                f"--socket-path={socket_path}",
            ]
            try:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=None,
                    env=os.environ.copy(),
                )
                with _bridge_lock:
                    _bridge_proc = proc
            except Exception as e:
                print(f"Firestore sync: failed to launch bridge: {e}")

        threading.Thread(target=_launch, daemon=True).start()


def restart_firestore_sync(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    """
    Restart Firestore sync bridge:
    - Terminates any existing bridge process.
    - Clears the current client.
    - Starts a fresh bridge and socket server.

    Intended to be called from a long-running WiFi monitor when connectivity
    transitions from offline to online.
    """
    global _client, _bridge_proc

    with _bridge_lock:
        proc = _bridge_proc
        _bridge_proc = None

    if proc is not None:
        try:
            proc.terminate()
            # Give the process a brief window to exit cleanly before forcing.
            for _ in range(10):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()
        except Exception as e:
            print(f"Firestore sync: error while stopping existing bridge: {e}")

    _client = None
    ensure_firestore_sync_running(device_info, reload_config, on_config_updated)


def stop_firestore_sync() -> None:
    """
    Stop Firestore sync bridge if it is running. This does not prevent future
    calls to ensure_firestore_sync_running/restart_firestore_sync from starting
    it again.
    """
    global _client, _bridge_proc

    with _bridge_lock:
        proc = _bridge_proc
        _bridge_proc = None

    if proc is not None:
        try:
            proc.terminate()
            for _ in range(10):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()
        except Exception as e:
            print(f"Firestore sync: error while stopping bridge: {e}")

    _client = None
