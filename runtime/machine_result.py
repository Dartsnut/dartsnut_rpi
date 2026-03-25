from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MachineResult:
    """Transport-agnostic machine action result."""

    payload: dict[str, Any]


def to_websocket_payload(result: MachineResult | dict[str, Any]) -> dict[str, Any]:
    """Serialize machine result into the existing websocket response shape."""
    if isinstance(result, MachineResult):
        return result.payload
    return result
