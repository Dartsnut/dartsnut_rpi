# Error Codes Reference

This document describes all error codes used in the websocket API. Error codes are organized by category and include a description of when each error occurs.

## Error Response Format

All error responses follow this format:
```json
{
  "action": "action_name",
  "error": "User-friendly error message [error_code]",
  "error_code": "error_code"
}
```

The error message includes the error code in brackets at the end for customer support identification.

---

## 1xxx: File/IO Errors

### 1001 - FILE_NOT_FOUND
**Description:** The requested file could not be found.

**Common Causes:**
- File path is incorrect
- File has been deleted or moved
- File does not exist in the expected location

**Example Scenarios:**
- Reading a JSON file that doesn't exist
- Getting file MD5 for a non-existent file
- Reading device info file that's missing

---

### 1002 - DIRECTORY_NOT_FOUND
**Description:** The requested directory could not be found.

**Common Causes:**
- Directory path is incorrect
- Directory has been deleted
- Directory does not exist

**Example Scenarios:**
- Listing files in a non-existent directory
- Removing a directory that doesn't exist

---

### 1003 - PERMISSION_DENIED
**Description:** You do not have permission to perform this operation.

**Common Causes:**
- Insufficient file system permissions
- Read-only file system
- Access denied by system security policies

**Example Scenarios:**
- Writing to a protected directory
- Reading files without proper permissions
- Creating directories in restricted locations

---

### 1004 - FILE_READ_ERROR
**Description:** Unable to read the file.

**Common Causes:**
- File is locked by another process
- File is corrupted
- Disk I/O errors

---

### 1005 - FILE_WRITE_ERROR
**Description:** Unable to write to the file.

**Common Causes:**
- Disk is full
- File is read-only
- Insufficient permissions

---

### 1006 - DIRECTORY_ALREADY_EXISTS
**Description:** The directory already exists.

**Common Causes:**
- Attempting to create a directory that already exists

**Example Scenarios:**
- Creating a directory with a name that's already taken

---

### 1007 - MD5_CALCULATION_ERROR
**Description:** Unable to calculate file checksum.

**Common Causes:**
- File is inaccessible
- Disk read errors
- File is too large or corrupted

---

### 1008 - FILE_EXTRACTION_ERROR
**Description:** Unable to extract the downloaded file.

**Common Causes:**
- Archive file is corrupted
- Insufficient disk space
- Archive format is invalid
- Extraction tool failure

**Example Scenarios:**
- Extracting a corrupted .tar.gz file
- Running out of disk space during extraction

---

## 2xxx: Network Errors

### 2001 - NETWORK_ERROR
**Description:** Unable to connect to the network.

**Common Causes:**
- Network interface is down
- No internet connection
- Firewall blocking connection
- DNS resolution failure

**Example Scenarios:**
- Network timeout when fetching data
- Connection refused errors

---

### 2002 - CONNECTION_FAILED
**Description:** Failed to establish connection.

**Common Causes:**
- Remote server is unreachable
- Port is blocked
- Connection timeout

---

### 2003 - DOWNLOAD_FAILED
**Description:** Unable to download the file.

**Common Causes:**
- Network interruption
- Server is unavailable
- Invalid download URL
- Download timeout
- Insufficient disk space

**Example Scenarios:**
- wget command fails
- File not found after download attempt
- Network disconnection during download

---

### 2004 - MD5_MISMATCH
**Description:** File integrity check failed. The downloaded file may be corrupted.

**Common Causes:**
- File was corrupted during download
- Incorrect MD5 checksum provided
- File was modified after download

**Example Scenarios:**
- Downloaded file's MD5 doesn't match expected value
- Network corruption during transfer

---

### 2005 - INVALID_URL
**Description:** The provided URL is invalid.

**Common Causes:**
- Malformed URL format
- Invalid protocol
- URL contains invalid characters

---

### 2006 - DOWNLOAD_ALREADY_IN_PROGRESS
**Description:** A download with the same parameters is already in progress.

**Common Causes:**
- Duplicate `download_app` request with the same url and md5
- Duplicate async game download for the same game_id while a download is pending, initializing, downloading, or extracting

