from multiprocessing import shared_memory
import subprocess
import os
import signal
import time
import json
import threading
import base64
import tempfile
import requests
from PIL import Image, ImageDraw, ImageFont
import io
from python_ble.ble_server import start_ble_server
from python_websocket.websocket_server import start_websocket_server
from python_websocket.user_data_operations import start_game_tracking, stop_game_tracking, _load_user_data
from python_websocket.device_operations import _parse_hhmm
from pydartsnut import Dartsnut
import struct
import glob
from datetime import datetime, time as dt_time

dartsnut = Dartsnut()

# Load the loading sprite sheet and extract frames
loading_sprite_sheet = Image.open("./loading_sprite.png")
loading_sprite_width = loading_sprite_sheet.size[0] // 7  # 7 frames horizontally
loading_sprite_height = loading_sprite_sheet.size[1]
loading_frames = []
for i in range(7):
    frame = loading_sprite_sheet.crop((i * loading_sprite_width, 0, (i + 1) * loading_sprite_width, loading_sprite_height))
    loading_frames.append(frame)
# Load the big loading sprite sheet and extract frames (for top 128x128 area)
loading_sprite_big_sheet = Image.open("./loading_sprite_big.png")
loading_sprite_big_width = loading_sprite_big_sheet.size[0] // 7  # 7 frames horizontally
loading_sprite_big_height = loading_sprite_big_sheet.size[1]  # 64px tall
loading_frames_big = []
for i in range(7):
    frame = loading_sprite_big_sheet.crop((i * loading_sprite_big_width, 0, (i + 1) * loading_sprite_big_width, loading_sprite_big_height))
    loading_frames_big.append(frame)
# Animation state
loading_frame_index = 0
loading_frame_last_update = time.time()
loading_frame_duration = 0.1  # 10 fps (0.1 seconds per frame)
# Load the logo image
logo_image = Image.open("./logo.png").resize((128,128))
# Load the identify image
identify_image = Image.open("./identify.png")
# Load the icons
game_icon = Image.open("./game_icon.png")
settings_icon = Image.open("./settings_icon.png")
widget_icon = Image.open("./widget_icon.png")
lock_widget_icon = Image.open("./lock_widget_icon.png")
wifi_disconnect_icon = Image.open("./wifi_disconnect_icon.png")
internet_disconnect_icon = Image.open("./internet_disconnect_icon.png")
# Load the game select image
game_select_image = Image.open("./game_sel.png")
# Load the font
font8 = ImageFont.load("./dartsnut-6X8.pil")
font16 = ImageFont.truetype("./Micro5.ttf", size=16)
font24 = ImageFont.truetype("./Micro5.ttf", size=24)

# Function to set PR_SET_PDEATHSIG
def set_pdeathsig():
    import ctypes
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)

# Function to get user data store path
def _get_user_data_store_path(app_id):
    """Get the user data store path based on user_id and app_id, defaulting to 'guest' if user_id is empty."""
    try:
        user_data = _load_user_data()
        user_id = user_data.get("user_id", "")
        if user_id == "":
            user_id = "guest"
        data_store_path = f"/var/lib/dartsnut/user/{user_id}/{app_id}/"
        # Ensure the directory exists
        os.makedirs(data_store_path, mode=0o755, exist_ok=True)
        return data_store_path
    except Exception as e:
        # On error, default to guest
        print(f"Warning: Failed to load user data, defaulting to guest: {e}")
        data_store_path = f"/var/lib/dartsnut/user/guest/{app_id}/"
        os.makedirs(data_store_path, mode=0o755, exist_ok=True)
        return data_store_path

# Function to get the current loading frame based on animation timing
def get_current_loading_frame():
    global loading_frame_index, loading_frame_last_update
    current_time = time.time()
    # Update frame if enough time has passed
    if current_time - loading_frame_last_update >= loading_frame_duration:
        loading_frame_index = (loading_frame_index + 1) % 7
        loading_frame_last_update = current_time
    return loading_frames[loading_frame_index]

# Function to get the current big loading frame based on animation timing
def get_current_loading_frame_big():
    global loading_frame_index, loading_frame_last_update
    current_time = time.time()
    # Update frame if enough time has passed (uses same timing as regular frame)
    if current_time - loading_frame_last_update >= loading_frame_duration:
        loading_frame_index = (loading_frame_index + 1) % 7
        loading_frame_last_update = current_time
    return loading_frames_big[loading_frame_index]

# Function to create a 128x160 loading image with sprites positioned correctly
def create_loading_image():
    """Create a 128x160 image with big loading sprite centered in upper 128x128 area and regular sprite in bottom 64x32 area."""
    current_loading_frame_big = get_current_loading_frame_big()
    current_loading_frame = get_current_loading_frame()
    # Ensure they're in RGB mode
    if current_loading_frame_big.mode != "RGB":
        current_loading_frame_big = current_loading_frame_big.convert("RGB")
    if current_loading_frame.mode != "RGB":
        current_loading_frame = current_loading_frame.convert("RGB")
    # Create a 128x160 black image
    loading_image = Image.new("RGB", (128, 160), (0, 0, 0))
    # Paste the big loading sprite in the upper 128x128 area
    # Big sprite is 128x64, upper area is 128x128, so position at x=0, y=(128-64)/2=32
    loading_image.paste(current_loading_frame_big, (0, 32))
    # Paste the regular loading sprite centered in the bottom 64x32 area
    # Bottom area is 64x32 (x=0-63, y=128-159), sprite is 64x32, so it fits perfectly at (0, 128)
    loading_image.paste(current_loading_frame, (0, 128))
    return loading_image

# Function to check if widget is ready by checking top and bottom pixel rows
def check_widget_ready(widget_frame):
    """
    Check if widget is ready by examining top and bottom rows of pixels.
    Returns True if any pixel in top or bottom row is not black (0,0,0).
    """

    def _get_flattened_data_compat(img):
        """
        Backwards-compatible pixel flattener.
        Prefer new get_flattened_data() API, but fall back to old getdata().
        """
        # New API: get_flattened_data()
        if hasattr(img, "get_flattened_data"):
            try:
                return img.get_flattened_data()
            except Exception:
                # If anything goes wrong, fall through to the old API
                pass

        # Old API: getdata()
        if hasattr(img, "getdata"):
            try:
                data_iter = iter(img.getdata())
                first = next(data_iter, None)
                if first is None:
                    return []
                # If pixels are tuples/lists, flatten them; otherwise return scalars
                if isinstance(first, (tuple, list)):
                    flat = list(first)
                    for px in data_iter:
                        flat.extend(px)
                    return flat
                else:
                    return [first, *list(data_iter)]
            except Exception:
                pass

        # Fallback: use raw bytes
        try:
            return list(img.tobytes())
        except Exception:
            return []

    if widget_frame is None:
        return False
    
    width, height = widget_frame.size
    
    # Check top row (y=0)
    top_row = widget_frame.crop((0, 0, width, 1))
    top_pixels_flat = _get_flattened_data_compat(top_row)
    top_has_content = any(value != 0 for value in top_pixels_flat)
    
    # Check bottom row (y=height-1)
    if height > 1:
        bottom_row = widget_frame.crop((0, height - 1, width, height))
        bottom_pixels_flat = _get_flattened_data_compat(bottom_row)
        bottom_has_content = any(value != 0 for value in bottom_pixels_flat)
    else:
        # If height is 1, we already checked it in top_row
        bottom_has_content = False
    
    return top_has_content or bottom_has_content

# Private function to draw "Firmware Updated" text with gap (font doesn't support spaces)
def _draw_firmware_updated_text(draw, x, y):
    """Draw 'FIRMWARE UPDATED' text split into two parts with a gap.
    
    Args:
        draw: ImageDraw object to draw on
        x: Target x coordinate (will be centered around this if negative)
        y: Target y coordinate
    """
    firmware_text1 = "FIRMWARE"
    firmware_text2 = "UPDATED"
    gap_width = 6  # gap width in pixels (equivalent to one character)
    firmware_text1_width = len(firmware_text1) * 6
    firmware_text2_width = len(firmware_text2) * 6
    total_width = firmware_text1_width + gap_width + firmware_text2_width
    
    # If x is negative, center the text horizontally on screen (128px wide)
    if x < 0:
        firmware_text_x = int((128 - total_width) / 2)
    else:
        firmware_text_x = x
    
    draw.text((firmware_text_x, y), firmware_text1, fill=(255, 255, 255), font=font8)
    draw.text((firmware_text_x + firmware_text1_width + gap_width, y), firmware_text2, fill=(255, 255, 255), font=font8)

