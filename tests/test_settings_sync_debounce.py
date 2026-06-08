import time

from runtime.settings_sync_debounce import SettingsSyncDebouncer


def test_debounce_publishes_latest_value_after_quiet_period():
    published = []
    debouncer = SettingsSyncDebouncer(
        published.append,
        debounce_seconds=0.05,
    )

    debouncer.schedule("volume", 60)
    debouncer.schedule("volume", 70)
    debouncer.schedule("volume", 80)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if published:
            break
        time.sleep(0.01)

    assert published == [{"volume": 80}]


def test_debounce_tracks_brightness_and_volume_independently():
    published = []
    debouncer = SettingsSyncDebouncer(
        published.append,
        debounce_seconds=0.05,
    )

    debouncer.schedule("brightness", 45)
    debouncer.schedule("volume", 60)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if len(published) >= 2:
            break
        time.sleep(0.01)

    assert {"brightness": 45} in published
    assert {"volume": 60} in published


def test_debounce_resets_timer_on_each_change():
    published = []
    debouncer = SettingsSyncDebouncer(
        published.append,
        debounce_seconds=0.08,
    )

    debouncer.schedule("volume", 50)
    time.sleep(0.05)
    debouncer.schedule("volume", 55)
    time.sleep(0.05)
    assert published == []

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if published:
            break
        time.sleep(0.01)

    assert published == [{"volume": 55}]
