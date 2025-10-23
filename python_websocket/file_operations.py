from base64 import b64encode, b64decode
import os
import json
import shutil
import hashlib
import requests
import tarfile

APPS_DIR = "apps"  # Update this to your desired save directory
DOWNLOAD_DIR = "downloads"

def receive_file(websocket, data):
    # data = await websocket.recv()
    # file_info = json.loads(data)

    file_name = data.get("file_name")
    file_data_base64 = data.get("file_data")

    if not file_name or not file_data_base64:
        return {"action":"send_file", "file_name": file_name, "error": "Invalid file information received"}

    if os.path.isabs(file_name):
        file_name = file_name.lstrip("/")
    full_save_path = os.path.join(os.getcwd(), APPS_DIR, file_name)

    # Decode the Base64 file data
    file_data = b64decode(file_data_base64)

    # Save the file locally
    with open(full_save_path, "wb") as file:
        file.write(file_data)

    return {"action": "send_file", "file_name": file_name, "message": "Success"}

def send_file(websocket, data):
    file_name = data.get("file_name")
    full_file_path = os.path.join(os.getcwd(), APPS_DIR, file_name)

    if not os.path.isfile(full_file_path):
        return {"action": "get_file", "file_name": file_name, "error": "File not found"}

    with open(full_file_path, "rb") as file:
        file_data = file.read()
        file_data_base64 = b64encode(file_data).decode('utf-8')

    return {"action": "get_file", "file_name": file_name, "file_data": file_data_base64}

def get_file_md5(websocket, file_name):
    full_file_path = os.path.join(os.getcwd(), APPS_DIR, file_name)

    if not os.path.isfile(full_file_path):
        return {"action": "get_file_md5", "file_name": file_name, "error": "File not found"}

    try:
        # Calculate the MD5 hash of the file
        md5_hash = hashlib.md5()
        with open(full_file_path, "rb") as file:
            for chunk in iter(lambda: file.read(4096), b""):
                md5_hash.update(chunk)
        file_md5 = md5_hash.hexdigest()

        return {"action": "get_file_md5", "file_name": file_name, "md5": file_md5}
    except Exception as e:
        return {"action": "get_file_md5", "file_name": file_name, "error": f"Failed to calculate MD5: {str(e)}"}

def remove_directory(websocket, dir_name):
    if os.path.isabs(dir_name):
        dir_name = dir_name.lstrip("/")

    full_dir_path = os.path.join(os.getcwd(), APPS_DIR, dir_name)

    if not os.path.isdir(full_dir_path):
        return {"action": "remove_directory", "directory": dir_name, "error": "Directory not found"}

    try:
        # Remove the directory and its contents
        shutil.rmtree(full_dir_path)
        # Return success message 
        return {"action": "remove_directory", "directory": dir_name, "message": "Success"}
    except Exception as e:
        return {"action": "remove_directory", "directory": dir_name, "error": f"Failed to remove directory: {str(e)}"}

def create_directory(websocket, dir_name):
    if os.path.isabs(dir_name):
        dir_name = dir_name.lstrip("/")

    full_dir_path = os.path.join(os.getcwd(), APPS_DIR, dir_name)

    if os.path.exists(full_dir_path):
        return {"action": "create_directory", "directory": dir_name, "error": "Directory already exists"}

    try:
        # Create the directory
        os.makedirs(full_dir_path)
        return {"action": "create_directory", "directory": dir_name, "message": "Success"}
    except Exception as e:
        return {"action": "create_directory", "directory": dir_name, "error": f"Failed to create directory: {str(e)}"}

def get_file_list(directory):
    try:
        # List all files in the specified directory
        if os.path.isabs(directory):
            directory = directory.lstrip("/")
        return {"action": "list_files", "directory": directory, "file_list": os.listdir(os.path.join(os.getcwd(), APPS_DIR, directory))}
    except Exception as e:
        return {"action": "list_files", "directory": directory, "error": f"Failed to list file: {str(e)}"}

def download_app(url, md5):
    try:
        if not url.endswith(".tar.gz"):
            return {"action": "download_app", "url": url, "error": "File type not support"}
        
        # Ensure the download directory exists
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        file_name = url.split("/")[-1]
        download_path = os.path.join(DOWNLOAD_DIR, file_name)

        # Download the file using system console (wget)
        os.system(f"wget -O '{download_path}' '{url}'")

        # Check if file exists after download
        if not os.path.isfile(download_path):
            return {"action": "download_app", "url": url, "error": "Download failed"}

        # Check MD5 using system console
        md5_check_cmd = f"md5sum '{download_path}' | awk '{{print $1}}'"
        downloaded_md5_console = os.popen(md5_check_cmd).read().strip()
        if downloaded_md5_console != md5:
            os.remove(download_path)
            return {"action": "download_app", "url": url, "error": "MD5 mismatch (console)"}

        # Extract tar.gz using system console
        extract_cmd = f"tar -xzf '{download_path}' -C '{os.path.join(os.getcwd(), APPS_DIR)}'"
        extract_result = os.system(extract_cmd)
        if extract_result != 0:
            return {"action": "download_app", "url": url, "error": "Extraction failed (console)"}

        if os.path.isfile(download_path):
            os.remove(download_path)
           
        return {"action": "download_app", "url": url, "message": "Success"}
    except Exception as e:
        return {"action": "download_app", "error": str(e)}

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
                            app_list.append({
                                "name": name,
                                "conf":  b64encode(json.dumps(conf).encode("utf-8")).decode("utf-8")
                            })
                        except Exception:
                            pass
        return {"action": "list_apps", "apps": app_list}
    except Exception as e:
        return {"action": "list_apps", "error": f"Failed to list apps: {str(e)}"}