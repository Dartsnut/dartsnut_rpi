"""Local controller input readers for GPIO, Linux joystick, and evdev devices."""

from __future__ import annotations

import glob
import fcntl
import logging
import os
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable

try:
    from pydartsnut._controller_broker import ControllerBrokerPublisher
except Exception:  # pragma: no cover - firmware can run with older package
    ControllerBrokerPublisher = None

_log = logging.getLogger(__name__)

APP_BUTTONS = (
    "btn_a",
    "btn_b",
    "btn_left",
    "btn_up",
    "btn_right",
    "btn_down",
    "btn_home",
    "btn_reserved",
)

AXIS_THRESHOLD = 16000
DUPLICATE_PRESS_WINDOW_SECONDS = 0.08
INPUT_DISCOVERY_INTERVAL_SECONDS = 1.0

JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02

EV_KEY = 0x01
EV_ABS = 0x03

KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_ENTER = 28
KEY_SPACE = 57
KEY_HOME = 102
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108

BTN_SOUTH = 0x130
BTN_EAST = 0x131
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C

ABS_X = 0x00
ABS_Y = 0x01
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

JS_BUTTON_TO_APP = {
    0: "btn_a",
    1: "btn_b",
    8: "btn_home",
    9: "btn_home",
    10: "btn_home",
}

EV_KEY_TO_APP = {
    BTN_SOUTH: "btn_a",
    KEY_ENTER: "btn_a",
    KEY_SPACE: "btn_a",
    BTN_EAST: "btn_b",
    KEY_ESC: "btn_b",
    KEY_BACKSPACE: "btn_b",
    KEY_LEFT: "btn_left",
    KEY_UP: "btn_up",
    KEY_RIGHT: "btn_right",
    KEY_DOWN: "btn_down",
    BTN_MODE: "btn_home",
    BTN_START: "btn_home",
    BTN_SELECT: "btn_home",
    KEY_HOME: "btn_home",
}

_JS_EVENT = struct.Struct("Ihbb")
_EVDEV_EVENT = struct.Struct("@llHHi")
_ABS_INFO = struct.Struct("iiiiii")
_BT_DEVICE_RE = re.compile(r"^hci\d+:([0-9A-Fa-f:]{17})$")
_INPUT_DEVICE_RE = re.compile(r"^(?:event|js)\d+$")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _event_has_gamepad_buttons(device_path: Path) -> bool:
    raw = _read_text(device_path / "capabilities" / "key")
    if not raw:
        return False
    try:
        words = [int(word, 16) for word in reversed(raw.split())]
    except ValueError:
        return False

    def has_key(code: int) -> bool:
        word_index, bit_index = divmod(code, 64)
        return word_index < len(words) and bool(words[word_index] & (1 << bit_index))

    return any(
        has_key(code)
        for code in (BTN_SOUTH, BTN_EAST, BTN_SELECT, BTN_START, BTN_MODE)
    )


def _bluetooth_address(device_path: Path) -> str:
    uniq = _read_text(device_path / "uniq").upper()
    if re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", uniq):
        return uniq
    for parent in (device_path, *device_path.parents):
        match = _BT_DEVICE_RE.match(parent.name)
        if match:
            return match.group(1).upper()
    return ""


def _bluetooth_controller_identity(
    path: str, *, sysfs_root: Path = Path("/sys/class/input")
) -> str | None:
    """Return a physical Bluetooth controller key for a Linux input node."""
    node_name = os.path.basename(path)
    if not _INPUT_DEVICE_RE.match(node_name):
        return None
    try:
        device_path = (sysfs_root / node_name / "device").resolve(strict=True)
    except OSError:
        return None

    is_controller = node_name.startswith("js") or _event_has_gamepad_buttons(device_path)
    if not is_controller:
        return None
    address = _bluetooth_address(device_path)
    return f"bluetooth:{address}" if address else None


