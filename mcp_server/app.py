from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mcp_server.tools import ToolError, call_tool, list_tools

PROTOCOL_VERSION = "2025-06-18"

app = FastAPI(title="Dartsnut MCP Server")


def _response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    req_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(req_id, -32602, "params must be an object")

    if method == "initialize":
        return _response(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "dartsnut-firmware", "version": "1.0.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _response(req_id, {"tools": list_tools()})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(req_id, -32602, "tools/call requires name and object arguments")
        try:
            return _response(req_id, call_tool(name, arguments))
        except ToolError as exc:
            return _response(
                req_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": str(exc)}],
                },
            )
        except Exception as exc:
            return _response(
                req_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                },
            )
    return _error(req_id, -32601, f"Method not found: {method}")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)

    if isinstance(payload, list):
        responses = []
        for item in payload:
            if not isinstance(item, dict):
                responses.append(_error(None, -32600, "Invalid Request"))
                continue
            response = _handle_message(item)
            if response is not None:
                responses.append(response)
        return JSONResponse(responses)

    if not isinstance(payload, dict):
        return JSONResponse(_error(None, -32600, "Invalid Request"), status_code=400)

    response = _handle_message(payload)
    if response is None:
        return JSONResponse({}, status_code=202)
    return JSONResponse(response)
