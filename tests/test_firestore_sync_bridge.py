import json
import threading
import time

import firestore_sync_bridge as fsb


class _FakeClient:
    def __init__(self):
        self.sent = []
        self.lock = threading.Lock()

    def send_state(self, payload, *, full=False):
        with self.lock:
            self.sent.append((payload, full))


def _reset_write_cache():
    cache = fsb._WRITE_CACHE
    with cache._lock:
        cache._last_sent_per_key.clear()
        cache._pending = {}
        cache._flush_timer = None
        cache._flush_in_progress = False
        cache._stats = {"sent": 0, "skipped": 0, "coalesced": 0}


def test_notify_device_state_update_skips_duplicate_payload():
    _reset_write_cache()
    fake = _FakeClient()
    fsb._client = fake
    try:
        fsb.notify_device_state_update({"volume": 10})
        fsb.notify_device_state_update({"volume": 10})
        time.sleep(0.35)
    finally:
        fsb._client = None

    assert len(fake.sent) == 1
    assert fake.sent[0][0] == {"volume": 10}
    assert fake.sent[0][1] is False


def test_notify_device_state_update_coalesces_burst_updates():
    _reset_write_cache()
    fake = _FakeClient()
    fsb._client = fake
    try:
        fsb.notify_device_state_update({"brightness": 20})
        fsb.notify_device_state_update({"volume": 30})
        time.sleep(0.35)
    finally:
        fsb._client = None

    assert len(fake.sent) == 1
    sent_payload, is_full = fake.sent[0]
    assert is_full is False
    assert sent_payload == {"brightness": 20, "volume": 30}


def test_notify_device_state_update_coerces_null_pages_games_to_empty_lists():
    _reset_write_cache()
    fake = _FakeClient()
    fsb._client = fake
    try:
        fsb.notify_device_state_update({"pages": None, "games": None})
        time.sleep(0.35)
    finally:
        fsb._client = None

    assert len(fake.sent) == 1
    sent_payload, _ = fake.sent[0]
    assert sent_payload == {"pages": [], "games": []}


def test_merge_remote_and_local_coerces_null_pages_games(monkeypatch):
    # Avoid reading workspace apps/conf.json or device.json so merge reflects remote nulls.
    monkeypatch.setattr(fsb.os.path, "isfile", lambda _p: False)

    merged = fsb._merge_remote_and_local({"pages": None, "games": None})
    assert merged["pages"] == []
    assert merged["games"] == []


def test_write_cache_detects_echo_payload():
    cache = fsb._DeviceStateWriteCache()
    payload = {"brightness": 25, "volume": 50}
    key = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert cache.is_probable_echo_payload(payload) is False
    with cache._lock:
        cache._recent_payload_fingerprints[key] = time.time()
    assert cache.is_probable_echo_payload(payload) is True
    assert cache.is_probable_echo_payload({"brightness": 26, "volume": 50}) is False


def test_request_device_reset_state_sends_expected_payload(monkeypatch):
    captured = []

    def _capture(payload):
        captured.append(payload)

    monkeypatch.setattr(fsb, "notify_device_state_update", _capture)

    fsb.request_device_reset_state()

    assert captured == [
        {
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    ]


def test_normalize_config_payload_converts_null_lists_to_empty_lists():
    payload = {
        "ip_address": "",
        "ssid": "",
        "pages": None,
        "games": None,
        "dim_window": {"dim_window_enabled": False},
    }

    normalized = fsb._normalize_config_payload(payload)

    assert normalized["pages"] == []
    assert normalized["games"] == []
    assert normalized["ip_address"] == ""
