from base64 import b64encode, b64decode
import os
import json
import shutil
import hashlib
import requests
import tarfile
import threading
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_file_not_found,
    handle_directory_not_found,
    create_error_response,
)

APPS_DIR = "apps"  # Update this to your desired save directory
DOWNLOAD_DIR = "downloads"

# In-memory store for download progress keyed by game_id
DOWNLOAD_PROGRESS = {}

_MISSING = object()


def _read_version_from_conf(game_id):
    """Read conf.json["version"] from apps_dir/{game_id}/conf.json. Returns None on any error."""
    try:
        path = os.path.join(os.getcwd(), APPS_DIR, game_id, "conf.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("version")
    except Exception:
        return None


def _set_download_progress(
    game_id, progress=None, status=None, error=None, version=_MISSING
):
    """
    Internal helper to update progress information for a given game_id.
    """
    entry = DOWNLOAD_PROGRESS.get(
        game_id,
        {
            "game_id": game_id,
            "progress": 0,
            "status": "pending",
            "error": None,
        },
    )

    if progress is not None:
        # Clamp progress to [0, 100]
        entry["progress"] = max(0, min(100, int(progress)))
    if status is not None:
        entry["status"] = status
    # Allow explicitly clearing error by passing error=""
    if error is not None:
        entry["error"] = error
    if version is not _MISSING:
        entry["version"] = version

    DOWNLOAD_PROGRESS[game_id] = entry


def get_download_progress(game_id):
    """
    Public helper used by websocket_server.py to expose current progress.
    Accepts either a single game_id (string) or a list of game_ids.
    Returns a dictionary with progress information for all requested game_ids.
    """
    # Handle list of game_ids
    if isinstance(game_id, list):
        progresses = {}
        for gid in game_id:
            entry = DOWNLOAD_PROGRESS.get(gid)
            if not entry:
                progresses[gid] = {
                    "game_id": gid,
                    "progress": 0,
                    "status": "not_found",
                    "error": None,
                }
            else:
                progresses[gid] = dict(entry)

        return {
            "action": "get_download_progress",
            "progresses": progresses,
        }

    # Handle single game_id (backward compatibility)
    entry = DOWNLOAD_PROGRESS.get(game_id)
    if not entry:
        return {
            "action": "get_download_progress",
            "game_id": game_id,
            "progress": 0,
            "status": "not_found",
            "error": None,
        }

    result = dict(entry)
    result["action"] = "get_download_progress"
    return result


def receive_file(websocket, data):
    try:
        file_name = data.get("file_name")
        file_data_base64 = data.get("file_data")

        if not file_name or not file_data_base64:
            return create_error_response(
                "send_file",
                ErrorCode.MISSING_PARAMETER,
                "Required file information is missing",
                file_name=file_name,
            )

        if os.path.isabs(file_name):
            file_name = file_name.lstrip("/")
        full_save_path = os.path.join(os.getcwd(), APPS_DIR, file_name)

        # Decode the Base64 file data
        try:
            file_data = b64decode(file_data_base64)
        except Exception as e:
            return create_error_response(
                "send_file",
                ErrorCode.INVALID_INPUT,
                "The file data format is invalid",
                file_name=file_name,
            )

        # Save the file locally
        with open(full_save_path, "wb") as file:
            file.write(file_data)

        return {"action": "send_file", "file_name": file_name, "message": "Success"}
    except PermissionError:
        return handle_exception(
            "send_file",
            PermissionError(),
            "Failed to save file",
            file_name=data.get("file_name"),
        )
    except Exception as e:
        return handle_exception(
            "send_file", e, "Failed to receive file", file_name=data.get("file_name")
        )


def send_file(websocket, data):
    try:
        file_name = data.get("file_name")
        full_file_path = os.path.join(os.getcwd(), APPS_DIR, file_name)

        if not os.path.isfile(full_file_path):
            return handle_file_not_found("get_file", file_name)

        with open(full_file_path, "rb") as file:
            file_data = file.read()
            file_data_base64 = b64encode(file_data).decode("utf-8")

        return {
            "action": "get_file",
            "file_name": file_name,
            "file_data": file_data_base64,
        }
    except PermissionError:
        return handle_exception(
            "get_file",
            PermissionError(),
            "Failed to read file",
            file_name=data.get("file_name"),
        )
    except Exception as e:
        return handle_exception(
            "get_file", e, "Failed to send file", file_name=data.get("file_name")
        )


def get_file_md5(websocket, file_name):
    full_file_path = os.path.join(os.getcwd(), APPS_DIR, file_name)

    if not os.path.isfile(full_file_path):
        return handle_file_not_found("get_file_md5", file_name)

    try:
        # Calculate the MD5 hash of the file
        md5_hash = hashlib.md5()
        with open(full_file_path, "rb") as file:
            for chunk in iter(lambda: file.read(4096), b""):
                md5_hash.update(chunk)
        file_md5 = md5_hash.hexdigest()

        return {"action": "get_file_md5", "file_name": file_name, "md5": file_md5}
    except PermissionError:
        return handle_exception(
            "get_file_md5",
            PermissionError(),
            "Failed to read file for MD5 calculation",
            file_name=file_name,
        )
    except Exception as e:
        return handle_exception(
            "get_file_md5", e, "Failed to calculate MD5", file_name=file_name
        )


def remove_directory(websocket, dir_name):
    if os.path.isabs(dir_name):
        dir_name = dir_name.lstrip("/")

    full_dir_path = os.path.join(os.getcwd(), APPS_DIR, dir_name)

    if not os.path.isdir(full_dir_path):
        return handle_directory_not_found("remove_directory", dir_name)

    try:
        # Remove the directory and its contents
        shutil.rmtree(full_dir_path)
        # Return success message
        return {
            "action": "remove_directory",
            "directory": dir_name,
            "message": "Success",
        }
    except PermissionError:
        return handle_exception(
            "remove_directory",
            PermissionError(),
            "Failed to remove directory",
            directory=dir_name,
        )
    except Exception as e:
        return handle_exception(
            "remove_directory", e, "Failed to remove directory", directory=dir_name
        )


def create_directory(websocket, dir_name):
    if os.path.isabs(dir_name):
        dir_name = dir_name.lstrip("/")

    full_dir_path = os.path.join(os.getcwd(), APPS_DIR, dir_name)

    if os.path.exists(full_dir_path):
        return create_error_response(
            "create_directory",
            ErrorCode.DIRECTORY_ALREADY_EXISTS,
            "The directory already exists",
            directory=dir_name,
        )

    try:
        # Create the directory
        os.makedirs(full_dir_path)
        return {
            "action": "create_directory",
            "directory": dir_name,
            "message": "Success",
        }
    except PermissionError:
        return handle_exception(
            "create_directory",
            PermissionError(),
            "Failed to create directory",
            directory=dir_name,
        )
    except Exception as e:
        return handle_exception(
            "create_directory", e, "Failed to create directory", directory=dir_name
        )


def get_file_list(directory):
    try:
        # List all files in the specified directory
        if os.path.isabs(directory):
            directory = directory.lstrip("/")
        full_dir_path = os.path.join(os.getcwd(), APPS_DIR, directory)
        if not os.path.isdir(full_dir_path):
            return handle_directory_not_found("list_files", directory)
        return {
            "action": "list_files",
            "directory": directory,
            "file_list": os.listdir(full_dir_path),
        }
    except PermissionError:
        return handle_exception(
            "list_files", PermissionError(), "Failed to list files", directory=directory
        )
    except Exception as e:
        return handle_exception(
            "list_files", e, "Failed to list files", directory=directory
        )


def download_app(url, md5):
    try:
        if not url.endswith(".tar.gz"):
            return create_error_response(
                "download_app",
                ErrorCode.INVALID_FILE_TYPE,
                "This file type is not supported. Only .tar.gz files are supported",
                url=url,
            )

        # Ensure the download directory exists
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        file_name = url.split("/")[-1]
        download_path = os.path.join(DOWNLOAD_DIR, file_name)

        # Download the file using system console (wget)
        wget_result = os.system(f"wget -O '{download_path}' '{url}'")
        if wget_result != 0:
            return create_error_response(
                "download_app",
                ErrorCode.DOWNLOAD_FAILED,
                "Unable to download the file",
                url=url,
            )

        # Check if file exists after download
        if not os.path.isfile(download_path):
            return create_error_response(
                "download_app",
                ErrorCode.DOWNLOAD_FAILED,
                "Unable to download the file",
                url=url,
            )

        # Check MD5 using system console
        md5_check_cmd = f"md5sum '{download_path}' | awk '{{print $1}}'"
        downloaded_md5_console = os.popen(md5_check_cmd).read().strip()
        if downloaded_md5_console != md5:
            if os.path.isfile(download_path):
                os.remove(download_path)
            return create_error_response(
                "download_app",
                ErrorCode.MD5_MISMATCH,
                "File integrity check failed. The downloaded file may be corrupted",
                url=url,
            )

        # Extract tar.gz using system console
        extract_cmd = (
            f"tar -xzf '{download_path}' -C '{os.path.join(os.getcwd(), APPS_DIR)}'"
        )
        extract_result = os.system(extract_cmd)
        if extract_result != 0:
            if os.path.isfile(download_path):
                os.remove(download_path)
            return create_error_response(
                "download_app",
                ErrorCode.FILE_EXTRACTION_ERROR,
                "Unable to extract the downloaded file",
                url=url,
            )

        if os.path.isfile(download_path):
            os.remove(download_path)

        return {"action": "download_app", "url": url, "message": "Success"}
    except Exception as e:
        return handle_exception("download_app", e, "Download failed", url=url)


def _download_game_worker(game_id):
    """
    Background worker to download and extract a game by game_id with progress updates.
    """
    download_path = None
    try:
        _set_download_progress(game_id, progress=0, status="initializing", error=None)

        # Get download info from remote API (same as in main.start_game_process)
        response = requests.get(
            f"https://api.dartsnut.com/v1/mobile/game/get-download-info?id={game_id}"
        )
        if response.status_code != 200:
            _set_download_progress(
                game_id,
                status="error",
                error=f"Failed to get download info: {response.status_code}",
            )
            return

        download_info = response.json().get("data")
        if not download_info:
            _set_download_progress(
                game_id,
                status="error",
                error="Download info missing in response",
            )
            return

        game_download_url = download_info.get("game_download_url")
        game_download_md5 = download_info.get("game_download_md5")

        if not game_download_url or not game_download_md5:
            _set_download_progress(
                game_id,
                status="error",
                error="Download URL or MD5 missing",
            )
            return

        if not game_download_url.endswith(".tar.gz"):
            _set_download_progress(
                game_id,
                status="error",
                error="File type not support",
            )
            return

        # Ensure the download directory exists
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        file_name = game_download_url.split("/")[-1]
        download_path = os.path.join(DOWNLOAD_DIR, file_name)

        # Stream download with progress and MD5 calculation
        _set_download_progress(game_id, progress=0, status="downloading")
        with requests.get(game_download_url, stream=True) as r:
            if r.status_code != 200:
                _set_download_progress(
                    game_id,
                    status="error",
                    error=f"Download failed with status {r.status_code}",
                )
                return

            total_length = r.headers.get("Content-Length")
            total_length = int(total_length) if total_length is not None else None

            hash_md5 = hashlib.md5()
            downloaded = 0
            chunk_size = 8192

            with open(download_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    hash_md5.update(chunk)
                    downloaded += len(chunk)

                    if total_length:
                        progress = int(downloaded * 100 / total_length)
                        # Avoid prematurely reporting 100% until post-processing is done
                        if progress >= 100:
                            progress = 99
                        _set_download_progress(
                            game_id, progress=progress, status="downloading"
                        )

        # Verify MD5
        downloaded_md5 = hash_md5.hexdigest()
        if downloaded_md5 != game_download_md5:
            if download_path and os.path.isfile(download_path):
                os.remove(download_path)
            _set_download_progress(
                game_id,
                status="error",
                error="MD5 mismatch",
            )
            return

        # Extract tar.gz using tarfile (Python stdlib)
        apps_dir = os.path.join(os.getcwd(), APPS_DIR)
        try:
            with tarfile.open(download_path, "r:gz") as tar:
                tar.extractall(apps_dir)
        except Exception as e:
            _set_download_progress(
                game_id,
                status="error",
                error=f"Extraction failed: {str(e)}",
            )
            return
        finally:
            if download_path and os.path.isfile(download_path):
                os.remove(download_path)

        version = _read_version_from_conf(game_id)
        _set_download_progress(
            game_id, progress=100, status="completed", error=None, version=version
        )
    except Exception as e:
        # Best-effort cleanup
        try:
            if download_path and os.path.isfile(download_path):
                os.remove(download_path)
        except Exception:
            pass

        _set_download_progress(
            game_id,
            status="error",
            error=str(e),
        )


def _download_game_worker_with_url(game_id, url, md5):
    """
    Background worker to download and extract a game using provided url and md5 with progress updates.
    """
    download_path = None
    try:
        _set_download_progress(game_id, progress=0, status="initializing", error=None)

        if not url or not md5:
            _set_download_progress(
                game_id,
                status="error",
                error="Download URL or MD5 missing",
            )
            return

        if not url.endswith(".tar.gz"):
            _set_download_progress(
                game_id,
                status="error",
                error="File type not support",
            )
            return

        # Ensure the download directory exists
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        file_name = url.split("/")[-1]
        download_path = os.path.join(DOWNLOAD_DIR, file_name)

        # Stream download with progress and MD5 calculation
        _set_download_progress(game_id, progress=0, status="downloading")
        with requests.get(url, stream=True) as r:
            if r.status_code != 200:
                _set_download_progress(
                    game_id,
                    status="error",
                    error=f"Download failed with status {r.status_code}",
                )
                return

            total_length = r.headers.get("Content-Length")
            total_length = int(total_length) if total_length is not None else None

            hash_md5 = hashlib.md5()
            downloaded = 0
            chunk_size = 8192

            with open(download_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    hash_md5.update(chunk)
                    downloaded += len(chunk)

                    if total_length:
                        progress = int(downloaded * 100 / total_length)
                        # Avoid prematurely reporting 100% until post-processing is done
                        if progress >= 100:
                            progress = 99
                        _set_download_progress(
                            game_id, progress=progress, status="downloading"
                        )

        # Verify MD5
        downloaded_md5 = hash_md5.hexdigest()
        if downloaded_md5 != md5:
            if download_path and os.path.isfile(download_path):
                os.remove(download_path)
            _set_download_progress(
                game_id,
                status="error",
                error="MD5 mismatch",
            )
            return

        # Extract tar.gz using tarfile (Python stdlib)
        apps_dir = os.path.join(os.getcwd(), APPS_DIR)
        try:
            with tarfile.open(download_path, "r:gz") as tar:
                tar.extractall(apps_dir)
        except Exception as e:
            _set_download_progress(
                game_id,
                status="error",
                error=f"Extraction failed: {str(e)}",
            )
            return
        finally:
            if download_path and os.path.isfile(download_path):
                os.remove(download_path)

        version = _read_version_from_conf(game_id)
        _set_download_progress(
            game_id, progress=100, status="completed", error=None, version=version
        )
    except Exception as e:
        # Best-effort cleanup
        try:
            if download_path and os.path.isfile(download_path):
                os.remove(download_path)
        except Exception:
            pass

        _set_download_progress(
            game_id,
            status="error",
            error=str(e),
        )


def start_game_download_async(game_id):
    """
    Start an asynchronous game download identified by game_id.
    Used as the v2 behavior of the websocket download_app action.
    """
    if not game_id:
        return create_error_response(
            "download_app",
            ErrorCode.MISSING_PARAMETER,
            "Required information is missing",
            game_id=game_id,
        )

    # Initialize / reset progress entry
    _set_download_progress(game_id, progress=0, status="pending", error=None)

    worker = threading.Thread(
        target=_download_game_worker,
        args=(game_id,),
        daemon=True,
    )
    worker.start()

    return {
        "action": "download_app",
        "game_id": game_id,
        "message": "Success",
    }


def start_game_download_async_with_url(game_id, url, md5):
    """
    Start an asynchronous game download using provided url and md5, tracking progress by game_id.
    """
    if not game_id:
        return create_error_response(
            "download_app",
            ErrorCode.MISSING_PARAMETER,
            "Required information is missing",
            game_id=game_id,
        )

    if not url or not md5:
        return create_error_response(
            "download_app",
            ErrorCode.MISSING_PARAMETER,
            "Required information is missing",
            game_id=game_id,
        )

    # Initialize / reset progress entry
    _set_download_progress(game_id, progress=0, status="pending", error=None)

    worker = threading.Thread(
        target=_download_game_worker_with_url,
        args=(game_id, url, md5),
        daemon=True,
    )
    worker.start()

    return {
        "action": "download_app",
        "game_id": game_id,
        "message": "Success",
    }


def get_app_list():
    try:
        # List all directories in the apps directory
        apps_dir = os.path.join(os.getcwd(), APPS_DIR)

        app_list = []
        for name in os.listdir(apps_dir):
            if os.path.isdir(os.path.join(apps_dir, name)):
                conf_path = os.path.join(apps_dir, name, "conf.json")
                if os.path.isfile(conf_path):
                    with open(conf_path, "r") as conf_file:
                        try:
                            conf = json.load(conf_file)
                            app_list.append(
                                {
                                    "name": name,
                                    "conf": b64encode(
                                        json.dumps(conf).encode("utf-8")
                                    ).decode("utf-8"),
                                }
                            )
                        except Exception:
                            pass
        return {"action": "list_apps", "apps": app_list}
    except PermissionError:
        return handle_exception("list_apps", PermissionError(), "Failed to list apps")
    except Exception as e:
        return handle_exception("list_apps", e, "Failed to list apps")
