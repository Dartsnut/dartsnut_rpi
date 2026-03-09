## WebSocket API

This document describes the JSON‑based WebSocket API exposed by the Dartsnut firmware. It is intended for mobile and web apps that manage a device already connected to the local network.

Typical use cases:

- Discover devices on the LAN
- Configure brightness, volume, time zone, and dimming behavior
- Manage apps/games: upload files, list/remove apps, download games, track download progress
- Control games and widgets
- Manage Wi‑Fi/SSH and check system version/update status
- Manage user data and game playtime

For initial Wi‑Fi onboarding before the device is online, use the BLE API described in `bluetooth-api.md`.

---

## 1. Connection Details

- **Transport:** WebSocket (over TCP)
- **Host:** IP address of the device (on the same LAN as the client)
- **Port:** `9251`
- **Path:** `/ws`
- **CORS:** All origins are allowed (no special headers required)
- **Subprotocols:** None required

### Example Connection (Pseudo‑Code)

```text
// Pseudocode (language‑agnostic)
const ws = new WebSocket("ws://192.168.0.42:9251/ws");

ws.onopen = () => {
  // Send a request once connected
  ws.send(JSON.stringify({
    action: "get_device_info",
    req_id: "1"
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  // msg.req_id will match the one in your request
};
```

---

## 2. Message Envelope

All WebSocket messages are JSON objects.

### 2.1 Request Format

Every request sent by the client must include:

- **action** (string, required): which operation to perform.
- **req_id** (string or number, recommended): an opaque correlation ID chosen by the client.
- Additional fields: action‑specific parameters.

Example:

```json
{
  "action": "set_brightness",
  "req_id": "42",
  "brightness": 80
}
```

### 2.2 Success Response Format

Handlers typically return a dict that includes:

- **action** (string): the action name (e.g. `"set_brightness"`).
- **req_id** (string/number): copied from the request by the server wrapper.
- Other fields: action‑specific data (e.g. `message`, `brightness`, `file_list`, etc.).

Example:

```json
{
  "action": "set_brightness",
  "message": "Success",
  "req_id": "42"
}
```

Another example (querying brightness):

```json
{
  "action": "get_brightness",
  "brightness": 80,
  "req_id": "100"
}
```

### 2.3 Error Response Format

All errors use a standardized format (shared with BLE). The core shape:

```json
{
  "action": "action_name",
  "error": "User-friendly error message (XX-YY)",
  "error_code": "XXXX",
  "req_id": "42"
}
```

- **action**: the action that failed.
- **error**: user‑friendly message plus a short code suffix `(XX-YY)`.
- **error_code**: the 4‑digit machine‑readable code (see `ERROR_CODES.md`).
- **req_id**: the same ID as in the request, when available.

On invalid JSON requests, an error is sent with `action: "unknown"` and an `INVALID_JSON` error code.

The full list and semantics of error codes are documented in `ERROR_CODES.md`.

---

## 3. Action Reference

This section groups actions by domain. All actions are invoked by sending a JSON object with `"action": "<action_name>"` plus any required parameters, as described below.

### 3.1 File & App Management

These actions operate on files in the device’s `apps` directory or on installed apps/games.

#### 3.1.1 `send_file`

Upload a file (e.g. app bundle or asset) to the device.

- **Request fields:**
  - `file_name` (string, required): relative path inside `apps/`.
  - `file_data` (string, required): Base64‑encoded file contents.

```json
{
  "action": "send_file",
  "req_id": "1",
  "file_name": "my_app/data.bin",
  "file_data": "<base64>"
}
```

- **Success response:**

```json
{
  "action": "send_file",
  "file_name": "my_app/data.bin",
  "message": "Success",
  "req_id": "1"
}
```

#### 3.1.2 `get_file`

Download a file from the device.

- **Request fields:**
  - `file_name` (string, required): relative path in `apps/`.

```json
{
  "action": "get_file",
  "req_id": "2",
  "file_name": "my_app/data.bin"
}
```

- **Success response:**

```json
{
  "action": "get_file",
  "file_name": "my_app/data.bin",
  "file_data": "<base64>",
  "req_id": "2"
}
```

