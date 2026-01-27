"""
Unified error handling module for websocket operations.
Provides standardized error codes and user-friendly error messages.
"""

import subprocess
import json
import re
from enum import Enum

# Mapping of error codes to user-friendly messages
# This dictionary should be kept in sync with ERROR_MESSAGES.md
# Edit messages in ERROR_MESSAGES.md for reference, then update this dict
ERROR_MESSAGES = {
    # 1xxx: File/IO errors - Unified message for all file system errors
    "1001": "A file system error occurred",
    "1002": "A file system error occurred",
    "1003": "A file system error occurred",
    "1004": "A file system error occurred",
    "1005": "A file system error occurred",
    "1006": "A file system error occurred",
    "1007": "A file system error occurred",
    "1008": "A file system error occurred",
    
    # 2xxx: Network errors
    "2001": "Unable to connect to the network",
    "2002": "Failed to establish connection",
    "2003": "A network error occurred",  # Download - unified
    "2004": "A network error occurred",  # MD5 mismatch - unified
    "2005": "The provided URL is invalid",
    "2006": "A network error occurred",  # Download already in progress - unified
    "2007": "Already connected to the given network",
    "2008": "The WiFi password is incorrect",
    
    # 3xxx: Validation errors
    "3001": "The provided input is invalid",
    "3002": "Required information is missing",
    "3003": "The data format is invalid",
    "3004": "This file type is not supported. Only .tar.gz files are supported",
    "3005": "Brightness must be between 10 and 100",
    
    # 4xxx: System/Command errors
    "4001": "Unable to complete the operation",
    "4002": "Service operation failed",
    "4003": "Unable to start SSH service",
    "4004": "Unable to stop SSH service",
    "4005": "Unable to retrieve WiFi signal strength",
    
    # 5xxx: Bluetooth errors - Unified message for firmware version
    "5001": "This feature requires a newer firmware version",
    "5002": "This feature requires a newer firmware version",
    "5003": "This feature requires a newer firmware version",
    "5004": "This feature requires a newer firmware version",
    "5005": "This feature requires a newer firmware version",
    "5006": "This feature requires a newer firmware version",
    "5007": "This feature requires a newer firmware version",
    "5008": "This feature requires a newer firmware version",
    
    # 6xxx: Git/Update errors - Unified message for network errors
    "6001": "A network error occurred",
    "6002": "A network error occurred",
    "6003": "A network error occurred",
    "6004": "A network error occurred",
    "6005": "A network error occurred",
    
    # 7xxx: General errors
    "7001": "An unexpected error occurred",
    "7002": "This feature requires a newer firmware version",  # FUNCTION_NOT_AVAILABLE - unified
    "7003": "The requested action is not recognized",
}

# Mapping of commands to user-friendly error messages
COMMAND_MESSAGES = {
    # Network/WiFi commands
    'iwconfig': "Unable to retrieve WiFi signal strength",
    'nmcli': "Unable to manage network connection",
    'nmcli connection delete': "Unable to remove network connection",
    'nmcli radio wifi': "Unable to change WiFi radio state",
    
    # System/Service commands
    'systemctl is-active ssh': "Unable to check SSH service status",
    'systemctl start ssh': "Unable to start SSH service",
    'systemctl stop ssh': "Unable to stop SSH service",
    'reboot': "Unable to reboot system",
    
    # Bluetooth commands
    'bluetoothctl': "Unable to manage Bluetooth device",
    'bluetoothctl devices Paired': "Unable to list paired Bluetooth devices",
    
    # Git commands
    'git describe': "Unable to retrieve version information",
    'git fetch': "Unable to fetch updates from server",
    'git reset': "Unable to update system files",
    'git diff': "Unable to check for changes",
    
    # File operations
    'wget': "Unable to download file",
    'md5sum': "Unable to verify file integrity",
    'tar': "Unable to extract file",
    
    # System info
    'hostname': "Unable to retrieve network information",
    'hcitool': "Unable to retrieve Bluetooth information",
    'ip': "Unable to retrieve network interface information",
}


