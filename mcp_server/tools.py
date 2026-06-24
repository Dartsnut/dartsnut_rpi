from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from mcp_server import framebuffer, git_ops, state_snapshot

MAIN_SERVICE = "dartsnut_python.service"
SCRIPT_DIR = os.path.abspath(
    os.getenv(
        "DARTSNUT_MCP_SCRIPT_DIR",
        os.path.join(git_ops.REPO_DIR, "scripts", "mcp_tools"),
    )
)


class ToolError(Exception):
    pass


def _run_command(args: list[str], *, timeout: float = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Command timed out after {timeout:g}s") from exc
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _network_diagnostics() -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    ip = _run_command(["hostname", "-I"], timeout=5)
    diagnostics["ip_address"] = ip["stdout"].strip().split(" ")[0] if ip["stdout"].strip() else ""

    ssid = _run_command(["iwgetid", "-r"], timeout=5)
    diagnostics["ssid"] = ssid["stdout"].strip() if ssid["returncode"] == 0 else ""
    diagnostics["wifi_associated"] = ssid["returncode"] == 0

    ping_targets = ["1.1.1.1", "api.github.com"]
    pings = {}
    for target in ping_targets:
        result = _run_command(["ping", "-c", "1", "-W", "2", target], timeout=5)
        pings[target] = {
            "ok": result["returncode"] == 0,
            "returncode": result["returncode"],
        }
    diagnostics["pings"] = pings
    diagnostics["problems"] = [
        name
        for name, problem in {
            "no_ip_address": not diagnostics["ip_address"],
            "wifi_not_associated": not diagnostics["wifi_associated"],
            "internet_ping_failed": not any(item["ok"] for item in pings.values()),
        }.items()
        if problem
    ]
    return diagnostics


def _text_result(text: str, structured: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _json_result(data: dict[str, Any]) -> dict[str, Any]:
    return _text_result(json.dumps(data, sort_keys=True), data)


def tool_capture_screen(arguments: dict[str, Any]) -> dict[str, Any]:
    captured = framebuffer.capture_screen(
        surface=str(arguments.get("surface") or "full"),
        image_format=str(arguments.get("format") or "png"),
    )
    structured = {
        "surface": captured.surface,
        "width": captured.width,
        "height": captured.height,
        "mime_type": captured.mime_type,
    }
    return {
        "content": [
            {"type": "image", "mimeType": captured.mime_type, "data": captured.data},
            {"type": "text", "text": json.dumps(structured, sort_keys=True)},
        ],
        "structuredContent": structured,
    }


def tool_restart_main_service(_arguments: dict[str, Any]) -> dict[str, Any]:
    restart = _run_command(["systemctl", "restart", MAIN_SERVICE], timeout=60)
    status = _run_command(["systemctl", "is-active", MAIN_SERVICE], timeout=10)
    data = {
        "restart": restart,
        "active_state": status["stdout"].strip(),
        "status_returncode": status["returncode"],
    }
    return _json_result(data)


def tool_get_main_service_logs(arguments: dict[str, Any]) -> dict[str, Any]:
    tail = int(arguments.get("tail") or 200)
    if tail < 1:
        raise ToolError("tail must be at least 1")
    tail = min(tail, 2000)
    args = ["journalctl", "-u", MAIN_SERVICE, "--no-pager", "-n", str(tail)]
    since = str(arguments.get("since") or "").strip()
    if since:
        args.extend(["--since", since])
    result = _run_command(args, timeout=30)
    return _text_result(result["stdout"], {"tail": tail, "since": since, **result})


def tool_get_ui_state(_arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        data = state_snapshot.read_snapshot()
    except Exception as exc:
        raise ToolError(f"Unable to read UI state snapshot: {exc}") from exc
    data["network"] = _network_diagnostics()
    return _json_result(data)


def _resolve_script(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        raise ToolError("script name is required")
    candidate = os.path.abspath(os.path.join(SCRIPT_DIR, raw))
    if not candidate.startswith(SCRIPT_DIR + os.sep):
        raise ToolError("script path must stay inside the MCP script directory")
    if not os.path.isfile(candidate):
        raise ToolError(f"script not found: {raw}")
    if not os.access(candidate, os.X_OK):
        raise ToolError(f"script is not executable: {raw}")
    return candidate


def tool_run_script(arguments: dict[str, Any]) -> dict[str, Any]:
    script = _resolve_script(str(arguments.get("name") or ""))
    raw_args = arguments.get("args") or []
    if not isinstance(raw_args, list) or not all(isinstance(v, str) for v in raw_args):
        raise ToolError("args must be a list of strings")
    timeout = float(arguments.get("timeout_seconds") or 30)
    if timeout <= 0 or timeout > 300:
        raise ToolError("timeout_seconds must be between 1 and 300")
    result = _run_command([script, *raw_args], timeout=timeout)
    return _json_result({"script": os.path.basename(script), **result})


def tool_get_firmware_version(_arguments: dict[str, Any]) -> dict[str, Any]:
    return _json_result(git_ops.get_firmware_version())


def tool_check_firmware_update(_arguments: dict[str, Any]) -> dict[str, Any]:
    return _json_result(git_ops.check_firmware_update())


def tool_perform_firmware_update(_arguments: dict[str, Any]) -> dict[str, Any]:
    return _json_result(git_ops.perform_firmware_update())


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

TOOLS: dict[str, dict[str, Any]] = {
    "capture_screen": {
        "description": "Capture the current Dartsnut framebuffer as a PNG image.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "surface": {"type": "string", "enum": ["full", "top", "bottom"], "default": "full"},
                "format": {"type": "string", "enum": ["png"], "default": "png"},
            },
        },
        "handler": tool_capture_screen,
    },
    "restart_main_service": {
        "description": "Restart dartsnut_python.service and report active state.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_restart_main_service,
    },
    "get_main_service_logs": {
        "description": "Return journal logs for dartsnut_python.service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tail": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                "since": {"type": "string"},
            },
        },
        "handler": tool_get_main_service_logs,
    },
    "get_ui_state": {
        "description": "Return the latest UI and network state snapshot from the main service.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_get_ui_state,
    },
    "run_script": {
        "description": "Run an executable script from the configured MCP script allowlist directory.",
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}, "default": []},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 30},
            },
        },
        "handler": tool_run_script,
    },
    "get_firmware_version": {
        "description": "Return the current firmware git version.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_get_firmware_version,
    },
    "check_firmware_update": {
        "description": "Check whether the firmware git checkout has an available update.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_check_firmware_update,
    },
    "perform_firmware_update": {
        "description": "Update firmware using the existing git/update.sh workflow.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_perform_firmware_update,
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        for name, spec in TOOLS.items()
    ]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool: {name}")
    handler: ToolHandler = spec["handler"]
    return handler(arguments or {})
