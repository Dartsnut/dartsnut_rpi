from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import requests

import supabase_sync_bridge as ssb


pytestmark = [pytest.mark.integration, pytest.mark.contract]


def _require_local_bridge_env() -> tuple[str, str]:
    if os.getenv("RUN_SUPABASE_LOCAL_E2E") != "1":
        pytest.skip("Set RUN_SUPABASE_LOCAL_E2E=1 to run local Supabase bridge E2E tests")
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    api_key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
    if not base_url or not api_key:
        pytest.skip("SUPABASE_URL and SUPABASE_KEY/SUPABASE_ANON_KEY are required")
    bridge_bin = os.getenv("DARTSNUT_SUPABASE_BRIDGE", ssb._DEFAULT_BRIDGE_BIN)
    if not os.path.isfile(bridge_bin) or not os.access(bridge_bin, os.X_OK):
        pytest.skip(f"Supabase bridge binary not executable: {bridge_bin}")
    return base_url, api_key


def _command_worker_bin() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    worker_bin = os.getenv(
        "DARTSNUT_SUPABASE_COMMAND_WORKER",
        os.path.join(repo_root, "command_worker"),
    )
    if not os.path.isfile(worker_bin) or not os.access(worker_bin, os.X_OK):
        pytest.skip(f"Supabase command worker binary not executable: {worker_bin}")
    return worker_bin


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.2, desc: str = "condition"):
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # defensive polling helper
            last_err = exc
        time.sleep(interval)
    if last_err:
        raise AssertionError(f"Timed out waiting for {desc}: {last_err}")
    raise AssertionError(f"Timed out waiting for {desc}")


@pytest.fixture()
def local_bridge_runtime(monkeypatch):
    base_url, api_key = _require_local_bridge_env()
    socket_path = f"/tmp/dartsnut-supabase-sync-{uuid.uuid4()}.sock"
    monkeypatch.setenv("DARTSNUT_SUPABASE_SOCKET", socket_path)
    monkeypatch.setenv("SUPABASE_URL", base_url)
    monkeypatch.setenv("SUPABASE_KEY", api_key)

    # Ensure clean global state for each test.
    ssb.stop_supabase_sync()
    ssb.set_supabase_connectivity_callback(None)

    incoming_configs: list[dict[str, Any]] = []
    reload_count = {"n": 0}
    connected_event = threading.Event()

    def on_cfg(config: dict[str, Any]) -> None:
        incoming_configs.append(dict(config))

    def on_reload() -> None:
        reload_count["n"] += 1

    def on_conn(connected: bool) -> None:
        if connected:
            connected_event.set()

    ssb.set_supabase_connectivity_callback(on_conn)
    device_id = f"ITEST-{uuid.uuid4()}".upper()
    device_info = {
        "id": device_id,
        "brightness": "50",
        "volume": "50",
        "name": "LocalE2E",
        "updated_at": "2026-03-30T00:00:00",
    }
    # Local non-BLE dev machines can force bridge device_id via env override.
    monkeypatch.setenv("DARTSNUT_SUPABASE_DEVICE_ID", device_id)

    ssb.start_supabase_sync_if_available(device_info, on_reload, on_cfg)
    _wait_until(lambda: connected_event.is_set() or ssb.is_supabase_connected(), desc="bridge connection")

    yield {
        "base_url": base_url,
        "api_key": api_key,
        "device_id": device_id,
        "incoming_configs": incoming_configs,
        "reload_count": reload_count,
    }

    ssb.stop_supabase_sync()
    ssb.set_supabase_connectivity_callback(None)


