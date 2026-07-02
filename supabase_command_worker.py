"""Supabase command worker sidecar.

Rust owns Supabase credentials and subscriptions. This worker only accepts
command frames over a Unix socket, runs the command, archives logs, uploads the
archive, and sends a completion frame back.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

import requests

DEFAULT_SOCKET_PATH = "/tmp/dartsnut-supabase-command.sock"
DEFAULT_UPLOAD_URL = "https://api.dartsnut.com/v1/mobile/device-log/upload"
DEFAULT_TIMEOUT_SECONDS = 20.0
TIMEOUT_STATUS_CODE = 124


@dataclass
class CommandResult:
    command: str
    status_code: int
    stdout: str
    stderr: str
    timed_out: bool
    started_at: str
    finished_at: str


@dataclass
class LogArchive:
    path: Path
    log_name: str

    @property
    def name(self) -> str:
        return self.path.name


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_shell_command(command: str) -> str:
    """Make sudo noninteractive for shell commands.

    The production service runs as root, so sudo is unnecessary on-device. A
    shell function keeps incoming "sudo ..." commands working by dispatching
    directly to the wrapped command. If the worker is ever run as non-root,
    sudo uses -n so password prompts fail fast instead of hanging forever.
    """
    sudo_function = (
        'sudo() { command "$@"; };'
        if os.geteuid() == 0
        else 'sudo() { command sudo -n "$@"; };'
    )
    return f"{sudo_function} {command}"


def run_command(
    command: str,
    *,
    cwd: str | os.PathLike[str],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    started_at = utc_now_iso()
    proc = subprocess.Popen(
        prepare_shell_command(command),
        cwd=str(cwd),
        shell=True,
        executable="/bin/sh",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        status_code = int(proc.returncode or 0)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        status_code = TIMEOUT_STATUS_CODE
    finished_at = utc_now_iso()
    return CommandResult(
        command=command,
        status_code=status_code,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        started_at=started_at,
        finished_at=finished_at,
    )


def _safe_name(value: str) -> str:
    out = []
    for ch in str(value or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "command"


def write_log_archive(
    result: CommandResult,
    *,
    log_dir: str | os.PathLike[str],
    command_id: str,
    device_id: str,
) -> LogArchive:
    base = f"{_safe_name(device_id)}-{_safe_name(command_id)}"
    log_root = Path(log_dir)
    log_root.mkdir(parents=True, exist_ok=True)
    log_name = f"{base}.log"
    archive_path = log_root / f"{base}.tar.gz"
    log_payload = {
        "device_id": device_id,
        "command_id": command_id,
        **asdict(result),
    }

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / log_name
        with log_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(log_payload, indent=2, sort_keys=True))
            f.write("\n\n--- stdout ---\n")
            f.write(result.stdout)
            f.write("\n\n--- stderr ---\n")
            f.write(result.stderr)
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(log_path, arcname=log_name)

    return LogArchive(path=archive_path, log_name=log_name)


def upload_archive(
    archive_path: str | os.PathLike[str],
    upload_url: str,
    *,
    log_id: str,
    device_id: str,
    command_id: str,
    event: str = "engineer_command",
    device_version: str = "",
    os_version: str = "",
) -> None:
    path = Path(archive_path)
    metadata = json.dumps(
        {
            "command_id": command_id,
            "reason": "engineer requested logs",
            "log_range": "latest",
        },
        sort_keys=True,
    )
    data = {
        "log_id": log_id,
        "device_id": device_id,
        "event": event,
        "metadata": metadata,
    }
    if device_version:
        data["device_version"] = device_version
    if os_version:
        data["os_version"] = os_version
    with path.open("rb") as f:
        response = requests.post(
            upload_url,
            data=data,
            files={"file": (path.name, f, "application/gzip")},
            timeout=30,
        )
    response.raise_for_status()


def log_id_for_archive(archive_path: str | os.PathLike[str]) -> str:
    name = Path(archive_path).name
    if name.endswith(".tar.gz"):
        return name[: -len(".tar.gz")]
    return Path(name).stem


class CommandSocketServer:
    def __init__(
        self,
        socket_path: str,
        *,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        self.socket_path = socket_path
        self.handler = handler
        self._stop = threading.Event()

    def serve_forever(self) -> None:
        while not self._stop.is_set():
            self.serve_once()

    def serve_once(self) -> None:
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(self.socket_path)
            srv.listen(1)
            conn, _ = srv.accept()
            with conn:
                self._serve_connection(conn)
        finally:
            srv.close()
            try:
                os.remove(self.socket_path)
            except FileNotFoundError:
                pass

    def stop(self) -> None:
        self._stop.set()

    def _serve_connection(self, conn: socket.socket) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                    payload = msg.get("payload") if isinstance(msg, dict) else None
                    if msg.get("kind") != "run_command" or not isinstance(payload, dict):
                        continue
                    response = self.handler(payload)
                except Exception as exc:
                    response = {
                        "kind": "command_result",
                        "payload": {
                            "status_code": 1,
                            "log_filename": "",
                            "error": str(exc),
                        },
                    }
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def build_handler(
    *,
    repo_root: str | os.PathLike[str],
    log_dir: str | os.PathLike[str],
    upload_url: str,
    timeout_seconds: float,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    def _handle(payload: Dict[str, Any]) -> Dict[str, Any]:
        command = str(payload.get("command") or "")
        command_id = str(payload.get("command_id") or int(time.time()))
        device_id = str(payload.get("device_id") or "UNKNOWN-DEVICE")
        result = run_command(command, cwd=repo_root, timeout_seconds=timeout_seconds)
        archive = write_log_archive(
            result,
            log_dir=log_dir,
            command_id=command_id,
            device_id=device_id,
        )
        try:
            upload_archive(
                archive.path,
                upload_url,
                log_id=log_id_for_archive(archive.path),
                device_id=device_id,
                command_id=command_id,
                device_version=os.getenv("DARTSNUT_FIRMWARE_VERSION", ""),
                os_version=os.getenv("DARTSNUT_OS_VERSION", ""),
            )
        except Exception:
            pass
        return {
            "kind": "command_result",
            "payload": {
                "command_id": command_id,
                "status_code": result.status_code,
                "log_filename": archive.name,
            },
        }

    return _handle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--socket-path",
        default=os.getenv("DARTSNUT_SUPABASE_COMMAND_SOCKET", DEFAULT_SOCKET_PATH),
    )
    parser.add_argument(
        "--upload-url",
        default=os.getenv("DARTSNUT_COMMAND_UPLOAD_URL", DEFAULT_UPLOAD_URL),
    )
    parser.add_argument(
        "--log-dir",
        default=os.getenv(
            "DARTSNUT_COMMAND_LOG_DIR",
            os.path.join(os.getcwd(), "logs", "supabase_commands"),
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("DARTSNUT_COMMAND_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    args = parser.parse_args()

    server = CommandSocketServer(
        args.socket_path,
        handler=build_handler(
            repo_root=os.getcwd(),
            log_dir=args.log_dir,
            upload_url=args.upload_url,
            timeout_seconds=args.timeout,
        ),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
