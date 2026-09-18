"""
User data operations module for persistent storage of user identity and tokens.
Data is stored in /var/lib/dartsnut/user_data.json to survive folder deletion.
"""

import json
import logging
import os
from datetime import datetime, timezone
from python_websocket.error_handler import handle_exception

# Storage paths
PERSISTENT_DATA_DIR = "/var/lib/dartsnut"
PERSISTENT_DATA_FILE = "/var/lib/dartsnut/user_data.json"

_log = logging.getLogger(__name__)

# Default data structure
DEFAULT_USER_DATA = {
    "user_id": "",
    "jwt_token": "",
    "refresh_token": ""
}


def _publish_identity_presence() -> None:
    try:
        from supabase_sync_bridge import publish_device_state_update
        publish_device_state_update(
            {"device_updated_at": datetime.now(timezone.utc).isoformat()}
        )
    except Exception:
        pass


def _ensure_data_directory():
    """Ensure the persistent data directory exists with proper permissions."""
    try:
        if not os.path.exists(PERSISTENT_DATA_DIR):
            os.makedirs(PERSISTENT_DATA_DIR, mode=0o755)
        return True
    except PermissionError:
        return False
    except Exception:
        return False


def _load_user_data():
    """Load user data from persistent storage, creating default if not exists."""
    try:
        if not os.path.exists(PERSISTENT_DATA_FILE):
            # Initialize with default data
            _ensure_data_directory()
            with open(PERSISTENT_DATA_FILE, 'w') as f:
                json.dump(DEFAULT_USER_DATA, f, indent=2)
            return DEFAULT_USER_DATA.copy()
        
        with open(PERSISTENT_DATA_FILE, 'r') as f:
            data = json.load(f)
            data.pop("game_playtimes", None)
            # Ensure all required keys exist
            if "user_id" not in data:
                data["user_id"] = ""
            if "jwt_token" not in data:
                data["jwt_token"] = ""
            if "refresh_token" not in data:
                data["refresh_token"] = ""
            return data
    except json.JSONDecodeError:
        # If JSON is corrupted, reinitialize
        try:
            with open(PERSISTENT_DATA_FILE, 'w') as f:
                json.dump(DEFAULT_USER_DATA, f, indent=2)
            return DEFAULT_USER_DATA.copy()
        except Exception as e:
            raise Exception(f"Failed to reinitialize user data file: {str(e)}")
    except Exception as e:
        raise Exception(f"Failed to load user data: {str(e)}")


def _save_user_data(data):
    """Save user data to persistent storage."""
    try:
        if not _ensure_data_directory():
            raise PermissionError("Cannot create data directory")
        
        # Write to temp file first, then move (atomic operation)
        temp_file = PERSISTENT_DATA_FILE + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        # Atomic move
        os.replace(temp_file, PERSISTENT_DATA_FILE)
        return True
    except PermissionError:
        raise
    except Exception as e:
        # Clean up temp file if it exists
        try:
            if os.path.exists(PERSISTENT_DATA_FILE + ".tmp"):
                os.remove(PERSISTENT_DATA_FILE + ".tmp")
        except:
            pass
        raise Exception(f"Failed to save user data: {str(e)}")


def reset_user_data_file() -> None:
    """Reset persistent user data to defaults and remove in-progress playtime temp file."""
    try:
        if not _ensure_data_directory():
            raise PermissionError("Cannot create data directory")
        _save_user_data(
            {
                "user_id": "",
                "jwt_token": "",
                "refresh_token": "",
            }
        )
        _publish_identity_presence()
    except Exception as e:
        _log.error("Error resetting user data file: %s", e)


def get_user_data():
    """Get all user data from persistent storage."""
    try:
        data = _load_user_data()
        return {
            "action": "get_user_data",
            "user_id": data.get("user_id", ""),
            "jwt_token": data.get("jwt_token", ""),
            "refresh_token": data.get("refresh_token", "")
        }
    except PermissionError:
        return handle_exception("get_user_data", PermissionError(), "Failed to read user data")
    except Exception as e:
        return handle_exception("get_user_data", e, "Failed to get user data")


def update_user_info(user_id=None, jwt_token=None, refresh_token=None):
    """
    Update user identification and tokens.
    All parameters are optional - only provided ones will be updated.
    """
    try:
        data = _load_user_data()
        
        if user_id is not None:
            data["user_id"] = user_id
        if jwt_token is not None:
            data["jwt_token"] = jwt_token
        if refresh_token is not None:
            data["refresh_token"] = refresh_token
        
        _save_user_data(data)
        _publish_identity_presence()
        
        return {
            "action": "update_user_info",
            "message": "Success"
        }
    except PermissionError:
        return handle_exception("update_user_info", PermissionError(), "Failed to update user info")
    except Exception as e:
        return handle_exception("update_user_info", e, "Failed to update user info")
