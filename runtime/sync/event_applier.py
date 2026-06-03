"""Apply semantic sync events to firmware (delegates to remote config applier)."""

from __future__ import annotations

from typing import Any, Callable, Dict

from runtime.sync.events import (
    BluetoothChanged,
    DeviceSettingsChanged,
    FirmwareUpdateRequested,
    FullSnapshot,
    PagesChanged,
    ResetConfirmed,
    SyncEvent,
)


def apply_events(
    events: list[SyncEvent],
    *,
    apply_config: Callable[[Dict[str, Any]], None],
) -> None:
    """Apply accepted semantic events in stable order."""
    for event in events:
        if isinstance(event, (FullSnapshot, ResetConfirmed)):
            apply_config(event.config)
        elif isinstance(event, PagesChanged):
            apply_config({"pages": event.pages, "pages_updated_at": event.pages_updated_at})
        elif isinstance(event, BluetoothChanged):
            apply_config({"bluetooth": event.bluetooth})
        elif isinstance(event, DeviceSettingsChanged):
            apply_config(dict(event.config))
        elif isinstance(event, FirmwareUpdateRequested):
            apply_config({"firmware": event.firmware})
