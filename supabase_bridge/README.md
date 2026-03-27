# Dartsnut Supabase Bridge (Rust)

Rust executable used by the Python runtime to sync device state with Supabase over a Unix socket.

## Build

```bash
cd supabase_bridge
cargo build --release
cp target/release/dartsnut-supabase-bridge ./bridge
chmod +x ./bridge
```

## Runtime environment

- `SUPABASE_URL` (required)
- `SUPABASE_KEY` (recommended) or `SUPABASE_ANON_KEY`
- `DARTSNUT_DEVICE_ID` (optional; defaults to `unknown-device`)
- `DARTSNUT_SUPABASE_BRIDGE` (optional; overrides executable path)
- `DARTSNUT_SUPABASE_SOCKET` (optional; overrides socket path)

## Notes

- The Python app uses `runtime/remote_sync_port.py` and does not need Supabase SDKs.
- This bridge module is isolated so it can be stripped for OSS distribution.