class ErrorCode(Enum):
    """Standardized error codes for websocket operations."""
    
    # 1xxx: File/IO errors
    FILE_NOT_FOUND = "1001"
    DIRECTORY_NOT_FOUND = "1002"
    PERMISSION_DENIED = "1003"
    FILE_READ_ERROR = "1004"
    FILE_WRITE_ERROR = "1005"
    DIRECTORY_ALREADY_EXISTS = "1006"
    MD5_CALCULATION_ERROR = "1007"
    FILE_EXTRACTION_ERROR = "1008"
    
    # 2xxx: Network errors
    NETWORK_ERROR = "2001"
    CONNECTION_FAILED = "2002"
    DOWNLOAD_FAILED = "2003"
    MD5_MISMATCH = "2004"
    INVALID_URL = "2005"
    DOWNLOAD_ALREADY_IN_PROGRESS = "2006"
    WIFI_ALREADY_CONNECTED = "2007"
    WIFI_PASSWORD_WRONG = "2008"
    
    # 3xxx: Validation errors
    INVALID_INPUT = "3001"
    MISSING_PARAMETER = "3002"
    INVALID_JSON = "3003"
    INVALID_FILE_TYPE = "3004"
    INVALID_BRIGHTNESS = "3005"
    
    # 4xxx: System/Command errors
    COMMAND_FAILED = "4001"
    SERVICE_ERROR = "4002"
    SSH_START_FAILED = "4003"
    SSH_STOP_FAILED = "4004"
    SIGNAL_LEVEL_NOT_FOUND = "4005"
    
    # 5xxx: Bluetooth errors
    BLUETOOTH_SCAN_FAILED = "5001"
    BLUETOOTH_LIST_FAILED = "5002"
    BLUETOOTH_REMOVE_FAILED = "5003"
    BLUETOOTH_CONNECT_FAILED = "5004"
    BLUETOOTH_DEVICE_NOT_FOUND = "5005"
    BLUETOOTH_PAIRING_FAILED = "5006"
    BLUETOOTH_TRUST_FAILED = "5007"
    BLUETOOTH_CONNECTION_FAILED = "5008"
    
    # 6xxx: Git/Update errors
    GIT_ERROR = "6001"
    GIT_VERSION_ERROR = "6002"
    GIT_UPDATE_CHECK_FAILED = "6003"
    GIT_UPDATE_FAILED = "6004"
    GIT_ROLLBACK_FAILED = "6005"
    
    # 7xxx: General errors
    UNKNOWN_ERROR = "7001"
    FUNCTION_NOT_AVAILABLE = "7002"
    UNKNOWN_ACTION = "7003"


def _format_error_code(error_code_value):
    """
    Format error code as XX-YY where XX is category prefix and YY is code suffix.
    
    Args:
        error_code_value: 4-digit error code string (e.g., "1001")
    
    Returns:
        str: Formatted error code (e.g., "10-01")
    """
    if not error_code_value or len(error_code_value) != 4:
        return error_code_value
    return f"{error_code_value[:2]}-{error_code_value[2:]}"


def _get_error_message(error_code_value):
    """
    Get user-friendly error message for an error code.
    
    Args:
        error_code_value: 4-digit error code string (e.g., "1001")
    
    Returns:
        str: User-friendly error message, or default message if not found
    """
    return ERROR_MESSAGES.get(error_code_value, "An unexpected error occurred")


def _get_user_friendly_command_message(command):
    """Get user-friendly message for a command."""
    if not command:
        return "Unable to complete the operation"
    
    # Normalize command (remove sudo, paths, etc.)
    normalized = command.lower()
    # Remove sudo prefix
    normalized = re.sub(r'^sudo\s+', '', normalized)
    # Remove file paths and arguments for matching
    base_cmd = normalized.split()[0] if normalized.split() else normalized
    
    # Try exact match first
    for cmd_key, msg in COMMAND_MESSAGES.items():
        if normalized.startswith(cmd_key.lower()):
            return msg
    
    # Try base command match
    if base_cmd in COMMAND_MESSAGES:
        return COMMAND_MESSAGES[base_cmd]
    
    # Generic message
    return "Unable to complete the operation"


def create_error_response(action, error_code, message, **kwargs):
    """
    Create a standardized error response that maintains backward compatibility.
    
    Args:
        action: The action name
        error_code: ErrorCode enum value
        message: User-friendly error message (if None, will be looked up from ERROR_MESSAGES)
        **kwargs: Additional fields to include in the response
    
    Returns:
        dict: Error response with format:
            {
                "action": action,
                "error": "message (XX-YY)",  # Formatted with message first, code in parentheses
                "error_code": error_code.value,  # 4-digit code for programmatic handling
                ...kwargs  # Any additional fields
            }
    """
    error_code_value = error_code.value if isinstance(error_code, ErrorCode) else error_code
    
    # Use provided message or look it up from ERROR_MESSAGES
    if message is None:
        message = _get_error_message(error_code_value)
    
    # Format as "message (XX-YY)"
    formatted_code = _format_error_code(error_code_value)
    message_with_code = f"{message} ({formatted_code})"
    
    response = {
        "action": action,
        "error": message_with_code,
        "error_code": error_code_value,
    }
    response.update(kwargs)
    return response


