"""Apply semantic sync events to firmware (delegates to remote config applier)."""

from __future__ import annotations

from typing import Any, Callable, Dict

from runtime.sync.events import (
    BluetoothChanged,
    DeviceSettingsChanged,
    FirmwareUpdateRequested,
    FullSnapshot,
    GamesSnapshotChanged,
    PagesChanged,
    ResetConfirmed,
    SyncEvent,
    SyncEventMeta,
)


def _config_with_meta(event: SyncEvent, patch: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(patch)
    meta: SyncEventMeta = event.meta  # type: ignore[attr-defined]
    if meta.last_update_source:
        cfg.setdefault("last_update_source", meta.last_update_source)
    if meta.updated_at is not None:
        cfg.setdefault("updated_at", meta.updated_at.isoformat())
    return cfg


def apply_events(
    events: list[SyncEvent],
    *,
    apply_config: Callable[[Dict[str, Any]], None],
) -> None:
    """Apply accepted semantic events in stable order."""
    full_events = [e for e in events if isinstance(e, (FullSnapshot, ResetConfirmed))]
    if full_events:
        for event in full_events:
            apply_config(event.config)
        return

    for event in events:
        if isinstance(event, PagesChanged):
            apply_config(
                _config_with_meta(
                    event,
                    {
                        "pages": event.pages,
                        "pages_updated_at": (
                            event.pages_updated_at.isoformat()
                            if event.pages_updated_at is not None
                            else None
                        ),
                    },
                )
            )
        elif isinstance(event, GamesSnapshotChanged):
            apply_config(_config_with_meta(event, {"games": event.games}))
        elif isinstance(event, BluetoothChanged):
            apply_config(_config_with_meta(event, {"bluetooth": event.bluetooth}))
        elif isinstance(event, DeviceSettingsChanged):
            apply_config(_config_with_meta(event, dict(event.config)))
        elif isinstance(event, FirmwareUpdateRequested):
            apply_config(_config_with_meta(event, {"firmware": event.firmware}))
