"""
User data operations module for persistent storage of user information and game playtime.
Data is stored in /var/lib/dartsnut/user_data.json to survive folder deletion.
"""

import os
import json
import time
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_file_not_found,
    create_error_response
)

# Storage paths
PERSISTENT_DATA_DIR = "/var/lib/dartsnut"
PERSISTENT_DATA_FILE = "/var/lib/dartsnut/user_data.json"
TEMP_GAME_START_FILE = "/tmp/dartsnut_game_start.json"

# Default data structure
DEFAULT_USER_DATA = {
    "user_id": "",
    "jwt_token": "",
    "refresh_token": "",
    "game_playtimes": {}
}


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
            # Ensure all required keys exist
            if "game_playtimes" not in data:
                data["game_playtimes"] = {}
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
                "game_playtimes": {},
            }
        )
    except Exception as e:
        print(f"Error resetting user data file: {e}")
    try:
        if os.path.exists(TEMP_GAME_START_FILE):
            os.remove(TEMP_GAME_START_FILE)
    except OSError:
        pass


def get_user_data():
    """Get all user data from persistent storage."""
    try:
        data = _load_user_data()
        return {
            "action": "get_user_data",
            "user_id": data.get("user_id", ""),
            "jwt_token": data.get("jwt_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "game_playtimes": data.get("game_playtimes", {})
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
        
        return {
            "action": "update_user_info",
            "message": "Success"
        }
    except PermissionError:
        return handle_exception("update_user_info", PermissionError(), "Failed to update user info")
    except Exception as e:
        return handle_exception("update_user_info", e, "Failed to update user info")


def get_game_playtime(game_id):
    """Get total playtime in seconds for a specific game."""
    try:
        if not game_id:
            return create_error_response(
                "get_game_playtime",
                ErrorCode.MISSING_PARAMETER,
                "Game ID is required",
                game_id=game_id
            )
        
        data = _load_user_data()
        playtimes = data.get("game_playtimes", {})
        playtime = playtimes.get(game_id, 0)
        
        return {
            "action": "get_game_playtime",
            "game_id": game_id,
            "playtime_seconds": playtime
        }
    except PermissionError:
        return handle_exception("get_game_playtime", PermissionError(), "Failed to get game playtime")
    except Exception as e:
        return handle_exception("get_game_playtime", e, "Failed to get game playtime")


def add_game_playtime(game_id, seconds):
    """Add playtime (in seconds) to a game's total."""
    try:
        if not game_id:
            return create_error_response(
                "add_game_playtime",
                ErrorCode.MISSING_PARAMETER,
                "Game ID is required",
                game_id=game_id
            )
        
        if seconds < 0:
            return create_error_response(
                "add_game_playtime",
                ErrorCode.INVALID_INPUT,
                "Playtime must be non-negative",
                game_id=game_id
            )
        
        data = _load_user_data()
        if "game_playtimes" not in data:
            data["game_playtimes"] = {}
        
        current_playtime = data["game_playtimes"].get(game_id, 0)
        data["game_playtimes"][game_id] = current_playtime + seconds
        
        _save_user_data(data)
        
        return {
            "action": "add_game_playtime",
            "game_id": game_id,
            "total_playtime_seconds": data["game_playtimes"][game_id],
            "message": "Success"
        }
    except PermissionError:
        return handle_exception("add_game_playtime", PermissionError(), "Failed to add game playtime")
    except Exception as e:
        return handle_exception("add_game_playtime", e, "Failed to add game playtime")


def start_game_tracking(game_id):
    """Save game start time to temporary file."""
    try:
        if not game_id:
            return create_error_response(
                "start_game_tracking",
                ErrorCode.MISSING_PARAMETER,
                "Game ID is required",
                game_id=game_id
            )
        
        start_data = {
            "game_id": game_id,
            "start_time": time.time()
        }
        
        # Write to temp file
        with open(TEMP_GAME_START_FILE, 'w') as f:
            json.dump(start_data, f)
        
        return {
            "action": "start_game_tracking",
            "game_id": game_id,
            "message": "Success"
        }
    except PermissionError:
        return handle_exception("start_game_tracking", PermissionError(), "Failed to start game tracking")
    except Exception as e:
        return handle_exception("start_game_tracking", e, "Failed to start game tracking")


def stop_game_tracking():
    """Calculate game duration and add to persistent storage."""
    try:
        # Check if temp file exists
        if not os.path.exists(TEMP_GAME_START_FILE):
            return create_error_response(
                "stop_game_tracking",
                ErrorCode.FILE_NOT_FOUND,
                "No active game session found",
                file_path=TEMP_GAME_START_FILE
            )
        
        # Read start time from temp file
        try:
            with open(TEMP_GAME_START_FILE, 'r') as f:
                start_data = json.load(f)
        except json.JSONDecodeError:
            # Clean up corrupted temp file
            try:
                os.remove(TEMP_GAME_START_FILE)
            except:
                pass
            return create_error_response(
                "stop_game_tracking",
                ErrorCode.INVALID_JSON,
                "Game tracking data is corrupted",
                file_path=TEMP_GAME_START_FILE
            )
        
        game_id = start_data.get("game_id")
        start_time = start_data.get("start_time")
        
        if not game_id or start_time is None:
            # Clean up invalid temp file
            try:
                os.remove(TEMP_GAME_START_FILE)
            except:
                pass
            return create_error_response(
                "stop_game_tracking",
                ErrorCode.INVALID_INPUT,
                "Invalid game tracking data",
                file_path=TEMP_GAME_START_FILE
            )
        
        # Calculate duration
        current_time = time.time()
        duration_seconds = max(0, int(current_time - start_time))
        
        # Add to persistent storage
        result = add_game_playtime(game_id, duration_seconds)
        
        # Clean up temp file
        try:
            os.remove(TEMP_GAME_START_FILE)
        except:
            pass
        
        # Return result with duration info
        if "error" in result:
            return result
        
        return {
            "action": "stop_game_tracking",
            "game_id": game_id,
            "duration_seconds": duration_seconds,
            "total_playtime_seconds": result.get("total_playtime_seconds", 0),
            "message": "Success"
        }
    except PermissionError:
        return handle_exception("stop_game_tracking", PermissionError(), "Failed to stop game tracking")
    except Exception as e:
        return handle_exception("stop_game_tracking", e, "Failed to stop game tracking")