#### 3.1.3 `read_json`

Read a JSON file from the `apps` directory. Long string fields are truncated to 100 characters for safety.

- **Request fields:**
  - `file_path` (string, required): relative path in `apps/`.

```json
{
  "action": "read_json",
  "req_id": "3",
  "file_path": "my_app/conf.json"
}
```

- **Success response:**

```json
{
  "action": "read_json",
  "file_path": "my_app/conf.json",
  "content": "<base64-encoded JSON string>",
  "req_id": "3"
}
```

#### 3.1.4 `write_json`

Write a JSON file into `apps`.

- **Request fields:**
  - `file_path` (string, required).
  - `content` (string, required): Base64‑encoded JSON string.

```json
{
  "action": "write_json",
  "req_id": "4",
  "file_path": "my_app/conf.json",
  "content": "<base64-encoded JSON>"
}
```

- **Success response:**

```json
{
  "action": "write_json",
  "file_path": "my_app/conf.json",
  "message": "Success",
  "req_id": "4"
}
```

#### 3.1.5 `remove_directory`

Remove an app/game directory (and clear its download progress).

- **Request fields:**
  - `directory` (string, required): relative directory under `apps/` (often the game ID).

```json
{
  "action": "remove_directory",
  "req_id": "5",
  "directory": "my_game"
}
```

- **Success response:**

```json
{
  "action": "remove_directory",
  "directory": "my_game",
  "message": "Success",
  "req_id": "5"
}
```

#### 3.1.6 `create_directory`

Create a directory under `apps/`.

- **Request fields:**
  - `directory` (string, required).

```json
{
  "action": "create_directory",
  "req_id": "6",
  "directory": "my_game"
}
```

- **Success response:**

```json
{
  "action": "create_directory",
  "directory": "my_game",
  "message": "Success",
  "req_id": "6"
}
```

If the directory already exists, you receive an error with `DIRECTORY_ALREADY_EXISTS (1006)`.

#### 3.1.7 `list_files`

List files in a directory under `apps/`.

- **Request fields:**
  - `directory` (string, required).

```json
{
  "action": "list_files",
  "req_id": "7",
  "directory": "my_game"
}
```

- **Success response:**

```json
{
  "action": "list_files",
  "directory": "my_game",
  "file_list": ["conf.json", "data.bin"],
  "req_id": "7"
}
```

#### 3.1.8 `list_apps`

List installed apps/games.

- **Request:**

```json
{
  "action": "list_apps",
  "req_id": "8"
}
```

- **Success response:**

```json
{
  "action": "list_apps",
  "apps": [
    {
      "name": "my_game",
      "conf": "<base64-encoded JSON without previews>"
    }
  ],
  "req_id": "8"
}
```

#### 3.1.9 `get_file_md5`

Get the MD5 checksum of a file under `apps/`.

- **Request fields:**
  - `file_name` (string, required).

```json
{
  "action": "get_file_md5",
  "req_id": "9",
  "file_name": "my_game/game.tar.gz"
}
```

- **Success response:**

```json
{
  "action": "get_file_md5",
  "file_name": "my_game/game.tar.gz",
  "md5": "abc123...",
  "req_id": "9"
}
```

---

### 3.2 Display & Device Settings

#### 3.2.1 `set_brightness`

Set display brightness.

- **Request fields:**
  - `brightness` (integer, required): between 10 and 100.

```json
{
  "action": "set_brightness",
  "req_id": "10",
  "brightness": 80
}
```

- **Success response:**

```json
{
  "action": "set_brightness",
  "message": "Success",
  "req_id": "10"
}
```

> **Note:** The on-device settings UI presents brightness as 9 discrete levels (1–9) that internally map to raw brightness values `[10, 20, 30, 40, 50, 59, 73, 79, 100]`. The WebSocket API continues to accept and return raw brightness values in the 10–100 range.

If the value is invalid or out of range, you receive an error with `INVALID_BRIGHTNESS (3005)` or `INVALID_INPUT (3001)`.

#### 3.2.2 `get_brightness`

