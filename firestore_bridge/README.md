# Dartsnut Firestore Bridge

Go service that talks to Firestore (gRPC `google.firestore.v1.Firestore` API with Firebase ID-token auth) and to the Python app over a Unix socket. Python spawns this executable and sends device state; the bridge syncs with `devices/{deviceId}` and pushes config updates back.

## Requirements

- Go toolchain (1.22 or newer)
- Firebase project `my-dartsnut` with Firestore enabled and security rules allowing this client (email `pi@dartsnut.com`) to read/write the `devices` collection

## Configuration

Firebase project details, Web API key, and the email/password for this bridge are defined in `internal/config/config.go`. The bridge signs in with Firebase Auth (email/password) to obtain a Firebase ID token, then sends it as `Authorization: Bearer <idToken>` on all Firestore gRPC calls.

Python can override the bridge binary and socket path:

- **`DARTSNUT_FIRESTORE_BRIDGE`** – Path to the compiled Go bridge executable (recommended default: `./firestore_bridge/bridge` built from `cmd/bridge`).
- **`DARTSNUT_FIRESTORE_SOCKET`** – Unix socket path (default: `/tmp/dartsnut-firestore-sync.sock`).

## Build (Go bridge)

From this directory:

```bash
cd firestore_bridge
go build -o bridge ./cmd/bridge
```

The executable is written to `bridge` (Linux arm64 when built on the Pi).

## Protocol

- Python listens on the Unix socket and spawns the bridge with `--device-id=<ble_suffix>` and `--socket-path=<path>`.
- Bridge connects and sends `{"kind":"ready","payload":{}}`.
- Python sends `{"kind":"initial_state","payload":<full device state>}`.
- Bridge checks Firestore `devices/{deviceId}`: if the doc does not exist, it creates it with the payload; if it exists, it sends the current doc as `{"kind":"config_initial","payload":<data>}`. It then subscribes with a Firestore `Listen` stream and sends `{"kind":"config","payload":<data>}` on every change.
- Python sends `{"kind":"device_state","payload":<partial state>}` when brightness/volume/etc. change; the bridge merges into the Firestore document via a partial `Commit` update.

In current versions, Python derives `deviceId` from the full BLE adapter MAC address (e.g. `aa:bb:cc:dd:ee:ff`) and passes that as `--device-id`, so Firestore documents are keyed by the BLE MAC.

## Development

Run the Go bridge directly (no compilation step beyond `go build`):

```bash
go run ./cmd/bridge --device-id=1234 --socket-path=/tmp/dartsnut-firestore-sync.sock
```

Ensure Python has started first and is listening on the socket.

> Note: the legacy Node.js bridge in `src/index.ts` and related npm build scripts are now deprecated and kept only for reference. New deployments should use the Go bridge described above.