# Private function to check if firmware update flag exists
def _check_firmware_updated_flag():
    """Check if the firmware update flag file exists.
    
    Returns:
        bool: True if flag file exists, False otherwise
    """
    flag_path = "/tmp/firmware_updated.flag"
    return os.path.isfile(flag_path)

# Private function to remove firmware update flag
def _remove_firmware_updated_flag():
    """Remove the firmware update flag file if it exists."""
    flag_path = "/tmp/firmware_updated.flag"
    try:
        if os.path.isfile(flag_path):
            os.remove(flag_path)
    except Exception:
        pass  # Ignore errors removing flag file

# Function to process the widget's fields
def process_widget_fields(widget_id, widget_fields_parameter):
    params = widget_fields_parameter.copy()
    # read the app's conf.json file
    conf_path = os.path.join(os.getcwd(), "apps", widget_id, "conf.json")
    with open(conf_path, "r") as f:
        conf = json.load(f)
        # special handling for files and image type
        for field in conf["fields"]:
            # if there is image type in the field, decode the base64 data
            if field["type"] == "image":
                if params.get(field["id"]) is not None:
                    # read the file data
                    file = params[field["id"]]["image"]
                    # file is longer then 500 bytes, it's from an older version of the app
                    if len(file) > 500:
                        file_data = base64.b64decode(file)
                        # write the file data into a named temp file
                        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                            tmp_file.write(file_data)
                            tmp_file_path = tmp_file.name
                            # replace the file field with the file paths
                            params[field["id"]]["image"] = tmp_file_path
    return params

# ============================================================================
# Widget Update Feature
# ============================================================================
# Automatic widget version checking and background updates.
# Widgets are checked when pages become active, and updates are downloaded
# in the background. Processes are killed and restarted when appropriate
# to ensure widgets run with the latest version.
# ============================================================================

# Global dictionary to track last update check time for each widget
widget_update_checks = {}
# Global set to track widgets that have been updated and need restart
widgets_updated = set()

# Function to check widget version and determine if update is needed
def check_and_update_widget_version(widget_id):
    """
    Check if widget needs update by comparing local version with API version.
    Returns tuple: (needs_update: bool, download_info: dict or None)
    """
    try:
        # Read current version from local conf.json
        conf_path = os.path.join(os.getcwd(), "apps", widget_id, "conf.json")
        local_version = None
        if os.path.isfile(conf_path):
            try:
                with open(conf_path, "r") as f:
                    conf = json.load(f)
                    local_version = conf.get("version")
            except Exception as e:
                print(f"Error reading conf.json for widget {widget_id}: {e}")
        
        # Fetch latest version from API
        try:
            response = requests.get(f"https://api.dartsnut.com/v1/mobile/widget/get-download-info?id={widget_id}")
            if response.status_code == 200:
                download_info = response.json().get("data")
                if download_info is not None:
                    api_version = download_info.get("version")
                    
                    # If local version is missing, schedule update
                    if local_version is None:
                        return (True, download_info)
                    
                    # If API version is missing, don't update
                    if api_version is None:
                        return (False, None)
                    
                    # Compare versions (simple string comparison)
                    if local_version != api_version:
                        return (True, download_info)
                    
                    return (False, download_info)
            else:
                print(f"Failed to get download info for widget {widget_id}: {response.status_code}")
                return (False, None)
        except Exception as e:
            print(f"Error fetching widget download info for {widget_id}: {e}")
            return (False, None)
    except Exception as e:
        print(f"Error checking widget version for {widget_id}: {e}")
        return (False, None)

# Function to download widget asynchronously in background
def download_widget_async(widget_id, url, md5):
    """
    Download widget in background thread without blocking widget startup.
    
    After download completes, calls kill_widget_if_page_inactive() to handle
    process termination based on whether the widget's page is currently active.
    
    Args:
        widget_id: The widget ID to download
        url: Download URL for the widget
        md5: MD5 hash for verification
    """
    def download_worker():
        try:
            print(f"Starting background download for widget {widget_id}")
            result = download_app(url, md5)
            if result:
                print(f"Successfully updated widget {widget_id}")
                # Kill widget process if its page is not currently active
                kill_widget_if_page_inactive(widget_id)
            else:
                print(f"Failed to update widget {widget_id}")
        except Exception as e:
            print(f"Error in background download for widget {widget_id}: {e}")
    
    worker = threading.Thread(target=download_worker, daemon=True)
    worker.start()

# Widget Update Feature Functions

# Helper function to kill a widget process and clean up resources
def _kill_widget_process(widget_entry, widget_id, reason=""):
    """
    Helper function to kill a widget process and clean up resources.
    
    Args:
        widget_entry: Widget entry dictionary with process and shm
        widget_id: Widget ID for logging
        reason: Optional reason string for logging
    """
    try:
        process = widget_entry.get("process")
        if process and process.poll() is None:
            log_msg = f"Killing widget {widget_id} process"
            if reason:
                log_msg += f" ({reason})"
            print(log_msg)
            os.kill(process.pid, signal.SIGCONT)
            os.kill(process.pid, signal.SIGKILL)
            # Clean up shared memory
            shm = widget_entry.get("shm")
            if shm:
                try:
                    shm.close()
                    shm.unlink()
                except Exception as e:
                    print(f"Error cleaning up shared memory for widget {widget_id}: {e}")
            # Mark process as None so it can be restarted
            widget_entry["process"] = None
            widget_entry["shm"] = None
            widget_entry["launched"] = False  # Reset launch status when killed
    except Exception as e:
        print(f"Error killing widget {widget_id} process: {e}")

# Function to kill widget process if its page is not currently active
def kill_widget_if_page_inactive(widget_id):
    """
    Handle widget process termination after update download completes.
    
    If the widget's page is not currently active, kills the process immediately.
    If the page is active, marks the widget in widgets_updated set so it will
    be killed when the page is suspended.
    
    Args:
        widget_id: The widget ID that was just updated
    """
    global pages, page_index, state
    
    # Only proceed if we're in widget state and pages exist
    if state != "widget" or pages is None:
        return
    
    # Find the page containing this widget
    for page_idx, page in enumerate(pages):
        for widget_entry in page.get("widgets", []):
            widget = widget_entry.get("widget")
            if widget and widget.get("id") == widget_id:
                # Found the widget, check if its page is currently active
                if page_idx != page_index:
                    # Page is not active, kill the widget process
                    _kill_widget_process(widget_entry, widget_id, "page not active, update complete")
                else:
                    # Page is currently active, mark widget as updated (will kill when suspended)
                    widgets_updated.add(widget_id)
                    print(f"Widget {widget_id} update complete, page is active - will restart when suspended")
                return

# Function to restart a widget process
def restart_widget_process(widget_entry, page, widget_index):
    """
    Restart a widget process that was killed after an update.
    
    Creates new shared memory and process for the widget, loading the updated
    widget code. This is called when a page becomes active and its widgets
    need to be restarted with the new version.
    
    Args:
        widget_entry: Widget entry dictionary to update with new process/shm
        page: Page object containing the widget
        widget_index: Index of the widget within the page
    """
    widget = widget_entry.get("widget")
    if not widget:
        return
    
    widget_id = widget.get("id")
    if widget_id == "0":
        return  # Skip default widget
    
    widget_path = os.path.join(os.getcwd(), "apps", widget_id)
    if not os.path.isdir(widget_path):
        return
    
    try:
        page_uuid = page["uuid"]
        shm_name = f"widget_{page_uuid}_{widget_index}_shm"
        shm_size = (widget["position"][2] - widget["position"][0] + 1) * (widget["position"][3] - widget["position"][1] + 1) * 3 + 1
        
        # Clean up any existing shared memory
        try:
            existing_shm = shared_memory.SharedMemory(name=shm_name)
            existing_shm.close()
            shared_memory.SharedMemory(name=shm_name).unlink()
        except FileNotFoundError:
            pass
        except FileExistsError:
            shared_memory.SharedMemory(name=shm_name).unlink()
        
        # Create new shared memory and process
        shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
        shm.buf[0] = 1
        command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "apps/", widget_id, "main.py")]
        command.extend(["--params", json.dumps(process_widget_fields(widget_id, widget["fields"]))])
        command.extend(["--shm", shm_name])
        data_store_path = _get_user_data_store_path(widget_id)
        command.extend(["--data-store", data_store_path])
        process = subprocess.Popen(
            command,
            cwd=os.path.join("./apps/", widget_id),
            preexec_fn=set_pdeathsig
        )
        
        # Update widget entry with new process and shared memory
        widget_entry["process"] = process
        widget_entry["shm"] = shm
        widget_entry["launched"] = False  # Reset launch status for new process
        widget_entry["has_small_widget"] = None  # Reset small widget detection for new process
        print(f"Successfully restarted widget {widget_id}")
    except Exception as e:
        print(f"Error restarting widget {widget_id}: {e}")

