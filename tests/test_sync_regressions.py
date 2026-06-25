"""Regression tests for sync resilience under bad networks."""

from datetime import datetime, timedelta, timezone

import pytest

from runtime.sync.game_ready import resolve_authoritative_ready_ids
from runtime.sync.outbox import SyncOutbox
from runtime.sync.reducer import SyncReducer
from runtime.sync.tokenizer import tokenize_snapshot


def test_resolve_authoritative_ready_holds_when_all_playing():
    previous = frozenset({"chess", "darts"})
    games_cfg = [
        {"id": "chess", "status": "playing"},
        {"id": "darts", "status": "downloading"},
    ]
    assert resolve_authoritative_ready_ids(previous, games_cfg, games_key_present=True) is None


def test_resolve_authoritative_ready_clears_on_explicit_empty():
    previous = frozenset({"chess"})
    assert resolve_authoritative_ready_ids(previous, [], games_key_present=True) == frozenset()


def test_reducer_does_not_clear_ready_on_playing_only_snapshot():
    reducer = SyncReducer()
    reducer.cache.game_ready_ids = frozenset({"g1"})
    reducer.cache.last_row_updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(
        tzinfo=None
    )
    config = {
        "updated_at": "2026-06-01T12:00:00Z",
        "last_update_source": "mobile_app",
        "games": [{"id": "g1", "status": "playing", "version": "1"}],
    }
    events = tokenize_snapshot(config, emit_full_snapshot=False)
    _, game_ready = reducer.reduce(events)
    assert game_ready is None
    assert reducer.cache.game_ready_ids == frozenset({"g1"})


def test_reducer_accepts_app_settings_change_with_equal_timestamp():
    reducer = SyncReducer()
    reducer.cache.has_seen_remote_row = True
    reducer.cache.last_row_updated_at = datetime(2026, 6, 1, 12, 0, 0)
    baseline = {
        "updated_at": "2026-06-01T12:00:00Z",
        "last_update_source": "mobile_app",
        "games": [{"id": "g1", "status": "ready", "version": "1"}],
        "volume": 50,
    }
    events = tokenize_snapshot(baseline, emit_full_snapshot=False)
    accepted, _ = reducer.reduce(events)
    assert accepted

    updated = dict(baseline)
    updated["volume"] = 80
    events = tokenize_snapshot(updated, emit_full_snapshot=False)
    accepted, _ = reducer.reduce(events)
    assert accepted


def test_reducer_accepts_app_update_when_only_device_updated_at_is_newer():
    reducer = SyncReducer()
    reducer.cache.has_seen_remote_row = True
    reducer.cache.last_row_updated_at = datetime(2026, 6, 1, 10, 0, 0)
    baseline = {
        "updated_at": "2026-06-01T10:00:00Z",
        "device_updated_at": "2026-06-01T10:00:00Z",
        "last_update_source": "mobile_app",
        "games": [{"id": "g1", "status": "ready", "version": "1"}],
        "volume": 50,
    }
    events = tokenize_snapshot(baseline, emit_full_snapshot=False)
    accepted, _ = reducer.reduce(events)
    assert accepted

    updated = dict(baseline)
    updated["volume"] = 80
    updated["device_updated_at"] = "2026-06-01T11:00:00Z"
    events = tokenize_snapshot(updated, emit_full_snapshot=False)
    accepted, _ = reducer.reduce(events)
    assert accepted


def test_reducer_rejects_stale_bridge_echo_with_same_fingerprint():
    reducer = SyncReducer()
    reducer.cache.has_seen_remote_row = True
    reducer.cache.last_row_updated_at = datetime(2026, 6, 1, 12, 0, 1)
    config = {
        "updated_at": "2026-06-01T12:00:00Z",
        "last_update_source": "supabase_bridge",
        "games": [{"id": "g1", "status": "ready", "version": "1"}],
        "volume": 50,
    }
    events = tokenize_snapshot(config, emit_full_snapshot=False)
    reducer.cache.last_snapshot_fingerprint = events[0].meta.fingerprint
    reducer.cache.last_snapshot_fingerprint_at = datetime(2026, 6, 1, 12, 0, 1)
    accepted, _ = reducer.reduce(events)
    assert accepted == []


