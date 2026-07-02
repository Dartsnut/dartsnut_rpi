#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${REPO_ROOT}/supabase_bridge"
BRIDGE_BIN="${REPO_ROOT}/bridge"
COMMAND_WORKER_BIN="${REPO_ROOT}/command_worker"
SUPABASE_URL_DEFAULT="http://127.0.0.1:54321"

if ! command -v supabase >/dev/null 2>&1; then
  echo "Error: supabase CLI is required but not found in PATH." >&2
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "Error: cargo is required but not found in PATH." >&2
  exit 1
fi

if [[ -z "${SUPABASE_KEY:-}" && -z "${SUPABASE_ANON_KEY:-}" ]]; then
  echo "Error: set SUPABASE_KEY or SUPABASE_ANON_KEY before running this script." >&2
  echo "Example:" >&2
  echo "  SUPABASE_KEY=<local-anon-or-service-role-key> ${0}" >&2
  exit 1
fi

echo "Bootstrapping local Supabase..."
"${SCRIPT_DIR}/supabase_bootstrap.sh"

echo "Building Supabase bridge and command worker..."
cd "${BRIDGE_DIR}"
cargo build --release --bins
cp "${BRIDGE_DIR}/target/release/dartsnut-supabase-bridge" "${BRIDGE_BIN}"
chmod +x "${BRIDGE_BIN}"
cp "${BRIDGE_DIR}/target/release/dartsnut-command-worker" "${COMMAND_WORKER_BIN}"
chmod +x "${COMMAND_WORKER_BIN}"

cd "${REPO_ROOT}"
export RUN_SUPABASE_LOCAL_E2E=1
export SUPABASE_URL="${SUPABASE_URL:-${SUPABASE_URL_DEFAULT}}"

echo "Running local Supabase bridge E2E tests..."
python -m pytest tests/integration/test_supabase_local_bridge_e2e.py "$@"
