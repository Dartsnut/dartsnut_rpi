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

The service reads **`DARTSNUT_LOG_LEVEL`** (default **`INFO`**). Set it in a systemd drop-in or the unit file, for example `DEBUG` for more verbose output. Invalid values fall back to `INFO`.

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
