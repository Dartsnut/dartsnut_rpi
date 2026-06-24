"""Tests for placeholder preview generation."""
import pytest


def test_generate_placeholder_returns_single_frame():
    from game_lifecycle import generate_placeholder_preview
    result = generate_placeholder_preview("Test Game", "Loading Preview")
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], bytearray)


def test_generate_placeholder_correct_size():
    from game_lifecycle import generate_placeholder_preview
    result = generate_placeholder_preview("My Game", "Preview unavailable")
    # 128 * 160 * 3 bytes = RGB data for 128x160 image
    assert len(result[0]) == 128 * 160 * 3


def test_generate_placeholder_not_all_black():
    """Placeholder with a game name should have some non-black pixels (text)."""
    from game_lifecycle import generate_placeholder_preview
    result = generate_placeholder_preview("A" * 20, "Loading Preview")
    # There should be at least some non-zero pixels from the text
    has_nonzero = any(b != 0 for b in result[0])
    assert has_nonzero


def test_generate_placeholder_text_stays_in_top_preview_area():
    from game_lifecycle import generate_placeholder_preview
    result = generate_placeholder_preview("Test Game", "Loading Preview")[0]
    rows_with_text = [
        y
        for y in range(160)
        if any(result[(y * 128 + x) * 3 + c] != 0 for x in range(128) for c in range(3))
    ]
    assert rows_with_text
    assert min(rows_with_text) >= 0
    assert max(rows_with_text) < 128


def test_is_blank_frame_detects_black():
    from game_lifecycle import _is_blank_frame
    blank = bytearray(128 * 160 * 3)  # all zeros = black
    assert _is_blank_frame(blank) is True


def test_is_blank_frame_detects_nonblank():
    from game_lifecycle import _is_blank_frame
    frame = bytearray(128 * 160 * 3)
    frame[100] = 255  # one red pixel
    assert _is_blank_frame(frame) is False
