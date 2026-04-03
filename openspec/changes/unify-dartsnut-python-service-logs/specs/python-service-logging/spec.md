## ADDED Requirements

### Requirement: Central logging configuration

The Python process started by `dartsnut_python.service` SHALL configure the root logger (or an application-wide logging policy) exactly once at startup so that subsequent application log records use a single format and level policy.

#### Scenario: Service starts under systemd

- **WHEN** `main.py` begins execution as the service entrypoint
- **THEN** logging is configured before substantive application initialization (e.g. before starting BLE, websocket, or the main display loop) such that log calls emit to the process standard streams in a consistent format

### Requirement: Journal-friendly output

Log output SHALL be line-oriented text suitable for viewing with `journalctl` for `dartsnut_python.service`, without requiring a separate log file for normal service operation.

#### Scenario: Operator views unit logs

- **WHEN** an operator runs `journalctl` for `dartsnut_python.service`
- **THEN** log lines are readable single-line records that include severity and logger/component identification sufficient to distinguish subsystems

### Requirement: Default verbosity and important events

At default configuration, the system MUST emit at INFO (or stricter) severity for: service readiness after critical early startup, failures in paths covered by implementation tasks (including brightness, volume, timezone, device reset, remote sync / network poller errors in `main.py`, and startup-related failures in `runtime/bootstrap.py`).

The system MUST NOT enable verbose per-tick or high-frequency debug logging by default.

#### Scenario: Failure in a covered path

- **WHEN** an error occurs in a covered path during normal service operation
- **THEN** a log record at WARNING or ERROR (as appropriate) is emitted with a clear message

#### Scenario: Default log level

- **WHEN** no verbose override is configured
- **THEN** routine high-frequency internal events are not logged at INFO

### Requirement: Configurable log level

The effective log level SHALL be overridable without code changes (e.g. via an environment variable such as `DARTSNUT_LOG_LEVEL`), defaulting to INFO or equivalent.

#### Scenario: Enable debug on device

- **WHEN** an operator sets the documented environment override to a more verbose level and restarts the service
- **THEN** additional DEBUG records appear for components that emit DEBUG messages

### Requirement: Prefer logging over print in covered paths

For the code paths enumerated in `tasks.md` for this change, operational and error messages SHALL use the logging API instead of bare `print()`.

#### Scenario: Covered path emits diagnostic

- **WHEN** code in a covered path reports an operational or error condition
- **THEN** the message is emitted through the logging system at an appropriate level