# Function to check and update widgets in a page, respecting throttling
def check_page_widget_updates(page):
    """
    Check and update widgets in the given page, respecting throttling.
    
    Only checks widgets if 30 seconds have passed since the last check for
    that widget. If an update is needed, starts background download.
    
    Args:
        page: Page object containing widgets to check
    """
    current_time = time.time()
    check_interval = 3600  # 1 hour
    
    for widget_entry in page["widgets"]:
        # Widget structure: {"process": ..., "shm": ..., "widget": {"id": ..., ...}}
        widget = widget_entry.get("widget")
        if widget is None:
            continue
        
        widget_id = widget.get("id")
        if widget_id is None:
            continue
        
        # Skip default widget (id == "0")
        if widget_id == "0":
            continue
        
        # Check throttling
        last_check_time = widget_update_checks.get(widget_id, 0)
        if current_time - last_check_time > check_interval:
            try:
                needs_update, download_info = check_and_update_widget_version(widget_id)
                widget_update_checks[widget_id] = current_time
                
                if needs_update and download_info is not None:
                    widget_download_url = download_info.get("widget_download_url")
                    widget_download_md5 = download_info.get("widget_download_md5")
                    if widget_download_url and widget_download_md5:
                        download_widget_async(widget_id, widget_download_url, widget_download_md5)
            except Exception as e:
                print(f"Error checking widget version for {widget_id}: {e}")

# Function to download widget from the server
def download_app(url, md5):
    try:
        if not url.endswith(".tar.gz"):
            return False
        
        # Ensure the download directory exists
        os.makedirs("downloads", exist_ok=True)
        file_name = url.split("/")[-1]
        download_path = os.path.join("downloads", file_name)

        # Download the file using system console (wget)
        try:
            # -T/--timeout sets all timeouts (DNS, connect, read)
            # --read-timeout sets the read (idle) timeout specifically
            subprocess.run(["wget", "--read-timeout=10", "-O", download_path, url], check=True)
        except subprocess.CalledProcessError:
            return False

        # Check if file exists after download
        if not os.path.isfile(download_path):
            return False

        # Check MD5 using system console
        try:
            result = subprocess.run(["md5sum", download_path], capture_output=True, text=True, check=True)
            downloaded_md5_console = result.stdout.split()[0]
        except subprocess.CalledProcessError:
            os.remove(download_path)
            return False
            
        if downloaded_md5_console != md5:
            os.remove(download_path)
            return False

        # Extract tar.gz using system console
        apps_path = os.path.join(os.getcwd(), "apps")
        try:
            subprocess.run(["tar", "-xzf", download_path, "-C", apps_path], check=True)
        except subprocess.CalledProcessError:
            return False
            
        return True

        if os.path.isfile(download_path):
            os.remove(download_path)

        return True
    except Exception as e:
        print(f"Error downloading app: {e}")
        return False

# Function to start the widget page process, return with the widget process and shared memory object
def start_page_process(page):
    widgets = []
    for widget_index in range(len(page["widgets"])):
        widget = page["widgets"][widget_index]
        # check if the widget is default widget
        if widget["id"] == "0":
            page_uuid = page["uuid"]
            shm_name = f"widget_{page_uuid}_{widget_index}_shm"
            shm_size = (widget["position"][2] - widget["position"][0] + 1) * (widget["position"][3] - widget["position"][1] + 1) * 3 + 1  # Example size in bytes
            try:
                existing_shm = shared_memory.SharedMemory(name=shm_name)
                existing_shm.close()
                shared_memory.SharedMemory(name=shm_name).unlink()
            except FileNotFoundError:
                pass
            except FileExistsError:
                shared_memory.SharedMemory(name=shm_name).unlink()
            # start the process
            try:
                shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
                # if the uuid is "0", it is a default widget
                command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "default.py")]
                command.extend(["--params", "{}"])
                command.extend(["--shm", shm_name])
                data_store_path = _get_user_data_store_path("0")
                command.extend(["--data-store", data_store_path])
                process = subprocess.Popen(
                    command,
                    cwd=os.getcwd(),
                    preexec_fn=set_pdeathsig
                )
                widgets.append({"process":process, "shm": shm, "widget": widget, "launched": False, "has_small_widget": None})
            except Exception as e:
                print(f"Error starting default widget: {e}")
                # If the widget fails to start, we don't add it to the page
                continue
        # check if the widget exists
        else:
            widget_path = os.path.join(os.getcwd(), "apps", widget["id"])
            # if the widget does not exist, try to get the download url
            if not os.path.isdir(widget_path):
                try:
                    response = requests.get(f"https://api.dartsnut.com/v1/mobile/widget/get-download-info?id={widget['id']}")
                    if response.status_code == 200:
                        download_info = response.json().get("data")
                        if download_info is not None:
                            widget_download_url = download_info.get("widget_download_url")
                            widget_download_md5 = download_info.get("widget_download_md5")
                            download_app(widget_download_url, widget_download_md5)
                    else:
                        print(f"Failed to get download info for widget {widget['id']}: {response.status_code}")
                except Exception as e:
                    print(f"Error fetching widget download info: {e}")

            # if the widget exists, start the process
            if os.path.isdir(widget_path):
                page_uuid = page["uuid"]
                shm_name = f"widget_{page_uuid}_{widget_index}_shm"
                shm_size = (widget["position"][2] - widget["position"][0] + 1) * (widget["position"][3] - widget["position"][1] + 1) * 3 + 1  # Example size in bytes
                try:
                    existing_shm = shared_memory.SharedMemory(name=shm_name)
                    existing_shm.close()
                    shared_memory.SharedMemory(name=shm_name).unlink()
                except FileNotFoundError:
                    pass
                except FileExistsError:
                    shared_memory.SharedMemory(name=shm_name).unlink()
                # start the process
                try:
                    shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
                    shm.buf[0] = 1
                    command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "apps/", widget["id"], "main.py")]
                    command.extend(["--params", json.dumps(process_widget_fields(widget["id"], widget["fields"]))])
                    command.extend(["--shm", shm_name])
                    data_store_path = _get_user_data_store_path(widget["id"])
                    command.extend(["--data-store", data_store_path])
                    process = subprocess.Popen(
                        command,
                        cwd=os.path.join("./apps/", widget["id"]),
                        preexec_fn=set_pdeathsig
                    )
                    widgets.append({"process":process, "shm": shm, "widget": widget, "launched": False, "has_small_widget": None})
                except Exception as e:
                    print(f"Error starting widget {widget['id']}: {e}")
                    # If the widget fails to start, we don't add it to the page
                    continue
    # if at lease one widget is valid, add the page
    if len(widgets) > 0:
        # Initialize page framebuffer as black - widgets will render their content immediately
        img = Image.new("RGB", (128, 160), (0, 0, 0))
        for widget in widgets:
            try:
                # pause all widgets process
                os.kill(widget["process"].pid, signal.SIGSTOP)
                # Reset launched flag when pausing - will need to re-establish when resumed
                widget["launched"] = False
            except Exception as e:
                print(f"Error pausing widget process: {e}")
        
        return {"widgets" : widgets, "duration" : page["duration"], "uuid" : page["uuid"], "framebuffer": bytearray(img.tobytes()), "enabled": page.get("enabled", True)}
    else:
        return None

# Function to start the game process, return with the game process and shared memory object
def start_game_process(gameid):
    game_path = os.path.join(os.getcwd(), "apps", gameid)

    if not os.path.isdir(game_path):
        try:
            response = requests.get(f"https://api.dartsnut.com/v1/mobile/game/get-download-info?id={gameid}")
            if response.status_code == 200:
                download_info = response.json().get("data")
                if download_info is not None:
                    game_download_url = download_info.get("game_download_url")
                    game_download_md5 = download_info.get("game_download_md5")
                    download_app(game_download_url, game_download_md5)
            else:
                print(f"Failed to get download info for game {gameid}: {response.status_code}")
        except Exception as e:
            print(f"Error fetching widget download info: {e}")

    if os.path.isdir(game_path):
        shm_name = f"game_shm"
        shm_size = 128 * 160 * 3 + 1  # Example size in bytes
        try:
            existing_shm = shared_memory.SharedMemory(name=shm_name)
            existing_shm.close()
            shared_memory.SharedMemory(name=shm_name).unlink()
        except FileNotFoundError:
            pass
        except FileExistsError:
            shared_memory.SharedMemory(name=shm_name).unlink()
        # start the process
        try:
            shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
            # Initialize shared memory with the current loading frame
            loading_image = create_loading_image()
            img_bytes = loading_image.tobytes()
            shm.buf[1:1+len(img_bytes)] = img_bytes
            shm.buf[0] = 0
            # if the uuid is "0", it is a default widget
            command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "apps/", gameid, "main.py")]
            command.extend(["--shm", shm_name])
            data_store_path = _get_user_data_store_path(gameid)
            command.extend(["--data-store", data_store_path])
            process = subprocess.Popen(
                command,
                cwd=os.path.join("./apps/", gameid),
                preexec_fn=set_pdeathsig
            )
            # Start tracking game playtime
            try:
                start_game_tracking(gameid)
            except Exception as e:
                print(f"Warning: Failed to start game tracking: {e}")
            return {"process":process, "shm": shm, "game_id": gameid, "launched": False, "pico8_first_frame_seen": False}
        except Exception as e:
            print(f"Error starting game {gameid}: {e}")
    return None

