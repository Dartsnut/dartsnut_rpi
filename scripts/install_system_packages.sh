#!/bin/bash

set -euo pipefail

PACKAGES_FILE="${1:-}"

if [ -z "${PACKAGES_FILE}" ]; then
    echo "Usage: $0 <system-packages-file>"
    exit 1
fi

if [ ! -f "${PACKAGES_FILE}" ]; then
    echo "Error: system packages file not found at ${PACKAGES_FILE}"
    exit 1
fi

REQUIRED_PACKAGES=()
while IFS= read -r pkg; do
    # Ignore blank lines and comments to keep this file maintainable.
    if [ -z "${pkg}" ] || [[ "${pkg}" =~ ^[[:space:]]*# ]]; then
        continue
    fi
    REQUIRED_PACKAGES+=("${pkg}")
done < "${PACKAGES_FILE}"

MISSING_PACKAGES=()
for pkg in "${REQUIRED_PACKAGES[@]}"; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
        MISSING_PACKAGES+=("${pkg}")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo "Installing missing packages: ${MISSING_PACKAGES[*]}"
    sudo apt-get update
    sudo apt-get install -y "${MISSING_PACKAGES[@]}"
else
    echo "All required apt packages are already installed; skipping apt update/install."
fi
