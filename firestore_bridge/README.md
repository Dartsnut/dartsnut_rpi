# Dartsnut Firestore Bridge

Bun service that talks to Firestore (Firebase JS client SDK) and to the Python app over a Unix socket. Python spawns this executable and sends device state; the bridge syncs with `devices/{deviceId}` and pushes config updates back. Firebase config is in source and is bundled into the compiled executable.

## Requirements

- [Bun](https://bun.sh) (for building and development)
- Firebase project with Firestore enabled and security rules allowing read/write for your app

## Configuration

Firebase is initialized from the config in `src/index.ts` (no env vars required for Firestore). Ensure your Firestore security rules allow the client to read/write the `devices` collection as needed.

Python can override the bridge binary and socket path:

- **`DARTSNUT_FIRESTORE_BRIDGE`** – Path to the compiled executable (default: `./firestore_bridge/dist/dartsnut_firestore_bridge`).
- **`DARTSNUT_FIRESTORE_SOCKET`** – Unix socket path (default: `/tmp/dartsnut-firestore-sync.sock`).

## Build

From this directory:

```bash
bun install
bun run build
```

The executable is written to `dist/dartsnut_firestore_bridge`.

For Raspberry Pi (Linux ARM64), cross-compile:

```bash
bun build ./src/index.ts --compile --target=bun-linux-arm64 --outfile ./dist/dartsnut_firestore_bridge
```

## Protocol

- Python listens on the Unix socket and spawns the bridge with `--device-id=<ble_suffix>` and `--socket-path=<path>`.
- Bridge connects and sends `{"kind":"ready","payload":{}}`.
- Python sends `{"kind":"initial_state","payload":<full device state>}`.
- Bridge checks Firestore `devices/{deviceId}`: if the doc does not exist, it creates it with the payload; if it exists, it sends the current doc as `{"kind":"config","payload":<data>}`. It then subscribes with `onSnapshot` and sends `config` on every change.
- Python sends `{"kind":"device_state","payload":<partial state>}` when brightness/volume/etc. change; the bridge merges into the Firestore document.

## Development

Run without compiling (requires Bun on the host):

```bash
bun run start -- --device-id=1234 --socket-path=/tmp/dartsnut-firestore-sync.sock
```

Ensure Python has started first and is listening on the socket.