**Example Scenarios:**
- Starting a game download when that game is already downloading
- Sending a sync download_app with the same url and checksum while another is in progress

---

## 3xxx: Validation Errors

### 3001 - INVALID_INPUT
**Description:** The provided input is invalid.

**Common Causes:**
- Input doesn't match expected format
- Invalid data type
- Out of range values
- Malformed data

**Example Scenarios:**
- Invalid base64 encoded data
- Invalid JSON structure
- Invalid parameter values

---

### 3002 - MISSING_PARAMETER
**Description:** Required information is missing.

**Common Causes:**
- Required field not provided in request
- Missing mandatory parameters
- Incomplete request data

**Example Scenarios:**
- Missing file_name or file_data
- Missing url or md5 for download
- Missing game_id parameter

---

### 3003 - INVALID_JSON
**Description:** The data format is invalid.

**Common Causes:**
- Malformed JSON syntax
- Invalid JSON structure
- Encoding issues

**Example Scenarios:**
- Corrupted JSON file
- Invalid JSON in request body
- JSON decode errors

---

### 3004 - INVALID_FILE_TYPE
**Description:** This file type is not supported. Only .tar.gz files are supported.

**Common Causes:**
- File extension doesn't match supported types
- Unsupported archive format

**Example Scenarios:**
- Attempting to download a .zip file
- Providing URL that doesn't end with .tar.gz

---

### 3005 - INVALID_BRIGHTNESS
**Description:** Brightness must be between 10 and 100.

**Common Causes:**
- Brightness value out of allowed range
- Invalid brightness parameter

---

## 4xxx: System/Command Errors

### 4001 - COMMAND_FAILED
**Description:** Unable to complete the operation.

**Common Causes:**
- System command execution failed
- Command returned non-zero exit code
- Command not found in system PATH
- Insufficient system permissions

**Example Scenarios:**
- iwconfig command fails
- nmcli command execution error
- systemctl command failure
- Any subprocess execution error

---

### 4002 - SERVICE_ERROR
**Description:** Service operation failed.

**Common Causes:**
- Service is not available
- Service configuration error
- Service dependency issues

---

### 4003 - SSH_START_FAILED
**Description:** Unable to start SSH service.

**Common Causes:**
- SSH service is already running
- SSH configuration error
- Insufficient permissions
- Port already in use

---

### 4004 - SSH_STOP_FAILED
**Description:** Unable to stop SSH service.

**Common Causes:**
- SSH service is not running
- Insufficient permissions
- Service control error

---

### 4005 - SIGNAL_LEVEL_NOT_FOUND
**Description:** Unable to retrieve WiFi signal strength.

**Common Causes:**
- WiFi interface is not available
- iwconfig command failed
- No WiFi connection active
- Signal level information not available in command output

---

## 5xxx: Bluetooth Errors

### 5001 - BLUETOOTH_SCAN_FAILED
**Description:** Unable to scan for Bluetooth devices.

**Common Causes:**
- Bluetooth adapter is not available
- Bluetooth is disabled
- Permission issues
- Bluetooth service error

---

### 5002 - BLUETOOTH_LIST_FAILED
**Description:** Unable to list paired Bluetooth devices.

**Common Causes:**
- bluetoothctl command failed
- Bluetooth service is not running
- Permission denied
- Bluetooth adapter error

---

### 5003 - BLUETOOTH_REMOVE_FAILED
**Description:** Unable to remove Bluetooth device.

**Common Causes:**
- Device is not paired
- Bluetooth service error
- Permission issues
- Device is currently connected

---

### 5004 - BLUETOOTH_CONNECT_FAILED
**Description:** Unable to connect to Bluetooth device.

**Common Causes:**
- General Bluetooth connection failure
- Bluetooth service error
- Device is out of range

---

### 5005 - BLUETOOTH_DEVICE_NOT_FOUND
**Description:** Bluetooth device not found during scan.

**Common Causes:**
- Device is not in range
- Device is not discoverable
- Device is powered off
- Scan timeout expired

---

