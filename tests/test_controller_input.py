import errno
import struct

import pytest

from runtime.controller_input import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_X,
    ABS_Y,
    BTN_EAST,
    BTN_MODE,
    BTN_SELECT,
    BTN_SOUTH,
    BTN_START,
    ControllerInputManager,
    EV_ABS,
    EV_KEY,
    KEY_BACKSPACE,
    KEY_DOWN,
    KEY_ENTER,
    KEY_ESC,
    KEY_HOME,
    KEY_LEFT,
    KEY_RIGHT,
    KEY_SPACE,
    KEY_UP,
)


class _Dartsnut:
    def __init__(self, states=None):
        self.states = states or {}

    def get_buttons(self):
        return dict(self.states)


class _FakeInputFile:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def fileno(self):
        return 99

    def read(self, _size):
        if not self._chunks:
            raise BlockingIOError()
        chunk = self._chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def close(self):
        self.closed = True


def _js_button(number, value=1, event_type=0x01):
    return struct.pack("Ihbb", 1, value, event_type, number)


def _js_axis(number, value):
    return struct.pack("Ihbb", 1, value, 0x02, number)


def _ev_key(code, value=1):
    return struct.pack("@llHHi", 1, 2, EV_KEY, code, value)


def _ev_abs(code, value):
    return struct.pack("@llHHi", 1, 2, EV_ABS, code, value)


def _manager(monkeypatch, js_files=None, ev_files=None, now=None):
    js_files = js_files or {}
    ev_files = ev_files or {}
    monkeypatch.setattr("runtime.controller_input.glob.glob", lambda pattern: {
        "/dev/input/js*": list(js_files.keys()),
        "/dev/input/event*": list(ev_files.keys()),
    }[pattern])

    def fake_open(path, mode):
        assert mode == "rb"
        if path in js_files:
            return js_files[path]
        if path in ev_files:
            return ev_files[path]
        raise FileNotFoundError(path)

    monkeypatch.setattr("runtime.controller_input.open", fake_open, raising=False)
    monkeypatch.setattr("runtime.controller_input.os.set_blocking", lambda _fd, _flag: None)
    clock = iter(now or [1.0, 1.2, 1.4, 1.6])
    monkeypatch.setattr("runtime.controller_input.time.monotonic", lambda: next(clock))
    return ControllerInputManager()


def test_gpio_edges_return_pressed_and_current(monkeypatch):
    manager = _manager(monkeypatch)
    dartsnut = _Dartsnut({"btn_a": False, "btn_b": False, "btn_home": False})

    first = manager.poll(dartsnut, consume_app_controls=True)
    assert first.pressed["btn_a"] is False
    assert first.current["btn_a"] is False

    dartsnut.states["btn_a"] = True
    second = manager.poll(dartsnut, consume_app_controls=True)
    assert second.pressed["btn_a"] is True
    assert second.current["btn_a"] is True


@pytest.mark.parametrize(
    ("number", "button"),
    [(0, "btn_a"), (1, "btn_b"), (8, "btn_home"), (9, "btn_home"), (10, "btn_home")],
)
def test_legacy_joystick_buttons_map_to_app_buttons(monkeypatch, number, button):
    js = _FakeInputFile([_js_button(number)])
    manager = _manager(monkeypatch, js_files={"/dev/input/js0": js})

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed[button] is True
    assert result.current[button] is True


@pytest.mark.parametrize(
    ("event", "button"),
    [
        (_js_axis(6, -20000), "btn_left"),
        (_js_axis(6, 20000), "btn_right"),
        (_js_axis(7, -20000), "btn_up"),
        (_js_axis(7, 20000), "btn_down"),
    ],
)
def test_legacy_joystick_axes_map_to_directions(monkeypatch, event, button):
    js = _FakeInputFile([event])
    manager = _manager(monkeypatch, js_files={"/dev/input/js0": js})

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed[button] is True
    assert result.current[button] is True


@pytest.mark.parametrize(
    ("code", "button"),
    [
        (BTN_SOUTH, "btn_a"),
        (BTN_EAST, "btn_b"),
        (BTN_MODE, "btn_home"),
        (BTN_START, "btn_home"),
        (BTN_SELECT, "btn_home"),
    ],
)
def test_evdev_gamepad_buttons_map_to_app_buttons(monkeypatch, code, button):
    ev = _FakeInputFile([_ev_key(code)])
    manager = _manager(monkeypatch, ev_files={"/dev/input/event0": ev})

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed[button] is True
    assert result.current[button] is True


