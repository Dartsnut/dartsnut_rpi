## Bluetooth API (BLE UART)

This document describes how mobile apps can communicate with a Dartsnut device (PixelDart / PixelBoard) over Bluetooth Low Energy (BLE) using a simple JSON‑over‑UART protocol.

The BLE link is primarily used for:

- Initial onboarding when the device is not yet on Wi‑Fi
- Scanning and configuring Wi‑Fi
- Reading basic device information
- Locating the device (e.g. make it blink / play a sound)

Once the device is on Wi‑Fi, most ongoing management should be done via the WebSocket API described in `websocket-api.md`.

---

## 1. GATT Service & Characteristics

The firmware exposes a Nordic UART–style GATT service:

- **Service UUID**: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
- **RX Characteristic UUID** (phone → device):
  - UUID: `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
  - Properties: `write`, `write-without-response`
- **TX Characteristic UUID** (device → phone):
  - UUID: `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`
  - Properties: `notify`

**Roles:**

- Phone / tablet acts as the **central**
- Dartsnut device acts as the **peripheral**

**Advertising name:**

- The device advertises using the `model` field from `device.json`, e.g. `PixelDart` or `PixelBoard`.

---

## 2. Connection & Discovery

At a high level, a mobile app should:

1. Scan for BLE peripherals whose name matches your expected model (`PixelDart`, `PixelBoard`, etc.).
2. Connect to the device.
3. Discover the UART service and RX/TX characteristics by UUID.
4. Enable notifications on the TX characteristic.
5. Send commands by writing UTF‑8 JSON strings to the RX characteristic.
6. Receive responses as UTF‑8 JSON notifications on the TX characteristic.

### Example Flow (Pseudo‑Code)

```text
// Pseudocode (platform‑agnostic)
scanForPeripherals(namePrefix = "Pixel")
connect(peripheral)
discoverService(UART_SERVICE_UUID)
rx = discoverCharacteristic(RX_CHARACTERISTIC_UUID)
tx = discoverCharacteristic(TX_CHARACTERISTIC_UUID)
enableNotifications(tx)

// Send a command
write(rx, JSON.stringify({ command: "wifi_status" }))

