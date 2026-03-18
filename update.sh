#!/bin/bash

# Step 7: Install the python modules
sudo venv0/bin/pip install --upgrade pip
sudo venv0/bin/pip install --upgrade -r requirement.txt

# Ensure boot configuration tweaks are present (Bookworm paths)
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

# Refresh PixelDarts early-boot splash from local services
SERVICES_DIR="/home/rpi/dartsnut_rpi/services"

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

SYSTEMD_UNITS_UPDATED=0

if [ -d "${SERVICES_DIR}" ]; then
    install_or_update_service_unit "dartsnut_matrix.service"
    install_or_update_service_unit "dartsnut_python.service"
    install_or_update_service_unit "dartsnut_splash.service"

    if [ -f "${SERVICES_DIR}/splash_matrix" ]; then
        echo "Updating splash_matrix at /usr/local/bin/splash_matrix"
        sudo install -m 0755 "${SERVICES_DIR}/splash_matrix" /usr/local/bin/splash_matrix
    else
        echo "Warning: splash_matrix not found in ${SERVICES_DIR}; skipping binary update."
    fi

    SPLASH_DEST_PPM="/boot/pixeldarts_logo.ppm"
    if [ ! -d "/boot" ] && [ -d "/boot/firmware" ]; then
        SPLASH_DEST_PPM="/boot/firmware/pixeldarts_logo.ppm"
    fi

    if [ -f "${SERVICES_DIR}/pixeldarts_logo.ppm" ]; then
        echo "Updating pixeldarts_logo.ppm at ${SPLASH_DEST_PPM}"
        sudo install -m 0644 "${SERVICES_DIR}/pixeldarts_logo.ppm" "${SPLASH_DEST_PPM}"
    else
        echo "Warning: pixeldarts_logo.ppm not found in ${SERVICES_DIR}; skipping logo update."
    fi

    DEVICE_JSON_SRC="${SERVICES_DIR}/device.json"
    DEVICE_JSON_DEST="/boot/device.json"
    if [ -f "${DEVICE_JSON_SRC}" ]; then
        if [ ! -f "${DEVICE_JSON_DEST}" ]; then
            echo "Copying device.json to ${DEVICE_JSON_DEST}"
            sudo install -m 0644 "${DEVICE_JSON_SRC}" "${DEVICE_JSON_DEST}"
            if [ -f "${SPLASH_DEST_PPM}" ]; then
                sudo chown --reference="${SPLASH_DEST_PPM}" "${DEVICE_JSON_DEST}"
                sudo chmod --reference="${SPLASH_DEST_PPM}" "${DEVICE_JSON_DEST}"
            fi
        else
            echo "device.json already present at ${DEVICE_JSON_DEST}"
        fi
    else
        echo "Warning: device.json not found in ${SERVICES_DIR}; skipping device.json copy."
    fi

    if [ "${SYSTEMD_UNITS_UPDATED}" -eq 1 ]; then
        sudo systemctl daemon-reload
    fi
else
    echo "Warning: services directory not found at ${SERVICES_DIR}; skipping early-boot splash update."
fi

# Step 12: Setup cron job for automatic git updates
CRON_SCHEDULE="0 3 * * *"  # 3am every day
UPDATE_SCRIPT="/home/rpi/dartsnut_rpi/check_and_update.py"
PYTHON_INTERPRETER="/home/rpi/dartsnut_rpi/venv0/bin/python"
CRON_ENTRY="${CRON_SCHEDULE} ${PYTHON_INTERPRETER} ${UPDATE_SCRIPT} >> /var/log/dartsnut_update.log 2>&1"

# Get existing cron jobs (or empty if none exist)
EXISTING_CRON=$(sudo crontab -l 2>/dev/null || echo "")

# Check if a cron entry for check_and_update.py already exists
if echo "$EXISTING_CRON" | grep -q "check_and_update.py"; then
    echo "Update cron job already exists. Updating it..."
    # Remove existing entry for check_and_update.py and add new one
    NEW_CRON=$(echo "$EXISTING_CRON" | grep -v "check_and_update.py")
    # Add new entry
    echo "$NEW_CRON" | grep -v "^$" > /tmp/new_crontab.txt 2>/dev/null || true
    echo "$CRON_ENTRY" >> /tmp/new_crontab.txt
    sudo crontab /tmp/new_crontab.txt
    rm -f /tmp/new_crontab.txt
    echo "Updated cron job for automatic git updates"
else
    echo "Adding cron job for automatic git updates..."
    # Add new entry while preserving existing ones
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

# Restart dartsnut_python.service
sudo systemctl restart dartsnut_python.service
echo "Restarted dartsnut_python.service"