def _controller_identity(path: str, *, sysfs_root: Path = Path("/sys/class/input")) -> str | None:
    """Resolve one physical gamepad identity across JS and evdev nodes.

    Bluetooth address wins, followed by ``uniq``, a stable by-id symlink, and
    finally the deterministic physical sysfs path.  Non-gamepad keyboards are
    deliberately ignored.
    """
    node_name = os.path.basename(path)
    if not _INPUT_DEVICE_RE.match(node_name):
        return None
    try:
        device_path = (sysfs_root / node_name / "device").resolve(strict=True)
    except OSError:
        return None
    is_controller = node_name.startswith("js") or _event_has_gamepad_buttons(device_path)
    if not is_controller:
        return None
    address = _bluetooth_address(device_path)
    if address:
        return f"bluetooth:{address}"
    uniq = _read_text(device_path / "uniq")
    if uniq:
        return f"uniq:{uniq.upper()}"
    try:
        for link in Path("/dev/input/by-id").glob("*"):
            if link.resolve() == Path(path).resolve():
                return f"by-id:{link.name}"
    except OSError:
        pass
    return f"sysfs:{device_path}"


@dataclass
class ControllerPollResult:
    pressed: dict[str, bool]
    current: dict[str, bool]
    press_counts: dict[str, int] = field(default_factory=dict)