@pytest.mark.parametrize(
    ("code", "button"),
    [
        (KEY_ENTER, "btn_a"),
        (KEY_SPACE, "btn_a"),
        (KEY_ESC, "btn_b"),
        (KEY_BACKSPACE, "btn_b"),
        (KEY_LEFT, "btn_left"),
        (KEY_RIGHT, "btn_right"),
        (KEY_UP, "btn_up"),
        (KEY_DOWN, "btn_down"),
        (KEY_HOME, "btn_home"),
    ],
)
def test_evdev_keyboard_style_controls_map_to_app_buttons(monkeypatch, code, button):
    ev = _FakeInputFile([_ev_key(code)])
    manager = _manager(monkeypatch, ev_files={"/dev/input/event0": ev})

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed[button] is True
    assert result.current[button] is True


@pytest.mark.parametrize(
    ("event", "button"),
    [
        (_ev_abs(ABS_HAT0X, -1), "btn_left"),
        (_ev_abs(ABS_HAT0X, 1), "btn_right"),
        (_ev_abs(ABS_HAT0Y, -1), "btn_up"),
        (_ev_abs(ABS_HAT0Y, 1), "btn_down"),
        (_ev_abs(ABS_X, -20000), "btn_left"),
        (_ev_abs(ABS_X, 20000), "btn_right"),
        (_ev_abs(ABS_Y, -20000), "btn_up"),
        (_ev_abs(ABS_Y, 20000), "btn_down"),
    ],
)
def test_evdev_hat_and_analog_axes_map_to_directions(monkeypatch, event, button):
    ev = _FakeInputFile([event])
    manager = _manager(monkeypatch, ev_files={"/dev/input/event0": ev})

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed[button] is True
    assert result.current[button] is True


def test_in_game_poll_suppresses_app_controls_but_allows_home(monkeypatch):
    ev = _FakeInputFile([_ev_key(BTN_SOUTH), _ev_key(BTN_MODE)])
    manager = _manager(monkeypatch, ev_files={"/dev/input/event0": ev})

    result = manager.poll(_Dartsnut(), consume_app_controls=False)

    assert result.pressed["btn_a"] is False
    assert result.pressed["btn_home"] is True
    assert result.current["btn_a"] is True
    assert result.current["btn_home"] is True


def test_in_game_poll_can_suppress_evdev_home_for_pico8(monkeypatch):
    ev = _FakeInputFile([_ev_key(BTN_MODE)])
    manager = _manager(monkeypatch, ev_files={"/dev/input/event0": ev})

    result = manager.poll(
        _Dartsnut(),
        consume_app_controls=False,
        should_consume_button=lambda button: button != "btn_home",
    )

    assert result.pressed["btn_home"] is False
    assert result.current["btn_home"] is True


def test_in_game_poll_can_suppress_legacy_joystick_home_for_pico8(monkeypatch):
    js = _FakeInputFile([_js_button(8)])
    manager = _manager(monkeypatch, js_files={"/dev/input/js0": js})

    result = manager.poll(
        _Dartsnut(),
        consume_app_controls=False,
        should_consume_button=lambda button: button != "btn_home",
    )

    assert result.pressed["btn_home"] is False
    assert result.current["btn_home"] is True


def test_overlay_poll_consumes_pico8_controller_buttons(monkeypatch):
    ev = _FakeInputFile([_ev_key(BTN_SOUTH), _ev_key(BTN_EAST), _ev_key(BTN_MODE)])
    manager = _manager(
        monkeypatch,
        ev_files={"/dev/input/event0": ev},
        now=[1.0, 1.2, 1.4],
    )

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed["btn_a"] is True
    assert result.pressed["btn_b"] is True
    assert result.pressed["btn_home"] is True


def test_evdev_current_state_persists_until_release_event(monkeypatch):
    ev = _FakeInputFile([_ev_key(BTN_SOUTH)])
    manager = _manager(
        monkeypatch,
        ev_files={"/dev/input/event0": ev},
        now=[1.0, 1.2, 1.4],
    )

    pressed = manager.poll(_Dartsnut(), consume_app_controls=True)
    held = manager.poll(_Dartsnut(), consume_app_controls=True)
    ev._chunks.append(_ev_key(BTN_SOUTH, value=0))
    released = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert pressed.pressed["btn_a"] is True
    assert pressed.current["btn_a"] is True
    assert held.pressed["btn_a"] is False
    assert held.current["btn_a"] is True
    assert released.pressed["btn_a"] is False
    assert released.current["btn_a"] is False


def test_duplicate_suppression_drops_identical_presses_within_window(monkeypatch):
    js = _FakeInputFile([_js_button(0)])
    ev = _FakeInputFile([_ev_key(BTN_SOUTH)])
    manager = _manager(
        monkeypatch,
        js_files={"/dev/input/js0": js},
        ev_files={"/dev/input/event0": ev},
        now=[10.0, 10.04],
    )

    result = manager.poll(_Dartsnut(), consume_app_controls=True)

    assert result.pressed["btn_a"] is True
    assert result.press_counts["btn_a"] == 1