Query current brightness.

```json
{
  "action": "get_brightness",
  "req_id": "11"
}
```

- **Success response:**

```json
{
  "action": "get_brightness",
  "brightness": 80,
  "req_id": "11"
}
```

#### 3.2.3 `set_volume`

Set device volume.

- **Request fields:**
  - `volume` (integer, required): between 0 and 100.

```json
{
  "action": "set_volume",
  "req_id": "12",
  "volume": 50
}
```

- **Success response:**

```json
{
  "action": "set_volume",
  "message": "Success",
  "req_id": "12"
}
```

Invalid values return an `INVALID_INPUT (3001)` error.

#### 3.2.4 `get_volume`

Query current volume.

```json
{
  "action": "get_volume",
  "req_id": "13"
}
```

- **Success response:**

```json
{
  "action": "get_volume",
  "volume": 50,
  "req_id": "13"
}
```

#### 3.2.5 `set_time_zone`

Set the device’s time zone (implementation provided by firmware host).

- **Request fields:**
  - `time_zone` (string, optional, default `"UTC"`).

```json
{
  "action": "set_time_zone",
  "req_id": "14",
  "time_zone": "Europe/Berlin"
}
```

- **Success response:**

```json
{
  "action": "set_time_zone",
  "message": "Success",
  "req_id": "14"
}
```

#### 3.2.6 `get_dim_window`

Get current dimming configuration (from `device.json`).

```json
{
  "action": "get_dim_window",
  "req_id": "15"
}
```

- **Success response:**

```json
{
  "action": "get_dim_window",
  "dim_window_start": "22:00",
  "dim_window_end": "07:00",
  "dim_level": 10,
  "dim_restore_seconds": 30,
  "dim_window_enabled": true,
  "req_id": "15"
}
```

#### 3.2.7 `set_dim_window`

Configure the automatic dim window.

- **Request fields:**
  - `dim_window_start` (string, optional): `"HH:MM"` (24‑hour).
  - `dim_window_end` (string, optional): `"HH:MM"` (24‑hour).
  - `dim_level` (integer, optional): 1–100, default 10.
  - `dim_restore_seconds` (integer, optional): 5–300, default 30.
  - `dim_window_enabled` (bool/string, optional): enable/disable behavior.

Rules:

- If **both** `dim_window_start` and `dim_window_end` are empty/missing, the call either:
  - Just toggles `dim_window_enabled`, or
  - Clears dim window settings.
- If one of start/end is provided but not the other, it returns an `INVALID_INPUT` error.
- Time strings must be valid `HH:MM`.

Example (enable dimming):

```json
{
  "action": "set_dim_window",
  "req_id": "16",
  "dim_window_start": "22:00",
  "dim_window_end": "07:00",
  "dim_level": 10,
  "dim_restore_seconds": 30,
  "dim_window_enabled": true
}
```

- **Success response:**

```json
{
  "action": "set_dim_window",
  "message": "Success",
  "req_id": "16"
}
```

If the change succeeds and the firmware registered a `trigger_dim_check` callback, the device will re‑evaluate brightness immediately.

#### 3.2.8 `get_device_info`

Get device info, including Wi‑Fi MAC and SSID.

```json
{
  "action": "get_device_info",
  "req_id": "17"
}
```

- **Success response:**

```json
{
  "action": "get_device_info",
  "device_info": {
    "name": "PixelDart",
    "serial": "1234567890",
    "model": "PixelDart",
    "brightness": "100",
    "volume": "100",
    "mac_address": "aa:bb:cc:dd:ee:ff",
    "ssid": "MyWiFi"
  },
  "req_id": "17"
}
```

#### 3.2.9 `set_device_name`

Set the `name` field in `device.json`.

```json
{
  "action": "set_device_name",
  "req_id": "18",
  "device_name": "Living Room Board"
}
```

- **Success response:**

```json
{
  "action": "set_device_name",
  "device_name": "Living Room Board",
  "message": "Success",
  "req_id": "18"
}
```

#### 3.2.10 `locate_device`

Trigger a “locate” behavior on the device (e.g. LEDs or sound).

