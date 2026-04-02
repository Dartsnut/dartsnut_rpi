#!/bin/bash

REPO_DIR="/home/rpi/dartsnut_rpi"
SERVICES_DIR="${REPO_DIR}/services"
VENV_DIR="${REPO_DIR}/venv0"
VENV_PIP="${VENV_DIR}/bin/pip"
SYSTEM_PACKAGES_FILE="${REPO_DIR}/system-packages.txt"
INSTALL_PACKAGES_SCRIPT="${REPO_DIR}/scripts/install_system_packages.sh"

SYSTEMD_UNITS_UPDATED=0

install_or_update_service_unit() {
    local unit_name="$1"
    local src="${SERVICES_DIR}/${unit_name}"
    local dst="/etc/systemd/system/${unit_name}"

    if [ ! -f "${src}" ]; then
        echo "Warning: ${unit_name} not found in ${SERVICES_DIR}; skipping."
        return 0
    fi

    if [ ! -f "${dst}" ] || ! cmp -s "${src}" "${dst}"; then
        echo "Installing/updating ${unit_name}"
        sudo install -m 0644 "${src}" "${dst}"
        SYSTEMD_UNITS_UPDATED=1
    else
        echo "${unit_name} already up to date, skipping."
    fi
}

install_splash_assets_if_present() {
    if [ ! -d "${SERVICES_DIR}" ]; then
        echo "Warning: services directory not found at ${SERVICES_DIR}; skipping early-boot splash setup."
        return 0
    fi

    local splash_dest_ppm="/boot/logo.ppm"
    if [ ! -d "/boot" ] && [ -d "/boot/firmware" ]; then
        splash_dest_ppm="/boot/firmware/logo.ppm"
    fi

    if [ -f "${SERVICES_DIR}/logo.ppm" ]; then
        echo "Copying logo.ppm to ${splash_dest_ppm}"
        sudo install -m 0644 "${SERVICES_DIR}/logo.ppm" "${splash_dest_ppm}"
    else
        echo "Warning: logo.ppm not found in ${SERVICES_DIR}; skipping logo copy."
    fi

    local device_json_dest="/boot/device.json"
    if [ ! -f "${device_json_dest}" ]; then
        local repo_device_json="${REPO_DIR}/device.json"
        if [ -f "${repo_device_json}" ]; then
            local model
            model=$(grep -o '"model"[[:space:]]*:[[:space:]]*"[^"]*"' "${repo_device_json}" | head -1 | sed 's/.*"model"[[:space:]]*:[[:space:]]*"//;s/"//')
            if [ -n "${model}" ]; then
                echo "Creating partial device.json at ${device_json_dest} with model=${model}"
                echo "{\"model\": \"${model}\", \"brightness\": \"100\"}" | sudo tee "${device_json_dest}" > /dev/null
                if [ -f "${splash_dest_ppm}" ]; then
                    sudo chown --reference="${splash_dest_ppm}" "${device_json_dest}"
                    sudo chmod --reference="${splash_dest_ppm}" "${device_json_dest}"
                fi
            else
                echo "Warning: could not read model from ${repo_device_json}; skipping /boot/device.json creation."
            fi
        else
            echo "Warning: ${repo_device_json} not found; skipping /boot/device.json creation."
        fi
    else
        echo "device.json already present at ${device_json_dest}"
    fi
}

echo "== Boot configuration (Bookworm) =="

# Ensure CPU isolation.
if ! grep -qw "isolcpus=3" /boot/firmware/cmdline.txt; then
    echo -n " isolcpus=3" | sudo tee -a /boot/firmware/cmdline.txt > /dev/null
    echo "Added isolcpus=3 to cmdline.txt"
else
    echo "isolcpus=3 already present in cmdline.txt"
fi

# Disable audio (and ensure kms overlay has noaudio).
sudo sed -i 's/dtparam=audio=on/dtparam=audio=off/g' /boot/firmware/config.txt
sudo sed -i '/dtoverlay=vc4-kms-v3d$/ s/$/,noaudio/' /boot/firmware/config.txt

# Early splash tweaks.
if ! grep -q "^disable_splash=1" /boot/firmware/config.txt; then
    echo "disable_splash=1" | sudo tee -a /boot/firmware/config.txt > /dev/null
    echo "Added disable_splash=1 to /boot/firmware/config.txt"
else
    echo "disable_splash=1 already present in /boot/firmware/config.txt"
fi

if ! grep -q "^boot_delay=0" /boot/firmware/config.txt; then
    echo "boot_delay=0" | sudo tee -a /boot/firmware/config.txt > /dev/null
    echo "Added boot_delay=0 to /boot/firmware/config.txt"
