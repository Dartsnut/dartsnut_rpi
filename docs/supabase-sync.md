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

## OSS strip boundary

Remove these paths before publishing:

- `supabase_bridge/`
- `supabase_sync_bridge.py`
- `supabase/` (if migrations are not intended for OSS release)
