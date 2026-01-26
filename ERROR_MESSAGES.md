# Error Messages Reference

This file contains all user-friendly error messages organized by error code category. Messages can be edited here and will be automatically used by the error handling system.

## Format

Error messages are displayed in the format: `"message (XX-YY)"` where:
- `message` is the user-friendly error message
- `XX` is the 2-digit category prefix (10, 20, 30, etc.)
- `YY` is the 2-digit error code within the category (01, 02, 03, etc.)
- The error code appears in parentheses at the end for support reference
- The full 4-digit error code (e.g., 1001) is still available in the `error_code` field for programmatic handling

**Example**: `"A file system error occurred (10-01)"`

## Message Grouping Strategy

Error messages are grouped into unified categories for better user experience:
- **File System Errors** (1001-1008): All use the same unified message
- **Network Errors** (2003, 2004, 2006, 6001-6005): Git and download operations use unified network error message
- **Firmware Version Errors** (5001-5008, 7002): Bluetooth and websocket actions use unified firmware version message

Individual error codes are still maintained for programmatic error handling and debugging.

---

## 1xxx: File/IO Errors

**Unified Message**: All file system errors use: `"A file system error occurred"`

### 1001 - FILE_NOT_FOUND
A file system error occurred

### 1002 - DIRECTORY_NOT_FOUND
A file system error occurred

### 1003 - PERMISSION_DENIED
A file system error occurred

### 1004 - FILE_READ_ERROR
A file system error occurred

### 1005 - FILE_WRITE_ERROR
A file system error occurred

### 1006 - DIRECTORY_ALREADY_EXISTS
A file system error occurred

### 1007 - MD5_CALCULATION_ERROR
A file system error occurred

### 1008 - FILE_EXTRACTION_ERROR
A file system error occurred

---

## 2xxx: Network Errors

### 2001 - NETWORK_ERROR
Unable to connect to the network

### 2002 - CONNECTION_FAILED
Failed to establish connection

### 2003 - DOWNLOAD_FAILED
**Unified Message**: A network error occurred

### 2004 - MD5_MISMATCH
**Unified Message**: A network error occurred

### 2005 - INVALID_URL
The provided URL is invalid

### 2006 - DOWNLOAD_ALREADY_IN_PROGRESS
**Unified Message**: A network error occurred

---

## 3xxx: Validation Errors

### 3001 - INVALID_INPUT
The provided input is invalid

### 3002 - MISSING_PARAMETER
Required information is missing

### 3003 - INVALID_JSON
The data format is invalid

### 3004 - INVALID_FILE_TYPE
This file type is not supported. Only .tar.gz files are supported

### 3005 - INVALID_BRIGHTNESS
Brightness must be between 10 and 100

---

## 4xxx: System/Command Errors

### 4001 - COMMAND_FAILED
Unable to complete the operation

### 4002 - SERVICE_ERROR
Service operation failed

### 4003 - SSH_START_FAILED
Unable to start SSH service

### 4004 - SSH_STOP_FAILED
Unable to stop SSH service

### 4005 - SIGNAL_LEVEL_NOT_FOUND
Unable to retrieve WiFi signal strength

---

## 5xxx: Bluetooth Errors

**Unified Message**: All Bluetooth errors use: `"This feature requires a newer firmware version"`

### 5001 - BLUETOOTH_SCAN_FAILED
This feature requires a newer firmware version

### 5002 - BLUETOOTH_LIST_FAILED
This feature requires a newer firmware version

### 5003 - BLUETOOTH_REMOVE_FAILED
This feature requires a newer firmware version

### 5004 - BLUETOOTH_CONNECT_FAILED
This feature requires a newer firmware version

### 5005 - BLUETOOTH_DEVICE_NOT_FOUND
This feature requires a newer firmware version

### 5006 - BLUETOOTH_PAIRING_FAILED
This feature requires a newer firmware version

### 5007 - BLUETOOTH_TRUST_FAILED
This feature requires a newer firmware version

### 5008 - BLUETOOTH_CONNECTION_FAILED
This feature requires a newer firmware version

---

## 6xxx: Git/Update Errors

**Unified Message**: All Git/Update errors use: `"A network error occurred"`

### 6001 - GIT_ERROR
A network error occurred

### 6002 - GIT_VERSION_ERROR
A network error occurred

