"""Local-only emulator sideload sessions."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from typing import Any, Callable

from PIL import Image

from core.app_env import ensure_sideload_app_venv
from core.helpers import subprocess_launch_kwargs, terminate_process_group

_log = logging.getLogger(__name__)

DISPLAY_SIZE = (128, 160)
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_EXPIRY_SECONDS = 30
MAX_LOG_ENTRIES = 500
SUPPORTED_PLACEMENTS = {
    (128, 128): (0, 0),
    (64, 32): (0, 128),
    (128, 64): (0, 0),
    (128, 160): (0, 0),
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SideloadError(ValueError):
    pass


@dataclass
class _Session:
    session_id: str
    app_id: str
    size: tuple[int, int]
    shm: Any
    process: Any
    started_at: float
    last_heartbeat: float
    data_store: str
    canvas: Image.Image = field(default_factory=lambda: Image.new("RGB", DISPLAY_SIZE, "black"))
    logs: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_LOG_ENTRIES)
    )
    emit: Callable[[dict[str, Any]], None] | None = None
    closed: bool = False
    reader_threads: list[threading.Thread] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    process_generation: int = 0


class SideloadSessionManager:
    """Own one private app process and expose its frame on a black canvas."""

    def __init__(
        self,
        *,
        apps_dir: str | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        shm_factory: Callable[..., Any] = shared_memory.SharedMemory,
        popen: Callable[..., Any] = subprocess.Popen,
        ensure_venv: Callable[[str, str], bool] = ensure_sideload_app_venv,
        suspend_production: Callable[[], None] | None = None,
        restore_production: Callable[[], None] | None = None,
        start_watchdog: bool = True,
    ) -> None:
        self.apps_dir = apps_dir or os.path.join(os.getcwd(), "apps")
        self._monotonic = monotonic
        self._shm_factory = shm_factory
        self._popen = popen
        self._ensure_venv = ensure_venv
        self._suspend_production = suspend_production or (lambda: None)
        self._restore_production = restore_production or (lambda: None)
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._active: _Session | None = None
        self._retained: _Session | None = None
        self._shutdown = threading.Event()
        if start_watchdog:
            threading.Thread(target=self._watchdog_loop, daemon=True).start()

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "action": "sideload_capabilities",
            "message": "Success",
            "protocol_version": 1,
            "features": [
                "private_shm",
                "exclusive_display",
                "logs",
                "heartbeat",
                "raw_frame",
            ],
            "supported_sizes": [list(size) for size in SUPPORTED_PLACEMENTS],
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
            "heartbeat_expiry_seconds": HEARTBEAT_EXPIRY_SECONDS,
        }

    @staticmethod
    def _validate_id(value: Any, label: str) -> str:
        normalized = str(value or "").strip()
        if not _SAFE_ID.fullmatch(normalized):
            raise SideloadError(f"invalid {label}")
        return normalized

    def upload_file(self, session_id: Any, app_id: Any, relative_path: Any, data: bytes) -> dict:
        sid = self._validate_id(session_id, "session_id")
        aid = self._validate_id(app_id, "app_id")
        rel = str(relative_path or "").replace("\\", "/").lstrip("/")
        pieces = rel.split("/")
        if not rel or any(piece in ("", ".", "..") for piece in pieces):
            raise SideloadError("invalid relative_path")
        app_root = self._session_app_path(sid, aid)
        target = os.path.realpath(os.path.join(app_root, *pieces))
        if os.path.commonpath((app_root, target)) != app_root:
            raise SideloadError("relative_path escapes app directory")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as file:
            file.write(data)
        return {
            "action": "sideload_upload",
            "message": "Success",
            "session_id": sid,
            "app_id": aid,
            "relative_path": rel,
        }

    def start(
        self,
        *,
        session_id: Any,
        app_id: Any,
        size: Any = None,
        params: Any = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        with self._start_lock:
            return self._start_locked(
                session_id=session_id,
                app_id=app_id,
                size=size,
                params=params,
                emit=emit,
            )

    def _start_locked(
        self,
        *,
        session_id: Any,
        app_id: Any,
        size: Any,
        params: Any,
        emit: Callable[[dict[str, Any]], None] | None,
    ) -> dict:
        sid = self._validate_id(session_id, "session_id")
        aid = self._validate_id(app_id, "app_id")
        app_path = self._session_app_path(sid, aid)
        main_path = os.path.join(app_path, "main.py")
        if not os.path.isfile(main_path):
            raise SideloadError("sideload app main.py not found")
        resolved_size = self._resolve_size(app_path, size)
        if resolved_size not in SUPPORTED_PLACEMENTS:
            raise SideloadError(f"unsupported sideload size: {resolved_size[0]}x{resolved_size[1]}")

        with self._lock:
            previous = self._active
        if previous is not None:
            self.stop(previous.session_id, reason="replaced")

        app_type = "game" if resolved_size == DISPLAY_SIZE else "widget"
        if not self._ensure_venv(app_path, app_type):
            raise SideloadError("failed to prepare app environment")

        shm_name = f"dartsnut_sideload_{sid.replace('-', '_')}"
        try:
            stale = self._shm_factory(name=shm_name)
            stale.close()
            stale.unlink()
        except FileNotFoundError:
            pass
        shm = self._shm_factory(
            name=shm_name,
            create=True,
            size=1 + resolved_size[0] * resolved_size[1] * 3,
        )
        shm.buf[0] = 1
        data_store = tempfile.mkdtemp(prefix=f"dartsnut-sideload-{sid}-")
        command = [os.path.join(app_path, ".venv", "bin", "python"), "main.py"]
        command.extend(["--params", json.dumps(params if isinstance(params, dict) else {})])
        command.extend(["--shm", shm_name, "--data-store", data_store])
        try:
            process = self._popen(
                command,
                cwd=app_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                **subprocess_launch_kwargs(),
            )
        except Exception:
            shm.close()
            shm.unlink()
            shutil.rmtree(data_store, ignore_errors=True)
            raise

        now = self._monotonic()
        session = _Session(
            sid,
            aid,
            resolved_size,
            shm,
            process,
            now,
            now,
            data_store,
            emit=emit,
            params=params if isinstance(params, dict) else {},
        )
        with self._lock:
            self._active = session
        try:
            self._suspend_production()
        except Exception:
            self.stop(sid, reason="start_failed")
            raise

        self._emit(session, self._frame_event(session))
        self._start_process_observers(session)
        _log.info("sideload: started session=%s app=%s size=%sx%s", sid, aid, *resolved_size)
        return {
            "action": "sideload_start",
            "message": "Success",
            "session_id": sid,
            "app_id": aid,
            "size": list(resolved_size),
            "position": list(SUPPORTED_PLACEMENTS[resolved_size]),
            "capabilities": self.capabilities(),
        }

    def update_params(self, session_id: Any, params: Any = None) -> dict:
        """Restart active staged app with new params; never re-upload files."""
        with self._start_lock:
            session = self._require_active(session_id)
            next_params = params if isinstance(params, dict) else {}
            with self._lock:
                old_process = session.process
                old_shm = session.shm
                session.process_generation += 1
            if old_process.poll() is None:
                terminate_process_group(old_process.pid)
                try:
                    old_process.wait(timeout=2)
                except Exception:
                    pass
            try:
                old_shm.close()
                old_shm.unlink()
            except FileNotFoundError:
                pass
            shm_name = f"dartsnut_sideload_{session.session_id.replace('-', '_')}"
            shm = None
            try:
                shm = self._shm_factory(
                    name=shm_name,
                    create=True,
                    size=1 + session.size[0] * session.size[1] * 3,
                )
                shm.buf[0] = 1
                app_path = self._session_app_path(session.session_id, session.app_id)
                command = [os.path.join(app_path, ".venv", "bin", "python"), "main.py"]
                command.extend(["--params", json.dumps(next_params)])
                command.extend(["--shm", shm_name, "--data-store", session.data_store])
                process = self._popen(
                    command,
                    cwd=app_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    **subprocess_launch_kwargs(),
                )
            except Exception as exc:
                if shm is not None:
                    try:
                        shm.close()
                        shm.unlink()
                    except Exception:
                        pass
                self._finish(session, reason="params_update_failed", exit_code=None)
                raise SideloadError("failed to restart sideload app with updated params") from exc
            with self._lock:
                session.shm = shm
                session.process = process
                session.params = next_params
                session.last_heartbeat = self._monotonic()
            self._start_process_observers(session)
            return {
                "action": "sideload_update_params",
                "message": "Success",
                "session_id": session.session_id,
                "app_id": session.app_id,
                "size": list(session.size),
                "position": list(SUPPORTED_PLACEMENTS[session.size]),
            }

    def heartbeat(self, session_id: Any) -> dict:
        session = self._require_active(session_id)
        with self._lock:
            session.last_heartbeat = self._monotonic()
        return {"action": "sideload_heartbeat", "message": "Success", "session_id": session.session_id}

    def logs(
        self,
        session_id: Any,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        sid = self._validate_id(session_id, "session_id")
        with self._lock:
            session = self._active if self._active and self._active.session_id == sid else self._retained
            if session is None or session.session_id != sid:
                raise SideloadError("unknown sideload session")
            if emit is not None and self._active is session:
                session.emit = emit
            entries = list(session.logs)
            running = self._active is session and session.process.poll() is None
        return {
            "action": "sideload_logs",
            "message": "Success",
            "session_id": sid,
            "logs": entries,
            "running": running,
        }

    def frame(self, session_id: Any, *, raw: bool = False) -> dict[str, Any]:
        sid = self._validate_id(session_id, "session_id")
        with self._lock:
            session = (
                self._active
                if self._active and self._active.session_id == sid
                else self._retained
            )
            if session is None or session.session_id != sid:
                raise SideloadError("unknown sideload session")
            return self._frame_event(session, raw=raw)

    def stop(self, session_id: Any, *, reason: str = "stopped") -> dict:
        session = self._require_active(session_id)
        exit_code = session.process.poll()
        if exit_code is None:
            terminate_process_group(session.process.pid)
            try:
                exit_code = session.process.wait(timeout=2)
            except Exception:
                exit_code = session.process.poll()
        self._finish(session, reason=reason, exit_code=exit_code)
        return {
            "action": "sideload_stop",
            "message": "Success",
            "session_id": session.session_id,
            "reason": reason,
        }

    def render(self, display: Any) -> bytes | None:
        with self._lock:
            session = self._active
            if session is None:
                return None
            if session.shm.buf[0] == 0:
                width, height = session.size
                expected = width * height * 3
                frame = Image.frombytes("RGB", session.size, bytes(session.shm.buf[1 : 1 + expected]))
                canvas = Image.new("RGB", DISPLAY_SIZE, "black")
                canvas.paste(frame, SUPPORTED_PLACEMENTS[session.size])
                session.canvas = canvas
                session.shm.buf[0] = 1
                self._emit(session, self._frame_event(session))
            frame_bytes = session.canvas.tobytes()
        display.update_frame_buffer(Image.frombytes("RGB", DISPLAY_SIZE, frame_bytes))
        return frame_bytes

    def check_expiry(self) -> bool:
        with self._lock:
            session = self._active
            expired = bool(
                session
                and self._monotonic() - session.last_heartbeat >= HEARTBEAT_EXPIRY_SECONDS
            )
        if expired and session is not None:
            try:
                self.stop(session.session_id, reason="heartbeat_expired")
                return True
            except SideloadError:
                return False
        return False

    def is_active(self) -> bool:
        with self._lock:
            return self._active is not None

    def close(self) -> None:
        self._shutdown.set()
        with self._lock:
            session = self._active
        if session is not None:
            self.stop(session.session_id, reason="firmware_shutdown")

    def _require_active(self, session_id: Any) -> _Session:
        sid = self._validate_id(session_id, "session_id")
        with self._lock:
            if self._active is None or self._active.session_id != sid:
                raise SideloadError("stale or unknown sideload session")
            return self._active

    def _session_app_path(self, session_id: str, app_id: str) -> str:
        return os.path.realpath(
            os.path.join(self.apps_dir, ".sideload", session_id, app_id)
        )

    @staticmethod
    def _resolve_size(app_path: str, size: Any) -> tuple[int, int]:
        value = size
        if value is None:
            try:
                with open(os.path.join(app_path, "conf.json"), encoding="utf-8") as file:
                    value = json.load(file).get("size")
            except (OSError, ValueError, TypeError):
                value = DISPLAY_SIZE
        try:
            return int(value[0]), int(value[1])
        except (IndexError, TypeError, ValueError):
            raise SideloadError("size must contain width and height") from None

    def _start_process_observers(self, session: _Session) -> None:
        process = session.process
        generation = session.process_generation
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                reader = threading.Thread(
                    target=self._read_logs,
                    args=(session, stream_name, stream),
                    daemon=True,
                )
                session.reader_threads.append(reader)
                reader.start()
        threading.Thread(
            target=self._watch_process,
            args=(session, process, generation),
            daemon=True,
        ).start()

    def _read_logs(self, session: _Session, stream_name: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                entry = {
                    "timestamp": time.time(),
                    "stream": stream_name,
                    "text": line.rstrip("\r\n"),
                }
                with self._lock:
                    session.logs.append(entry)
                self._emit(
                    session,
                    {"action": "sideload_log", "session_id": session.session_id, **entry},
                )
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _watch_process(self, session: _Session, process: Any, generation: int) -> None:
        exit_code = process.wait()
        with self._lock:
            active = (
                self._active is session
                and session.process is process
                and session.process_generation == generation
                and not session.closed
            )
        if active:
            self._finish(session, reason="process_exited", exit_code=exit_code)

    def _finish(self, session: _Session, *, reason: str, exit_code: Any) -> None:
        with self._lock:
            if session.closed:
                return
            session.closed = True
            if self._active is session:
                self._active = None
            self._retained = session
        try:
            session.shm.close()
            session.shm.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            _log.warning("sideload: shm cleanup failed session=%s: %s", session.session_id, exc)
        shutil.rmtree(session.data_store, ignore_errors=True)
        shutil.rmtree(
            os.path.join(self.apps_dir, ".sideload", session.session_id),
            ignore_errors=True,
        )
        current_thread = threading.current_thread()
        for reader in session.reader_threads:
            if reader is not current_thread:
                reader.join(timeout=0.2)
        try:
            self._restore_production()
        except Exception as exc:
            _log.exception("sideload: production restore failed: %s", exc)
        self._emit(
            session,
            {
                "action": "sideload_exit",
                "session_id": session.session_id,
                "app_id": session.app_id,
                "exit_code": exit_code,
                "reason": reason,
                "logs": list(session.logs),
            },
        )
        _log.info("sideload: ended session=%s reason=%s exit=%s", session.session_id, reason, exit_code)

    def _frame_event(self, session: _Session, *, raw: bool = False) -> dict[str, Any]:
        image = session.canvas
        width, height = DISPLAY_SIZE
        scope = "composed"
        if raw:
            x, y = SUPPORTED_PLACEMENTS[session.size]
            width, height = session.size
            image = image.crop((x, y, x + width, y + height))
            scope = "raw"
        output = io.BytesIO()
        image.save(output, format="PNG")
        return {
            "action": "sideload_frame",
            "session_id": session.session_id,
            "width": width,
            "height": height,
            "encoding": "png",
            "scope": scope,
            "frame": base64.b64encode(output.getvalue()).decode("ascii"),
        }

    @staticmethod
    def _emit(session: _Session, payload: dict[str, Any]) -> None:
        if session.emit is None:
            return
        try:
            session.emit(payload)
        except Exception:
            _log.debug("sideload: event subscriber unavailable", exc_info=True)

    def _watchdog_loop(self) -> None:
        while not self._shutdown.wait(1):
            try:
                self.check_expiry()
            except Exception:
                _log.exception("sideload: heartbeat watchdog failed")