# Function to terminate the game process and clean up
def term_game_process(g):
    if g is not None:
        try:
            # Stop tracking game playtime before terminating
            try:
                stop_game_tracking()
            except Exception as e:
                print(f"Warning: Failed to stop game tracking: {e}")
            os.kill(g["process"].pid, signal.SIGCONT)
            os.kill(g["process"].pid, signal.SIGKILL)
            g["shm"].close()
            g["shm"].unlink()
            g.clear()
            g = None
        except Exception as e:
            print(f"Error terminating game: {e}")
    return None

# Function to load the game list from the apps directory, return with the game list along with the preview images
def load_game_list():
    game_list = []
    apps_dir = os.path.join(os.getcwd(), "apps")
    for name in os.listdir(apps_dir):
        if os.path.isdir(os.path.join(apps_dir, name)):
            conf_path = os.path.join(apps_dir, name, "conf.json")
            if os.path.isfile(conf_path):
                try:
                    with open(conf_path, "r") as conf_file:
                        conf = json.load(conf_file)
                        if conf["type"] == "game":
                            if "preview" in conf:
                                images = []
                                for img_b64 in conf["preview"]:
                                    img_data = base64.b64decode(img_b64)
                                    img = Image.open(io.BytesIO(img_data))
                                    img = img.convert("RGB").resize((128, 128), Image.LANCZOS)
                                    image = Image.new("RGB", (128, 160), (0, 0, 0))
                                    image.paste(img, (0, 0))
                                    images.append(bytearray(image.tobytes()))
                                conf["preview"] = images
                            game_list.append(conf)
                except Exception as e:
                    print(f"Error loading game config for {name}: {e}")
    return game_list

# Function to initialize the pages based on the configuration, return with the page list
def init_pages(config):
    pages = []
    # Start the processes based on the configuration
    for page in config["pages"]:
        # if (page["enabled"]):
        page_process = start_page_process(page)
        if page_process is not None:
            pages.append(page_process)
    # add the default widget
    pages.append(start_page_process({
        "uuid": "0",
        "title": "default widget",
        "duration" : "60",
        "combination" : "0",
        "enabled" : True,
        "widgets" : [{
            "id": "0",
            "position": [0,0,127,159],
            "fields": {}
        }]
    }))
    return pages

# Function to terminate all widget processes and clean up
def term_widget_processes(pages):
    if pages is not None:
        for page in pages:
            for widget in page["widgets"]:
                try:
                    os.kill(widget["process"].pid, signal.SIGCONT)
                    os.kill(widget["process"].pid, signal.SIGKILL)
                    widget["shm"].close()
                    widget["shm"].unlink()
                    widget.clear()
                except Exception as e:
                    print(f"Error terminating widget: {e}")
            page.clear()
        pages.clear()

# Function to read the buttons state and return which buttons are pressed (triggered)
def get_buttons_pressed():
    # initialize the old_buttons attribute on the first call
    if not hasattr(get_buttons_pressed, "old_buttons"):
        get_buttons_pressed.old_buttons = {
            "btn_a" : False,
            "btn_b" : False,
            "btn_left" : False,
            "btn_up" : False,
            "btn_right" : False,
            "btn_down" : False,
            "btn_home" : False,
            "btn_reserved" : False
        }
    # Get the buttons from the shared memory
    button_states = dartsnut.get_buttons()
    button_pressed = {
        "btn_a" : False,
        "btn_b" : False,
        "btn_left" : False,
        "btn_up" : False,
        "btn_right" : False,
        "btn_down" : False,
        "btn_home" : False,
        "btn_reserved" : False
    }
    # GPIO Buttons (Rising Edge)
    for i, key in enumerate(button_states):
        if (button_states[key] != get_buttons_pressed.old_buttons[key]):
            get_buttons_pressed.old_buttons[key] = button_states[key]
            if (button_states[key]):
                button_pressed[key] = True

    # Initialize joystick readers if not already done
    if not hasattr(get_buttons_pressed, "js_files"):
        get_buttons_pressed.js_files = {}
    # Scan for new joystick devices
    for js_path in glob.glob("/dev/input/js*"):
        if js_path not in get_buttons_pressed.js_files:
            try:
                f = open(js_path, "rb")
                os.set_blocking(f.fileno(), False)
                get_buttons_pressed.js_files[js_path] = f
            except Exception:
                pass
    # Controller Buttons (Events)
    if get_buttons_pressed.js_files and not state == "in_game":
        # Iterate over a copy of keys to allow modification during iteration
        for js_path in list(get_buttons_pressed.js_files.keys()):
            js_file = get_buttons_pressed.js_files[js_path]
            while True:
                try:
                    event_data = js_file.read(8)
                    if event_data is None:
                        break
                    if not event_data:
                        # EOF, device disconnected
                        raise OSError("Device disconnected")

                    time_ms, value, type_, number = struct.unpack("Ihbb", event_data)
                    # JS_EVENT_BUTTON = 0x01
                    if type_ & 0x01:
                        if value == 1: # Button press
                            if number == 0: button_pressed["btn_a"] = True       # A / Cross
                            elif number == 1: button_pressed["btn_b"] = True     # B / Circle
                            elif number == 8: button_pressed["btn_home"] = True  # Select / Back
                            elif number == 9: button_pressed["btn_home"] = True  # Start
                    
                    # JS_EVENT_AXIS = 0x02
                    elif type_ & 0x02:
                        if number == 6: # X axis
                            if value < -16000: button_pressed["btn_left"] = True
                            elif value > 16000: button_pressed["btn_right"] = True
                        elif number == 7: # Y axis
                            if value < -16000: button_pressed["btn_up"] = True
                            elif value > 16000: button_pressed["btn_down"] = True
                except (BlockingIOError, InterruptedError):
                    break
                except Exception:
                    # If reading fails (e.g. device disconnected), close and remove
                    try:
                        js_file.close()
                    except:
                        pass
                    if js_path in get_buttons_pressed.js_files:
                        del get_buttons_pressed.js_files[js_path]
                    break

    return button_pressed

# Function to read device.json file
def get_device_info():
    # Caching mechanism
    if not hasattr(get_device_info, "_last_mtime"):
        get_device_info._last_mtime = 0
        get_device_info._cached_device_info = None
    # Read device.json file
    file_path = os.path.join(os.getcwd(), "device.json")
    try:
        current_mtime = os.path.getmtime(file_path)
        if current_mtime != get_device_info._last_mtime or get_device_info._cached_device_info is None:
            with open(file_path, 'r') as file:
                get_device_info._cached_device_info = json.load(file)
            get_device_info._last_mtime = current_mtime
        return get_device_info._cached_device_info
    except Exception:
        return {}

# Dim window state (module-level)
_currently_in_dim_window = False
_brightness_before_dim = None
_last_dim_check_time = 0
_dim_temporary_restore_until = None


def _set_brightness_hardware(brightness):
    """Set brightness on hardware only (no persist). Used for dim/restore."""
    dartsnut.set_brightness(brightness)


# Function to set brightness
def set_brightness(brightness):
    global _brightness_before_dim
    # Only-persist when in dim window and currently showing dimmed (not in active temporary restore)
    if _currently_in_dim_window and (_dim_temporary_restore_until is None or time.time() >= _dim_temporary_restore_until):
        try:
            device_info = get_device_info()
            device_info["brightness"] = str(brightness)
            with open("./device.json", "w") as file:
                json.dump(device_info, file)
            _brightness_before_dim = brightness
        except FileNotFoundError:
            print("Device info file not found")
        except json.JSONDecodeError:
            print("Error decoding JSON from device info file")
        except Exception as e:
            print(f"An error occurred while updating device info: {e}")
        return
    # Else: set hardware and persist
    _set_brightness_hardware(brightness)
    try:
        device_info = get_device_info()
        device_info["brightness"] = str(brightness)
        with open("./device.json", "w") as file:
            json.dump(device_info, file)
    except FileNotFoundError:
        print("Device info file not found")
    except json.JSONDecodeError:
        print("Error decoding JSON from device info file")
    except Exception as e:
        print(f"An error occurred while updating device info: {e}")

