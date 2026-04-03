## 1. Logging bootstrap

- [x] 1.1 Add `runtime/logging_config.py` (or equivalent) with `configure_logging()` that attaches a single line-oriented handler to the root logger, idempotent if called twice, and reads `DARTSNUT_LOG_LEVEL` (default `INFO`) with safe fallback for invalid values.
- [x] 1.2 Call `configure_logging()` from `main.py` after the matrix wait loop and before `start_background_subsystems` / BLE / websocket startup; emit one INFO line confirming logging is active and the effective level.

## 2. Replace operational prints (covered paths)

- [x] 2.1 In `main.py`, replace `print()` used for errors or operational messages with `logging.getLogger(__name__)` at appropriate levels (brightness while dimmed, brightness, volume, timezone, reset sequence, remote sync / connection / network poller errors).
- [x] 2.2 In `runtime/bootstrap.py`, replace startup-related `print()` calls (firmware version, Supabase sync, remote game status reset) with logging.
- [x] 2.3 If any third-party library floods INFO during normal run, cap specific logger names to WARNING after root configuration (document which ones).

## 3. Service unit, docs, verification

- [x] 3.1 Optionally add `Environment=DARTSNUT_LOG_LEVEL=INFO` to `services/dartsnut_python.service` or document the variable in `docs/logging.md` (Python service section only).
- [ ] 3.2 On device or dev environment: restart `dartsnut_python.service` and confirm `journalctl -u dartsnut_python.service` shows uniform lines, important errors remain visible, and default output is not excessively verbose.

## 4. Tests (lightweight)

- [x] 4.1 Add a minimal unit test that `configure_logging()` is idempotent (no duplicate handlers), if the test layout allows without hardware.
