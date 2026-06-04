from datetime import datetime

from runtime.sync.timestamps import resolve_snapshot_updated_at


def test_resolve_snapshot_updated_at_prefers_newer_device_updated_at():
    cfg = {
        "updated_at": "2026-06-01T10:00:00Z",
        "device_updated_at": "2026-06-01T11:00:00Z",
    }
    assert resolve_snapshot_updated_at(cfg) == datetime(2026, 6, 1, 11, 0, 0)


def test_resolve_snapshot_updated_at_prefers_newer_row_updated_at():
    cfg = {
        "updated_at": "2026-06-01T12:00:00Z",
        "device_updated_at": "2026-06-01T11:00:00Z",
    }
    assert resolve_snapshot_updated_at(cfg) == datetime(2026, 6, 1, 12, 0, 0)
