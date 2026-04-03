## Context

`dartsnut_python.service` runs `main.py`; systemd captures stdout/stderr to **journald**. Today most messages are `print()`-based, so severity and component context are missing. Project docs already describe log locations (`docs/logging.md`); this change tightens only the **service** stream.

## Goals / Non-Goals

**Goals:**

- Configure stdlib `logging` once at startup: short line format (level + logger name + message), default INFO.
- Cover high-signal paths first: `main.py` operational errors, `runtime/bootstrap.py` startup failures.
- Environment override for level (e.g. `DARTSNUT_LOG_LEVEL`).

**Non-Goals:**

- Cron `check_and_update.py` or `/var/log/dartsnut_update.log` (unchanged).
- Structured JSON logs, remote log shipping, or log files for the service.
- Replacing every `print()` in the repository in one change.

## Decisions

1. **Stdlib `logging`** — No new dependencies; works with journald via process stderr (or stdout) as today.
2. **Configure once** — Single `configure_logging()` (or equivalent) with a guard against duplicate root handlers; call from `main.py` after the early matrix wait loop and before background subsystems.
3. **Human-readable one-line format** — e.g. `%(levelname)s %(name)s: %(message)s` for easy `journalctl` reading.
4. **Incremental `print()` migration** — Prioritize files listed in tasks; other modules can follow later.

## Risks / Trade-offs

- **Duplicate handlers** if configure runs twice → guard on existing handlers.
- **Noisy libraries** at INFO → optionally set specific logger names to WARNING after setup.
- **Trade-off** — Some modules may still `print()` until migrated; spec scopes “must” to listed paths.

## Migration Plan

1. Land logging module + `main.py` wiring; restart service; spot-check `journalctl -u dartsnut_python.service`.
2. Migrate listed `print()` sites; restart again.
3. Rollback: revert commit; behavior returns to prior output style.

## Open Questions

- Whether to add `Environment=DARTSNUT_LOG_LEVEL=INFO` to the unit file or only document the variable in comments or `docs/logging.md`.