def test_disconnected_input_file_is_closed_and_removed(monkeypatch):
    ev = _FakeInputFile([OSError(errno.ENODEV, "gone")])
    manager = _manager(monkeypatch, ev_files={"/dev/input/event0": ev})

    manager.poll(_Dartsnut(), consume_app_controls=True)

    assert ev.closed is True
    assert "/dev/input/event0" not in manager.ev_files


def test_controller_connection_callback_deduplicates_input_nodes(monkeypatch):
    js = _FakeInputFile([])
    event = _FakeInputFile([])
    notifications = []
    manager = _manager(
        monkeypatch,
        js_files={"/dev/input/js0": js},
        ev_files={"/dev/input/event4": event},
    )
    manager.on_controller_connected = lambda: notifications.append("connected")
    manager._input_device_identity = lambda path: "bluetooth:AA:BB"  # noqa: SLF001

    manager.poll(_Dartsnut())
    assert notifications == ["connected"]

    manager.poll(_Dartsnut())
    assert notifications == ["connected"]


def test_controller_connection_callback_fires_after_real_disconnect(monkeypatch):
    first = _FakeInputFile([OSError("gone")])
    second = _FakeInputFile([])
    discovered = {"/dev/input/js0": first}
    monkeypatch.setattr(
        "runtime.controller_input.glob.glob",
        lambda pattern: list(discovered.keys()) if pattern == "/dev/input/js*" else [],
    )
    monkeypatch.setattr(
        "runtime.controller_input.open", lambda path, _mode: discovered[path], raising=False
    )
    monkeypatch.setattr("runtime.controller_input.os.set_blocking", lambda _fd, _flag: None)
    notifications = []
    manager = ControllerInputManager(
        on_controller_connected=lambda: notifications.append("connected"),
        input_device_identity=lambda _path: "bluetooth:AA:BB",
    )

    manager.poll(_Dartsnut())
    assert notifications == ["connected"]
    assert manager.js_files == {}

    discovered["/dev/input/js0"] = second
    manager.poll(_Dartsnut())
    assert notifications == ["connected", "connected"]


def test_controller_connection_callback_ignores_unidentified_inputs(monkeypatch):
    keyboard = _FakeInputFile([])
    notifications = []
    manager = _manager(
        monkeypatch, ev_files={"/dev/input/event1": keyboard}
    )
    manager.on_controller_connected = lambda: notifications.append("connected")
    manager._input_device_identity = lambda _path: None  # noqa: SLF001

    manager.poll(_Dartsnut())
    assert notifications == []


def test_bluetooth_controller_identity_uses_uniq_and_gamepad_capabilities(tmp_path):
    from runtime.controller_input import _bluetooth_controller_identity

    sysfs = tmp_path / "input"
    js_device = sysfs / "js0" / "device"
    js_device.mkdir(parents=True)
    (js_device / "uniq").write_text("aa:bb:cc:dd:ee:ff\n", encoding="utf-8")

    event_device = sysfs / "event4" / "device"
    (event_device / "capabilities").mkdir(parents=True)
    (event_device / "uniq").write_text("aa:bb:cc:dd:ee:ff\n", encoding="utf-8")
    key_words = ["0"] * 5
    key_words[BTN_SOUTH // 64] = f"{1 << (BTN_SOUTH % 64):x}"
    (event_device / "capabilities" / "key").write_text(
        " ".join(reversed(key_words)) + "\n", encoding="utf-8"
    )

    assert _bluetooth_controller_identity(
        "/dev/input/js0", sysfs_root=sysfs
    ) == "bluetooth:AA:BB:CC:DD:EE:FF"
    assert _bluetooth_controller_identity(
        "/dev/input/event4", sysfs_root=sysfs
    ) == "bluetooth:AA:BB:CC:DD:EE:FF"


def test_bluetooth_controller_identity_ignores_keyboard_and_wired_joystick(tmp_path):
    from runtime.controller_input import _bluetooth_controller_identity

    sysfs = tmp_path / "input"
    keyboard = sysfs / "event1" / "device"
    (keyboard / "capabilities").mkdir(parents=True)
    (keyboard / "capabilities" / "key").write_text(
        f"{1 << KEY_ENTER:x}\n", encoding="utf-8"
    )

    wired = sysfs / "js0" / "device"
    wired.mkdir(parents=True)
    (wired / "uniq").write_text("\n", encoding="utf-8")

    assert _bluetooth_controller_identity(
        "/dev/input/event1", sysfs_root=sysfs
    ) is None
    assert _bluetooth_controller_identity(
        "/dev/input/js0", sysfs_root=sysfs
    ) is None
