#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BRIDGE_DIR="${REPO_ROOT}/supabase_bridge"
OUT_BIN="${REPO_ROOT}/bridge"
TMP_OUT_BIN="${REPO_ROOT}/bridge.new"

EMBED_URL="https://base.dartsnut.com"
EMBED_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzc2NjE0NDAwLCJleHAiOjE5MzQzODA4MDB9.3ay1VYFklSZb3Qkfqc8dqZH5bML8Ib9W9H_mPtHkGvc"

echo "Building Supabase bridge with embedded credentials..."
cd "${BRIDGE_DIR}"

DARTSNUT_EMBEDDED_SUPABASE_URL="${EMBED_URL}" \
DARTSNUT_EMBEDDED_SUPABASE_KEY="${EMBED_KEY}" \
cargo build --release

cp "${BRIDGE_DIR}/target/release/dartsnut-supabase-bridge" "${TMP_OUT_BIN}"
chmod +x "${TMP_OUT_BIN}"
mv -f "${TMP_OUT_BIN}" "${OUT_BIN}"

echo "Done: ${OUT_BIN}"