```json
{
  "action": "locate_device",
  "req_id": "19"
}
```

- **Success response:**

```json
{
  "action": "locate_device",
  "message": "Success",
  "req_id": "19"
}
```

#### 3.2.11 `reload_conf`

Request the firmware to reload its configuration.

```json
{
  "action": "reload_conf",
  "req_id": "20"
}
```

- **Success response:**

```json
{
  "action": "reload_conf",
  "message": "Success",
  "req_id": "20"
}
```

---

### 3.3 Bluetooth Management over WebSocket

These actions manage **other** Bluetooth devices (controllers, speakers, etc.) using the host’s Bluetooth stack.

#### 3.3.1 `bluetooth_scan`

Scan for nearby Bluetooth devices likely to be controllers/headphones/speakers.

```json
{
  "action": "bluetooth_scan",
  "req_id": "30"
}
```

- **Success response:**

```json
{
  "action": "bluetooth_scan",
  "devices": [
    { "address": "AA:BB:CC:DD:EE:FF", "name": "Game Controller" }
  ],
  "req_id": "30"
}
```

#### 3.3.2 `bluetooth_list`

List paired devices.

```json
{
  "action": "bluetooth_list",
  "req_id": "31"
}
```

- **Success response:**

```json
{
  "action": "bluetooth_list",
  "devices": [
    { "address": "AA:BB:CC:DD:EE:FF", "name": "Game Controller" }
  ],
  "req_id": "31"
}
```

#### 3.3.3 `bluetooth_remove`

Unpair and disconnect a device.

- **Request fields:**
  - `address` (string, required): MAC address.

```json
{
  "action": "bluetooth_remove",
  "req_id": "32",
  "address": "AA:BB:CC:DD:EE:FF"
}
```

- **Success response:**

```json
{
  "action": "bluetooth_remove",
  "address": "AA:BB:CC:DD:EE:FF",
  "message": "Success",
  "req_id": "32"
}
```

#### 3.3.4 `bluetooth_connect`

Pair, trust, and connect to a Bluetooth device.

- **Request fields:**
  - `address` (string, required): MAC address.

```json
{
  "action": "bluetooth_connect",
  "req_id": "33",
  "address": "AA:BB:CC:DD:EE:FF"
}
```

- **Success response:**

```json
{
  "action": "bluetooth_connect",
  "address": "AA:BB:CC:DD:EE:FF",
  "message": "Success",
  "req_id": "33"
}
```

Failures are reported with Bluetooth‑specific error codes (5001–5008).

---

### 3.4 Game Download & Widgets

#### 3.4.1 `download_app`

Download and install a game/app. This action supports two modes:

1. **Async with progress tracking by `game_id`** (recommended).
2. **Legacy sync download by direct URL/MD5** (not recommended for new clients).

**Async mode (recommended):**

- **Request fields:**
  - `game_id` (string, required).
  - `url` (string, optional).
  - `md5` (string, optional).

If you provide `game_id`, `url`, and `md5`, the device starts an async download using the provided URL and MD5 and tracks progress by `game_id`. If you provide only `game_id`, the device fetches download info from `api.dartsnut.com`.

Example (async with URL and MD5):

```json
{
  "action": "download_app",
  "req_id": "40",
  "game_id": "my_game",
  "url": "https://example.com/my_game.tar.gz",
  "md5": "abc123..."
}
```

- **Success response (immediate):**

```json
{
  "action": "download_app",
  "game_id": "my_game",
  "message": "Success",
  "req_id": "40"
}
```

The actual download and extraction run in a background thread. Track progress with `get_download_progress`.

If another download for the same `game_id` or URL/MD5 is in progress, you receive an error with `DOWNLOAD_ALREADY_IN_PROGRESS (2006)`.

**Legacy sync mode (URL + MD5, no game_id):**

- **Request fields:**
  - `url` (string, required).
  - `md5` (string, required).

```json
{
  "action": "download_app",
  "req_id": "41",
  "url": "https://example.com/my_game.tar.gz",
  "md5": "abc123..."
}
```

The server starts the download in a worker task and sends a final response (or error) once complete.

