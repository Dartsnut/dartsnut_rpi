"""
Single root logging configuration for the dartsnut_python.service process.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure_logging() -> str:
    """
    Attach one StreamHandler to the root logger with a line-oriented format.
    Idempotent: safe to call multiple times.

    Reads DARTSNUT_LOG_LEVEL (default INFO). Invalid values fall back to INFO.

    Returns the normalized level name in effect (e.g. "INFO", "DEBUG").
    """
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        return logging.getLevelName(root.getEffectiveLevel())

    raw = (os.environ.get("DARTSNUT_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, raw, None)
    if not isinstance(level, int):
        level = logging.INFO
        raw = "INFO"

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)

    # PIL can be chatty at DEBUG when decoding images for widgets / framebuffer.
    logging.getLogger("PIL").setLevel(logging.WARNING)

    tune_third_party_logging()

    _CONFIGURED = True
    return raw


def _bluezero_logger_names() -> list[str]:
    mgr = logging.Logger.manager.loggerDict
    return [k for k in mgr if isinstance(k, str) and k.startswith("bluezero")]


def tune_third_party_logging() -> None:
    """
    Cap noisy framework loggers and strip duplicate handlers on bluezero loggers
    (bluezero installs its own StreamHandler; without this, each message appears twice).
    Safe to call multiple times (e.g. before BLE GATT registration).
    """
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in _bluezero_logger_names():
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
