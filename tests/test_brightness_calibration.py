from __future__ import annotations

import json
import time

from runtime.brightness import (
    calibrated_raw_for_index,
    load_calibration,
    load_calibration_state,
    nearest_index,
    save_brightness_level,
    save_calibration,
    select_evenly,
)
from runtime.brightness_calibration import BrightnessCalibration
from runtime.brightness_calibration import sweep_brightness_values


def test_calibration_file_isolated_and_atomic(tmp_path):
    calibration_path = tmp_path / "brightness_calibration.json"
    device_path = tmp_path / "device.json"
    device_path.write_text('{"brightness":"45"}', encoding="utf-8")
    before = device_path.read_bytes()

    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    save_calibration(values, {"hardware_version": "444f"}, str(calibration_path), current_level=4)

    assert device_path.read_bytes() == before
    assert load_calibration({"hardware_version": "444f"}, str(calibration_path)) == values
    assert load_calibration({"hardware_version": "444e"}, str(calibration_path)) is None
    assert not list(tmp_path.glob(".brightness_calibration.*.tmp"))


def test_level_update_stays_in_calibration_file(tmp_path):
    path = tmp_path / "brightness_calibration.json"
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    save_calibration(values, path=str(path), current_level=1)
    save_brightness_level(8, path=str(path))
    assert load_calibration_state(path=str(path))["current_level"] == 8


def test_mapping_and_even_pass_selection():
    assert nearest_index(44, [10, 20, 40, 60]) == 2
    assert select_evenly(range(10, 101)) == [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def test_sweep_tests_every_raw_brightness_level():
    assert sweep_brightness_values(10, 100) == list(range(100, 9, -1))


def test_sweep_detects_hit_and_saves_passing_values():
    frames = []
    brightness_calls = []
    saved = []
    samples = iter([[[ -1, -1 ]] * 12, [[0, 0]] + [[-1, -1]] * 11] * 100)
    calibration = BrightnessCalibration(
        frame_writer=lambda frame: frames.append(frame.copy()),
        brightness_setter=brightness_calls.append,
        dart_reader=lambda: next(samples, [[-1, -1]] * 12),
        on_success=saved.append,
        brightness_min=10,
        brightness_max=19,
        baseline_timeout_seconds=0.002,
        pass_duration_seconds=0,
        poll_seconds=0.001,
    )
    assert calibration.start(prior_brightness=45)
    deadline = time.monotonic() + 1
    while calibration.status.state == "running" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert calibration.status.state == "success"
    assert saved == [[10, 11, 12, 13, 14, 15, 16, 17, 18, 19]]
    assert brightness_calls[-1] == 45
    assert frames


def test_cancel_restores_prior_brightness_without_save():
    brightness_calls = []
    saved = []
    calibration = BrightnessCalibration(
        frame_writer=lambda _frame: None,
        brightness_setter=brightness_calls.append,
        dart_reader=lambda: [[-1, -1]] * 12,
        on_success=saved.append,
        brightness_min=10,
        brightness_max=100,
        baseline_timeout_seconds=0.01,
        pass_duration_seconds=1,
        poll_seconds=0.001,
    )
    calibration.start(prior_brightness=45)
    calibration.cancel()
    deadline = time.monotonic() + 1
    while calibration.status.state == "running" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert calibration.status.state == "cancelled"
    assert brightness_calls[-1] == 45
    assert saved == []
