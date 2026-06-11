"""
Canonical serial/model identity for ./device.json.

Factory values live on the Pi at /boot/device.json. After any write to the
workspace copy we re-read and align serial + model with boot so identity cannot
be accidentally dropped or drift from the machine.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

BOOT_DEVICE_JSON = "/boot/device.json"
FACTORY_PLACEHOLDER_SERIAL = "UNASSIGNED"


def load_boot_device_identity() -> Dict[str, Any]:
    """Return non-empty serial/model from /boot/device.json, if present."""
    try:
        if not os.path.isfile(BOOT_DEVICE_JSON):
            return {}
        with open(BOOT_DEVICE_JSON, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        if not isinstance(payload, dict):
            return {}
        serial = str(payload.get("serial", "")).strip()
        model = str(payload.get("model", "")).strip()
        out: Dict[str, Any] = {}
        if serial:
            out["serial"] = serial
        if model:
            out["model"] = model
        return out
    except Exception as e:
        _log.debug("Could not read boot device identity: %s", e)
        return {}


def apply_factory_serial_if_needed(
    data: Dict[str, Any], *, device_id: str = ""
) -> Dict[str, Any]:
    """
    Assign a factory placeholder serial when boot and disk have none.

    Production machines with serial in /boot/device.json are unchanged.
    """
    _ = device_id
    out = dict(data or {})
    boot = load_boot_device_identity()
    if str(boot.get("serial", "")).strip():
        return out
    if str(out.get("serial", "")).strip():
        return out
    out["serial"] = FACTORY_PLACEHOLDER_SERIAL
    return out


def verify_and_repair_device_json(path: Optional[str] = None) -> bool:
    """
    Re-read device.json and ensure serial + model match boot when boot defines them.

    Call after every write to device.json. If boot has a value for a key, it is
    the source of truth and overwrites the workspace file when missing or wrong.

    Returns True when both serial and model are non-empty after repair; otherwise False.
    """
    resolved = path or os.path.join(os.getcwd(), "device.json")
    data: Dict[str, Any]
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            raw = json.load(f)
        data = raw if isinstance(raw, dict) else {}
    except Exception as e:
        _log.warning("verify device.json: could not read %s: %s", resolved, e)
        data = {}

    boot = load_boot_device_identity()
    changed = False
    for key in ("serial", "model"):
        disk_val = str(data.get(key, "")).strip()
        boot_val = str(boot.get(key, "")).strip()
        if boot_val and disk_val != boot_val:
            data[key] = boot_val
            changed = True
        elif not disk_val and boot_val:
            data[key] = boot_val
            changed = True

    if not str(data.get("serial", "")).strip() and not str(boot.get("serial", "")).strip():
        repaired = apply_factory_serial_if_needed(data)
        if str(repaired.get("serial", "")).strip() != str(data.get("serial", "")).strip():
            data = repaired
            changed = True

    if changed:
        try:
            with open(resolved, "w", encoding="utf-8") as f:
                json.dump(data, f)
            _log.warning(
                "device.json identity repaired from boot (path=%s)", resolved
            )
        except Exception as e:
            _log.error("device.json identity repair write failed: %s", e)
            return False

    ok = True
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            check = json.load(f)
        if not isinstance(check, dict):
            check = {}
    except Exception:
        check = {}

    for key in ("serial", "model"):
        if not str(check.get(key, "")).strip():
            ok = False
            boot_hint = str(boot.get(key, "")).strip()
            if boot_hint:
                _log.error(
                    "device.json missing %s after repair; boot had %r - check permissions/path %s",
                    key,
                    boot_hint,
                    resolved,
                )
            else:
                _log.error(
                    "device.json missing required identity key %r (no boot fallback); path=%s",
                    key,
                    resolved,
                )
    return ok
