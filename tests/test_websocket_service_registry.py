import sys
import types

sys.modules.setdefault("bluetooth", types.SimpleNamespace())

from runtime.websocket_service_registry import (
    _adapt,
    build_default_machine_registry,
    build_default_websocket_registry,
)


def test_adapt_wraps_exceptions_into_error_shape():
    wrapped = _adapt("x_action", lambda: (_ for _ in ()).throw(ValueError("boom")), "context")
    result = wrapped()
    assert result["action"] == "x_action"
    assert result["error_code"] == "3001"


def test_build_default_machine_registry_alias():
    ws = build_default_websocket_registry()
    machine = build_default_machine_registry()
    assert machine.file_ops is not None
    assert ws.json_ops is not None


def test_registry_uses_custom_firmware_update_callable():
    calls = []
    registry = build_default_websocket_registry(
        perform_update=lambda: calls.append("update") or {"message": "ok"}
    )
    assert registry.git_ops.perform_update() == {"message": "ok"}
    assert calls == ["update"]
