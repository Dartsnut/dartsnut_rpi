#!/bin/bash

# Step 7: Install the python modules
sudo venv0/bin/pip install --upgrade pip
sudo venv0/bin/pip install --upgrade -r requirement.txt

# Step 12: Setup cron job for automatic git updates
CRON_SCHEDULE="* * * * *"  # Every minute (for testing). Change this to adjust schedule.
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
