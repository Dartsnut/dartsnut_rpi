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

## Sync architecture (Python-owned)

- **Rust bridge** (`supabase_bridge/`): credentialed transport only — Realtime subscribe, REST RPC forward, reconnect/health, Unix socket framing. Sends inbound `remote_row` snapshots and returns `ack` / `error` for outbound `rpc_patch` / `initial_state` / `device_state`.
- **Python sync engine** (`runtime/sync/`): tokenizes raw rows into semantic events, reduces them with timestamp/dedupe caches, applies firmware config, and queues outbound patches through a retry **outbox** when the socket is down.
- **Stored JSON schema** in `remote_devices.state` is unchanged. Older firmware keeps using the legacy `apply_remote_device_patch` RPC with shallow merge semantics. New firmware uses `apply_remote_device_patch_v2`, which merges partial `games` arrays **by `id`** (see migration `20260603120000_merge_games_array_by_id.sql`) so single-game status patches cannot wipe the full games list during network failures.
- **Game menu resilience**: transient snapshots where all games are `playing`/`downloading` no longer clear the on-device ready set; authoritative empty lists still apply.

### Unix socket message kinds

| Direction | Kind | Purpose |
|-----------|------|---------|
| Rust → Python | `ready` | Bridge connected; Python sends initial state |
| Rust → Python | `remote_row` | Raw remote device state + row metadata (`updated_at`, `last_update_source`) |
| Rust → Python | `bridge_health` | WS connectivity (`state`) plus REST health (`rest_probe_ok`, `rest_latency_ms` from last successful outbound v2 RPC) |
| Rust → Python | `ack` / `error` | Outbound RPC result (`ref` correlates to Python outbox entry) |
| Python → Rust | `initial_state` / `rpc_patch` / `device_state` | Outbound state patch (with optional `ref`, `full`, `source`) |

Legacy kinds `config` / `config_initial` are still accepted on the Python side for older bridge binaries.

## Realtime inbound config

- The Rust bridge subscribes to Supabase Realtime (`postgres_changes`) on `public.remote_devices`.
- Subscription is filtered to this device only: `device_id=eq.<BLE_MAC_UPPER>`.
- Inbound rows where `last_update_source = 'supabase_bridge_heartbeat'` are always ignored. Other `supabase_bridge` rows are ignored unless they carry games or reset-confirmation state (Python reducer also dedupes bridge snapshots).
- Bridge auto-reconnects with backoff and emits `bridge_health` state over the Unix socket.

### REST health and idle `device_updated_at` heartbeat

- `rest_latency_ms` is the round-trip time of the **last successful** `apply_remote_device_patch_v2` RPC (games, settings, initial state, or idle heartbeat). Settings WiFi color uses this write RTT, not a separate read probe.
- Every outbound patch RPC records latency and `last_outbound_at` in the bridge. Failed RPCs clear `rest_probe_ok` until the next success.
- A background probe wakes every 30s and always emits `bridge_health`. If an outbound RPC succeeded within the last 30s, it **reuses** the cached snapshot (no extra Supabase call).
- When idle for 30s or more, the probe posts a timestamp-only patch (`device_updated_at`, `last_update_source = supabase_bridge_heartbeat`). Realtime rows with that source are always dropped in Rust before Python sees them (full row state still contains games/pages from the DB merge).
- All v2 patch RPCs (socket thread and idle probe) share one mutex so concurrent writes cannot race.
- Read-only `remote_devices` GET (row existence during initial connect) does not update latency.

## Device ID behavior

- Supabase bridge resolves `device_id` from BLE adapter MAC in Rust.
- Stored and published in uppercase with `:` separators (for example `AA:BB:CC:DD:EE:FF`).

## Bluetooth state contract

Remote sync uses a canonical `state.bluetooth` object:

- `is_scan` (boolean): app sets `true` to request a scan; Python bridge resets to `false` after scan completes.
- `controllers` (array): paired/known controllers, unique by `mac`.
- `scan_results` (array): most recent scan snapshot, unique by `mac`.
- `last_scan_at` (ISO string): timestamp of the latest completed scan, used by app freshness logic.

Each row in `controllers` and `scan_results` uses:

- `name` (string)
- `mac` (string, uppercase `AA:BB:CC:DD:EE:FF`)
- `status` (`idle | connecting | connected | error`)
- `last_error` (optional string, populated when status is `error`)

Behavior notes:

- Scan start clears `scan_results` first, then writes new results when scan completes.
- If app sets an entry status to `connecting` (in either list), firmware attempts connection and updates status to `connected` or `error`.
- If app removes an entry from `controllers`, firmware unpairs/disconnects that `mac` (same behavior as websocket `bluetooth_remove`).

## OSS strip boundary

Remove these paths before publishing:

- `supabase_bridge/`
- `supabase_sync_bridge.py`
- `supabase/` (if migrations are not intended for OSS release)

## Secret hygiene

- Never commit real values for `SUPABASE_KEY` or `SUPABASE_ANON_KEY`.
- Use placeholders in documentation and shell history examples.
