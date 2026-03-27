#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${REPO_ROOT}/supabase_bridge"
OUT_BIN="${BRIDGE_DIR}/bridge"
TMP_OUT_BIN="${BRIDGE_DIR}/bridge.new"

EMBED_URL="https://csofgmhsoswpqobxmftm.supabase.co"
EMBED_KEY="sb_publishable_5IXiYDDpMlgP4xJuX-on9A_m-A25T5H"

echo "Building Supabase bridge with embedded credentials..."
cd "${BRIDGE_DIR}"

DARTSNUT_EMBEDDED_SUPABASE_URL="${EMBED_URL}" \
DARTSNUT_EMBEDDED_SUPABASE_KEY="${EMBED_KEY}" \
cargo build --release

cp "${BRIDGE_DIR}/target/release/dartsnut-supabase-bridge" "${TMP_OUT_BIN}"
chmod +x "${TMP_OUT_BIN}"
mv -f "${TMP_OUT_BIN}" "${OUT_BIN}"

echo "Done: ${OUT_BIN}"