### 6003 - GIT_UPDATE_CHECK_FAILED
A network error occurred

### 6004 - GIT_UPDATE_FAILED
A network error occurred

### 6005 - GIT_ROLLBACK_FAILED
A network error occurred

---

## 7xxx: General Errors

### 7001 - UNKNOWN_ERROR
An unexpected error occurred

### 7002 - FUNCTION_NOT_AVAILABLE
**Unified Message**: This feature requires a newer firmware version

### 7003 - UNKNOWN_ACTION
The requested action is not recognized

---

## Command-Specific Messages

These messages are used when system commands fail. They map to error code 4001 (COMMAND_FAILED).

### Network/WiFi Commands
- `iwconfig`: Unable to retrieve WiFi signal strength
- `nmcli`: Unable to manage network connection
- `nmcli connection delete`: Unable to remove network connection
- `nmcli radio wifi`: Unable to change WiFi radio state

### System/Service Commands
- `systemctl is-active ssh`: Unable to check SSH service status
- `systemctl start ssh`: Unable to start SSH service
- `systemctl stop ssh`: Unable to stop SSH service
- `reboot`: Unable to reboot system

### Bluetooth Commands
- `bluetoothctl`: Unable to manage Bluetooth device
- `bluetoothctl devices Paired`: Unable to list paired Bluetooth devices

### Git Commands
- `git describe`: Unable to retrieve version information
- `git fetch`: Unable to fetch updates from server
- `git reset`: Unable to update system files
- `git diff`: Unable to check for changes

### File Operations
- `wget`: Unable to download file
- `md5sum`: Unable to verify file integrity
- `tar`: Unable to extract file

### System Info
- `hostname`: Unable to retrieve network information
- `hcitool`: Unable to retrieve Bluetooth information
- `ip`: Unable to retrieve network interface information

---

## Context-Specific Messages

These messages are used when additional context is provided to error handlers. They typically combine with base error messages.

### Common Context Prefixes
- "Device info file not found" - Used when device.json is missing
- "Failed to decode" - Used when JSON parsing fails
- "Failed to read" - Used when file read operations fail
- "Failed to write" - Used when file write operations fail
- "Failed to save" - Used when saving operations fail
- "Failed to get" - Used when retrieval operations fail
- "Failed to set" - Used when setting operations fail
- "Failed to start" - Used when start operations fail
- "Failed to stop" - Used when stop operations fail
- "Failed to remove" - Used when removal operations fail
- "Failed to create" - Used when creation operations fail
- "Failed to list" - Used when listing operations fail
- "Failed to calculate" - Used when calculation operations fail
- "Failed to connect" - Used when connection operations fail
- "Failed to receive" - Used when receiving operations fail
- "Failed to send" - Used when sending operations fail
- "Download failed" - Used when download operations fail
- "Update failed" - Used when update operations fail
- "Bluetooth scan failed" - Used when Bluetooth scanning fails
- "Invalid brightness value" - Used when brightness validation fails
- "Invalid volume value" - Used when volume validation fails

### Additional Specific Messages
- "Volume must be between 0 and 100"
- "Game ID is required"
- "Playtime must be non-negative"
- "No active game session found"
- "Game tracking data is corrupted"
- "Invalid game tracking data"
- "Required file information is missing"
- "The file data format is invalid"
- "dim_window_start and dim_window_end must both be provided to enable, or both empty to disable"
- "dim_window_start and dim_window_end must be in HH:MM format (00-23:00-59)"
- "dim_level must be between 1 and 100"
- "dim_restore_seconds must be between 5 and 300"
- "Unable to start the game"

---

## Notes

- All messages should be user-friendly and avoid technical jargon when possible
- Messages should be concise but descriptive
- When editing messages, maintain consistency in tone and style
- The error code format (XX-YY) is automatically generated from the 4-digit error code and appears in parentheses at the end
- **Format**: Messages are displayed as `"message (XX-YY)"` with the error code reference at the end
- **Grouping**: Related errors share unified messages for better user experience:
  - File system errors (1001-1008) all use the same message
  - Network errors for downloads (2003, 2004, 2006) and git operations (6001-6005) use unified network error message
  - Firmware version errors for bluetooth (5001-5008) and websocket (7002) use unified firmware version message
- Individual error codes are still maintained for programmatic error handling and debugging
- Context messages are prepended to base messages when provided
