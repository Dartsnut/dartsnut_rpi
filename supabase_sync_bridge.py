"""Supabase sync bridge client (Python side) over Unix socket."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional

SOCKET_PATH = "/tmp/dartsnut-supabase-sync.sock"
_DEFAULT_BRIDGE_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "supabase_bridge",
    "bridge",
)

_client: Optional["_SyncClient"] = None
_bridge_proc: Optional[subprocess.Popen] = None
_bridge_lock = threading.Lock()
_connected = False
_connected_lock = threading.Lock()
_connectivity_callback: Optional[Callable[[bool], None]] = None


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
                            elif kind in ("config", "config_initial") and isinstance(payload, dict):
                                try:
                                    self._on_config_updated(payload)
                                except Exception:
                                    pass
                                try:
                                    self._reload_config()
                                except Exception:
                                    pass
                            elif kind == "bridge_health" and isinstance(payload, dict):
                                _set_connected(str(payload.get("state", "")).lower() == "connected")
            except Exception:
                pass
            finally:
                with self._conn_lock:
                    self._conn = None
                _set_connected(False)

        threading.Thread(target=_server, daemon=True).start()

    def send_state(self, payload: Dict[str, Any], *, full: bool = False) -> bool:
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


def _build_initial_state(device_info: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(device_info or {})
    try:
        from game_lifecycle import get_games_summary

        state["games"] = get_games_summary()
    except Exception:
        state.setdefault("games", [])
    return state


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
    global _client, _bridge_proc
    with _bridge_lock:
        if _bridge_proc is not None and _bridge_proc.poll() is None:
            return
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
                    env=os.environ.copy(),
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
    global _client, _bridge_proc
    with _bridge_lock:
        proc = _bridge_proc
        _bridge_proc = None
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
    _client = None
    _set_connected(False)
