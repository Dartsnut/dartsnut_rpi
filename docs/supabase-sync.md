# Supabase Sync Setup

This branch uses Supabase for remote sync and disables legacy remote-sync runtime wiring.

## Local development

```bash
./scripts/supabase_bootstrap.sh
```

The script starts local Supabase and applies migrations from `supabase/migrations`.

## Hosted project setup

```bash
supabase link --project-ref <project-ref>
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

- `SUPABASE_URL` (for example `https://<project-ref>.supabase.co`)
- `SUPABASE_KEY` (preferred) or `SUPABASE_ANON_KEY`
- `DARTSNUT_SUPABASE_BRIDGE` (optional bridge executable override)
- `DARTSNUT_SUPABASE_SOCKET` (optional socket override)
- `DARTSNUT_SUPABASE_DEVICE_ID` (optional local-dev override when BLE MAC is unavailable)

## Integration tests

Run hardware-free end-to-end integration tests:

```bash
python -m pytest -m integration tests/integration
```

Run the full suite except optional Supabase contract tests:

```bash
python -m pytest -m "not contract"
```

Run optional Supabase contract tests (local or hosted):

```bash
RUN_SUPABASE_CONTRACT=1 \
SUPABASE_URL="http://127.0.0.1:54321" \
SUPABASE_KEY="<supabase-key>" \
python -m pytest -m contract tests/integration/test_supabase_sql_contract.py
```

The contract test validates JSON patch merge behavior and `last_update_source`
through `public.apply_remote_device_patch`.

Run full local bridge E2E tests (opt-in):

```bash
./scripts/supabase_bootstrap.sh
cd supabase_bridge && cargo build --release && cp target/release/dartsnut-supabase-bridge ./bridge && chmod +x ./bridge
cd ..
RUN_SUPABASE_LOCAL_E2E=1 \
SUPABASE_URL="http://127.0.0.1:54321" \
SUPABASE_KEY="<supabase-key>" \
python -m pytest tests/integration/test_supabase_local_bridge_e2e.py
```

This suite launches the real Rust bridge process, verifies device-to-Supabase
state writes, and verifies Supabase realtime updates are delivered back to
Python callbacks.

One-command helper:

```bash
SUPABASE_KEY="<supabase-key>" ./scripts/run_local_supabase_e2e.sh
```

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

## Secret hygiene

- Never commit real values for `SUPABASE_KEY` or `SUPABASE_ANON_KEY`.
- Use placeholders in documentation and shell history examples.
