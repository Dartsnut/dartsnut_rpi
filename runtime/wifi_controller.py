"""Threaded Wi-Fi scan/connect state for the on-device Settings UI."""
from __future__ import annotations

import copy
import threading
from typing import Any, Callable


def _connection_error_message(exc: Exception) -> str:
    text = " ".join(
        str(value or "")
        for value in (getattr(exc, "stderr", ""), getattr(exc, "stdout", ""), exc)
    ).lower()
    password_signals = (
        "secrets were required",
        "invalid password",
        "wrong password",
        "no secrets",
        "802-11-wireless-security.psk",
    )
    if any(signal in text for signal in password_signals):
        return "Incorrect password"
    if isinstance(exc, ValueError):
        return str(exc) or "Invalid network"
    return "Unable to connect"


class WifiController:
    """Run NetworkManager operations outside the display/input loop."""

    def __init__(
        self,
        *,
        scan_networks: Callable[[], list[dict[str, Any]]],
        connect_network: Callable[[str, str, bool], Any],
    ) -> None:
        self._scan_networks = scan_networks
        self._connect_network = connect_network
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "is_scan": False,
            "networks": [],
            "connected_network": None,
            "scan_error": "",
            "connection_status": "idle",
            "connection_ssid": "",
            "connection_error": "",
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def start_scan_if_requested(self) -> bool:
        with self._lock:
            if self._state["is_scan"]:
                return False
            self._state["is_scan"] = True
            self._state["scan_error"] = ""
        threading.Thread(target=self._scan_worker, daemon=True).start()
        return True

    def _scan_worker(self) -> None:
        try:
            scanned = list(self._scan_networks())
            connected_network = next(
                (dict(network) for network in scanned if network.get("connected")),
                None,
            )
            connected_ssid = str((connected_network or {}).get("ssid") or "")
            networks = [
                dict(network)
                for network in scanned
                if not connected_ssid or str(network.get("ssid") or "") != connected_ssid
            ]
            error = ""
        except Exception:
            networks = None
            connected_network = None
            error = "Unable to scan"
        with self._lock:
            if networks is not None:
                self._state["networks"] = networks
                self._state["connected_network"] = connected_network
            self._state["scan_error"] = error
            self._state["is_scan"] = False

    def start_connect_if_requested(
        self,
        ssid: str,
        password: str,
        secured: bool,
    ) -> bool:
        normalized_ssid = str(ssid or "")
        with self._lock:
            if self._state["connection_status"] == "connecting":
                return False
            self._state["connection_status"] = "connecting"
            self._state["connection_ssid"] = normalized_ssid
            self._state["connection_error"] = ""
        threading.Thread(
            target=self._connect_worker,
            args=(normalized_ssid, str(password or ""), bool(secured)),
            daemon=True,
        ).start()
        return True

    def _connect_worker(self, ssid: str, password: str, secured: bool) -> None:
        try:
            self._connect_network(ssid, password, secured)
            status = "success"
            error = ""
        except Exception as exc:
            status = "error"
            error = _connection_error_message(exc)
        with self._lock:
            self._state["connection_status"] = status
            self._state["connection_error"] = error

    def clear_connection_result(self) -> None:
        with self._lock:
            if self._state["connection_status"] == "connecting":
                return
            self._state["connection_status"] = "idle"
            self._state["connection_ssid"] = ""
            self._state["connection_error"] = ""
