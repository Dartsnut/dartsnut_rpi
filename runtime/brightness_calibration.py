"""Non-blocking full-white ghost sweep used by Display calibration."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from PIL import Image

from runtime.brightness import select_evenly

WIDTH = 128
HEIGHT = 160
WHITE_FRAME = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
BRIGHTNESS_MAX = 100
BRIGHTNESS_MIN = 10
BASELINE_TIMEOUT_SECONDS = 5.0
PASS_DURATION_SECONDS = 300.0
POLL_SECONDS = 0.005
SWEEP_LEVEL_COUNT = 10


def sweep_brightness_values(brightness_min: int, brightness_max: int) -> list[int]:
    """Return ten evenly spaced raw levels, highest first."""
    low = min(int(brightness_min), int(brightness_max))
    high = max(int(brightness_min), int(brightness_max))
    if low == high:
        return [high]
    values = [
        round(high - index * (high - low) / (SWEEP_LEVEL_COUNT - 1))
        for index in range(SWEEP_LEVEL_COUNT)
    ]
    return list(dict.fromkeys(values))


@dataclass
class CalibrationStatus:
    state: str = "idle"
    current_brightness: int | None = None
    completed: int = 0
    total: int = SWEEP_LEVEL_COUNT
    passed: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    error: str = ""
    values: list[int] = field(default_factory=list)


def _is_clear(sample: Any) -> bool:
    if isinstance(sample, (bytes, bytearray, memoryview)):
        return bytes(sample) == bytes([0xFF] * 48)
    try:
        return all(
            isinstance(dart, (list, tuple))
            and len(dart) >= 2
            and int(dart[0]) < 0
            and int(dart[1]) < 0
            for dart in sample
        )
    except (TypeError, ValueError):
        return False


class BrightnessCalibration:
    """Own one cancellable sweep thread and expose immutable-ish status snapshots."""

    def __init__(
        self,
        *,
        frame_writer: Callable[[Image.Image], Any],
        brightness_setter: Callable[[int], Any],
        dart_reader: Callable[[], Any],
        on_success: Callable[[list[int]], Any],
        brightness_min: int = BRIGHTNESS_MIN,
        brightness_max: int = BRIGHTNESS_MAX,
        baseline_timeout_seconds: float = BASELINE_TIMEOUT_SECONDS,
        pass_duration_seconds: float = PASS_DURATION_SECONDS,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._frame_writer = frame_writer
        self._brightness_setter = brightness_setter
        self._dart_reader = dart_reader
        self._on_success = on_success
        self._minimum = min(brightness_min, brightness_max)
        self._maximum = max(brightness_min, brightness_max)
        self._sweep_values = sweep_brightness_values(self._minimum, self._maximum)
        self._baseline_timeout = max(0.0, baseline_timeout_seconds)
        self._pass_duration = max(0.0, pass_duration_seconds)
        self._poll_seconds = max(0.001, poll_seconds)
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._status = CalibrationStatus(total=len(self._sweep_values))
        self._thread: threading.Thread | None = None
        self._prior_brightness: int | None = None
        self._prior_frame: Any = None

    @property
    def status(self) -> CalibrationStatus:
        with self._lock:
            status = self._status
            return CalibrationStatus(
                state=status.state,
                current_brightness=status.current_brightness,
                completed=status.completed,
                total=status.total,
                passed=list(status.passed),
                failed=list(status.failed),
                error=status.error,
                values=list(status.values),
            )

    def start(self, prior_brightness: int | None = None, prior_frame: Any = None) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._cancel.clear()
            self._prior_brightness = prior_brightness
            self._prior_frame = prior_frame
            self._status = CalibrationStatus(
                state="running", total=len(self._sweep_values)
            )
            self._thread = threading.Thread(target=self._run, daemon=True, name="brightness-calibration")
            self._thread.start()
            return True

    def cancel(self) -> None:
        self._cancel.set()

    def _set_status(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._status, key, value)

    def _keep_white(self) -> None:
        self._frame_writer(WHITE_FRAME)

    def _wait_for_clear(self) -> bool:
        deadline = time.monotonic() + self._baseline_timeout
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return False
            self._keep_white()
            if _is_clear(self._dart_reader()):
                return True
            time.sleep(self._poll_seconds)
        return False

    def _run_pass(self, brightness: int) -> bool:
        self._brightness_setter(brightness)
        if not self._wait_for_clear():
            return False
        deadline = time.monotonic() + self._pass_duration
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return False
            self._keep_white()
            if not _is_clear(self._dart_reader()):
                return False
            time.sleep(self._poll_seconds)
        return True

    def _run(self) -> None:
        passed: list[int] = []
        failed: list[int] = []
        values: list[int] | None = None
        success = False
        try:
            for offset, brightness in enumerate(self._sweep_values, start=1):
                self._set_status(current_brightness=brightness, completed=offset)
                if self._cancel.is_set():
                    self._set_status(state="cancelled")
                    return
                if self._run_pass(brightness):
                    passed.append(brightness)
                else:
                    failed.append(brightness)
                self._set_status(passed=list(passed), failed=list(failed))
            if self._cancel.is_set():
                self._set_status(state="cancelled")
                return
            values = select_evenly(passed)
            success = True
        except Exception as exc:
            self._set_status(state="error", error=str(exc))
        finally:
            if self._prior_frame is not None:
                try:
                    self._frame_writer(self._prior_frame)
                except Exception:
                    pass
            if self._prior_brightness is not None:
                try:
                    self._brightness_setter(self._prior_brightness)
                except Exception:
                    pass
        if success and values is not None:
            try:
                self._on_success(values)
                self._set_status(state="success", values=values)
            except Exception as exc:
                self._set_status(state="error", error=str(exc))
