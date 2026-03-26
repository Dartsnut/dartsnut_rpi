"""
Optional Firestore sync bridge: spawns the Go Firestore bridge executable and
talks to it over a Unix socket. The bridge derives deviceId from local BLE,
sends initial state and partial updates; receives config pushes and applies them via the
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


SOCKET_PATH = "/tmp/dartsnut-firestore-sync.sock"
_DEFAULT_BRIDGE_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "firestore_bridge",
    "bridge",
)

_client: Optional["_SyncClient"] = None
_bridge_proc: Optional[subprocess.Popen] = None
_bridge_lock = threading.Lock()
_firestore_connected = False
_firestore_connected_lock = threading.Lock()
_connectivity_callback: Optional[Callable[[bool], None]] = None
_WRITE_DEBOUNCE_SECONDS = 0.20
_AV_WRITE_DEBOUNCE_SECONDS = 1.00
_ECHO_FINGERPRINT_TTL_SECONDS = 5.0


def _set_firestore_connected(connected: bool) -> None:
    global _firestore_connected
    notify = False
    with _firestore_connected_lock:
        prev = _firestore_connected
        _firestore_connected = bool(connected)
        notify = prev != _firestore_connected
    if notify:
        cb = _connectivity_callback
        if cb is not None:
            try:
                cb(_firestore_connected)
            except Exception:
                pass


def is_firestore_connected() -> bool:
    with _firestore_connected_lock:
        return _firestore_connected


def set_firestore_connectivity_callback(callback: Optional[Callable[[bool], None]]) -> None:
    global _connectivity_callback
    _connectivity_callback = callback


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
            if pages is None or not isinstance(pages, list):
                pages = []
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
        if games is None or not isinstance(games, list):
            games = []

    if games is None or not isinstance(games, list):
        games = []

    state = {
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
    # Avoid writing transient/invalid empty IP on startup.
    raw_ip = str(device_info.get("ip_address", "")).strip()
    if raw_ip and raw_ip != "0.0.0.0":
        state["ip_address"] = raw_ip
    return state


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


def _normalize_payload(value: Any) -> Any:
    """Convert payload values to a canonical structure for stable comparisons."""
    if isinstance(value, dict):
        return {str(k): _normalize_payload(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_payload(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_payload(v) for v in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(_normalize_payload(value), sort_keys=True, separators=(",", ":"))


def _normalize_config_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize incoming Firestore config payload to stable shapes expected by app.
    Keep list-based fields as lists (not null).
    """
    if not isinstance(cfg, dict):
        return {}
    normalized = dict(cfg)
    for key in ("pages", "games"):
        if key in normalized and normalized.get(key) is None:
            normalized[key] = []
    return normalized


