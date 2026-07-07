#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_KERNEL="${DARTSNUT_KERNEL_ROLLBACK_TARGET:-6.12.47+rpt-rpi-v8}"
TARGET_VERSION="${DARTSNUT_KERNEL_ROLLBACK_VERSION:-1:6.12.47-1+rpt1}"
BOOT_DIR="${DARTSNUT_BOOT_DIR:-/boot}"
FIRMWARE_DIR="${DARTSNUT_FIRMWARE_DIR:-/boot/firmware}"
CURRENT_KERNEL="${DARTSNUT_CURRENT_KERNEL:-$(uname -r)}"

if [ "${DARTSNUT_KERNEL_ROLLBACK_NO_SUDO:-0}" = "1" ]; then
    SUDO=""
elif [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

if [[ "${CURRENT_KERNEL}" != 6.18.* ]]; then
    echo "Kernel rollback not needed: current kernel is ${CURRENT_KERNEL}"
    exit 0
fi

if [ "${CURRENT_KERNEL}" = "${TARGET_KERNEL}" ]; then
    echo "Kernel rollback not needed: already running ${TARGET_KERNEL}"
    exit 0
fi

IMAGE_PKG="linux-image-${TARGET_KERNEL}"
HEADERS_PKG="linux-headers-${TARGET_KERNEL}"
KERNEL_SRC="${BOOT_DIR}/vmlinuz-${TARGET_KERNEL}"
INITRD_SRC="${BOOT_DIR}/initrd.img-${TARGET_KERNEL}"
KERNEL_DST="${FIRMWARE_DIR}/kernel8.img"
INITRD_DST="${FIRMWARE_DIR}/initramfs8"

echo "== Raspberry Pi kernel rollback =="
echo "Current kernel: ${CURRENT_KERNEL}"
echo "Target kernel:  ${TARGET_KERNEL}"
echo "Target version: ${TARGET_VERSION}"

${SUDO} apt-get update
${SUDO} apt-get install -y --allow-downgrades "${IMAGE_PKG}=${TARGET_VERSION}"

if apt-cache show "${HEADERS_PKG}=${TARGET_VERSION}" >/dev/null 2>&1; then
    ${SUDO} apt-get install -y --allow-downgrades "${HEADERS_PKG}=${TARGET_VERSION}"
else
    echo "Headers not available or not needed; skipping ${HEADERS_PKG}."
fi

if [ ! -f "${KERNEL_SRC}" ]; then
    echo "Error: installed kernel image not found: ${KERNEL_SRC}" >&2
    exit 1
fi

if [ ! -f "${INITRD_SRC}" ]; then
    echo "Error: installed initramfs not found: ${INITRD_SRC}" >&2
    exit 1
fi

if [ ! -d "${FIRMWARE_DIR}" ]; then
    echo "Error: firmware boot directory not found: ${FIRMWARE_DIR}" >&2
    exit 1
fi

if [ -f "${KERNEL_DST}" ] && [ ! -f "${KERNEL_DST}.${CURRENT_KERNEL}.bak" ]; then
    ${SUDO} cp -a "${KERNEL_DST}" "${KERNEL_DST}.${CURRENT_KERNEL}.bak"
fi

if [ -f "${INITRD_DST}" ] && [ ! -f "${INITRD_DST}.${CURRENT_KERNEL}.bak" ]; then
    ${SUDO} cp -a "${INITRD_DST}" "${INITRD_DST}.${CURRENT_KERNEL}.bak"
fi

${SUDO} install -m 0755 "${KERNEL_SRC}" "${KERNEL_DST}"
${SUDO} install -m 0755 "${INITRD_SRC}" "${INITRD_DST}"

for pkg in \
    linux-image-rpi-v8 \
    linux-headers-rpi-v8 \
    linux-image-rpi-2712 \
    linux-headers-rpi-2712 \
    "${IMAGE_PKG}" \
    "${HEADERS_PKG}"; do
    if dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -q "install ok installed"; then
        ${SUDO} apt-mark hold "${pkg}" >/dev/null
    fi
done

sync

echo
echo "Kernel rollback staged. Rebooting now to boot ${TARGET_KERNEL}."
echo "After reconnecting, verify with:"
echo "  uname -r"
echo "  bluetoothctl show | grep ActiveInstances"
${SUDO} reboot
