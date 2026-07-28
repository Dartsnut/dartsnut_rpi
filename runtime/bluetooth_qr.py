"""Generate the device-connect QR used across machine UI surfaces."""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional
from urllib.parse import quote

import qrcode
from PIL import Image

from runtime.bluetooth_identity import (
    resolve_bluetooth_device_id,
    resolve_bluetooth_local_name,
)


_BLUETOOTH_QR_LOGO = Image.open(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets_media",
        "images",
        "bluetooth_qr_logo.png",
    )
).convert("RGBA")


def bluetooth_qr_payload(local_name: str) -> str:
    """Return the Bluetooth-connect deep link encoded by an unbound machine QR."""
    encoded_name = quote(local_name, safe="")
    return f"dartsnut://device/connect?ble_name={encoded_name}"


def device_bind_qr_payload(device_id: str) -> str:
    """Return the Supabase device-bind deep link."""
    encoded_device_id = quote(str(device_id or "").strip(), safe="")
    return f"dartsnut://device/bind?device_id={encoded_device_id}"


def connection_qr_payload(
    local_name: str | None,
    *,
    supabase_connected: bool = False,
    device_id: str = "",
) -> Optional[str]:
    """Choose the bind link when cloud-connected, otherwise the BLE link."""
    normalized_device_id = str(device_id or "").strip()
    if supabase_connected and normalized_device_id:
        return device_bind_qr_payload(normalized_device_id)
    normalized_local_name = str(local_name or "").strip()
    if not normalized_local_name:
        return None
    return bluetooth_qr_payload(normalized_local_name)


def create_qr_surface(payload: str) -> Image.Image:
    """Create a scan-verified connection QR on a 128x128 black surface."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=1,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="white", back_color="black").convert("RGB")

    width, height = qr_image.size
    if width > 128 or height > 128:
        raise ValueError("Connection payload is too large for the display QR")
    scale = min(128 // width, 128 // height)
    if scale > 1:
        qr_image = qr_image.resize(
            (width * scale, height * scale),
            resample=Image.Resampling.NEAREST,
        )

    surface = Image.new("RGB", (128, 128), "black")
    x = (128 - qr_image.size[0]) // 2
    y = (128 - qr_image.size[1]) // 2
    surface.paste(qr_image, (x, y))

    # Cover at most 15x15 QR modules. With Q error correction this is the
    # largest logo size that still decodes for the connection URI on this display.
    logo_size = min(_BLUETOOTH_QR_LOGO.size[0], scale * 15)
    logo = _BLUETOOTH_QR_LOGO
    if logo.size != (logo_size, logo_size):
        logo = logo.resize(
            (logo_size, logo_size),
            resample=Image.Resampling.LANCZOS,
        )
    logo_x = (128 - logo.size[0]) // 2
    logo_y = (128 - logo.size[1]) // 2
    surface.paste(logo, (logo_x, logo_y), logo)
    return surface


def create_bluetooth_qr_surface(local_name: str) -> Image.Image:
    """Create a QR surface for the legacy Bluetooth-connect link."""
    return create_qr_surface(bluetooth_qr_payload(local_name))


def create_connection_qr_surface(
    local_name: str | None,
    *,
    supabase_connected: bool = False,
    device_id: str = "",
) -> Optional[Image.Image]:
    """Create the appropriate bind/connect QR surface for current connectivity."""
    payload = connection_qr_payload(
        local_name,
        supabase_connected=supabase_connected,
        device_id=device_id,
    )
    if payload is None:
        return None
    return create_qr_surface(payload)


def create_bluetooth_qr_for_device(
    device_info: Mapping[str, Any] | None,
) -> Optional[Image.Image]:
    """Resolve the advertised local name and generate its connection QR."""
    local_name = resolve_bluetooth_local_name(device_info)
    return create_connection_qr_surface(local_name)


def create_connection_qr_for_device(
    device_info: Mapping[str, Any] | None,
    *,
    supabase_connected: bool = False,
) -> Optional[Image.Image]:
    """Resolve the BLE identity and create the QR for current connectivity."""
    local_name = resolve_bluetooth_local_name(device_info)
    device_id = resolve_bluetooth_device_id(device_info) or ""
    return create_connection_qr_surface(
        local_name,
        supabase_connected=supabase_connected,
        device_id=device_id,
    )
