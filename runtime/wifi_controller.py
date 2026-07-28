"""Threaded Wi-Fi scan/connect/forget state for the on-device Settings UI."""
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
        connect_saved_network: Callable[[str], Any] | None = None,
        forget_network: Callable[[str], Any] | None = None,
    ) -> None:
        self._scan_networks = scan_networks
        self._connect_network = connect_network
        self._connect_saved_network = connect_saved_network
        self._forget_network = forget_network
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "is_scan": False,
            "networks": [],
            "connected_network": None,
            "remembered_networks": [],
            "scan_error": "",
            "connection_status": "idle",
            "connection_ssid": "",
            "connection_profile": "",
            "connection_error": "",
            "forget_status": "idle",
            "forget_profile": "",
            "forget_ssid": "",
            "forget_error": "",
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
            scanned = [dict(network) for network in self._scan_networks()]
            access_by_ssid = {
                str(network.get("ssid") or ""): network
                for network in scanned
                if not network.get("remembered") and network.get("ssid")
            }
            remembered = []
            for profile in scanned:
                if not profile.get("remembered"):
                    continue
                merged = dict(access_by_ssid.get(str(profile.get("ssid") or ""), {}))
                merged.update(profile)
                remembered.append(merged)
            remembered_ssids = {str(item.get("ssid") or "") for item in remembered}

            connected_network = next(
                (dict(network) for network in scanned if network.get("connected")),
                None,
            )
            connected_ssid = str((connected_network or {}).get("ssid") or "")
            if connected_network:
                saved = next(
                    (item for item in remembered if item.get("ssid") == connected_ssid),
                    None,
                )
                if saved:
                    connected_network.update(saved)
                    connected_network["connected"] = True
                    remembered = [
                        item for item in remembered if item.get("ssid") != connected_ssid
                    ]

            networks = [
                dict(network)
                for network in scanned
                if not network.get("remembered")
                and str(network.get("ssid") or "") != connected_ssid
                and str(network.get("ssid") or "") not in remembered_ssids
            ]
            error = ""
        except Exception:
            networks = None
            remembered = None
            connected_network = None
            error = "Unable to scan"
        with self._lock:
            if networks is not None:
                self._state["networks"] = networks
                self._state["remembered_networks"] = remembered
                self._state["connected_network"] = connected_network
            self._state["scan_error"] = error
            self._state["is_scan"] = False

    def start_connect_if_requested(
        self, ssid: str, password: str, secured: bool
    ) -> bool:
        normalized_ssid = str(ssid or "")
        with self._lock:
            if self._state["connection_status"] == "connecting":
                return False
            self._state["connection_status"] = "connecting"
            self._state["connection_ssid"] = normalized_ssid
            self._state["connection_profile"] = ""
            self._state["connection_error"] = ""
        threading.Thread(
            target=self._connect_worker,
            args=(normalized_ssid, str(password or ""), bool(secured)),
            daemon=True,
        ).start()
        return True

    def start_connect_saved_if_requested(self, profile: str, ssid: str) -> bool:
        normalized_profile = str(profile or "")
        normalized_ssid = str(ssid or normalized_profile)
        if not normalized_profile or self._connect_saved_network is None:
            return False
        with self._lock:
            if self._state["connection_status"] == "connecting":
                return False
            self._state["connection_status"] = "connecting"
            self._state["connection_ssid"] = normalized_ssid
            self._state["connection_profile"] = normalized_profile
            self._state["connection_error"] = ""
        threading.Thread(
            target=self._connect_saved_worker,
            args=(normalized_profile,),
            daemon=True,
        ).start()
        return True

    def _connect_worker(self, ssid: str, password: str, secured: bool) -> None:
        try:
            self._connect_network(ssid, password, secured)
            status, error = "success", ""
        except Exception as exc:
            status, error = "error", _connection_error_message(exc)
        with self._lock:
            self._state["connection_status"] = status
            self._state["connection_error"] = error

    def _connect_saved_worker(self, profile: str) -> None:
        try:
            self._connect_saved_network(profile)
            status, error = "success", ""
        except Exception as exc:
            status, error = "error", _connection_error_message(exc)
        with self._lock:
            self._state["connection_status"] = status
            self._state["connection_error"] = error

    def clear_connection_result(self) -> None:
        with self._lock:
            if self._state["connection_status"] == "connecting":
                return
            self._state["connection_status"] = "idle"
            self._state["connection_ssid"] = ""
            self._state["connection_profile"] = ""
            self._state["connection_error"] = ""

    def start_forget_if_requested(self, profile: str, ssid: str) -> bool:
        normalized_profile = str(profile or "")
        normalized_ssid = str(ssid or normalized_profile)
        if not normalized_profile or self._forget_network is None:
            return False
        with self._lock:
            if self._state["forget_status"] == "forgetting":
                return False
            self._state["forget_status"] = "forgetting"
            self._state["forget_profile"] = normalized_profile
            self._state["forget_ssid"] = normalized_ssid
            self._state["forget_error"] = ""
        threading.Thread(
            target=self._forget_worker, args=(normalized_profile,), daemon=True
        ).start()
        return True

    def _forget_worker(self, profile: str) -> None:
        try:
            self._forget_network(profile)
            status, error = "success", ""
        except Exception as exc:
            status, error = "error", "Unable to forget"
        with self._lock:
            self._state["forget_status"] = status
            self._state["forget_error"] = error

    def clear_forget_result(self) -> None:
        with self._lock:
            if self._state["forget_status"] == "forgetting":
                return
            self._state["forget_status"] = "idle"
            self._state["forget_profile"] = ""
            self._state["forget_ssid"] = ""
            self._state["forget_error"] = ""
