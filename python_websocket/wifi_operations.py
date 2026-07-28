"""NetworkManager-backed Wi-Fi scan, connect, and saved-profile operations."""
from __future__ import annotations

import subprocess
from typing import Any


def _split_nmcli_terse_line(line: str) -> list[str]:
    """Split nmcli terse output while preserving escaped colons/backslashes."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in str(line):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def parse_wifi_scan_output(output: str) -> list[dict[str, Any]]:
    """Normalize, deduplicate, and strongest-first sort nmcli scan output."""
    strongest_by_ssid: dict[str, dict[str, Any]] = {}
    for line in str(output or "").splitlines():
        parts = _split_nmcli_terse_line(line)
        if len(parts) < 3:
            continue
        connected = parts[0].strip().lower() in {"*", "yes"}
        ssid = parts[1]
        if not ssid:
            continue
        try:
            signal = int(parts[2])
        except (TypeError, ValueError):
            continue
        security = ":".join(parts[3:]).strip() if len(parts) > 3 else ""
        network = {
            "ssid": ssid,
            "rssi": signal,
            "security": security,
            "secured": bool(security and security != "--"),
            "connected": connected,
        }
        existing = strongest_by_ssid.get(ssid)
        if existing is None or signal > int(existing.get("rssi", -1)):
            if existing is not None and existing.get("connected"):
                network["connected"] = True
            strongest_by_ssid[ssid] = network
        elif connected:
            existing["connected"] = True
    return sorted(
        strongest_by_ssid.values(),
        key=lambda item: (-int(item["rssi"]), str(item["ssid"]).casefold()),
    )


def parse_saved_wifi_profiles(output: str) -> list[dict[str, Any]]:
    """Parse saved wireless profiles, deduplicated by exact SSID."""
    profiles_by_ssid: dict[str, dict[str, Any]] = {}
    for line in str(output or "").splitlines():
        parts = _split_nmcli_terse_line(line)
        if len(parts) < 2 or parts[1].strip() != "802-11-wireless":
            continue
        profile = parts[0]
        ssid = parts[2] if len(parts) > 2 and parts[2] else profile
        if not profile or not ssid or ssid in profiles_by_ssid:
            continue
        profiles_by_ssid[ssid] = {
            "ssid": ssid,
            "profile": profile,
            "remembered": True,
            "connected": False,
        }
    return sorted(
        profiles_by_ssid.values(), key=lambda item: str(item["ssid"]).casefold()
    )


def scan_wifi_networks() -> list[dict[str, Any]]:
    """Rescan and return access points plus saved NetworkManager profiles."""
    subprocess.run(
        ["nmcli", "dev", "wifi", "rescan"],
        capture_output=True,
        text=True,
        check=True,
    )
    scan_result = subprocess.run(
        [
            "nmcli",
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "dev",
            "wifi",
            "list",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    profiles_result = subprocess.run(
        [
            "nmcli",
            "-t",
            "-f",
            "NAME,TYPE,802-11-wireless.ssid",
            "connection",
            "show",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_wifi_scan_output(scan_result.stdout) + parse_saved_wifi_profiles(
        profiles_result.stdout
    )


def connect_saved_wifi(profile: str) -> dict[str, str]:
    """Activate a saved NetworkManager Wi-Fi profile without a password."""
    normalized_profile = str(profile or "")
    if not normalized_profile:
        raise ValueError("WiFi profile name is missing")
    result = subprocess.run(
        ["nmcli", "connection", "up", "id", normalized_profile],
        capture_output=True,
        text=True,
        check=True,
    )
    return {"profile": normalized_profile, "message": (result.stdout or "").strip()}


def forget_saved_wifi(profile: str) -> dict[str, str]:
    """Delete one saved NetworkManager Wi-Fi profile."""
    normalized_profile = str(profile or "")
    if not normalized_profile:
        raise ValueError("WiFi profile name is missing")
    result = subprocess.run(
        ["nmcli", "connection", "delete", "id", normalized_profile],
        capture_output=True,
        text=True,
        check=True,
    )
    return {"profile": normalized_profile, "message": (result.stdout or "").strip()}


def connect_wifi_network(ssid: str, password: str, secured: bool) -> dict[str, str]:
    """Connect to an available network and return a small success payload."""
    normalized_ssid = str(ssid or "")
    if not normalized_ssid:
        raise ValueError("WiFi network name is missing")
    command = ["nmcli", "dev", "wifi", "connect", normalized_ssid]
    if secured:
        command.extend(["password", str(password or "")])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )
    return {"ssid": normalized_ssid, "message": (result.stdout or "").strip()}