// Handle notifications
onNotify(tx, data => {
  const msg = JSON.parse(utf8Decode(data))
  // handle msg
})
```

---

## 3. Message Format

### 3.1 Request (phone → device)

All requests are UTF‑8 encoded JSON objects written to the **RX** characteristic:

- **command** (string, required): operation to perform.
- Additional fields depend on the command.

Example:

```json
{ "command": "wifi_status" }
```

```json
{ "command": "connect_wifi", "ssid": "MyWiFi", "password": "secret123" }
```

### 3.2 Response (device → phone)

Responses are UTF‑8 encoded JSON objects sent as notifications from the **TX** characteristic.

On **success**, the payload typically includes:

- **command** (string): echo of the original command.
- **message** or **status** fields where applicable.
- Command‑specific data (e.g. Wi‑Fi networks, IP address, device info).

On **error**, the payload is built using the same error handler as the WebSocket API and has this shape:

```json
{
  "action": "connect_wifi",
  "error": "User-friendly error message (XX-YY)",
  "error_code": "XXXX",
  "command": "connect_wifi"
}
```

- **action**: the logical action name (matches the command for BLE commands).
- **error**: human‑readable message plus formatted code in parentheses.
- **error_code**: 4‑digit code (see `ERROR_CODES.md`).
- **command**: set by the BLE layer so the app can correlate to the original command.

BLE responses that are not errors use whatever fields are returned by the underlying operation (examples below).

---

## 4. Command Reference

This section documents all BLE commands handled by the firmware (`UARTDevice.uart_write`).

### 4.1 `scan_wifi`

**Purpose:** Scan for nearby Wi‑Fi networks and stream SSIDs progressively over BLE.

**Request:**

```json
{ "command": "scan_wifi" }
```

**Success responses:**

- The device sends **multiple** notifications as the scan progresses.
- Each notification has:

```json
{ "command": "scan_wifi", "networks": ["SSID1", "SSID2"] }
```

- When the scan is complete, the final notification includes `end: true`:

```json
{ "command": "scan_wifi", "networks": ["LastSSID"], "end": true }
```

- If no networks are found, you still receive a final message:

```json
{ "command": "scan_wifi", "networks": [], "end": true }
```

**Error responses:**

- On a scan failure (e.g. `nmcli` error), the device sends an error object:

```json
{
  "action": "scan_wifi",
  "error": "Failed to scan WiFi networks (40-01)",
  "error_code": "4001",
  "command": "scan_wifi"
}
```

See `ERROR_CODES.md` for details on error codes.

**Client notes:**

- Accumulate SSIDs across multiple `scan_wifi` messages until you see `end: true`.
- Handle duplicates; the device already tries to de‑duplicate, but your app should tolerate repeats.

---

### 4.2 `enable_wifi`

**Purpose:** Turn Wi‑Fi on using `nmcli radio wifi on`.

**Request:**

```json
{ "command": "enable_wifi" }
```

**Success response:**

```json
{ "command": "enable_wifi", "status": "success" }
```

**Error responses:**

On failure to enable Wi‑Fi:

```json
{
  "action": "enable_wifi",
  "error": "Failed to enable WiFi (40-01)",
  "error_code": "4001",
  "command": "enable_wifi"
}
```

---

### 4.3 `wifi_status`

**Purpose:** Get current Wi‑Fi status, including whether Wi‑Fi is enabled, whether the device is connected, the SSID, and IP address.

**Request:**

```json
{ "command": "wifi_status" }
```

**Success responses:**

Wi‑Fi enabled and connected:

```json
{
  "command": "wifi_status",
  "wifi_enabled": true,
  "connected": true,
  "ssid": "MyWiFi",
  "ip_address": "192.168.0.42"
}
```

Wi‑Fi enabled but not connected:

```json
{
  "command": "wifi_status",
  "wifi_enabled": true,
  "connected": false
}
```

Wi‑Fi disabled:

```json
{
  "command": "wifi_status",
  "wifi_enabled": false
}
```

**Error responses:**

On failure to query status:

```json
{
  "action": "wifi_status",
  "error": "Failed to get WiFi status (40-01)",
  "error_code": "4001",
  "command": "wifi_status"
}
```

---

### 4.4 `connect_wifi`

**Purpose:** Connect the device to a Wi‑Fi network with SSID and password.

**Request:**

```json
{
  "command": "connect_wifi",
  "ssid": "MyWiFi",
  "password": "secret123"
}
```

Both `ssid` and `password` are required.

**Success response:**

On successful connection:

```json
{
  "command": "connect_wifi",
  "status": "success",
  "ip_address": "192.168.0.42"
}
```

**Validation errors:**

- Missing `ssid` or `password`:

```json
{
  "action": "connect_wifi",
  "error": "SSID or password missing (30-02)",
  "error_code": "3002",
  "command": "connect_wifi"
}
```

**Wi‑Fi‑specific errors:**

- Already connected to the given network:

```json
{
  "action": "connect_wifi",
  "error": "Already connected to the given network (20-07)",
  "error_code": "2007",
  "command": "connect_wifi"
}
```

- Wrong password:

```json
{
  "action": "connect_wifi",
  "error": "The WiFi password is incorrect (20-08)",
  "error_code": "2008",
  "command": "connect_wifi"
}
```

**Other failures:**

- Any other failure in `nmcli` or the connection process will be returned as a generic network/command error using the standard error format.

---

### 4.5 `reconnect_wifi`

**Purpose:** Force a Wi‑Fi reconnect (disconnect the interface and bring it back up).

**Request:**

```json
{ "command": "reconnect_wifi" }
```

**Success response:**

```json
{
  "command": "reconnect_wifi",
  "ip_address": "192.168.0.42",
  "status": "success"
}
```

**Error responses:**

On failure to reconnect:

```json
{
  "action": "reconnect_wifi",
  "error": "Failed to reconnect WiFi (40-01)",
  "error_code": "4001",
  "command": "reconnect_wifi"
}
```

---

### 4.6 `device_info`

**Purpose:** Get device metadata, including the contents of `device.json` and the Wi‑Fi MAC address.

**Request:**

```json
{ "command": "device_info" }
```

**Success response:**

```json
{
  "command": "device_info",
  "info": {
    "name": "PixelDart",
    "serial": "1234567890",
    "model": "PixelDart",
    "brightness": "100",
    "volume": "100",
    "mac_address": "aa:bb:cc:dd:ee:ff"
  }
}
```

Exact fields within `info` mirror the `device.json` structure plus a computed `mac_address`.

**Error responses:**

If `device.json` is missing or cannot be parsed:

```json
{
  "action": "device_info",
  "error": "Failed to get device info (10-01)",
  "error_code": "1001",
  "command": "device_info"
}
```

The exact `error_code` will reflect the underlying issue (file not found, invalid JSON, etc.).

---

### 4.7 `locate_device`

**Purpose:** Trigger a device‑side “locate” behavior (e.g. sound, LEDs). The actual effect is implemented in the main application and may vary.

**Request:**

```json
{ "command": "locate_device" }
```

**Success response:**

```json
{ "command": "locate_device", "status": "success" }
```

**Error responses:**

- If the locate callback fails internally, an error response will be generated using the standard error format.

---

### 4.8 Unknown / Invalid Commands

If the `command` field is missing or not recognized, the device returns an error response:

```json
{
  "action": "unknown",
  "error": "Unknown command (70-03)",
  "error_code": "7003",
  "command": "some_unknown_command"
}
```

If the payload is not valid JSON, the device returns:

```json
{
  "action": "unknown",
  "error": "Failed to decode JSON (30-03)",
  "error_code": "3003",
  "command": "unknown"
}
```

---

## 5. Best Practices & Limits

- **Payload size:** Wi‑Fi scan results are chunked to keep each BLE notification payload roughly under 64 bytes. Keep your outbound JSON commands reasonably small (simple key/value pairs) to avoid fragmentation on some BLE stacks.
- **Timeouts:** BLE operations are asynchronous and involve system commands (`nmcli`, `hostname`, file I/O). Use generous timeouts (e.g. 10–30 seconds) and show progress indicators in your UI for long‑running operations like `connect_wifi`.
- **Debouncing:** Avoid spamming commands (e.g. repeated `scan_wifi` or `wifi_status` in tight loops). Implement client‑side debouncing or cooldowns.
- **Error handling:** Always check for `error` and `error_code` fields in responses. Use `error_code` for programmatic handling and display the `error` string to users.
- **Transport choice:** Prefer BLE for **onboarding and recovery** (when the device may not yet be connected to Wi‑Fi). After Wi‑Fi is configured and reachable, prefer the WebSocket API for richer operations and higher throughput.

