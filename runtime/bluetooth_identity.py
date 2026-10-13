"""Shared helpers for the machine's Bluetooth identity."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from runtime.pixeldarts_hardware import resolve_device_model


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
    """Build the local name used by the BLE peripheral from live hardware."""
    _ = device_info
    model = resolve_device_model()
    return f"{model}-{bluetooth_mac_suffix(adapter_address)}"


def get_bluetooth_adapter_address() -> Optional[str]:
    """Return the first BlueZ adapter address, or None when unavailable.

    Reads BlueZ ObjectManager state directly. Constructing
    ``bluezero.adapter.Adapter`` (including via ``Adapter.available()``)
    registers three D-Bus signal receivers per instance that are never
    removed, so per-call construction leaks receivers and makes every
    later ObjectManager/Properties signal progressively slower to dispatch.
    """
    try:
        from bluezero import constants, dbus_tools  # type: ignore

        for obj in dbus_tools.get_managed_objects().values():
            props = obj.get(constants.ADAPTER_INTERFACE)
            if props:
                address = str(props.get("Address", "")).strip()
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
    """Resolve the machine's hardware-derived Bluetooth name and adapter suffix."""
    address = resolve_bluetooth_device_id(device_info)
    if not address:
        return None
    return build_bluetooth_local_name(device_info, address)
