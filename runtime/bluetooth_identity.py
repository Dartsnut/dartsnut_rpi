"""Shared helpers for the machine's advertised Bluetooth local name."""
from __future__ import annotations

from typing import Any, Mapping, Optional


def bluetooth_mac_suffix(adapter_address: str) -> str:
    """Return the last two MAC octets as four lowercase hex characters."""
    try:
        normalized = str(adapter_address).strip().lower().replace(":", "")
        if len(normalized) >= 4:
            return normalized[-4:]
    except Exception:
        pass
    return "0000"


def build_bluetooth_local_name(
    device_info: Mapping[str, Any] | None,
    adapter_address: str,
) -> str:
    """Build the local name used by the BLE peripheral."""
    model = str((device_info or {}).get("model") or "Dartsnut").strip()
    if not model:
        model = "Dartsnut"
    return f"{model}-{bluetooth_mac_suffix(adapter_address)}"


def get_bluetooth_adapter_address() -> Optional[str]:
    """Return the first Bluezero adapter address, or None when unavailable."""
    try:
        from bluezero import adapter  # type: ignore

        adapters = list(adapter.Adapter.available())
        if adapters:
            address = str(adapters[0].address).strip()
            return address or None
    except Exception:
        pass
    return None


def resolve_bluetooth_local_name(
    device_info: Mapping[str, Any] | None,
) -> Optional[str]:
    """Resolve the machine's Bluetooth local name from device data and adapter."""
    info = device_info or {}
    adapter_address = str(info.get("ble_mac") or "").strip()
    if not adapter_address:
        adapter_address = get_bluetooth_adapter_address() or ""
    if not adapter_address:
        return None
    return build_bluetooth_local_name(info, adapter_address)
