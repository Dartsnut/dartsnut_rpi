"""Supabase sync bridge client (Python side) over Unix socket."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

SOCKET_PATH = "/tmp/dartsnut-supabase-sync.sock"
_DEFAULT_BRIDGE_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "bridge",
)
_DOTENV_CANDIDATES = (".env", ".env.local")

_client: Optional["_SyncClient"] = None
_bridge_proc: Optional[subprocess.Popen] = None
_bridge_lock = threading.Lock()
_connected = False
_connected_lock = threading.Lock()
_connectivity_callback: Optional[Callable[[bool], None]] = None
_remote_game_ids: Optional[set[str]] = None
_remote_game_ids_lock = threading.Lock()


def _set_connected(connected: bool) -> None:
    global _connected
    notify = False
    with _connected_lock:
        prev = _connected
        _connected = bool(connected)
        notify = prev != _connected
    if notify and _connectivity_callback is not None:
        try:
            _connectivity_callback(_connected)
        except Exception:
            pass


def is_supabase_connected() -> bool:
    with _connected_lock:
        return _connected


def set_supabase_connectivity_callback(
    callback: Optional[Callable[[bool], None]],
) -> None:
    global _connectivity_callback
    _connectivity_callback = callback


def _parse_iso_ts(value: Any) -> Optional[datetime]:
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


def _normalize_config_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    normalized = dict(cfg)
    for key in ("pages", "games"):
        if key in normalized and normalized.get(key) is None:
            normalized[key] = []
    return normalized


def _coerce_pages_games_lists(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    for key in ("pages", "games"):
        if key not in out:
            continue
        v = out[key]
        if v is None or not isinstance(v, list):
            out[key] = []
    return out


def _remember_remote_game_ids(config: Dict[str, Any]) -> None:
    if not isinstance(config, dict) or "games" not in config:
        return
    games = config.get("games")
    next_ids: set[str] = set()
    if isinstance(games, list):
        next_ids = {
            str(g.get("id"))
            for g in games
            if isinstance(g, dict) and g.get("id") is not None
        }
    with _remote_game_ids_lock:
        global _remote_game_ids
        _remote_game_ids = next_ids


def _current_remote_game_ids() -> Optional[set[str]]:
    with _remote_game_ids_lock:
        if _remote_game_ids is None:
            return None
        return set(_remote_game_ids)


def _normalize_device_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = raw.split(":")
    if len(parts) == 6 and all(
        len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts
    ):
        return ":".join(p.upper() for p in parts)
    return raw


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

    resolved_device_id = _normalize_device_id(
        device_info.get("device_id")
        or device_info.get("id")
        or device_info.get("ble_mac")
        or device_info.get("mac_address")
    )
    dim_window = {
        "dim_window_enabled": bool(device_info.get("dim_window_enabled", False)),
        "dim_window_start": device_info.get("dim_window_start", ""),
        "dim_window_end": device_info.get("dim_window_end", ""),
        "dim_level": device_info.get("dim_level", 0),
        "dim_restore_seconds": device_info.get("dim_restore_seconds", 0),
    }
    device_meta = {
        "id": resolved_device_id,
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
            with open(apps_conf, "r", encoding="utf-8") as f:
                conf = json.load(f)
            pages = conf.get("pages", [])
            if pages is None or not isinstance(pages, list):
                pages = []
            pages_updated_at = conf.get("pages_updated_at", "") or ""
    except Exception:
        pass

    games = []
    try:
        from game_lifecycle import get_games_summary

        games = get_games_summary()
    except Exception:
        games = device_info.get("games", [])
    if games is None or not isinstance(games, list):
        games = []

    state: Dict[str, Any] = {
        "device_id": resolved_device_id,
        "time_zone": device_info.get("time_zone", ""),
        "volume": volume,
        "brightness": brightness,
        "games": games,
        "dim_window": dim_window,
        "pages": pages,
        "device_updated_at": device_info.get("updated_at", "") or "",
        "pages_updated_at": pages_updated_at,
        "device_info": device_meta,
        "firmware": firmware,
    }
    raw_ip = str(device_info.get("ip_address", "")).strip()
    if raw_ip and raw_ip != "0.0.0.0":
        state["ip_address"] = raw_ip
    return state


def _merge_remote_and_local(remote: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(remote or {})

    local_device: Dict[str, Any] = {}
    try:
        device_path = os.path.join(os.getcwd(), "device.json")
        with open(device_path, "r", encoding="utf-8") as f:
            local_device = json.load(f)
    except Exception:
        local_device = {}

    local_pages_conf: Dict[str, Any] = {}
    try:
        apps_conf = os.path.join(os.getcwd(), "apps", "conf.json")
        if os.path.isfile(apps_conf):
            with open(apps_conf, "r", encoding="utf-8") as f:
                local_pages_conf = json.load(f)
    except Exception:
        local_pages_conf = {}

    local_device_ts = _parse_iso_ts(local_device.get("updated_at"))
    remote_device_ts = _parse_iso_ts(
        remote.get("device_updated_at") or remote.get("updated_at")
    )
    local_pages_ts = _parse_iso_ts(local_pages_conf.get("pages_updated_at"))
    remote_pages_ts = _parse_iso_ts(remote.get("pages_updated_at"))

    local_initial = _build_initial_state(local_device or {})
    use_local_device = False
    if local_device_ts and remote_device_ts:
        use_local_device = local_device_ts > remote_device_ts
    elif local_device_ts and not remote_device_ts:
        use_local_device = True

    if use_local_device:
        for key in (
            "device_id",
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

    use_local_pages = False
    if local_pages_ts and remote_pages_ts:
        use_local_pages = local_pages_ts > remote_pages_ts
    elif local_pages_ts and not remote_pages_ts:
        use_local_pages = True
    if use_local_pages:
        merged["pages"] = local_pages_conf.get("pages", []) or []
        merged["pages_updated_at"] = local_pages_conf.get("pages_updated_at", "") or ""

    for key in ("pages", "games"):
        if key in merged and (merged[key] is None or not isinstance(merged[key], list)):
            merged[key] = []
    if "device_id" not in merged or not str(merged.get("device_id", "")).strip():
        merged["device_id"] = str(local_initial.get("device_id", "") or "").strip()
    return merged


class _SyncClient:
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

    def start_server(self) -> None:
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
                                _remember_remote_game_ids(payload)
                                cfg = _normalize_config_payload(payload)
                                if kind == "config_initial":
                                    try:
                                        cfg = _merge_remote_and_local(payload)
                                    except Exception:
                                        cfg = _normalize_config_payload(payload)
                                try:
                                    self._on_config_updated(cfg)
                                except Exception:
                                    pass
                                try:
                                    self._reload_config()
                                except Exception:
                                    pass
                            elif kind == "bridge_health" and isinstance(payload, dict):
                                _set_connected(
                                    str(payload.get("state", "")).lower() == "connected"
                                )
            except Exception:
                pass
            finally:
                with self._conn_lock:
                    self._conn = None
                _set_connected(False)

        threading.Thread(target=_server, daemon=True).start()

    def send_state(self, payload: Dict[str, Any], *, full: bool = False) -> bool:
        payload = _coerce_pages_games_lists(dict(payload))
        kind = "initial_state" if full else "device_state"
        line = (json.dumps({"kind": kind, "payload": payload}) + "\n").encode("utf-8")
        with self._conn_lock:
            conn = self._conn
        if conn is None:
            return False
        try:
            conn.sendall(line)
            return True
        except Exception:
            return False


def _load_supabase_env_overrides() -> Dict[str, str]:
    """
    Load simple KEY=VALUE pairs from project dotenv files.

    Existing process env always wins over file values.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    overrides: Dict[str, str] = {}
    for name in _DOTENV_CANDIDATES:
        path = os.path.join(base_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ and key not in overrides:
                        overrides[key] = value
        except Exception:
            continue
    return overrides


def start_supabase_sync_if_available(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    ensure_supabase_sync_running(device_info, reload_config, on_config_updated)


def publish_device_state_update(partial_state: Dict[str, Any]) -> None:
    if not isinstance(partial_state, dict) or not partial_state:
        return
    global _client
    with _bridge_lock:
        client = _client
    if client is None:
        return
    client.send_state(partial_state, full=False)


def is_supabase_bridge_active() -> bool:
    return _client is not None


def request_set_game_status(game_id: str, status: str) -> None:
    if not game_id:
        return
    try:
        from game_lifecycle import get_games_summary

        games = get_games_summary()
        remote_ids = _current_remote_game_ids()
        if remote_ids is not None:
            games = [
                g
                for g in games
                if isinstance(g, dict) and str(g.get("id") or "") in remote_ids
            ]
        found = False
        for g in games:
            if isinstance(g, dict) and g.get("id") == game_id:
                g["status"] = status
                found = True
        if not found:
            games.append({"id": game_id, "version": "", "status": status})
        publish_device_state_update({"games": games})
    except Exception:
        pass


def request_set_all_games_ready() -> None:
    try:
        from game_lifecycle import get_games_summary

        games = get_games_summary()
        remote_ids = _current_remote_game_ids()
        if remote_ids is not None:
            games = [
                g
                for g in games
                if isinstance(g, dict) and str(g.get("id") or "") in remote_ids
            ]
        for g in games:
            if isinstance(g, dict):
                g["status"] = "ready"
        if games:
            publish_device_state_update({"games": games})
    except Exception:
        pass


def request_device_reset_state() -> None:
    publish_device_state_update(
        {
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    )


def ensure_supabase_sync_running(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    global _client, _bridge_proc, _remote_game_ids
    with _bridge_lock:
        with _remote_game_ids_lock:
            _remote_game_ids = None
        if _bridge_proc is not None and _bridge_proc.poll() is None:
            return
        launch_env = os.environ.copy()
        launch_env.update(_load_supabase_env_overrides())
        executable_path = os.environ.get("DARTSNUT_SUPABASE_BRIDGE", _DEFAULT_BRIDGE_BIN)
        if not os.path.isfile(executable_path) or not os.access(executable_path, os.X_OK):
            print(f"Supabase sync skipped: bridge binary unavailable at {executable_path}")
            return
        socket_path = os.environ.get("DARTSNUT_SUPABASE_SOCKET", SOCKET_PATH)
        _client = _SyncClient(
            socket_path,
            reload_config,
            on_config_updated,
            _build_initial_state(device_info),
        )
        _set_connected(False)
        _client.start_server()

        def _launch() -> None:
            global _bridge_proc
            try:
                proc = subprocess.Popen(
                    [executable_path, f"--socket-path={socket_path}"],
                    stdout=subprocess.DEVNULL,
                    stderr=None,
                    env=launch_env,
                )
                with _bridge_lock:
                    _bridge_proc = proc
            except Exception as e:
                print(f"Supabase sync: failed to launch bridge: {e}")

        threading.Thread(target=_launch, daemon=True).start()


def restart_supabase_sync(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    stop_supabase_sync()
    time.sleep(0.05)
    ensure_supabase_sync_running(device_info, reload_config, on_config_updated)


def stop_supabase_sync() -> None:
    global _client, _bridge_proc, _remote_game_ids
    with _bridge_lock:
        proc = _bridge_proc
        _bridge_proc = None
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
    _client = None
    with _remote_game_ids_lock:
        _remote_game_ids = None
    _set_connected(False)