### 5006 - BLUETOOTH_PAIRING_FAILED
**Description:** Unable to pair with Bluetooth device.

**Common Causes:**
- Device rejected pairing request
- Authentication failed
- Pairing timeout
- Device doesn't support pairing

---

### 5007 - BLUETOOTH_TRUST_FAILED
**Description:** Unable to trust Bluetooth device.

**Common Causes:**
- Trust command failed
- Device not properly paired
- Permission issues

---

### 5008 - BLUETOOTH_CONNECTION_FAILED
**Description:** Unable to connect to Bluetooth device.

**Common Causes:**
- Connection command failed
- Device is not paired/trusted
- Device is out of range
- Connection timeout

---

## 6xxx: Git/Update Errors

### 6001 - GIT_ERROR
**Description:** Git operation failed.

**Common Causes:**
- Git repository is corrupted
- Git is not installed
- Repository access issues

---

### 6002 - GIT_VERSION_ERROR
**Description:** Unable to retrieve version information.

**Common Causes:**
- Git repository has no tags
- Git command failed
- Repository is not initialized
- No version tags available

**Example Scenarios:**
- `git describe --tags` command fails
- Repository doesn't have any tags

---

### 6003 - GIT_UPDATE_CHECK_FAILED
**Description:** Unable to check for updates.

**Common Causes:**
- Unable to fetch from remote repository
- Network connection issues
- Remote repository is unavailable
- Git fetch command failed

**Example Scenarios:**
- `git fetch origin` fails
- `git describe origin/release` fails

---

### 6004 - GIT_UPDATE_FAILED
**Description:** Unable to update the system. The system has been restored to the previous version.

**Common Causes:**
- Git reset/checkout failed
- Merge conflicts
- Repository corruption
- File system errors during update

**Note:** When this error occurs, the system automatically attempts to rollback to the previous version.

---

### 6005 - GIT_ROLLBACK_FAILED
**Description:** Unable to update the system. Unable to restore the previous version.

**Common Causes:**
- Update failed AND rollback also failed
- Critical system state corruption
- Git repository is in an inconsistent state

**Note:** This is a critical error indicating both update and rollback failed. Manual intervention may be required.

---

## 7xxx: General Errors

### 7001 - UNKNOWN_ERROR
**Description:** An unexpected error occurred.

**Common Causes:**
- Unhandled exception
- Unexpected system state
- Unknown error condition

**Note:** This is a catch-all error code for errors that don't fit into other categories.

---

### 7002 - FUNCTION_NOT_AVAILABLE
**Description:** This feature is not available.

**Common Causes:**
- Function handler is not registered
- Feature is disabled
- Optional functionality not implemented

**Example Scenarios:**
- start_game function not available
- get_widgets_screen function not available

---

### 7003 - UNKNOWN_ACTION
**Description:** The requested action is not recognized.

**Common Causes:**
- Invalid action name in request
- Action doesn't exist
- Typo in action name

**Example Scenarios:**
- Requesting action "get_fiel" instead of "get_file"
- Action name not in the supported list

---

## Error Code Categories Summary

| Category | Range | Description |
|----------|-------|-------------|
| File/IO | 1001-1008 | File and directory operations |
| Network | 2001-2005 | Network and download operations |
| Validation | 3001-3005 | Input validation and parameter errors |
| System/Command | 4001-4005 | System commands and services |
| Bluetooth | 5001-5008 | Bluetooth device operations |
| Git/Update | 6001-6005 | Version control and system updates |
| General | 7001-7003 | General and unknown errors |

---

## Best Practices for Error Handling

1. **Always check the error_code field** for programmatic error handling
2. **Display the error message** (without the bracketed code) to end users
3. **Log the full error response** including error_code for debugging
4. **Use error_code for customer support** - the code in brackets helps identify the exact failure point
5. **Handle errors gracefully** - provide fallback options when possible

---

## Support Information

When reporting errors, please include:
- The `error_code` value
- The full error message
- The action that was being performed
- Any relevant context (file paths, device addresses, etc.)

This information helps support teams quickly identify and resolve issues.