class ControllerInputManager:
    """Polls all local input sources and normalizes them to app button names."""

    def __init__(
        self,
        *,
        js_glob: str = "/dev/input/js*",
        evdev_glob: str = "/dev/input/event*",
        duplicate_press_window_seconds: float = DUPLICATE_PRESS_WINDOW_SECONDS,
        input_discovery_interval_seconds: float = INPUT_DISCOVERY_INTERVAL_SECONDS,
        input_discovery_clock: Callable[[], float] | None = None,
        on_controller_connected: Callable[[], None] | None = None,
        input_device_identity: Callable[[str], str | None] | None = None,
        controller_state_publisher: Any | None = None,
    ) -> None:
        self.js_glob = js_glob
        self.evdev_glob = evdev_glob
        self.duplicate_press_window_seconds = duplicate_press_window_seconds
        self.input_discovery_interval_seconds = max(
            0.0, float(input_discovery_interval_seconds)
        )
        self._input_discovery_clock = input_discovery_clock or time.monotonic
        self._next_input_discovery_at = 0.0
        self.on_controller_connected = on_controller_connected
        self._input_device_identity = (
            input_device_identity or _controller_identity
        )
        self._controller_identity_by_path: dict[str, str] = {}
        self._controller_paths_by_identity: dict[str, set[str]] = {}
        self._controller_slots: dict[str, int] = {}
        self._preferred_slots: dict[str, int] = {}
        self._controller_states: dict[str, dict[str, Any]] = {}
        self.controller_state_publisher = controller_state_publisher
        self.old_buttons = {button: False for button in APP_BUTTONS}
        self.current = {button: False for button in APP_BUTTONS}
        self.press_counts = {button: 0 for button in APP_BUTTONS}
        self.js_files: dict[str, BinaryIO] = {}
        self.ev_files: dict[str, BinaryIO] = {}
        self._last_press_at: dict[str, float] = {}
        self._axis_values: dict[tuple[str, int], float] = {}
        self._js_axes: dict[tuple[str, int], str | None] = {}
        self._ev_axes: dict[tuple[str, int], str | None] = {}
        self._ev_keys: dict[tuple[str, int], str] = {}
        self._ev_abs_ranges: dict[tuple[str, int], tuple[int, int]] = {}

    def poll(
        self,
        dartsnut: Any,
        *,
        consume_app_controls: bool = True,
        should_consume_button: Callable[[str], bool] | None = None,
        read_external_inputs: bool = True,
    ) -> ControllerPollResult:
        pressed = {button: False for button in APP_BUTTONS}
        self.press_counts = {button: 0 for button in APP_BUTTONS}
        self._poll_gpio(dartsnut, pressed)
        # Pico-8 owns SDL2 input directly.  When external reads are disabled,
        # leave joystick/evdev file descriptors untouched so SDL sees every
        # controller event (no broker-to-SDL translation or input grabbing).
        if read_external_inputs:
            self._discover_new_inputs_if_due()
            self._poll_js_files(
                pressed,
                consume_app_controls=consume_app_controls,
                should_consume_button=should_consume_button,
            )
            self._poll_evdev_files(
                pressed,
                consume_app_controls=consume_app_controls,
                should_consume_button=should_consume_button,
            )
        else:
            self._suspend_external_inputs()
        self._publish_controller_state()
        return ControllerPollResult(
            pressed=pressed,
            current=dict(self.current),
            press_counts=dict(self.press_counts),
        )

    def _publish_controller_state(self) -> None:
        publisher = self.controller_state_publisher
        if publisher is None:
            return
        states = self.get_controller_states()
        try:
            publisher.publish(states)
        except Exception as exc:
            _log.debug("Unable to publish controller state: %s", exc)

    def _discover_new_inputs_if_due(self) -> None:
        now = self._input_discovery_clock()
        if now < self._next_input_discovery_at:
            return
        self._next_input_discovery_at = (
            now + self.input_discovery_interval_seconds
        )
        self._open_new_inputs(self.js_glob, self.js_files)
        self._open_new_inputs(self.evdev_glob, self.ev_files)

    def _suspend_external_inputs(self) -> None:
        """Release firmware descriptors while Pico-8 owns native SDL input."""
        for path in list(self.js_files):
            self._close_input(path, self.js_files)
        for path in list(self.ev_files):
            self._close_input(path, self.ev_files)
        self._next_input_discovery_at = 0.0

    def _poll_gpio(self, dartsnut: Any, pressed: dict[str, bool]) -> None:
        try:
            gpio_reader = getattr(dartsnut, "get_gpio_buttons", None)
            button_states = (
                gpio_reader() if callable(gpio_reader) else dartsnut.get_buttons()
            )
        except Exception as e:
            _log.debug("Error reading GPIO buttons: %s", e)
            return
        if not isinstance(button_states, dict):
            return
        for button in APP_BUTTONS:
            if button not in button_states:
                continue
            state = bool(button_states.get(button, False))
            if state != self.old_buttons[button]:
                self.old_buttons[button] = state
                self.current[button] = state
                if state:
                    self._mark_pressed(button, pressed)
            else:
                self.current[button] = state

    def _open_new_inputs(self, pattern: str, files: dict[str, BinaryIO]) -> None:
        for path in glob.glob(pattern):
            if path in files:
                continue
            try:
                input_file = open(path, "rb")
                os.set_blocking(input_file.fileno(), False)
                files[path] = input_file
                _log.info("controller input opened: %s", path)
                self._register_controller_path(path)
                if path in self.ev_files:
                    self._read_evdev_axis_ranges(path, input_file)
            except OSError as e:
                _log.debug("Unable to open controller input %s: %s", path, e)

    def _poll_js_files(
        self,
        pressed: dict[str, bool],
        *,
        consume_app_controls: bool,
        should_consume_button: Callable[[str], bool] | None,
    ) -> None:
        for path in list(self.js_files.keys()):
            input_file = self.js_files[path]
            while True:
                try:
                    event_data = input_file.read(_JS_EVENT.size)
                    if event_data is None:
                        break
                    if not event_data:
                        raise OSError("Device disconnected")
                    if len(event_data) != _JS_EVENT.size:
                        continue
                    _time_ms, value, type_, number = _JS_EVENT.unpack(event_data)
                    event_kind = type_ & 0x7F
                    if event_kind == JS_EVENT_BUTTON:
                        button = JS_BUTTON_TO_APP.get(number)
                        if button is None:
                            continue
                        self._set_current(button, value != 0, path=path)
                        if value != 0:
                            self._press_if_allowed(
                                button,
                                pressed,
                                source_path=path,
                                consume_app_controls=consume_app_controls,
                                should_consume_button=should_consume_button,
                            )
                    elif event_kind == JS_EVENT_AXIS:
                        if number in (0, 1):
                            self._handle_axis(
                                source_axes=self._js_axes,
                                source_path=path,
                                number=number,
                                value=value,
                                x_axis=0,
                                y_axis=1,
                                pressed=pressed,
                                consume_app_controls=consume_app_controls,
                                should_consume_button=should_consume_button,
                                hat_axis=False,
                            )
                            continue
                        self._handle_axis(
                            source_axes=self._js_axes,
                            source_path=path,
                            number=number,
                            value=value,
                            x_axis=6,
                            y_axis=7,
                            pressed=pressed,
                            consume_app_controls=consume_app_controls,
                            should_consume_button=should_consume_button,
                            hat_axis=False,
                            publish_axis=False,
                        )
                except (BlockingIOError, InterruptedError):
                    break
                except Exception as e:
                    _log.debug("controller joystick input closed %s: %s", path, e)
                    self._close_input(path, self.js_files)
                    break

    def _poll_evdev_files(
        self,
        pressed: dict[str, bool],
        *,
        consume_app_controls: bool,
        should_consume_button: Callable[[str], bool] | None,
    ) -> None:
        for path in list(self.ev_files.keys()):
            input_file = self.ev_files[path]
            while True:
                try:
                    event_data = input_file.read(_EVDEV_EVENT.size)
                    if event_data is None:
                        break
                    if not event_data:
                        raise OSError("Device disconnected")
                    if len(event_data) != _EVDEV_EVENT.size:
                        continue
                    _sec, _usec, event_type, code, value = _EVDEV_EVENT.unpack(event_data)
                    if event_type == EV_KEY:
                        self._handle_evdev_key(
                            path,
                            code,
                            value,
                            pressed,
                            consume_app_controls=consume_app_controls,
                            should_consume_button=should_consume_button,
                        )
                    elif event_type == EV_ABS:
                        if code in (ABS_HAT0X, ABS_HAT0Y, ABS_X, ABS_Y):
                            self._handle_axis(
                                source_axes=self._ev_axes,
                                source_path=path,
                                number=code,
                                value=(
                                    value
                                    if code in (ABS_HAT0X, ABS_HAT0Y)
                                    else round(self._normalize_evdev_axis(path, code, value) * 32767)
                                ),
                                x_axis=ABS_X,
                                y_axis=ABS_Y,
                                hat_x_axis=ABS_HAT0X,
                                hat_y_axis=ABS_HAT0Y,
                                pressed=pressed,
                                consume_app_controls=consume_app_controls,
                                should_consume_button=should_consume_button,
                                hat_axis=code in (ABS_HAT0X, ABS_HAT0Y),
                            )
                except (BlockingIOError, InterruptedError):
                    break
                except Exception as e:
                    _log.debug("controller evdev input closed %s: %s", path, e)
                    self._close_input(path, self.ev_files)
                    break

    def _handle_evdev_key(
        self,
        path: str,
        code: int,
        value: int,
        pressed: dict[str, bool],
        *,
        consume_app_controls: bool,
        should_consume_button: Callable[[str], bool] | None,
    ) -> None:
        button = EV_KEY_TO_APP.get(code)
        if button is None:
            return
        if value == 2:
            return
        key = (path, code)
        if value:
            self._ev_keys[key] = button
            self._set_current(button, True, path=path)
            self._press_if_allowed(
                button,
                pressed,
                source_path=path,
                consume_app_controls=consume_app_controls,
                should_consume_button=should_consume_button,
            )
        else:
            self._ev_keys.pop(key, None)
            self._set_current(button, self._is_button_held_elsewhere(button), path=path)

    def _handle_axis(
        self,
        *,
        source_axes: dict[tuple[str, int], str | None],
        source_path: str,
        number: int,
        value: int,
        x_axis: int,
        y_axis: int,
        pressed: dict[str, bool],
        consume_app_controls: bool,
        should_consume_button: Callable[[str], bool] | None,
        hat_axis: bool,
        hat_x_axis: int | None = None,
        hat_y_axis: int | None = None,
        publish_axis: bool = True,
    ) -> None:
        axis_key = (source_path, number)
        identity = self._controller_identity_by_path.get(source_path)
        # Hats are digital D-pad sources. They must not overwrite raw
        # left-stick axes exported to games.
        if identity and publish_axis and not hat_axis:
            state = self._controller_states.setdefault(identity, self._new_controller_state())
            normalized = (float(value) if hat_axis else float(value) / 32767.0)
            normalized = max(-1.0, min(1.0, normalized))
            if number == x_axis or number == hat_x_axis:
                state["axes"]["x"] = normalized
            elif number == y_axis or number == hat_y_axis:
                state["axes"]["y"] = normalized
        previous_button = source_axes.get(axis_key)
        next_button = self._axis_button(
            number,
            value,
            x_axis=x_axis,
            y_axis=y_axis,
            hat_axis=hat_axis,
            hat_x_axis=hat_x_axis,
            hat_y_axis=hat_y_axis,
        )
        if previous_button == next_button:
            return
        if previous_button:
            self._set_current(
                previous_button,
                self._is_button_held_elsewhere(previous_button, excluding_axis=axis_key),
                path=source_path,
            )
        source_axes[axis_key] = next_button
        if next_button:
            self._set_current(next_button, True, path=source_path)
            self._press_if_allowed(
                next_button,
                pressed,
                source_path=source_path,
                consume_app_controls=consume_app_controls,
                should_consume_button=should_consume_button,
            )

    def _axis_button(
        self,
        number: int,
        value: int,
        *,
        x_axis: int,
        y_axis: int,
        hat_axis: bool,
        hat_x_axis: int | None,
        hat_y_axis: int | None,
    ) -> str | None:
        if hat_axis:
            if number == hat_x_axis:
                if value < 0:
                    return "btn_left"
                if value > 0:
                    return "btn_right"
            elif number == hat_y_axis:
                if value < 0:
                    return "btn_up"
                if value > 0:
                    return "btn_down"
            return None
        if number == x_axis:
            if value < -AXIS_THRESHOLD:
                return "btn_left"
            if value > AXIS_THRESHOLD:
                return "btn_right"
        elif number == y_axis:
            if value < -AXIS_THRESHOLD:
                return "btn_up"
            if value > AXIS_THRESHOLD:
                return "btn_down"
        return None

    @staticmethod
    def _eviocgabs(code: int) -> int:
        return (2 << 30) | (_ABS_INFO.size << 16) | (ord("E") << 8) | (0x40 + code)

    def _read_evdev_axis_ranges(self, path: str, input_file: BinaryIO) -> None:
        for code in (ABS_X, ABS_Y):
            buffer = bytearray(_ABS_INFO.size)
            try:
                fcntl.ioctl(input_file.fileno(), self._eviocgabs(code), buffer, True)
                _value, minimum, maximum, _fuzz, _flat, _resolution = _ABS_INFO.unpack(buffer)
                if maximum > minimum:
                    self._ev_abs_ranges[(path, code)] = (minimum, maximum)
            except (OSError, ValueError):
                continue

    def _normalize_evdev_axis(self, path: str, code: int, value: int) -> float:
        minimum, maximum = self._ev_abs_ranges.get((path, code), (-32767, 32767))
        center = (minimum + maximum) / 2.0
        span = (maximum - minimum) / 2.0
        if span <= 0:
            return 0.0
        return max(-1.0, min(1.0, (float(value) - center) / span))

    def _press_if_allowed(
        self,
        button: str,
        pressed: dict[str, bool],
        *,
        source_path: str | None = None,
        consume_app_controls: bool,
        should_consume_button: Callable[[str], bool] | None,
    ) -> None:
        if source_path is not None and not self._path_within_capacity(source_path):
            return
        # Broker event counters are gameplay data and must advance even when
        # firmware UI consumption is disabled (Python games read them from
        # shared memory independently).
        self._record_controller_press(button, source_path)
        if should_consume_button is not None and not should_consume_button(button):
            return
        if consume_app_controls or button == "btn_home":
            self._mark_pressed(button, pressed, source_path=source_path, record_controller=False)

    def _mark_pressed(
        self,
        button: str,
        pressed: dict[str, bool],
        *,
        source_path: str | None = None,
        record_controller: bool = True,
    ) -> None:
        now = time.monotonic()
        identity = self._controller_identity_by_path.get(source_path or "")
        key = f"{identity or 'gpio'}:{button}"
        last_press = self._last_press_at.get(key)
        if (
            last_press is not None
            and now - last_press < self.duplicate_press_window_seconds
        ):
            return
        self._last_press_at[key] = now
        pressed[button] = True
        self.press_counts[button] += 1
        if identity and record_controller:
            self._record_controller_press(button, source_path)

    def _record_controller_press(self, button: str, source_path: str | None) -> None:
        identity = self._controller_identity_by_path.get(source_path or "")
        if identity is None:
            return
        state = self._controller_states.setdefault(identity, self._new_controller_state())
        now = time.monotonic()
        key = f"broker:{identity}:{button}"
        last_press = self._last_press_at.get(key)
        if last_press is not None and now - last_press < self.duplicate_press_window_seconds:
            return
        self._last_press_at[key] = now
        state["press_counts"][button] += 1

    def _set_current(self, button: str, state: bool, *, path: str | None = None) -> None:
        if path is not None and not self._path_within_capacity(path):
            return
        if path is not None:
            identity = self._controller_identity_by_path.get(path)
            if identity:
                controller = self._controller_states.setdefault(identity, self._new_controller_state())
                controller["buttons"][button] = bool(state)
                self.current[button] = bool(self.old_buttons.get(button, False)) or any(
                    item["buttons"].get(button, False)
                    for key, item in self._controller_states.items()
                    if key in self._controller_slots
                    and self._controller_paths_by_identity.get(key)
                )
                return
        if button in self.current:
            self.current[button] = state

    @staticmethod
    def _new_controller_state() -> dict[str, Any]:
        return {
            "buttons": {button: False for button in APP_BUTTONS},
            "axes": {"x": 0.0, "y": 0.0},
            "press_counts": {button: 0 for button in APP_BUTTONS},
        }

    def _path_within_capacity(self, path: str) -> bool:
        identity = self._controller_identity_by_path.get(path)
        if identity is None:
            return True
        return identity in self._controller_slots

    def _is_button_held_elsewhere(
        self,
        button: str,
        *,
        excluding_axis: tuple[str, int] | None = None,
    ) -> bool:
        if self.old_buttons.get(button):
            return True
        if button in self._ev_keys.values():
            return True
        for axis_key, axis_button in self._js_axes.items():
            if axis_key != excluding_axis and axis_button == button:
                return True
        for axis_key, axis_button in self._ev_axes.items():
            if axis_key != excluding_axis and axis_button == button:
                return True
        return False

    def _register_controller_path(self, path: str) -> None:
        try:
            identity = self._input_device_identity(path)
        except Exception as e:
            _log.debug("Unable to identify controller input %s: %s", path, e)
            return
        if not identity:
            return

        paths = self._controller_paths_by_identity.setdefault(identity, set())
        is_new_connection = not paths
        paths.add(path)
        self._controller_identity_by_path[path] = identity
        self._controller_states.setdefault(identity, self._new_controller_state())
        if is_new_connection:
            occupied = set(self._controller_slots.values())
            preferred = self._preferred_slots.get(identity)
            if preferred is not None and preferred not in occupied:
                slot = preferred
            else:
                slot = next((i for i in range(4) if i not in occupied), None)
            if slot is None:
                _log.warning("Controller capacity exceeded; ignoring gameplay for %s", identity)
            else:
                self._controller_slots[identity] = slot
                self._preferred_slots[identity] = slot
        if is_new_connection and self.on_controller_connected is not None:
            try:
                self.on_controller_connected()
            except Exception as e:
                _log.warning("Controller connection callback failed: %s", e)

    def _unregister_controller_path(self, path: str) -> None:
        identity = self._controller_identity_by_path.pop(path, None)
        if identity is None:
            return
        paths = self._controller_paths_by_identity.get(identity)
        if paths is None:
            return
        paths.discard(path)
        if not paths:
            self._controller_paths_by_identity.pop(identity, None)
            self._controller_slots.pop(identity, None)
            state = self._controller_states.get(identity)
            if state:
                state["buttons"] = {button: False for button in APP_BUTTONS}
                state["axes"] = {"x": 0.0, "y": 0.0}

    def get_controller_states(self) -> list[dict[str, Any]]:
        """Return active per-controller snapshots for firmware/UI consumers."""
        result = []
        for identity, state in self._controller_states.items():
            if not self._controller_paths_by_identity.get(identity):
                continue
            if identity not in self._controller_slots:
                continue
            result.append({
                "identity": identity,
                "slot": self._controller_slots[identity],
                "order": self._controller_slots[identity],
                "buttons": dict(state["buttons"]),
                "axes": dict(state["axes"]),
                "press_counts": dict(state["press_counts"]),
            })
        result.sort(key=lambda item: item["order"])
        return result


    def _close_input(self, path: str, files: dict[str, BinaryIO]) -> None:
        input_file = files.pop(path, None)
        if input_file is None:
            return
        self._unregister_controller_path(path)
        self._ev_abs_ranges.pop((path, ABS_X), None)
        self._ev_abs_ranges.pop((path, ABS_Y), None)
        try:
            input_file.close()
        except Exception:
            pass

    def close(self) -> None:
        """Close all physical input descriptors owned by this manager."""
        self._suspend_external_inputs()