def _coerce_pages_games_lists(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Never send JSON null or non-list values for pages/games to the Firestore bridge.
    Returns a shallow copy; leaves other keys unchanged.
    """
    out = dict(payload)
    for key in ("pages", "games"):
        if key not in out:
            continue
        v = out[key]
        if v is None or not isinstance(v, list):
            out[key] = []
    return out


def _looks_like_reset_confirmation_payload(payload: Dict[str, Any]) -> bool:
    """
    Detect the reset-confirmation payload shape so we don't suppress it as an echo.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("ip_address") != "":
        return False
    if "ssid" in payload and payload.get("ssid") != "":
        return False
    pages = payload.get("pages")
    if not isinstance(pages, list) or len(pages) != 0:
        return False
    games = payload.get("games")
    if not isinstance(games, list) or len(games) != 0:
        return False
    dim_window = payload.get("dim_window")
    if not isinstance(dim_window, dict):
        return False
    return bool(dim_window.get("dim_window_enabled")) is False


class _DeviceStateWriteCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_sent_per_key: Dict[str, str] = {}
        self._pending: Dict[str, Any] = {}
        self._flush_timer: Optional[threading.Timer] = None
        self._flush_in_progress = False
        self._recent_payload_fingerprints: Dict[str, float] = {}
        self._stats = {"sent": 0, "skipped": 0, "coalesced": 0}

    def queue_update(
        self,
        payload: Dict[str, Any],
        sender: Callable[[Dict[str, Any]], bool],
        debounce_seconds: float = _WRITE_DEBOUNCE_SECONDS,
    ) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False

        changed: Dict[str, Any] = {}
        with self._lock:
            for key, value in payload.items():
                key_name = str(key)
                new_canonical = _canonical_json(value)
                prev_canonical = self._last_sent_per_key.get(key_name)
                pending_canonical = (
                    _canonical_json(self._pending[key_name])
                    if key_name in self._pending
                    else None
                )
                if new_canonical == prev_canonical or new_canonical == pending_canonical:
                    continue
                changed[key_name] = value

            if not changed:
                self._stats["skipped"] += 1
                return False

            for key, value in changed.items():
                if key in self._pending:
                    self._stats["coalesced"] += 1
                self._pending[key] = value

            if self._flush_timer is None:
                self._flush_timer = threading.Timer(
                    debounce_seconds, self._flush_pending, args=(sender,)
                )
                self._flush_timer.daemon = True
                self._flush_timer.start()
        return True

    def _flush_pending(self, sender: Callable[[Dict[str, Any]], bool]) -> None:
        with self._lock:
            self._flush_timer = None
            if self._flush_in_progress or not self._pending:
                return
            self._flush_in_progress = True
            payload = dict(self._pending)
            self._pending.clear()

        sent_ok = False
        try:
            sent_ok = bool(sender(payload))
        finally:
            with self._lock:
                if sent_ok:
                    now = time.time()
                    for key, value in payload.items():
                        self._last_sent_per_key[key] = _canonical_json(value)
                    self._recent_payload_fingerprints[_canonical_json(payload)] = now
                    self._prune_recent_fingerprints_locked(now)
                    self._stats["sent"] += 1
                else:
                    # Preserve failed payload for retry after transient disconnects.
                    for key, value in payload.items():
                        self._pending[key] = value
                self._flush_in_progress = False
                if self._pending and self._flush_timer is None:
                    self._flush_timer = threading.Timer(
                        _WRITE_DEBOUNCE_SECONDS, self._flush_pending, args=(sender,)
                    )
                    self._flush_timer.daemon = True
                    self._flush_timer.start()

    def _prune_recent_fingerprints_locked(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        cutoff = now - _ECHO_FINGERPRINT_TTL_SECONDS
        stale = [
            fp
            for fp, ts in self._recent_payload_fingerprints.items()
            if ts < cutoff
        ]
        for fp in stale:
            self._recent_payload_fingerprints.pop(fp, None)

    def is_probable_echo_payload(self, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        fingerprint = _canonical_json(payload)
        with self._lock:
            self._prune_recent_fingerprints_locked()
            return fingerprint in self._recent_payload_fingerprints

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)


_WRITE_CACHE = _DeviceStateWriteCache()
_AV_WRITE_CACHE = _DeviceStateWriteCache()


def _merge_remote_and_local(remote: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge Firestore config with local JSON configuration using timestamps.

    - For device-level fields, compare device_updated_at (remote) vs device.json[updated_at].
    - For pages, compare pages_updated_at (remote) vs apps/conf.json[pages_updated_at].
    - If timestamps are missing or equal, prefer the remote config for backward compatibility.
    """
    # Start with a copy of remote config; we will selectively overwrite from local.
    merged: Dict[str, Any] = dict(remote or {})

    # Normalize legacy capitalized fields (e.g. "Brightness" from older clients)
    # into their canonical lowercase equivalents if the lowercase key is absent.
    if "Brightness" in merged and "brightness" not in merged:
        merged["brightness"] = merged["Brightness"]

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

    for key in ("pages", "games"):
        if key in merged and (merged[key] is None or not isinstance(merged[key], list)):
            merged[key] = []

    return merged


class _SyncClient:
    """Holds the socket server thread and connection to the Firestore bridge; sends state, receives config."""

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
                                cfg = _normalize_config_payload(payload)
                                # Listener echoes for recent self-originated writes are
                                # often no-ops locally and can trigger secondary writes.
                                if (
                                    kind == "config"
                                    and (
                                        _WRITE_CACHE.is_probable_echo_payload(cfg)
                                        or _AV_WRITE_CACHE.is_probable_echo_payload(cfg)
                                    )
                                    and not _looks_like_reset_confirmation_payload(cfg)
                                ):
                                    continue
                                if kind == "config_initial":
                                    try:
                                        cfg = _merge_remote_and_local(payload)
                                    except Exception:
                                        cfg = payload
                                    # Push the merged state back to Firestore so it
                                    # becomes the new source of truth.
                                    try:
                                        publish_cfg = dict(cfg)
                                        # Runtime game status is transient and should not
                                        # be replayed from config_initial merge results.
                                        publish_cfg.pop("games", None)
                                        notify_device_state_update(publish_cfg)
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
                            elif kind == "bridge_health" and isinstance(payload, dict):
                                state = str(payload.get("state", "")).strip().lower()
                                _set_firestore_connected(state == "connected")
            except Exception:
                pass
            finally:
                with self._conn_lock:
                    self._conn = None
                _set_firestore_connected(False)

        self._reader_thread = threading.Thread(target=_server, daemon=True)
        self._reader_thread.start()

    def send_state(self, payload: Dict[str, Any], *, full: bool = False) -> bool:
        payload = _coerce_pages_games_lists(dict(payload))
        kind = "initial_state" if full else "device_state"
        data = (json.dumps({"kind": kind, "payload": payload}) + "\n").encode("utf-8")
        with self._conn_lock:
            conn = self._conn
        if conn is None:
            return False
        try:
            conn.sendall(data)
            return True
        except Exception:
            return False


def start_firestore_sync_if_available(
    device_info: Dict[str, Any],
    reload_config: Callable[[], None],
    on_config_updated: Callable[[Dict[str, Any]], None],
) -> None:
    """
    Start Firestore sync by launching the Go bridge executable and talking over a Unix socket.

    - Bridge derives deviceId from local BLE MAC.
    - Python listens on SOCKET_PATH; spawns the bridge with --socket-path.
    - Sends initial_state (full device + pages config); receives config pushes and applies via
      on_config_updated + reload_config. Partial local updates go out via notify_device_state_update.
    """
    ensure_firestore_sync_running(device_info, reload_config, on_config_updated)


def notify_device_state_update(partial_state: Dict[str, Any]) -> None:
    """Push local field changes to Firestore via the bridge (merge into devices/{deviceId})."""
    if not isinstance(partial_state, dict) or not partial_state:
        return
    partial_state = _coerce_pages_games_lists(partial_state)
    global _client

    def _sender(payload: Dict[str, Any]) -> bool:
        with _bridge_lock:
            client = _client
        if client is None:
            return False
        try:
            return bool(client.send_state(payload, full=False))
        except Exception:
            return False

    av_keys = {"brightness", "Brightness", "volume"}
    av_payload = {k: v for k, v in partial_state.items() if str(k) in av_keys}
    other_payload = {k: v for k, v in partial_state.items() if str(k) not in av_keys}

    # Keep non-A/V updates responsive while reducing Firestore write bursts for
    # brightness/volume controls.
    if other_payload:
        _WRITE_CACHE.queue_update(
            other_payload,
            _sender,
            debounce_seconds=_WRITE_DEBOUNCE_SECONDS,
        )
    if av_payload:
        _AV_WRITE_CACHE.queue_update(
            av_payload,
            _sender,
            debounce_seconds=_AV_WRITE_DEBOUNCE_SECONDS,
        )


def is_firestore_bridge_active() -> bool:
    """Return True if the Firestore bridge client appears to be connected."""
    return _client is not None


def request_set_brightness(value: int) -> None:
    """Proxy a brightness change request to Firestore when the bridge is active."""
    try:
        v = int(value)
        # Write both canonical "brightness" and legacy "Brightness" for
        # compatibility with any existing dashboards that still read the
        # capitalized field.
        notify_device_state_update({"brightness": v, "Brightness": v})
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


def request_set_game_status(game_id: str, status: str) -> None:
    """
    Update Firestore games list so that:
    - All locally present games are reported with at least status \"ready\".
    - The specified game_id is forced to the given status (e.g. \"playing\").
    """
    if not game_id or not isinstance(game_id, str):
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
        notify_device_state_update({"games": games})
    except Exception as e:
        print(f"Firestore bridge: failed to request game status update: {e}")


def request_device_reset_state() -> None:
    """
    Request Firestore device-reset fields in a single payload.
    """
    try:
        notify_device_state_update(
            {
                "ip_address": "",
                "ssid": "",
                "pages": [],
                "games": [],
                "dim_window": {"dim_window_enabled": False},
            }
        )
    except Exception as e:
        print(f"Firestore bridge: failed to request device reset state: {e}")


def request_set_all_games_ready() -> None:
    """
    On service start, force all locally known games to status \"ready\" in
    Firestore so any stale \"playing\" or transitional states are reset.
    """
    try:
        from game_lifecycle import get_games_summary

        games = get_games_summary()
        for g in games:
            if isinstance(g, dict):
                g["status"] = "ready"
        if games:
            notify_device_state_update({"games": games})
    except Exception as e:
        print(f"Firestore bridge: failed to reset all game statuses to ready: {e}")


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

        executable_path = os.environ.get("DARTSNUT_FIRESTORE_BRIDGE", _DEFAULT_BRIDGE_BIN)
        if not os.path.isfile(executable_path):
            print(f"Firestore sync skipped: bridge binary not found at {executable_path}")
            return
        if not os.access(executable_path, os.X_OK):
            print(f"Firestore sync skipped: bridge binary not executable: {executable_path}")
            return

        socket_path = os.environ.get("DARTSNUT_FIRESTORE_SOCKET", SOCKET_PATH)
        print(f"Firestore sync starting (bridge: {executable_path})")
        initial_state = _build_initial_state(device_info)
        _client = _SyncClient(socket_path, reload_config, on_config_updated, initial_state)
        _set_firestore_connected(False)
        _client.start_server()

        def _launch() -> None:
            global _bridge_proc
            args = [
                executable_path,
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
    _set_firestore_connected(False)
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
    _set_firestore_connected(False)
