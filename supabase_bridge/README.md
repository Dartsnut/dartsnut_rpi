# Dartsnut Supabase Bridge (Rust)

Rust executable used by the Python runtime to sync device state with Supabase over a Unix socket.

## Build

```bash
cd supabase_bridge
cargo build --release
cp target/release/dartsnut-supabase-bridge ./bridge
chmod +x ./bridge
```

## Credentials

You can supply Supabase credentials either at runtime or embed them at compile time.

### Compile-time embedded (preferred for appliance builds)

```bash
cd supabase_bridge
DARTSNUT_EMBEDDED_SUPABASE_URL="https://<project-ref>.supabase.co" \
DARTSNUT_EMBEDDED_SUPABASE_KEY="<supabase-key>" \
cargo build --release
cp target/release/dartsnut-supabase-bridge ./bridge
chmod +x ./bridge
```

### Runtime environment (fallback / override)

- `SUPABASE_URL` (required)
- `SUPABASE_KEY` (recommended) or `SUPABASE_ANON_KEY`
- `DARTSNUT_SUPABASE_BRIDGE` (optional; overrides executable path)
- `DARTSNUT_SUPABASE_SOCKET` (optional; overrides socket path)

## Device ID source

- The bridge derives `device_id` from local BLE adapter MAC address.
- Normalized format is uppercase with `:` separators (for example `AA:BB:CC:DD:EE:FF`).
- If BLE MAC cannot be resolved, bridge falls back to `UNKNOWN-DEVICE`.

## Inbound sync source

- Inbound config is driven by Supabase Realtime `postgres_changes` on `public.remote_devices`.
- The subscription is device-scoped via `device_id=eq.<BLE_MAC_UPPER>`.
- Events with `last_update_source = 'supabase_bridge'` are dropped to avoid echo loops.

## Notes

- The Python app uses `runtime/remote_sync_port.py` and does not need Supabase SDKs.
- This bridge module is isolated so it can be stripped for OSS distribution.