else
    echo "boot_delay=0 already present in /boot/firmware/config.txt"
fi

if ! grep -qw "quiet" /boot/firmware/cmdline.txt; then
    sudo sed -i 's/$/ quiet/' /boot/firmware/cmdline.txt
    echo "Added quiet to /boot/firmware/cmdline.txt"
else
    echo "quiet already present in /boot/firmware/cmdline.txt"
fi

echo "== System packages / Python venv =="
"${INSTALL_PACKAGES_SCRIPT}" "${SYSTEM_PACKAGES_FILE}"

sudo python3 -m venv "${VENV_DIR}"

sudo "${VENV_PIP}" install --upgrade pip
sudo "${VENV_PIP}" install -r "${REPO_DIR}/requirements.txt"

echo "== Kernel / device configuration =="

# Disable swap.
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=0/' /etc/dphys-swapfile
if ! grep -q "^CONF_SWAPSIZE=" /etc/dphys-swapfile; then
    echo "CONF_SWAPSIZE=0" | sudo tee -a /etc/dphys-swapfile
fi

# Audio device blacklist (for HDMI audio).
if [ ! -f /etc/modprobe.d/blacklist-bcm2835.conf ]; then
    echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-bcm2835.conf > /dev/null
    echo "Created blacklist-bcm2835.conf"
else
    echo "blacklist-bcm2835.conf already exists"
fi

# udev rule for HID.
if [ ! -f /etc/udev/rules.d/99-hid.rules ]; then
    echo 'SUBSYSTEM=="hidraw",MODE="0666"' | sudo tee /etc/udev/rules.d/99-hid.rules > /dev/null
    echo "Created 99-hid.rules"
else
    echo "99-hid.rules already exists"
fi
sudo udevadm control --reload-rules
sudo udevadm trigger

# Bluetooth discovery behavior.
sudo sed -i 's/^#ReverseServiceDiscovery = true/ReverseServiceDiscovery = false/' /etc/bluetooth/main.conf
echo "Updated ReverseServiceDiscovery in /etc/bluetooth/main.conf"

echo "== Services and splash assets =="

if [ ! -d "${SERVICES_DIR}" ]; then
    echo "Error: services directory not found at ${SERVICES_DIR}; cannot install systemd units."
    exit 1
fi

install_splash_assets_if_present

install_or_update_service_unit "dartsnut_matrix.service"
install_or_update_service_unit "dartsnut_python.service"
install_or_update_service_unit "dartsnut_splash.service"

if [ "${SYSTEMD_UNITS_UPDATED}" -eq 1 ]; then
    sudo systemctl daemon-reload
fi

sudo systemctl enable dartsnut_matrix.service
sudo systemctl enable dartsnut_python.service
sudo systemctl enable dartsnut_splash.service

echo "Service setup steps complete."

echo "== Git + cron auto-update =="

sudo git config --global --add safe.directory "${REPO_DIR}"

CRON_SCHEDULE="0 3 * * *"  # 3am every day
UPDATE_SCRIPT="${REPO_DIR}/check_and_update.py"
PYTHON_INTERPRETER="${VENV_DIR}/bin/python"
CRON_ENTRY="${CRON_SCHEDULE} ${PYTHON_INTERPRETER} ${UPDATE_SCRIPT} >> /var/log/dartsnut_update.log 2>&1"

EXISTING_CRON=$(sudo crontab -l 2>/dev/null || echo "")

if echo "$EXISTING_CRON" | grep -q "check_and_update.py"; then
    echo "Update cron job already exists. Updating it..."
    NEW_CRON=$(echo "$EXISTING_CRON" | grep -v "check_and_update.py")
    echo "$NEW_CRON" | grep -v "^$" > /tmp/new_crontab.txt 2>/dev/null || true
    echo "$CRON_ENTRY" >> /tmp/new_crontab.txt
    sudo crontab /tmp/new_crontab.txt
    rm -f /tmp/new_crontab.txt
    echo "Updated cron job for automatic git updates"
else
    echo "Adding cron job for automatic git updates..."
    if [ -n "$EXISTING_CRON" ]; then
        echo "$EXISTING_CRON" > /tmp/new_crontab.txt
    else
        touch /tmp/new_crontab.txt
    fi
    echo "$CRON_ENTRY" >> /tmp/new_crontab.txt
    sudo crontab /tmp/new_crontab.txt
    rm -f /tmp/new_crontab.txt
    echo "Added cron job for automatic git updates"
fi
