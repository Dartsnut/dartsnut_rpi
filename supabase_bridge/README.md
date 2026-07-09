# Dartsnut Supabase Bridge (Rust)

Rust executables used for Supabase device sync and watchdog tasks.

- `dartsnut-supabase-bridge`: syncs `remote_devices` with the Python runtime over a Unix socket.
- `dartsnut-watchdog`: standalone watchdog task runner.

## Build

```bash
cd supabase_bridge
cargo build --release --bins
cp target/release/dartsnut-supabase-bridge ../bridge
cp target/release/dartsnut-watchdog ../watchdog
chmod +x ../bridge ../watchdog
```

## Credentials

You can supply Supabase credentials either at runtime or embed them at compile time.

### Compile-time embedded (preferred for appliance builds)

```bash
cd supabase_bridge
DARTSNUT_EMBEDDED_SUPABASE_URL="https://<project-ref>.supabase.co" \
DARTSNUT_EMBEDDED_SUPABASE_KEY="<supabase-key>" \
cargo build --release --bins
cp target/release/dartsnut-supabase-bridge ../bridge
cp target/release/dartsnut-watchdog ../watchdog
chmod +x ../bridge ../watchdog
```

Do not commit real credential values into source files or documentation.

### Runtime environment (fallback / override)

- `SUPABASE_URL` (required)
- `SUPABASE_KEY` (recommended) or `SUPABASE_ANON_KEY`
- `DARTSNUT_SUPABASE_BRIDGE` (optional; overrides executable path)
- `DARTSNUT_SUPABASE_SOCKET` (optional; overrides socket path)
- `DARTSNUT_SUPABASE_DEVICE_ID` (optional; override `device_id` when BLE MAC is unavailable)
- `DARTSNUT_LOG_UPLOAD_URL` (optional; watchdog upload endpoint)
- `DARTSNUT_WATCHDOG_LOG_DIR` (optional; watchdog archive directory)

## Device ID source

- The bridge derives `device_id` from local BLE adapter MAC address.
- Normalized format is uppercase with `:` separators (for example `AA:BB:CC:DD:EE:FF`).
- If BLE MAC cannot be resolved, bridge falls back to `UNKNOWN-DEVICE`.

## Inbound sync source

- Inbound config is driven by Supabase Realtime `postgres_changes` on `public.remote_devices`.
- The subscription is device-scoped via `device_id=eq.<BLE_MAC_UPPER>`.
- Events with `last_update_source = 'supabase_bridge'` are dropped to avoid echo loops.

## Watchdog Tasks

- `dartsnut-watchdog` subscribes to `remote_device_commands` and also fetches the current row on every Realtime reconnect, so commands written while the device was offline are picked up when connectivity returns.
- New tasks should set `command`, a unique `command_token`, and `last_update_source`.
- Before shell execution starts, the watchdog claims the row by setting `running_command_token = command_token` and `started_at`. Claimed rows are not rerun after watchdog restart; if no local process owns the claim, the watchdog clears it with status `130`.
- Shell commands have no watchdog timeout. Set `stop_requested_at = now()` to stop the current process group; stopped commands return status `130`.
- A new command with a different `command_token` stops the running command, then runs the new command.
- Logs are written as `.tar.gz`, uploaded to the device-log API, and the returned `file_url` is saved.
- Completion clears `command` and `running_command_token`, stores `status_code` / `log_filename`, and sets `last_update_source = dartsnut_watchdog:<device_id>`.

## Notes

- The Python app uses `runtime/remote_sync_port.py` and does not need Supabase SDKs.
- This bridge module is isolated so it can be stripped for OSS distribution.