#### 3.4.2 `get_download_progress`

Query download progress for one or more games.

- **Request fields:**
  - `game_ids` (array of strings, preferred), or
  - `game_id` (string, legacy).

Example (multiple):

```json
{
  "action": "get_download_progress",
  "req_id": "42",
  "game_ids": ["game1", "game2"]
}
```

- **Success response:**

```json
{
  "action": "get_download_progress",
  "progresses": {
    "game1": {
      "game_id": "game1",
      "progress": 75,
      "status": "downloading",
      "error": null,
      "version": "1.0.0"
    },
    "game2": {
      "game_id": "game2",
      "progress": 0,
      "status": "not_found",
      "error": null
    }
  },
  "req_id": "42"
}
```

Single `game_id` requests return a single object instead.

If neither `game_ids` nor `game_id` is provided, you receive an error with `MISSING_PARAMETER (3002)`.

#### 3.4.3 `start_game`

Request the device to launch a game by `game_id`.

- **Request fields:**
  - `game_id` (string, required).

```json
{
  "action": "start_game",
  "req_id": "43",
  "game_id": "my_game"
}
```

- **Success response:**

```json
{
  "action": "start_game",
  "message": "Game started",
  "req_id": "43"
}
```

If the firmware host does not support game starting, you get `FUNCTION_NOT_AVAILABLE (7002)`. Other failures return `COMMAND_FAILED (4001)` or a more specific error.

#### 3.4.4 `get_widgets_screen`

Retrieve the serialized framebuffer(s) for widgets on the device.

```json
{
  "action": "get_widgets_screen",
  "req_id": "44"
}
```

- **Success response:**

```json
{
  "action": "get_widgets_screen",
  "framebuffers": [ /* implementation-specific data */ ],
  "req_id": "44"
}
```

If widgets are not supported, you receive `FUNCTION_NOT_AVAILABLE (7002)`.

---

### 3.5 Network & System Control

#### 3.5.1 `get_wifi_rssi`

Get Wi‑Fi signal strength (RSSI).

```json
{
  "action": "get_wifi_rssi",
  "req_id": "50"
}
```

- **Success response:**

```json
{
  "action": "get_wifi_rssi",
  "rssi": "-56",
  "req_id": "50"
}
```

#### 3.5.2 `forget_wifi`

Forget all saved Wi‑Fi connections and toggle Wi‑Fi off/on. This action does not return a structured success response (no body is sent beyond the function running).

```json
{
  "action": "forget_wifi",
  "req_id": "51"
}
```

Client should treat this as fire‑and‑forget and call `get_device_info` or `wifi_status` (BLE) later to confirm state.

#### 3.5.3 `reboot`

Request a system reboot. This is a fire‑and‑forget command; the device will restart and may close the WebSocket without sending a final response.

```json
{
  "action": "reboot",
  "req_id": "52"
}
```

#### 3.5.4 `get_ssh_status`

Check SSH service status.

```json
{
  "action": "get_ssh_status",
  "req_id": "53"
}
```

- **Success response:**

```json
{
  "action": "get_ssh_status",
  "status": "active",
  "req_id": "53"
}
```

#### 3.5.5 `start_ssh` / `stop_ssh`

Start or stop the SSH service.

```json
{
  "action": "start_ssh",
  "req_id": "54"
}
```

```json
{
  "action": "stop_ssh",
  "req_id": "55"
}
```

- **Success responses:**

```json
{
  "action": "start_ssh",
  "message": "SSH started successfully",
  "req_id": "54"
}
```

```json
{
  "action": "stop_ssh",
  "message": "SSH stopped successfully",
  "req_id": "55"
}
```

#### 3.5.6 `get_version`

Get the firmware version (Git tag).

```json
{
  "action": "get_version",
  "req_id": "56"
}
```

- **Success response:**

```json
{
  "action": "get_version",
  "version": "v1.0.14",
  "req_id": "56"
}
```

#### 3.5.7 `check_update`

Check the latest available version from the release branch.

```json
{
  "action": "check_update",
  "req_id": "57"
}
```

- **Success response:**

