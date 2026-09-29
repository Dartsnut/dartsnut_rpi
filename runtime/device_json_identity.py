"""Canonical local device identity reconciliation helpers."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Mapping, Optional

from runtime.bluetooth_identity import (
    get_bluetooth_adapter_address,
    normalize_bluetooth_adapter_address,
)

_log = logging.getLogger(__name__)

BOOT_DEVICE_JSON = "/boot/device.json"
BOOT_SERIAL_FILE = "/boot/serial.txt"


def normalize_device_id(value: Any) -> Optional[str]:
    """Return a canonical BLE MAC, or ``None`` for a non-MAC value."""
    return normalize_bluetooth_adapter_address(value)


def load_boot_serial() -> Optional[str]:
    """Read the authoritative serial, if the serial file exists and is usable."""
    try:
        if not os.path.isfile(BOOT_SERIAL_FILE):
            return None
        with open(BOOT_SERIAL_FILE, "r", encoding="utf-8") as f:
            serial = f.read().strip()
        return serial or None
    except Exception as e:
        _log.debug("Could not read boot serial file: %s", e)
        return None


def resolve_ble_mac(device_info: Mapping[str, Any] | None = None) -> Optional[str]:
    """Resolve the hardware BLE MAC without trusting a possibly stale ``id``."""
    try:
        hardware_address = normalize_device_id(get_bluetooth_adapter_address())
    except Exception:
        hardware_address = None
    if hardware_address:
        return hardware_address

    info = device_info or {}
    for key in ("ble_mac", "mac_address"):
        address = normalize_device_id(info.get(key))
        if address:
            return address
    return None


def canonicalize_identity(
    data: Mapping[str, Any] | None,
    *,
    ble_mac: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ``data`` with the locally authoritative identity fields applied."""
    out = dict(data or {})
    resolved_mac = normalize_device_id(ble_mac) if ble_mac else resolve_ble_mac(out)
    if resolved_mac:
        out["id"] = resolved_mac
    serial = load_boot_serial()
    if serial is not None:
        out["serial"] = serial
    return out


def _read_local_device_json(path: str) -> tuple[bool, Dict[str, Any]]:
    """Return whether ``path`` is safe to repair and its parsed contents."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return True, {}
    except Exception as e:
        _log.warning("could not read local device.json %s: %s", path, e)
        return False, {}
    if not isinstance(raw, dict):
        _log.warning("local device.json is not an object: %s", path)
        return False, {}
    return True, dict(raw)


def reconcile_device_json_identity(
    path: Optional[str] = None,
    *,
    device_info: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Repair local identity without ever writing the boot serial source."""
    resolved_path = path or os.path.join(os.getcwd(), "device.json")
    if os.path.realpath(resolved_path) == os.path.realpath(BOOT_SERIAL_FILE):
        _log.error("refusing to write boot serial source: %s", resolved_path)
        return {}

    can_repair, existing = _read_local_device_json(resolved_path)
    if not can_repair:
        return {}

    source = {**existing, **dict(device_info or {})}
    ble_mac = resolve_ble_mac(source)
    canonical = dict(existing)
    if ble_mac:
        canonical["id"] = ble_mac
    serial = load_boot_serial()
    if serial is not None:
        canonical["serial"] = serial
    if canonical != existing:
        try:
            with open(resolved_path, "w", encoding="utf-8") as f:
                json.dump(canonical, f)
        except Exception as e:
            _log.error("device.json identity repair write failed: %s", e)
            raise
    return canonical


def remote_identity_patch(
    remote_info: Mapping[str, Any] | None,
    *,
    ble_mac: Optional[str] = None,
) -> Dict[str, str]:
    """Return remote identity fields that differ from local authority.

    Supabase's stored ``device_info`` schema names the serial field ``sn``.
    """
    info = remote_info or {}
    patch: Dict[str, str] = {}
    resolved_mac = normalize_device_id(ble_mac) if ble_mac else resolve_ble_mac(info)
    if resolved_mac and str(info.get("id") or "").strip() != resolved_mac:
        patch["id"] = resolved_mac

    serial = load_boot_serial()
    if serial is not None:
        remote_serial = str(info.get("sn") or info.get("serial") or "").strip()
        if remote_serial != serial:
            patch["sn"] = serial
    return patch

def reconcile_local_identity_patch(
    reference_info: Mapping[str, Any] | None,
    path: Optional[str] = None,
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Repair local identity and return the matching partial remote patch."""
    repaired = reconcile_device_json_identity(path, device_info=reference_info)
    ble_mac = resolve_ble_mac(repaired)
    patch = remote_identity_patch(reference_info, ble_mac=ble_mac)
    return repaired, patch


def load_boot_device_identity() -> Dict[str, Any]:
    """Return the optional hardware model from read-only /boot/device.json."""
    try:
        if not os.path.isfile(BOOT_DEVICE_JSON):
            return {}
        with open(BOOT_DEVICE_JSON, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        if not isinstance(payload, dict):
            return {}
        model = str(payload.get("model", "")).strip()
        return {"model": model} if model else {}
    except Exception as e:
        _log.debug("Could not read boot device model: %s", e)
        return {}



def verify_and_repair_device_json(path: Optional[str] = None) -> bool:
    """Repair local identity from BLE hardware and ``/boot/serial.txt`` only."""
    resolved = path or os.path.join(os.getcwd(), "device.json")
    if os.path.realpath(resolved) == os.path.realpath(BOOT_SERIAL_FILE):
        _log.error("refusing to write boot serial source: %s", resolved)
        return False

    can_repair, data = _read_local_device_json(resolved)
    if not can_repair:
        return False

    boot = load_boot_device_identity()
    serial = load_boot_serial()
    changed = False
    ble_mac = resolve_ble_mac(data)
    if ble_mac and str(data.get("id") or "").strip() != ble_mac:
        data["id"] = ble_mac
        changed = True
    if serial is not None and str(data.get("serial", "")).strip() != serial:
        data["serial"] = serial
        changed = True

    boot_model = str(boot.get("model", "")).strip()
    if boot_model and str(data.get("model", "")).strip() != boot_model:
        data["model"] = boot_model
        changed = True

    if changed:
        try:
            with open(resolved, "w", encoding="utf-8") as f:
                json.dump(data, f)
            _log.warning("device.json identity repaired from local authorities (path=%s)", resolved)
        except Exception as e:
            _log.error("device.json identity repair write failed: %s", e)
            return False

    if serial is None:
        return True
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            check = json.load(f)
        return isinstance(check, dict) and str(check.get("serial", "")).strip() == serial
    except Exception:
        return False
