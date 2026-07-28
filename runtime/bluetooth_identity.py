"""Shared helpers for the machine's Bluetooth identity."""
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


def normalize_bluetooth_adapter_address(value: Any) -> Optional[str]:
    """Return a canonical colon-separated BLE MAC address when valid."""
    compact = str(value or "").strip().replace(":", "").replace("-", "")
    if len(compact) != 12 or any(
        char not in "0123456789abcdefABCDEF" for char in compact
    ):
        return None
    return ":".join(
        compact[index : index + 2].upper() for index in range(0, 12, 2)
    )


def resolve_bluetooth_device_id(
    device_info: Mapping[str, Any] | None,
) -> Optional[str]:
    """Resolve the BLE MAC used as the device bind identifier."""
    info = device_info or {}
    address = normalize_bluetooth_adapter_address(
        info.get("ble_mac") or info.get("mac_address") or info.get("id")
    )
    if address:
        return address
    return normalize_bluetooth_adapter_address(get_bluetooth_adapter_address())


def resolve_bluetooth_local_name(
    device_info: Mapping[str, Any] | None,
) -> Optional[str]:
    """Resolve the machine's Bluetooth local name from device data and adapter."""
    address = resolve_bluetooth_device_id(device_info)
    if not address:
        return None
    return build_bluetooth_local_name(device_info, address)