# Function to set volume
def set_volume(volume):
    try:
        if volume == 0:
            # Mute audio when volume is 0
            subprocess.run(
                ['amixer', '-c', '0', 'sset', 'PCM', 'mute'],
                check=True,
                capture_output=True
            )
        else:
            # Unmute and set volume when volume > 0
            # Map the volume from (0,100) to (50,100)
            mapped_volume = int(50 + (volume / 100) * 50)
            # First unmute, then set volume
            subprocess.run(
                ['amixer', '-c', '0', 'sset', 'PCM', 'unmute'],
                check=True,
                capture_output=True
            )
            subprocess.run(
                ['amixer', '-c', '0', 'sset', 'PCM', f'{mapped_volume}%'],
                check=True,
                capture_output=True
            )
        
        try:
            # Read the existing device info
            device_info = get_device_info()
            # Update the device volume
            device_info['volume'] = str(volume)
            # Write the updated info back to the file
            with open("./device.json", 'w') as file:
                json.dump(device_info, file)
        except FileNotFoundError:
            print(f"Device info file not found")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from device info file")
        except Exception as e:
            print(f"An error occurred while updating device info: {e}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to set volume: {e.stderr.decode().strip()}")

# Function to locate the device
def locate_device():
    global locate_device_intv
    locate_device_intv = 60*3 # 3 seconds for 60fps

# Function to reload the widget conf.json
def reload_config():
    global reload_conf
    reload_conf = True