def test_outbox_retries_when_send_returns_false():
    calls = []

    def send_fn(ref, patch, full, source):
        calls.append((ref, patch))
        return len(calls) >= 2

    outbox = SyncOutbox(send_fn, backoff_seconds=(0.01, 0.02))
    outbox.start()
    ref = outbox.enqueue({"brightness": 50})
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if outbox.pending_count() == 0:
            break
        time.sleep(0.05)
    outbox.stop()
    assert len(calls) >= 2
    assert calls[0][1] == {"brightness": 50}


def test_outbox_ack_removes_pending():
    sent = []

    def send_fn(ref, patch, full, source):
        sent.append(ref)
        return True

    outbox = SyncOutbox(send_fn, backoff_seconds=(0.01,))
    outbox.start()
    ref = outbox.enqueue({"volume": 10})
    outbox.on_ack(ref)
    assert outbox.pending_count() == 0
    outbox.stop()


def test_outbox_coalesces_pending_settings_so_latest_value_wins():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue({"volume": 60}, source="local")
    outbox.enqueue({"volume": 70}, source="local")

    assert outbox.pending_count() == 1
    pending = list(outbox._pending.values())
    assert pending[0].patch == {"volume": 70}


def test_outbox_keeps_different_source_settings_entries_separate():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue({"volume": 60}, source="local")
    outbox.enqueue({"volume": 70}, source="remote")

    assert outbox.pending_count() == 2


def test_outbox_skips_settings_patch_matching_last_ack():
    sent = []

    def send_fn(ref, patch, _full, _source):
        sent.append((ref, patch))
        return True

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    ref = outbox.enqueue({"brightness": 40})
    outbox.on_ack(ref)

    duplicate_ref = outbox.enqueue({"brightness": 40})

    assert duplicate_ref == ""
    assert outbox.pending_count() == 0
    assert sent == [(ref, {"brightness": 40})]


def test_outbox_coalesces_pending_game_status_so_latest_ready_wins():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue({"games": [{"id": "g1", "status": "playing", "version": "1"}]}, source="local")
    outbox.enqueue({"games": [{"id": "g1", "status": "ready", "version": "1"}]}, source="local")

    assert outbox.pending_count() == 1
    pending = list(outbox._pending.values())
    assert pending[0].patch == {
        "games": [{"id": "g1", "status": "ready", "version": "1"}]
    }


def test_outbox_coalesces_pending_game_status_so_latest_playing_wins():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue({"games": [{"id": "g1", "status": "ready", "version": "1"}]}, source="local")
    outbox.enqueue({"games": [{"id": "g1", "status": "playing", "version": "1"}]}, source="local")

    assert outbox.pending_count() == 1
    pending = list(outbox._pending.values())
    assert pending[0].patch == {
        "games": [{"id": "g1", "status": "playing", "version": "1"}]
    }


def test_outbox_keeps_different_game_status_ids_separate():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue({"games": [{"id": "g1", "status": "ready", "version": "1"}]}, source="local")
    outbox.enqueue({"games": [{"id": "g2", "status": "playing", "version": "1"}]}, source="local")

    assert outbox.pending_count() == 2


def test_outbox_keeps_different_source_game_status_entries_separate():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue({"games": [{"id": "g1", "status": "ready", "version": "1"}]}, source="local")
    outbox.enqueue({"games": [{"id": "g1", "status": "playing", "version": "1"}]}, source="remote")

    assert outbox.pending_count() == 2


def test_outbox_does_not_coalesce_full_patch_with_game_status():
    def send_fn(_ref, _patch, _full, _source):
        return False

    outbox = SyncOutbox(send_fn, backoff_seconds=(60.0,))
    outbox.enqueue(
        {"games": [{"id": "g1", "status": "ready", "version": "1"}]},
        full=True,
        source="local",
    )
    outbox.enqueue({"games": [{"id": "g1", "status": "playing", "version": "1"}]}, source="local")

    assert outbox.pending_count() == 2
