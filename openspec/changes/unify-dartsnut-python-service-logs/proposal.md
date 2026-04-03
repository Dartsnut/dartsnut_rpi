## Why

`dartsnut_python.service` mostly emits unstructured `print()` output and mixed styles, so `journalctl -u dartsnut_python` is uneven and hard to scan for failures and lifecycle signals. A single, simple logging setup (one format, sensible levels) keeps the journal readable while still surfacing everything important—without verbose debug noise by default.

## What Changes

- Add a small, shared logging configuration for the Python service (stdlib `logging`): one format, one place it is applied at startup.
- Replace service-path `print()` calls (starting with `main.py` and `runtime/bootstrap.py`) with leveled log calls so errors and high-signal events are obvious.
- Default to production-friendly verbosity (INFO): important startup, subsystem failures, reset/update errors visible; routine inner-loop chatter stays at DEBUG or unlogged.
- Optional log level override via environment (e.g. `DARTSNUT_LOG_LEVEL`) for field debugging.
- **Out of scope:** Cron-driven `check_and_update.py` and `/var/log/dartsnut_update.log` remain as documented in `docs/logging.md`; this change only affects the long-running service process.

## Capabilities

### New Capabilities

- `python-service-logging`: Logging format, default level, journal-friendly output, and which operational paths MUST use logging instead of `print()` for the `dartsnut_python.service` process.

### Modified Capabilities

- _(none — no existing `openspec/specs/` capabilities in this repo)_

## Impact

- **Code**: New small module for configure-once logging; `main.py`, `runtime/bootstrap.py`, and optionally noisy third-party logger tuning.
- **Service**: `services/dartsnut_python.service` only if an `Environment=` line documents `DARTSNUT_LOG_LEVEL` (optional).
- **Operations**: Operators rely on `journalctl -u dartsnut_python.service` for app logs; update cron logs stay in `/var/log/dartsnut_update.log` per existing docs.
