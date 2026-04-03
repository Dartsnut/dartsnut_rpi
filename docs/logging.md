---
title: Logs
nav_order: 2
---

# Where logs go

## Python service (`dartsnut_python.service`)

The main app is started by systemd as `dartsnut_python.service`. Its **standard output and standard error** are collected by **journald**.

View recent logs:

```bash
journalctl -u dartsnut_python.service -e
```

Follow live:

```bash
journalctl -u dartsnut_python.service -f
```

### Log level (optional)

The service reads **`DARTSNUT_LOG_LEVEL`** (default **`INFO`**). Set it in a systemd drop-in or the unit file, for example `DEBUG` for more verbose output (includes WebSocket action/response traces). Invalid values fall back to `INFO`.

Framework noise is capped by default: **uvicorn** / **FastAPI** log at WARNING so you do not get server startup banners on every boot; **bluezero** avoids duplicate lines by routing BLE logs through the root handler only.

At **INFO**, expect lines for: UI state changes (`domain.app_context` → `ui state: old -> new`), network Wi‑Fi/internet transitions, Supabase bridge connect/disconnect, remote config snapshots, game/widget lifecycle, BLE connect/disconnect, dim-window enter/exit, and machine-state persistence. Use **DEBUG** for BLE UART payloads and WebSocket request/response dumps.

## Automatic git update check (cron)

`setup.sh` and `update.sh` install a **root crontab** entry that runs `check_and_update.py` once per day. That job appends **both stdout and stderr** to:

```text
/var/log/dartsnut_update.log
```

View it:

```bash
sudo tail -f /var/log/dartsnut_update.log
```

To confirm the job exists:

```bash
sudo crontab -l
```

The schedule is defined in those scripts (currently daily at 03:00, local time).
