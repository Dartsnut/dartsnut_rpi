"""Tests for runtime.logging_config (no hardware)."""

import importlib
import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logging():
    """Isolate tests from each other and from default logging state."""
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logging.root.setLevel(logging.WARNING)
    yield
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logging.root.setLevel(logging.WARNING)


def test_configure_logging_idempotent():
    import runtime.logging_config as lc

    importlib.reload(lc)
    lc.configure_logging()
    first = len(logging.root.handlers)
    lc.configure_logging()
    second = len(logging.root.handlers)
    assert first == second == 1


def test_configure_logging_invalid_level_falls_back_to_info(monkeypatch):
    import runtime.logging_config as lc

    monkeypatch.setenv("DARTSNUT_LOG_LEVEL", "not_a_real_level")
    importlib.reload(lc)
    name = lc.configure_logging()
    assert name == "INFO"
    assert logging.getLogger().level == logging.INFO