# Device-originated publish reaches Supabase row
def test_local_bridge_e2e_device_publish_updates_supabase(local_bridge_runtime):
    base_url = local_bridge_runtime["base_url"]
    api_key = local_bridge_runtime["api_key"]
    device_id = local_bridge_runtime["device_id"]
    headers = {"apikey": api_key, "Authorization": f"Bearer {api_key}"}

    ssb.publish_device_state_update({"brightness": 73})

    def _state_updated() -> bool:
        r = requests.get(
            f"{base_url}/rest/v1/remote_devices",
            headers=headers,
            params={
                "device_id": f"eq.{device_id}",
                "select": "state,last_update_source",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return False
        rows = r.json()
        if not rows:
            return False
        row = rows[0]
        return row.get("state", {}).get("brightness") == 73 and bool(row.get("last_update_source"))

    _wait_until(_state_updated, desc="Supabase row brightness update")


# Supabase-originated patch reaches Python callback
def test_local_bridge_e2e_external_supabase_patch_reaches_python_callback(local_bridge_runtime):
    base_url = local_bridge_runtime["base_url"]
    api_key = local_bridge_runtime["api_key"]
    device_id = local_bridge_runtime["device_id"]
    incoming_configs = local_bridge_runtime["incoming_configs"]
    reload_count = local_bridge_runtime["reload_count"]

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    rpc_url = f"{base_url}/rest/v1/rpc/apply_remote_device_patch"

    payload = {
        "p_device_id": device_id,
        "p_patch": {"brightness": 64, "volume": 21},
        "p_full": False,
        "p_source": "mobile_app_test",
    }
    response = requests.post(rpc_url, headers=headers, json=payload, timeout=15)
    assert response.status_code in (200, 201), response.text

    def _callback_received() -> bool:
        if not incoming_configs:
            return False
        latest = incoming_configs[-1]
        return (
            latest.get("brightness") == 64
            and latest.get("volume") == 21
            and bool(latest.get("updated_at"))
            and bool(latest.get("last_update_source"))
            and reload_count["n"] > 0
        )

    _wait_until(_callback_received, desc="Python inbound config callback")
    latest = incoming_configs[-1]
    assert latest.get("brightness") == 64
    assert latest.get("volume") == 21
    assert latest.get("updated_at")
    assert latest.get("last_update_source") == "mobile_app_test"
    assert reload_count["n"] > 0


def test_local_bridge_e2e_command_row_reaches_worker_and_is_cleared(
    local_bridge_runtime, tmp_path
):
    base_url = local_bridge_runtime["base_url"]
    api_key = local_bridge_runtime["api_key"]
    device_id = local_bridge_runtime["device_id"]
    worker_bin = _command_worker_bin()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    row_url = f"{base_url}/rest/v1/remote_device_commands"
    completion_written = threading.Event()
    upload_seen = threading.Event()

    class UploadHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            upload_seen.set()
            length = int(self.headers.get("content-length", "0"))
            if length:
                self.rfile.read(length)
            body = (
                b'{"code":1001,"data":{"file_url":'
                b'"https://oss.example.com/e2e-command.tar.gz"}}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    upload_server = ThreadingHTTPServer(("127.0.0.1", 0), UploadHandler)
    upload_thread = threading.Thread(target=upload_server.serve_forever, daemon=True)
    upload_thread.start()
    upload_url = f"http://127.0.0.1:{upload_server.server_port}/v1/mobile/device-log/upload"

    env = {
        **os.environ,
        "SUPABASE_URL": base_url,
        "SUPABASE_KEY": api_key,
        "DARTSNUT_SUPABASE_DEVICE_ID": device_id,
        "DARTSNUT_LOG_UPLOAD_URL": upload_url,
        "DARTSNUT_COMMAND_TIMEOUT_SECONDS": "5",
        "DARTSNUT_COMMAND_LOG_DIR": str(tmp_path / "command-logs"),
    }
    worker_proc = subprocess.Popen(
        [worker_bin],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        inserted = requests.post(
            row_url,
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "device_id": device_id,
                "command": "printf e2e",
                "last_update_source": "integration_test",
            },
            timeout=15,
        )
        assert inserted.status_code in (200, 201), inserted.text

        def _row_cleared() -> bool:
            r = requests.get(
                row_url,
                headers=headers,
                params={
                    "device_id": f"eq.{device_id}",
                    "select": "command,status_code,log_filename,last_update_source",
                },
                timeout=10,
            )
            if r.status_code != 200:
                return False
            rows = r.json()
            if not rows:
                return False
            row = rows[0]
            ok = (
                row.get("command") == ""
                and row.get("status_code") == 0
                and row.get("log_filename") == "https://oss.example.com/e2e-command.tar.gz"
                and row.get("last_update_source")
                == f"dartsnut_command_bridge:{device_id}"
            )
            if ok:
                completion_written.set()
            return ok

        _wait_until(_row_cleared, desc="command row completion")
        assert completion_written.is_set()
        assert upload_seen.is_set()
    finally:
        worker_proc.terminate()
        try:
            worker_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_proc.kill()
            worker_proc.wait(timeout=5)
        upload_server.shutdown()
        upload_server.server_close()