# Function to set the time zone
def set_time_zone(time_zone):
    try:
        subprocess.run(['sudo', 'timedatectl', 'set-timezone', time_zone], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to set time zone: {e}")
    finally:
        return None

# Function to get widget screens
def get_widgets_framebuffer():
    framebuffers = []
    for page in pages:
        if page["uuid"] != "0":
            img = Image.frombytes('RGB', (128, 160), bytes(page["framebuffer"]))
            # main screen
            main_img = img.crop((0, 0, 128, 128))
            main_img_buffer = io.BytesIO()
            main_img.save(main_img_buffer, format='JPEG')
            main_img_bytes = main_img_buffer.getvalue()
            main_img_base64_str = "data:image/png;base64," + base64.b64encode(main_img_bytes).decode('utf-8')
            # secondary screen
            second_img = img.crop((0, 128, 64, 160))
            second_img_buffer = io.BytesIO()
            second_img.save(second_img_buffer, format='JPEG')
            second_img_bytes = second_img_buffer.getvalue()
            second_img_base64_str = "data:image/png;base64," + base64.b64encode(second_img_bytes).decode('utf-8')
            framebuffers.append({
                "uuid": page["uuid"],
                "main_screen": main_img_base64_str,
                "sec_screen": second_img_base64_str
            })
    return framebuffers

# Function to start game from websocket
def start_game_from_websocket(gameid):
    global start_game, game_id
    start_game = True
    game_id = gameid
    return True

# Function to init the widgets
def init_widgets():
    global pages, page_index, page_freeze, locate_device_intv, reload_conf, game, game_index, game_list, state, page_tick, last_page_index, next_page_prepared_index
    # go back to menu if in game
    if state == "in_game":
        state = "menu"
    # terminate all existing widget and game processes
    term_widget_processes(pages)
    term_game_process(game)
    #check if apps folder and apps/conf.json exist
    if not os.path.isdir("./apps"):
        os.makedirs("./apps")
    if not os.path.isfile("./apps/conf.json"):
        with open("./apps/conf.json", "w") as config_file:
            json.dump({"user": "","date": "","pages": [{"uuid": "e7b8c2e2-4f3a-4b7e-9c1a-2d6e8f5a1b3c","title": "factory_tool","duration" : "60","combination" : "0","enabled" : True,"widgets" : [{"id": "factory_tool","position": [0,0,127,159],"fields": {}}]}]}, config_file)
    # Read configuration from conf.json
    with open("./apps/conf.json", "r") as config_file:
        pages = init_pages(json.load(config_file))
    #init variables   
    page_index = 0
    last_page_index = -1
    next_page_prepared_index = -1
    page_freeze = False
    locate_device_intv = 0
    reload_conf = False
    game_index = 0
    if game_list is None:
        game_list = []
    else:
        game_list.clear()
    page_tick = time.time()

# Function to check WiFi and Internet connection in a loop
def check_connection_loop():
    global wifi_connected, internet_connected
    while True:
        try:
            # Check WiFi connection
            # iwgetid returns 0 if connected to an AP, non-zero otherwise
            wifi_check = subprocess.run(['iwgetid'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            wifi_connected = (wifi_check.returncode == 0)

            # Check Internet connection (ping github.com)
            if wifi_connected:
                # -c 1: count 1, -W 2: timeout 2 seconds
                internet_check = subprocess.run(['ping', '-c', '1', '-W', '2', 'github.com'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                internet_connected = (internet_check.returncode == 0)
            else:
                internet_connected = False
        except Exception as e:
            print(f"Error checking connection: {e}")
            wifi_connected = False
            internet_connected = False
        time.sleep(10)

# Declare globals only if they have been defined previously
global_vars = [
    "pages", "page_index", "page_freeze", "locate_device_intv", "reload_conf",
    "game", "game_index", "game_list", "state", "page_tick", "setting_select_index",
    "start_game", "game_id", "wifi_connected", "internet_connected", "last_page_index",
    "next_page_prepared_index"
]
for var in global_vars:
    if var in globals():
        globals()[var]
    else:
        # Optionally, initialize to None or suitable default if not declared
        globals()[var] = None

# display the loading image (animated)
# display the loading image (animated)
dartsnut.update_frame_buffer(create_loading_image())

# read device.info
device_info = get_device_info()
# set the volume
set_volume(int(device_info.get('volume', "50")) )

# start ble server
ble_thread = threading.Thread(target=start_ble_server, args=(locate_device,), daemon=True)
ble_thread.start()

# start websocket server
websocket_thread = threading.Thread(target=start_websocket_server, args=(set_brightness,locate_device,reload_config,set_time_zone,get_widgets_framebuffer,start_game_from_websocket,set_volume), daemon=True)
websocket_thread.start()

# Start the connection check thread
connection_thread = threading.Thread(target=check_connection_loop, daemon=True)
connection_thread.start()

# init
state = "menu"
reload_conf = False
start_game = False
menu_select_index = 0
setting_select_index = 0
init_widgets()
    
# start the loop
while dartsnut.running:
    try:
        time.sleep(1/30)
        # Update loading animation frame
        get_current_loading_frame()

        # Every-loop: temporary restore expiry (during dim window, re-dimmer when period ends)
        if _dim_temporary_restore_until is not None and time.time() >= _dim_temporary_restore_until:
            _dim_temporary_restore_until = None
            if _currently_in_dim_window:
                di = get_device_info()
                _set_brightness_hardware(int(di.get("dim_level", 10)))

        # 60s dim-window check
        if (time.time() - _last_dim_check_time) >= 60 or _last_dim_check_time == 0:
            _last_dim_check_time = time.time()
            di = get_device_info()
            enabled = str(di.get("dim_window_enabled", "false")).lower() == "true"
            start_s = (di.get("dim_window_start") or "").strip()
            end_s = (di.get("dim_window_end") or "").strip()
            dim_lvl = int(di.get("dim_level", 10))

            # Disabled (feature off or no window) or parse fail: restore and clear
            if not enabled or not start_s or not end_s:
                if _currently_in_dim_window:
                    restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
                    _set_brightness_hardware(restore)
                    _currently_in_dim_window = False
                    _dim_temporary_restore_until = None
            else:
                start_hm = _parse_hhmm(start_s)
                end_hm = _parse_hhmm(end_s)
                if start_hm is None or end_hm is None:
                    if _currently_in_dim_window:
                        restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
                        _set_brightness_hardware(restore)
                        _currently_in_dim_window = False
                        _dim_temporary_restore_until = None
                else:
                    now = datetime.now().time()
                    start_t = dt_time(start_hm[0], start_hm[1])
                    end_t = dt_time(end_hm[0], end_hm[1])
                    in_window = (start_t <= end_t and start_t <= now <= end_t) or (
                        start_t > end_t and (now >= start_t or now < end_t)
                    )
                    if in_window:
                        if not _currently_in_dim_window:
                            _brightness_before_dim = int(di.get("brightness", 50))
                            _set_brightness_hardware(dim_lvl)
                            _currently_in_dim_window = True
                    else:
                        if _currently_in_dim_window:
                            restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
                            _set_brightness_hardware(restore)
                            _currently_in_dim_window = False
                            _dim_temporary_restore_until = None

        # locate device
        if locate_device_intv:
            dartsnut.update_frame_buffer(identify_image)
            locate_device_intv -= 1
        # reload configuration
        elif reload_conf:
            reload_conf = False
            # call init widgets
            init_widgets()
        # start game from websocket
        elif start_game:
            start_game = False
            term_game_process(game)
            game = start_game_process(game_id)
            if game is not None:
                term_widget_processes(pages)
                state = "in_game"
        # main menu
        elif (state == "menu"):
            # check the modal
            if device_info["model"] == "PixelBoard":
                # load widgets
                reload_conf = True
                state = "widget"
            elif device_info["model"] == "PixelDart":
                # load the main menu
                menu_image = Image.new("RGB", (128, 160), (0, 0, 0))
                # if game process is running, show the game screen and add a overlay
                if game is not None and "process" in game and game["process"].poll() is None:
                    game_buf = game["shm"].buf[1:]
                    game_image = Image.frombytes("RGB", (128, 128), bytes(game_buf))
                    menu_image.paste(game_image, (0,0))
                    overlay = Image.new('RGBA', (128, 128), (0, 0, 0, 128))
                    menu_image.paste(overlay, (0, 0), overlay)
                    draw = ImageDraw.Draw(menu_image)
                    text = "B: End the game"
                    text_bbox = draw.textbbox((0, 0), text, font=font24)
                    draw.text(((128 - text_bbox[2]) / 2, (128 - text_bbox[3]) / 2), text, fill="white", font=font24)
                else:
                    # paste the logo
                    menu_image.paste(logo_image, (0,0))
                # paste the icons
                menu_image.paste(game_icon, (4, 132), game_icon.convert("RGBA"))
                menu_image.paste(widget_icon, (24, 132), widget_icon.convert("RGBA"))
                menu_image.paste(settings_icon, (44, 132), settings_icon.convert("RGBA"))
                # draw the menu selection
                draw = ImageDraw.Draw(menu_image)
                # Draw a rounded rectangle for the menu selection (border radius 4)
                draw.rounded_rectangle(
                    (menu_select_index*20+2, 130, menu_select_index*20+21, 149),
                    radius=4,
                    outline="white",
                    width=1
                )
                # draw the text at the bottom
                if menu_select_index == 0:
                    text = "GAMES"
                elif menu_select_index == 1:
                    text = "WIDGETS"
                elif menu_select_index == 2:
                    text = "SETTINGS"
                # get the text width, 6 pixels per character for 6x8 font
                text_width = len(text) * 6
                # Center the text horizontally and position it at y=152 (font is 8px tall, so 152-160 fits in 160px display)
                text_x = int((64 - text_width) / 2)
                draw.text((text_x, 152), text, fill=(255, 255, 255), font=font8)
                # draw "Firmware Updated" label if flag exists
                if _check_firmware_updated_flag():
                    _draw_firmware_updated_text(draw, -1, 120)
                # render the menu to the screen
                dartsnut.update_frame_buffer(menu_image)
        # widget mode
        elif (state == "widget"):
            # check if the widgets process is running:
            if pages is None or len(pages) == 0:
                reload_conf = True
            else:
                if (len(pages) > 1) and (not page_freeze):
                    # Calculate the next page index
                    next_index = page_index
                    found_enabled = False
                    for _ in range(len(pages)):
                        next_index = (next_index + 1) % len(pages)
                        if pages[next_index].get("enabled", True) and pages[next_index]["uuid"] != "0":
                            found_enabled = True
                            break
                    if not found_enabled:
                        next_index = len(pages) - 1  # uuid "0" page is always last
                    # Check if we should prepare the next page (3s before duration)
                    if (time.time() - page_tick > int(pages[page_index]["duration"]) - 3) and (next_page_prepared_index != next_index):
                        for widget in pages[next_index]["widgets"]:
                            try:
                                process = widget.get("process")
                                if process is None:
                                    # Process was killed, will be restarted when page becomes active
                                    widget["launched"] = False
                                    continue
                                os.kill(process.pid, signal.SIGCONT)
                                # Reset launched flag when resuming - will be set to True when we see buf[0]==0 in next cycle
                                widget["launched"] = False
                            except Exception as e:
                                print(f"Error resuming next widget process: {e}")
                        next_page_prepared_index = next_index
                    # Check if we should switch to the next page
                    if (time.time() - page_tick > int(pages[page_index]["duration"])) or (not pages[page_index]["enabled"]):
                        page_index = next_index
                        page_tick = time.time()
                        next_page_prepared_index = -1
                # resume the current page's process and pause other page's process
                if page_index != last_page_index:
                    for i in range(len(pages)):
                        if i == page_index:
                            for widget_idx, widget_entry in enumerate(pages[i]["widgets"]):
                                try:
                                    process = widget_entry.get("process")
                                    if process is None:
                                        # Process was killed, restart it
                                        widget = widget_entry.get("widget")
                                        if widget and widget.get("id") != "0":
                                            print(f"Restarting widget {widget.get('id')} (process was killed)")
                                            restart_widget_process(widget_entry, pages[i], widget_idx)
                                        continue
                                    # Check if process is still alive
                                    if process.poll() is None:
                                        # Process is alive, resume it
                                        os.kill(process.pid, signal.SIGCONT)
                                        # Reset launched flag when resuming - will be set to True when we see buf[0]==0 in next cycle
                                        widget_entry["launched"] = False
                                    else:
                                        # Process is dead (likely killed after update), restart it
                                        widget = widget_entry.get("widget")
                                        if widget and widget.get("id") != "0":
                                            print(f"Restarting widget {widget.get('id')} (process was killed)")
                                            restart_widget_process(widget_entry, pages[i], widget_idx)
                                except Exception as e:
                                    print(f"Error resuming widget process: {e}")
                        else:
                            for widget_entry in pages[i]["widgets"]:
                                try:
                                    widget = widget_entry.get("widget")
                                    widget_id = widget.get("id") if widget else None
                                    
                                    # Check if widget was updated and needs restart
                                    if widget_id and widget_id in widgets_updated:
                                        _kill_widget_process(widget_entry, widget_id, "suspending page, update complete")
                                        # Remove from updated set since we've handled it
                                        widgets_updated.discard(widget_id)
                                    else:
                                        # Normal suspend behavior
                                        process = widget_entry.get("process")
                                        if process and process.poll() is None:
                                            os.kill(process.pid, signal.SIGSTOP)
                                            # Reset launched flag when pausing - will need to re-establish when resumed
                                            widget_entry["launched"] = False
                                except Exception as e:
                                    print(f"Error pausing widget process: {e}")
                    last_page_index = page_index
                    # Check for widget updates when page becomes active
                    check_page_widget_updates(pages[page_index])
                else:
                    # Page hasn't changed, but check if current page's widgets need restarting
                    for widget_idx, widget_entry in enumerate(pages[page_index]["widgets"]):
                        try:
                            process = widget_entry.get("process")
                            if process is None:
                                # Process was killed (e.g., after update), restart it
                                widget = widget_entry.get("widget")
                                if widget and widget.get("id") != "0":
                                    print(f"Restarting widget {widget.get('id')} (process was killed)")
                                    restart_widget_process(widget_entry, pages[page_index], widget_idx)
                            elif process.poll() is not None:
                                # Process is dead, restart it
                                widget = widget_entry.get("widget")
                                if widget and widget.get("id") != "0":
                                    print(f"Restarting widget {widget.get('id')} (process died)")
                                    restart_widget_process(widget_entry, pages[page_index], widget_idx)
                        except Exception as e:
                            print(f"Error checking widget process: {e}")
                # load the widgets' frame buffer
                widget_img = Image.frombytes("RGB", (128, 160), bytes(pages[page_index]["framebuffer"]))
                # overlay the lock icon if the widget is freezing
                if page_freeze:
                    widget_img.paste(lock_widget_icon, (117, 117), lock_widget_icon.convert("RGBA"))
                # check the connection status and overlay the icon
                if not wifi_connected:
                    # overlay the wifi disconnected icon at top right corner
                    if (time.time() % 2) < 1:  # blink every second
                        widget_img.paste(wifi_disconnect_icon, (117, 0), wifi_disconnect_icon.convert("RGBA"))
                elif not internet_connected:
                    # overlay the internet disconnected icon at top right corner
                    if (time.time() % 2) < 1:  # blink every second
                        widget_img.paste(internet_disconnect_icon, (117, 0), internet_disconnect_icon.convert("RGBA"))
                #render the current page to the screen
                dartsnut.update_frame_buffer(widget_img)
        # game selecting page
        elif (state == "game_select"):
            if len(game_list) > 0:
                # draw the game preview to the screen
                if time.time() - page_tick > 5:
                    game_preview_index += 1
                    if (game_preview_index >= len(game_list[game_index]["preview"])):
                        game_preview_index = 0
                    page_tick = time.time()
                # Create a new image for the game select screen
                select_img = Image.new("RGB", (128, 160), (0, 0, 0))
                # Paste the preview image (already 128x128) at the top
                preview_img = Image.frombytes("RGB", (128, 128), bytes(game_list[game_index]["preview"][game_preview_index]))
                select_img.paste(preview_img, (0, 0))
                # Paste the game select image at the bottom (assume it's 128x32 or will be resized)
                select_img.paste(game_select_image, (0, 128))
                dartsnut.update_frame_buffer(select_img)
            else:
                state = "menu"
        # in game
        elif (state == "in_game"):
            # check if the game object is not None
            if game is None or game == {}:
                # trigger reload
                reload_conf = True
            # if the game process is not polling, trigger reload
            elif game["process"].poll() is not None:
                # Stop tracking game playtime when game ends
                try:
                    stop_game_tracking()
                except Exception as e:
                    print(f"Warning: Failed to stop game tracking: {e}")
                # trigger reload
                reload_conf = True
            # render the game frame buffer
            elif game is not None:
                game_id = game.get("game_id", "unknown")
                poll_result = game["process"].poll()
                shm_buf0 = game["shm"].buf[0] if game.get("shm") else None
                
                # Check if process is still running
                if poll_result is not None:
                    # Process has ended, handled above
                    pass
                
                # For pico8, bypass launch status check but still check shared memory for new frames
                if game_id == "pico8":
                    # Track previous buf[0] value to detect when pico8 writes a new frame
                    prev_buf0 = game.get("pico8_prev_buf0", None)
                    if shm_buf0 == 0:
                        # New frame ready, render it
                        try:
                            game_image = Image.frombytes("RGB", (128, 160), bytes(game["shm"].buf[1:1+128*160*3]))
                            dartsnut.update_frame_buffer(game_image)
                            # Mark frame as read so pico8 can write the next frame
                            game["shm"].buf[0] = 1
                            # If buf[0] transitioned from 1 to 0, pico8 wrote a frame
                            if prev_buf0 == 1:
                                game["pico8_first_frame_seen"] = True
                            game["pico8_prev_buf0"] = 1
                        except Exception as e:
                            print(f"Error rendering pico8 frame: {e}")
                            dartsnut.update_frame_buffer(create_loading_image())
                    else:
                        # buf[0] == 1
                        game["pico8_prev_buf0"] = 1
                        if not game.get("pico8_first_frame_seen", False):
                            # We haven't seen pico8 write a frame yet, show loading animation
                            dartsnut.update_frame_buffer(create_loading_image())
                        # else: buf[0] == 1 and we've seen at least one frame from pico8, keep showing last frame (don't update frame buffer)
                # For other games, use buf[0] == 0 as signal that game is launched and ready
                else:
                    if shm_buf0 == 0:
                        # buf[0] == 0 means game is launched and has a new frame ready
                        game["launched"] = True
                        # Read the frame and mark it as read
                        game_image = Image.frombytes("RGB", (128, 160), bytes(game["shm"].buf[1:1+128*160*3]))
                        dartsnut.update_frame_buffer(game_image)
                        game["shm"].buf[0] = 1  # Mark frame as read
                    elif game.get("launched", False):
                        # Game is launched but waiting for new frame (buf[0] == 1), show loading animation
                        dartsnut.update_frame_buffer(create_loading_image())
                    else:
                        # Game hasn't launched yet (never seen buf[0] == 0), show loading animation
                        dartsnut.update_frame_buffer(create_loading_image())
        # in settings
        elif (state == "settings"):
            # Draw the settings menu
            settings_image = Image.new("RGB", (128, 160), (0, 0, 0))
            draw = ImageDraw.Draw(settings_image)
            # Define settings items
            settings_items = [
                {"name": "Brightness", "type": "value"},
                {"name": "Volume", "type": "value"},
                {"name": "IP", "type": "info"},
                {"name": "Version", "type": "info"}
            ]
            # Get brightness and volume values
            try:
                device_info = get_device_info()
                brightness = int(device_info.get('brightness', 50))
                volume = int(device_info.get('volume', 50))
            except Exception:
                brightness = 50
                volume = 50
            # Get the IP address of the connected WiFi
            try:
                ip_address = subprocess.run(['hostname', '-I'], capture_output=True, text=True, check=True).stdout.strip().split()[0]
            except Exception:
                ip_address = "0.0.0.0"
            # Read the git tag as the version
            try:
                version = subprocess.run(
                    ['git', 'describe', '--tags', '--abbrev=0'],
                    capture_output=True, text=True, check=True
                ).stdout.strip()
            except Exception:
                version = 'v1.0.0'
            # Draw settings items
            item_height = 32
            for idx, item in enumerate(settings_items):
                y = idx * item_height
                focused = (idx == setting_select_index)
                # Draw background for focused item
                if focused:
                    draw.rectangle((0, y, 127, y + item_height - 1), fill=(40, 40, 40))
                # Draw item name
                draw.text((2, y + 12), item["name"].upper(), fill="white", font=font8)
                # Draw value/info
                if item["name"] == "Brightness":
                    value_str = f"{brightness}"
                    # Fixed positions for arrows and value
                    value_x = 80
                    arrow_left_x = value_x - 13
                    arrow_right_x = value_x + 23
                    if focused:
                        draw.text((arrow_left_x, y + 12), "<", fill="white", font=font8)
                        draw.text((arrow_right_x, y + 12), ">", fill="white", font=font8)
                        draw.text((value_x, y + 12), value_str, fill="white", font=font8)
                    else:
                        draw.text((value_x, y + 12), value_str, fill="white", font=font8)
                elif item["name"] == "Volume":
                    value_str = f"{volume}"
                    value_x = 80
                    arrow_left_x = value_x - 13
                    arrow_right_x = value_x + 23
                    if focused:
                        draw.text((arrow_left_x, y + 12), "<", fill="white", font=font8)
                        draw.text((arrow_right_x, y + 12), ">", fill="white", font=font8)
                        draw.text((value_x, y + 12), value_str, fill="white", font=font8)
                    else:
                        draw.text((value_x, y + 12), value_str, fill="white", font=font8)
                elif item["name"] == "IP":
                    text_width = len(ip_address) * 6
                    draw.text((48 + (80 - text_width) / 2, y + 12), ip_address, fill="white", font=font8)
                elif item["name"] == "Version":
                    text_width = len(version) * 6
                    draw.text((48 + (80 - text_width) / 2, y + 12), version, fill="white", font=font8)
            # Draw the settings icon at the bottom
            settings_image.paste(settings_icon, (24, 128), settings_icon.convert("RGBA"))
            # Draw the settings label text
            settings_text = "SETTINGS"
            # Get the text width, 6 pixels per character for 6x8 font
            settings_text_width = len(settings_text) * 6
            # Center the text horizontally and position it at y=152 (font is 8px tall, so 152-160 fits in 160px display)
            settings_text_x = int((64 - settings_text_width) / 2)
            draw.text((settings_text_x, 152), settings_text, fill=(255, 255, 255), font=font8)
            # Render settings to screen
            dartsnut.update_frame_buffer(settings_image)
        
        # read the buttons
        buttons = get_buttons_pressed()
        # Button-triggered temporary restore: during dim window, any press restores brightness for dim_restore_seconds
        if _currently_in_dim_window and any(buttons.values()):
            di = get_device_info()
            secs = max(5, min(300, int(di.get("dim_restore_seconds", 30))))
            _dim_temporary_restore_until = time.time() + secs
            restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
            _set_brightness_hardware(restore)
        if (buttons["btn_a"]):
            # button A to enter menu item
            if state == "menu":
                # Remove firmware update flag if it exists (when leaving menu state)
                if _check_firmware_updated_flag():
                    _remove_firmware_updated_flag()
                if menu_select_index == 0:
                    # load the game list
                    game_list = load_game_list()
                    # if there is at least one game
                    if (len(game_list) > 0) :
                        state = "game_select"
                        game_index = 0
                        game_preview_index = 0
                        page_tick = time.time()
                elif menu_select_index == 1:
                    # go to widgets
                    state = "widget"
                elif menu_select_index == 2:
                    # go to settings menu
                    state = "settings"

            # button A to toggle widget freeze in widget mode
            elif state == "widget":
                page_freeze = not page_freeze
                page_tick = time.time()
            # button A to start game in game select
            elif state == "game_select":
                term_game_process(game)
                game = start_game_process(game_list[game_index]["id"])
                if game is not None:
                    term_widget_processes(pages)
                    state = "in_game"
                else:
                    # start game failed, go back to widget
                    reload_conf = True
        elif (buttons["btn_b"]):
            # if in game_select, go back to menu
            if (state == "game_select"):
                state = "menu"
            # if in settings, go back to menu
            elif (state == "settings"):
                state = "menu"
            # if in menu and game process is running, terminate it
            elif (state == "menu"):
                if game is not None:
                    term_game_process(game)
                    game = None
        elif (buttons["btn_left"]):
            # select previous menu item
            if state == "menu":
                menu_select_index -= 1
                if menu_select_index < 0:
                    menu_select_index = 2
            # button LEFT to go to previous page in widget mode
            elif state == "widget":
                # Find the previous enabled page
                prev_index = page_index
                found_enabled = False
                for _ in range(len(pages)):
                    prev_index = (prev_index - 1 + len(pages)) % len(pages)
                    if pages[prev_index].get("enabled", True) and pages[prev_index]["uuid"] != "0":
                        page_index = prev_index
                        found_enabled = True
                        break
                if not found_enabled:
                    page_index = len(pages) - 1  # uuid "0" page is always last
                page_tick = time.time()
            # button LEFT to select previous game in game select
            elif state == "game_select":
                game_index -= 1
                if game_index < 0:
                    game_index = len(game_list) - 1
                game_preview_index = 0
                page_tick = time.time()
            # button LEFT to decrease value in settings
            elif state == "settings":
                if setting_select_index == 0:
                    # decrease brightness
                    device_info = device_info = get_device_info()
                    brightness = max(int(device_info.get('brightness', "50")) - 10, 10)
                    set_brightness(brightness)
                elif setting_select_index == 1:
                    # decrease volume
                    device_info = get_device_info()
                    volume = max(int(device_info.get('volume', "50")) - 10, 0)
                    set_volume(volume)
        elif (buttons["btn_right"]):
            # select previous menu item
            if state == "menu":
                menu_select_index += 1
                if menu_select_index > 2:
                    menu_select_index = 0
            # button RIGHT to go to next page in widget mode
            if state == "widget":
                # Find the next enabled page
                next_index = page_index
                found_enabled = False
                for _ in range(len(pages)):
                    next_index = (next_index + 1) % len(pages)
                    if pages[next_index].get("enabled", True) and pages[next_index]["uuid"] != "0":
                        page_index = next_index
                        found_enabled = True
                        break
                if not found_enabled:
                    page_index = len(pages) - 1  # uuid "0" page is always last
                page_tick = time.time()
            # button RIGHT to select next game in game select
            elif state == "game_select":
                game_index += 1
                if game_index >= len(game_list):
                    game_index = 0
                game_preview_index = 0
                page_tick = time.time()
            # button RIGHT to increase value in settings
            elif state == "settings":
                if setting_select_index == 0:
                    # increase brightness
                    device_info = get_device_info()
                    brightness = min(int(device_info.get('brightness', "50")) + 10, 100)
                    set_brightness(brightness)
                elif setting_select_index == 1:
                    # increase volume
                    device_info = get_device_info()
                    volume = min(int(device_info.get('volume', "50")) + 10, 100)
                    set_volume(volume)
        elif (buttons["btn_up"]):
            # select item in settings menu
            if state == "settings":
                setting_select_index -= 1
                if setting_select_index < 0:
                    setting_select_index = 0
        elif (buttons["btn_down"]):
            # select item in settings menu
            if state == "settings":
                setting_select_index += 1
                if setting_select_index > 1:
                    setting_select_index = 1
        elif (buttons["btn_home"]):
            device_info = get_device_info()
            if device_info["model"] == "PixelBoard":
                # to toggle widget freeze in widget mode
                if state == "widget":
                    page_freeze = not page_freeze
                    page_tick = time.time()
                # if in game, exit the game and go back to widget
                if (state == "in_game"):
                    reload_conf = True
                    state = "widget"
            elif device_info["model"] == "PixelDart":
                # if already in menu, return to game or widget if process exists
                if (state == "menu"):
                    # if there is a game process, return to game
                    if game is not None and "process" in game and game["process"].poll() is None:
                        game["process"].send_signal(signal.SIGCONT)
                        state = "in_game"
                    elif pages is not None and len(pages) > 0:
                        state = "widget"
                # if in game, reload config and go to menu
                else:
                    # if there is a game process, pause the game
                    if game is not None and "process" in game and game["process"].poll() is None:
                        game["process"].send_signal(signal.SIGSTOP)
                    state = "menu"
        elif (buttons["btn_reserved"]):
            pass

        # render widgets
        if pages is not None and len(pages) > 0:
            for page in pages:
                # Convert framebuffer to Image for easier manipulation
                page_img = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
                
                # Get current animated loading frames
                current_loading_frame_big = get_current_loading_frame_big()
                current_loading_frame = get_current_loading_frame()
                # Ensure they're in RGB mode
                if current_loading_frame_big.mode != "RGB":
                    current_loading_frame_big = current_loading_frame_big.convert("RGB")
                if current_loading_frame.mode != "RGB":
                    current_loading_frame = current_loading_frame.convert("RGB")
                
                for widget in page["widgets"]:
                    widget_data = widget.get("widget")
                    if widget_data is None:
                        continue
                    
                    widget_id = widget_data.get("id", "unknown")
                    
                    shm = widget.get("shm")
                    shm_buf0 = shm.buf[0] if shm is not None else None
                    
                    x0, y0, x1, y1 = widget_data["position"]
                    widget_width = x1 - x0 + 1
                    widget_height = y1 - y0 + 1
                    
                    # Always try to read and render widget frame from shared memory
                    widget_frame = None
                    if shm is not None:
                        width = x1 - x0 + 1
                        height = y1 - y0 + 1
                        try:
                            # Read widget frame from shared memory (even if buf[0] == 1)
                            widget_frame = Image.frombytes("RGB", (width, height), bytes(shm.buf[1:1+width*height*3]))
                            
                            # Paste widget frame onto page_img at widget position
                            page_img.paste(widget_frame, (x0, y0))
                            
                            # Check if new frame is ready (buf[0] == 0)
                            if shm.buf[0] == 0:
                                # Mark frame as read
                                shm.buf[0] = 1
                        except Exception as e:
                            print(f"Error reading widget frame for {widget_id}: {e}")
                    
                    # Check if widget is ready by examining top and bottom pixel rows
                    widget_ready = False
                    if widget_frame is not None:
                        was_not_launched = not widget.get("launched", False)
                        widget_ready = check_widget_ready(widget_frame)
                        if widget_ready:
                            widget["launched"] = True
                            
                            # Detect small widget usage for 160-height widgets on first frame
                            if widget_height == 160 and was_not_launched:
                                # Extract the small widget area (bottom 32 pixels: from y0+128 to y0+159)
                                small_widget_area = widget_frame.crop((0, 128, width, 160))
                                # Check if this area has non-black content (not all zeros)
                                area_bytes = small_widget_area.tobytes()
                                has_content = any(byte != 0 for byte in area_bytes)
                                widget["has_small_widget"] = has_content
                    
                    # Show loading overlay if widget is not ready
                    if not widget_ready:
                        # Show animated loading sprites based on widget height
                        if widget_height == 160:
                            # Show both sprites matching game's loading sprite coordinates
                            page_img.paste(current_loading_frame_big, (x0, y0 + 32))
                            # Only show small loading sprite if we haven't determined yet that there's no small widget
                            # has_small_widget: None = not yet determined, True = has small widget, False = no small widget
                            has_small_widget = widget.get("has_small_widget", None)
                            if has_small_widget is not False:  # Show if None (not determined) or True (has small widget)
                                page_img.paste(current_loading_frame, (x0, y0 + 128))
                        elif widget_height == 128:
                            # Show only big sprite matching game's big sprite offset
                            page_img.paste(current_loading_frame_big, (x0, y0 + 32))
                        elif widget_height == 32:
                            # Show only regular sprite at widget's top-left
                            page_img.paste(current_loading_frame, (x0, y0))
                
                # Always update framebuffer from page_img to ensure all widget updates are reflected
                page["framebuffer"] = bytearray(page_img.tobytes())
    except Exception as e:
        print(f"Error in main loop: {e}")

