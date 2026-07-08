import base64
import json

import pytest
from PIL import Image

from mcp_server import framebuffer, state_snapshot, tools


def test_mcp_initialize_and_tools_list():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from mcp_server import app as mcp_app

    client = TestClient(mcp_app.app)

    init = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    listed = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    assert init.status_code == 200
    assert init.json()["result"]["protocolVersion"] == "2025-06-18"
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "capture_screen" in names
    assert "run_script" in names


def test_mcp_unknown_tool_returns_tool_error():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from mcp_server import app as mcp_app

    client = TestClient(mcp_app.app)

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "missing", "arguments": {}},
        },
    )

    body = response.json()
    assert body["result"]["isError"] is True
    assert "Unknown tool" in body["result"]["content"][0]["text"]


def test_capture_screen_crops_expected_regions(tmp_path, monkeypatch):
    fb_path = tmp_path / "fb"
    data = bytearray()
    for y in range(framebuffer.FRAMEBUFFER_HEIGHT):
        for x in range(framebuffer.FRAMEBUFFER_WIDTH):
            data.extend([x % 256, y % 256, (x + y) % 256])
    fb_path.write_bytes(bytes(data))
    monkeypatch.setenv("DARTSNUT_MCP_FRAMEBUFFER_PATH", str(fb_path))

    top = framebuffer.capture_screen("top")
    bottom = framebuffer.capture_screen("bottom")
    full = framebuffer.capture_screen("full")

    assert (top.width, top.height) == (128, 128)
    assert (bottom.width, bottom.height) == (64, 32)
    assert (full.width, full.height) == (128, 160)

    decoded = base64.b64decode(bottom.data)
    image_path = tmp_path / "bottom.png"
    image_path.write_bytes(decoded)
    img = Image.open(image_path)
    assert img.getpixel((0, 0)) == (0, 128, 128)


def test_run_script_uses_allowlist_and_rejects_traversal(tmp_path, monkeypatch):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / "ok.sh"
    script.write_text("#!/bin/sh\necho script:$1\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(tools, "SCRIPT_DIR", str(script_dir))

    result = tools.tool_run_script({"name": "ok.sh", "args": ["arg"], "timeout_seconds": 5})
    assert result["structuredContent"]["returncode"] == 0
    assert result["structuredContent"]["stdout"] == "script:arg\n"


def test_run_script_traversal_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRIPT_DIR", str(tmp_path))
    try:
        tools.tool_run_script({"name": "../bad.sh"})
    except tools.ToolError as exc:
        assert "inside" in str(exc)
    else:
        raise AssertionError("expected ToolError")


def test_get_ui_state_adds_network_diagnostics(tmp_path, monkeypatch):
    snapshot = tmp_path / "state.json"
    snapshot.write_text(json.dumps({"state": "menu"}), encoding="utf-8")
    monkeypatch.setattr(state_snapshot, "SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(
        tools,
        "_network_diagnostics",
        lambda: {
            "ip_address": "192.168.1.20",
            "ssid": "wifi",
            "wifi_associated": True,
            "pings": {"1.1.1.1": {"ok": True, "returncode": 0}},
            "problems": [],
        },
    )

    result = tools.tool_get_ui_state({})

    assert result["structuredContent"]["state"] == "menu"
    assert result["structuredContent"]["network"]["ip_address"] == "192.168.1.20"


def test_service_install_scripts_reference_mcp_service():
    setup = open("setup.sh", encoding="utf-8").read()
    update = open("update.sh", encoding="utf-8").read()
    service = open("services/dartsnut_mcp.service", encoding="utf-8").read()
    repair_service = open("services/dartsnut_update_repair.service", encoding="utf-8").read()
    mcp_definition = json.load(open("mcp/dartsnut-firmware.mcp.json", encoding="utf-8"))

    assert "dartsnut_mcp.service" in setup
    assert "dartsnut_mcp.service" in update
    assert "dartsnut_update_repair.service" in setup
    assert "dartsnut_update_repair.service" in update
    assert "rollback_rpi_kernel_6_12.sh" in setup
    assert "rollback_rpi_kernel_6_12.sh" in update
    assert "repair_device_json.sh" in setup
    assert "repair_device_json.sh" in update
    assert 'git -C "${REPO_DIR}" diff --quiet "${before_ref}" HEAD -- DartsnutRGBMatrix' in update
    assert update.index("restart dartsnut_matrix.service") < update.index("restart dartsnut_python.service")
    assert "./update.sh" in repair_service
    assert "check_and_update.py" not in repair_service
    assert "dartsnut_update_pending" in repair_service
    assert "Restart=on-failure" in repair_service
    assert "RestartSec=60s" in repair_service
    assert "StartLimitIntervalSec=0" in repair_service
    assert "--port 9252" in service
    assert "SuccessExitStatus=143" in service
    assert update.index("refresh_uv_project") < update.index("restart dartsnut_mcp.service")
    assert mcp_definition == {
        "mcpServers": {
            "dartsnut-firmware": {
                "type": "http",
                "url": "${DARTSNUT_MACHINE_URL:-http://127.0.0.1:9252}/mcp",
            }
        }
    }


def test_kernel_rollback_check_order_matches_calling_context():
    setup = open("setup.sh", encoding="utf-8").read().strip()
    update = open("update.sh", encoding="utf-8").read().strip()

    final_check = """echo "== Final kernel compatibility check =="

if [ -x "${KERNEL_ROLLBACK_SCRIPT}" ]; then
    "${KERNEL_ROLLBACK_SCRIPT}" || exit $?
else
    echo "Warning: ${KERNEL_ROLLBACK_SCRIPT} not found or not executable; skipping kernel compatibility check."
fi"""

    assert setup.endswith(final_check)
    assert update.index(final_check) < update.index("restart dartsnut_python.service")


def test_update_script_can_defer_terminal_actions_for_python_orchestration():
    update = open("update.sh", encoding="utf-8").read()
    defer_block = """if [ "${DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS:-0}" = "1" ]; then
    echo "Terminal update actions deferred; caller will handle kernel rollback and service restarts."
    exit 0
fi"""
    final_check = 'echo "== Final kernel compatibility check =="'

    assert defer_block in update
    assert update.index(defer_block) < update.index(final_check)
    assert update.index(defer_block) < update.index("restart dartsnut_python.service")
