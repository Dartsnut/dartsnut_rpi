#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/firestore_sync"
OUT_BIN="${SCRIPT_DIR}/dartsnut_firestore_sync"

mkdir -p "${SRC_DIR}"

g++ -std=c++17 -O2 \
  "${SRC_DIR}/main.cpp" \
  -o "${OUT_BIN}" \
  -lgoogle_cloud_cpp_firestore -lprotobuf -lpthread

echo "Built Firestore sync executable at ${OUT_BIN}"