```json
{
  "action": "check_update",
  "latest_version": "v1.0.15",
  "req_id": "57"
}
```

#### 3.5.8 `perform_update`

Perform a firmware update from the release branch. This may be a long‑running operation and may restart services.

```json
{
  "action": "perform_update",
  "req_id": "58"
}
```

- **Success response:**

```json
{
  "action": "perform_update",
  "message": "Update successful",
  "req_id": "58"
}
```

On failure, you receive Git‑specific error codes (`6003`, `6004`, `6005`).

---

### 3.6 User Data & Playtime

#### 3.6.1 `get_user_data`

Fetch stored user info and per‑game playtime from persistent storage.

```json
{
  "action": "get_user_data",
  "req_id": "60"
}
```

- **Success response:**

```json
{
  "action": "get_user_data",
  "user_id": "user-123",
  "jwt_token": "…",
  "refresh_token": "…",
  "game_playtimes": {
    "my_game": 3600
  },
  "req_id": "60"
}
```

#### 3.6.2 `update_user_info`

Update user ID and/or tokens.

- **Request fields:** all optional; only provided fields are updated.
  - `user_id` (string, optional)
  - `jwt_token` (string, optional)
  - `refresh_token` (string, optional)

```json
{
  "action": "update_user_info",
  "req_id": "61",
  "user_id": "user-123",
  "jwt_token": "…",
  "refresh_token": "…"
}
```

- **Success response:**

```json
{
  "action": "update_user_info",
  "message": "Success",
  "req_id": "61"
}
```

#### 3.6.3 `get_game_playtime`

Get total accumulated playtime for a specific game.

- **Request fields:**
  - `game_id` (string, required).

```json
{
  "action": "get_game_playtime",
  "req_id": "62",
  "game_id": "my_game"
}
```

- **Success response:**

```json
{
  "action": "get_game_playtime",
  "game_id": "my_game",
  "playtime_seconds": 3600,
  "req_id": "62"
}
```

Missing `game_id` returns a `MISSING_PARAMETER (3002)` error.

---

### 3.7 Unknown Actions & JSON Errors

If you send an action that is not recognized:

```json
{
  "action": "invalid_action",
  "req_id": "999"
}
```

The server responds with:

```json
{
  "action": "invalid_action",
  "error": "The requested action is not recognized (70-03)",
  "error_code": "7003",
  "req_id": "999"
}
```

If the message is not valid JSON, you receive an error similar to:

```json
{
  "action": "unknown",
  "error": "Invalid JSON in request. The data format is invalid (30-03)",
  "error_code": "3003",
  "req_id": null
}
```

---

## 4. Example Workflows

### 4.1 Configure Device Settings

1. Call `get_device_info` to show current name, SSID, and model.
2. Call `set_device_name` to rename the board.
3. Call `set_brightness` and `set_volume` to adjust display and sound.
4. Optionally configure `set_dim_window` for night mode.

### 4.2 Download and Start a Game

1. Call `download_app` with `game_id` (and optionally `url`/`md5`) to start the download.
2. Poll `get_download_progress` periodically until status becomes `"completed"` or an `"error"` is set.
3. Once complete, call `start_game` with the same `game_id`.
4. Use `get_game_playtime` to show total playtime for that game.

### 4.3 Manage Widgets

1. Call `get_widgets_screen` to fetch the current widget framebuffers.
2. Render or inspect the returned framebuffers as appropriate for your app.

---

## 5. Best Practices

- **Always use `req_id`:** It makes it easy to correlate responses in clients handling multiple in‑flight requests.
- **Check `error_code` first:** If present, treat the response as an error even if some additional data fields are returned.
- **Handle long‑running actions gracefully:** `download_app`, `perform_update`, and similar operations can take time; show progress indicators and avoid tight polling loops (e.g. poll `get_download_progress` every 1–3 seconds).
- **Prefer async workflows:** Use async `download_app` + `get_download_progress` instead of blocking on large downloads.
- **Security model:** The WebSocket API is intended for use on a trusted local network. Deploy behind your own authentication or transport security if exposing beyond a private LAN.

