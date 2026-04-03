# Dartsnut PixelDart & PixelBoard Raspberry Pi Deployment Guide

This guide explains how to deploy the Dartsnut PixelDart and PixelBoard runtime environment on a Raspberry Pi.

## 1. Install Raspberry Pi OS

Use the [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to install the operating system. It is recommended to choose **2025-10-01-raspios-trixie-arm64-lite**.
Link: https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-10-02/2025-10-01-raspios-trixie-arm64-lite.img.xz

## 2. Install Git

```bash
sudo apt update
sudo apt install git
```

## 3. Clone the Project Repository

```bash
sudo mkdir /home/rpi
cd /home/rpi/
sudo git clone https://github.com/Dartsnut/dartsnut_rpi.git 
```

## 4. Run the Setup Script

```bash
cd /home/rpi/dartsnut_rpi
sudo chmod +x setup.sh
sudo ./setup.sh
```

## 5. Configure Device Information

After installation, edit the device configuration file:

```bash
sudo vi /home/rpi/dartsnut_rpi/device.json
```

Paste the following content according to your device type:

**PixelBoard:**
```json
{"name": "PixelBoard", "serial": "1234567890", "model": "PixelBoard", "brightness": "100", "volume": "100"}
```

**PixelDart:**
```json
{"name": "PixelDart", "serial": "1234567890", "model": "PixelDart", "brightness": "100", "volume": "100"}
```

## 6. Reboot the Device

```bash
sudo reboot
```

## 7. Run Tests (Local or CI)

```bash
python -m pytest
```

Hardware-free integration suite:

```bash
python -m pytest -m integration tests/integration
```

Skip optional contract tests in CI:

```bash
python -m pytest -m "not contract"
```

Optional Supabase contract tests (manual/nightly):

```bash
RUN_SUPABASE_CONTRACT=1 SUPABASE_URL="<url>" SUPABASE_KEY="<key>" python -m pytest -m contract
```

Optional local Supabase bridge E2E (manual/nightly):

```bash
RUN_SUPABASE_LOCAL_E2E=1 SUPABASE_URL="http://127.0.0.1:54321" SUPABASE_KEY="<key>" python -m pytest tests/integration/test_supabase_local_bridge_e2e.py
```

Helper script:

```bash
SUPABASE_KEY="<key>" ./scripts/run_local_supabase_e2e.sh
```

Never commit real API keys in docs, scripts, or shell snippets.

## Logs

- **Python service:** `journalctl -u dartsnut_python.service` (see [docs/logging.md](docs/logging.md)).
- **Nightly `check_and_update.py` cron:** `/var/log/dartsnut_update.log` (same doc).

---

For questions, please refer to the project repository or contact the developer.
