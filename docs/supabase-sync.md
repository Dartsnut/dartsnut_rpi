# Supabase Sync Setup

This branch uses Supabase for remote sync and disables Firestore runtime wiring.

## Local development

```bash
./scripts/supabase_bootstrap.sh
```

The script starts local Supabase and applies migrations from `supabase/migrations`.

## Hosted project setup

```bash
supabase link --project-ref csofgmhsoswpqobxmftm
supabase db push
```

If `db push` times out, rerun from a network that can reach `db.<project-ref>.supabase.co:5432`.

## Bridge build

```bash
cd supabase_bridge
cargo build --release
cp target/release/dartsnut-supabase-bridge ./bridge
chmod +x ./bridge
```

## Runtime env

- `SUPABASE_URL` (for example `https://csofgmhsoswpqobxmftm.supabase.co`)
- `SUPABASE_KEY` (preferred) or `SUPABASE_ANON_KEY`
- `DARTSNUT_SUPABASE_BRIDGE` (optional bridge executable override)
- `DARTSNUT_SUPABASE_SOCKET` (optional socket override)

## Realtime inbound config

- The Rust bridge subscribes to Supabase Realtime (`postgres_changes`) on `public.remote_devices`.
- Subscription is filtered to this device only: `device_id=eq.<BLE_MAC_UPPER>`.
- Inbound rows where `last_update_source = 'supabase_bridge'` are ignored to prevent self-echo loops.
- Bridge auto-reconnects with backoff and emits `bridge_health` state over the Unix socket.

## Device ID behavior

- Supabase bridge resolves `device_id` from BLE adapter MAC in Rust.
- Stored and published in uppercase with `:` separators (for example `AA:BB:CC:DD:EE:FF`).

## OSS strip boundary

Remove these paths before publishing:

- `supabase_bridge/`
- `supabase_sync_bridge.py`
- `supabase/` (if migrations are not intended for OSS release)