def handle_exception(action, exception, context=None, **kwargs):
    """
    Handle an exception and return a standardized error response.
    
    Args:
        action: The action name
        exception: The exception that was raised
        context: Optional context string for more specific error messages
        **kwargs: Additional fields to include in the response
    
    Returns:
        dict: Standardized error response
    """
    error_code = ErrorCode.UNKNOWN_ERROR
    message = "An unexpected error occurred"
    
    if isinstance(exception, FileNotFoundError):
        error_code = ErrorCode.FILE_NOT_FOUND
        message = "The requested file could not be found"
        if context:
            message = f"{context}. The requested file could not be found"
    elif isinstance(exception, PermissionError):
        error_code = ErrorCode.PERMISSION_DENIED
        message = "You do not have permission to perform this operation"
        if context:
            message = f"{context}. You do not have permission to perform this operation"
    elif isinstance(exception, subprocess.CalledProcessError):
        error_code = ErrorCode.COMMAND_FAILED
        # Get user-friendly message based on command
        cmd = exception.cmd
        if isinstance(cmd, list):
            cmd_str = ' '.join(cmd[:2])  # Take first two parts for matching
        else:
            cmd_str = str(cmd)
        
        message = _get_user_friendly_command_message(cmd_str)
        
        if context:
            message = f"{context}. {message}"
    elif isinstance(exception, json.JSONDecodeError):
        error_code = ErrorCode.INVALID_JSON
        message = "The data format is invalid"
        if context:
            message = f"{context}. The data format is invalid"
    elif isinstance(exception, (ConnectionError, TimeoutError)):
        error_code = ErrorCode.NETWORK_ERROR
        message = "Unable to connect to the network"
        if context:
            message = f"{context}. Unable to connect to the network"
    elif isinstance(exception, ValueError):
        error_code = ErrorCode.INVALID_INPUT
        # Use exception message if it's user-friendly, otherwise generic
        exception_msg = str(exception)
        if exception_msg and len(exception_msg) < 100 and not any(
            keyword in exception_msg.lower() for keyword in ['traceback', 'file', 'line', 'at 0x', 'object']
        ):
            message = exception_msg
        else:
            message = "The provided input is invalid"
        if context:
            message = f"{context}. {message}"
    elif isinstance(exception, KeyError):
        error_code = ErrorCode.MISSING_PARAMETER
        message = "Required information is missing"
        if context:
            message = f"{context}. Required information is missing"
    else:
        # For unknown exceptions, use generic message
        if context:
            message = f"{context}. An unexpected error occurred"
        else:
            message = "An unexpected error occurred"
    
    return create_error_response(action, error_code, message, **kwargs)


def handle_file_not_found(action, file_path=None, **kwargs):
    """Handle file not found error."""
    message = "The requested file could not be found"
    return create_error_response(action, ErrorCode.FILE_NOT_FOUND, message, **kwargs)


def handle_directory_not_found(action, directory=None, **kwargs):
    """Handle directory not found error."""
    message = "The requested directory could not be found"
    return create_error_response(action, ErrorCode.DIRECTORY_NOT_FOUND, message, **kwargs)


def handle_missing_parameter(action, parameter_name, **kwargs):
    """Handle missing required parameter error."""
    message = "Required information is missing"
    return create_error_response(action, ErrorCode.MISSING_PARAMETER, message, **kwargs)


def handle_invalid_input(action, message, **kwargs):
    """Handle invalid input error."""
    return create_error_response(action, ErrorCode.INVALID_INPUT, message, **kwargs)


def handle_bluetooth_error(action, error_code, message, address=None, **kwargs):
    """Handle bluetooth-specific errors."""
    if address:
        kwargs['address'] = address
    return create_error_response(action, error_code, message, **kwargs)


def handle_network_error(action, message, **kwargs):
    """Handle network-related errors."""
    return create_error_response(action, ErrorCode.NETWORK_ERROR, message, **kwargs)


def handle_command_error(action, command, returncode=None, stderr=None, **kwargs):
    """Handle command execution errors."""
    # Get user-friendly message based on command
    message = _get_user_friendly_command_message(command)
    
    return create_error_response(action, ErrorCode.COMMAND_FAILED, message, **kwargs)
