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

---

For questions, please refer to the project repository or contact the developer.
