# Dartsnut Raspberry Pi Runtime

This repository contains the Raspberry Pi runtime used by Dartsnut PixelDart
and PixelBoard devices. The release branch is kept as a minimal device tree:
runtime code, systemd services, media assets, update/install scripts, Python
dependency locks, and the compiled remote sync bridge needed on deployed units.

Product manuals, setup guides, and user-facing documentation are published in
the [Dartsnut docs](https://dartsnut.github.io/docs/).

## Project Structure

- `main.py` is the device entry point. It starts the display loop, local input
  handling, Bluetooth/BLE services, WebSocket control, and remote sync plumbing.
- `runtime/` contains bootstrapping, hardware adapters, display-loop support,
  WebSocket action routing, reset flows, and remote device configuration sync.
- `states/` contains the on-device UI states for menus, settings, widgets, and
  games.
- `domain/` and `core/` contain shared runtime models, helpers, retry utilities,
  and default app dependency manifests.
- `python_ble/` exposes the BLE GATT service used by companion clients.
- `python_websocket/` contains the local WebSocket API and device operations,
  including Bluetooth scan, pair, list, connect, and remove actions.
- `runtime/sync/`, `supabase_sync_bridge.py`, and `remote_sync_bridge.py`
  implement remote configuration and game sync around the external bridge.
- `mcp/` and `mcp_server/` provide the local firmware MCP integration.
- `assets_media/` stores fonts and images used by the runtime UI.
- `services/` contains the systemd units and Raspberry Pi service configuration.
- `scripts/` contains release, dependency, bridge-build, and local test helpers.
- `setup.sh`, `update.sh`, `uv`, `uv.lock`, and `system-packages.txt` support
  installation and updates on the device.

## Technology

- Python 3.11+ is the main runtime language.
- Dependencies are managed with `uv` and pinned in `pyproject.toml`/`uv.lock`.
- The display/UI stack uses `pygame-ce`, `Pillow`, and `numpy`.
- Bluetooth support uses BlueZ through `bluezero`, `pybluez-dartsnut`, and
  `bluetoothctl` operations.
- Local device control is exposed with FastAPI/Uvicorn WebSocket components.
- Remote sync is handled by a compiled `bridge` binary plus Python-side bridge
  clients. The bridge source is not planned for open source release because it
  protects customer privacy and remote data handling details.
- `DartsnutRGBMatrix` drives the RGB LED matrix and is based on
  [hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix).
- Raspberry Pi deployment is managed with systemd services.

## Bluetooth Controller Input

Bluetooth controller pairing and connection management are handled through the
runtime Bluetooth operations. Once a controller is connected, Linux exposes it
as a joystick device under `/dev/input/js*`. The runtime opens those joystick
devices in nonblocking mode, reads 8-byte Linux joystick events, and maps button
and axis events into the app button names (`btn_a`, `btn_b`, directions, and
`btn_home`). During in-game states, joystick input is left for the game unless
the exit overlay is active.
