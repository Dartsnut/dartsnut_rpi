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
from pydartsnut import Dartsnut

dartsnut = Dartsnut()

# Load the loading image
loading_image = Image.open("./loading.png")
# Load the logo image
logo_image = Image.open("./logo.png").resize((128,128))
# Load the icons
game_icon = Image.open("./game_icon.png")
settings_icon = Image.open("./settings_icon.png")
widget_icon = Image.open("./widget_icon.png")
lock_widget_icon = Image.open("./lock_widget_icon.png")
# Load the game select image
game_select_image = Image.open("./game_sel.png")
# Load the font
font12 = ImageFont.truetype("./Micro5.ttf", size=12)
font16 = ImageFont.truetype("./Micro5.ttf", size=16)
font24 = ImageFont.truetype("./Micro5.ttf", size=24)

# Function to set PR_SET_PDEATHSIG
def set_pdeathsig():
    import ctypes
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)

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
                    file_data = base64.b64decode(file)
                    # write the file data into a named temp file
                    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                        tmp_file.write(file_data)
                        tmp_file_path = tmp_file.name
                        # replace the file field with the file paths
                        params[field["id"]]["image"] = tmp_file_path
    return params

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
        os.system(f"wget -O '{download_path}' '{url}'")

        # Check if file exists after download
        if not os.path.isfile(download_path):
            return False

        # Check MD5 using system console
        md5_check_cmd = f"md5sum '{download_path}' | awk '{{print $1}}'"
        downloaded_md5_console = os.popen(md5_check_cmd).read().strip()
        if downloaded_md5_console != md5:
            os.remove(download_path)
            return False

        # Extract tar.gz using system console
        apps_path = os.path.join(os.getcwd(), "apps")
        extract_cmd = f"tar -xzf '{download_path}' -C '{apps_path}'"
        extract_result = os.system(extract_cmd)
        if extract_result != 0:
            return False

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
                process = subprocess.Popen(
                    command,
                    cwd=os.getcwd(),
                    preexec_fn=set_pdeathsig
                )
                widgets.append({"process":process, "shm": shm, "widget": widget})
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
                    process = subprocess.Popen(
                        command,
                        cwd=os.path.join("./apps/", widget["id"]),
                        preexec_fn=set_pdeathsig
                    )
                    widgets.append({"process":process, "shm": shm, "widget": widget})
                except Exception as e:
                    print(f"Error starting widget {widget['id']}: {e}")
                    # If the widget fails to start, we don't add it to the page
                    continue
    # if at lease one widget is valid, add the page
    if len(widgets) > 0:
        return {"widgets" : widgets, "duration" : page["duration"], "uuid" : page["uuid"], "framebuffer": bytearray(loading_image.tobytes()), "enabled": page.get("enabled", True)}
    else:
        return None

# Function to start the game process, return with the game process and shared memory object
def start_game_process(game_id):
    game_path = os.path.join(os.getcwd(), "apps", game_id)

    if not os.path.isdir(game_path):
        try:
            response = requests.get(f"https://api.dartsnut.com/v1/mobile/game/get-download-info?id={game_id}")
            if response.status_code == 200:
                download_info = response.json().get("data")
                if download_info is not None:
                    game_download_url = download_info.get("game_download_url")
                    game_download_md5 = download_info.get("game_download_md5")
                    download_app(game_download_url, game_download_md5)
            else:
                print(f"Failed to get download info for game {game_id}: {response.status_code}")
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
            # Initialize shared memory with the loading image
            img_bytes = loading_image.tobytes()
            shm.buf[1:1+len(img_bytes)] = img_bytes
            shm.buf[0] = 0
            # if the uuid is "0", it is a default widget
            command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "apps/", game_id, "main.py")]
            command.extend(["--shm", shm_name])
            process = subprocess.Popen(
                command,
                cwd=os.path.join("./apps/", game_id),
                preexec_fn=set_pdeathsig
            )
            return {"process":process, "shm": shm}
        except Exception as e:
            print(f"Error starting game {game['id']}: {e}")
    return None

# Function to terminate the game process and clean up
def term_game_process(g):
    if g is not None:
        try:
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
    for i, key in enumerate(button_states):
        if (button_states[key] != get_buttons_pressed.old_buttons[key]):
            get_buttons_pressed.old_buttons[key] = button_states[key]
            if (button_states[key]):
                button_pressed[key] = True
    return button_pressed

# Function to set brightness
def set_brightness(brightness):
    # Set the brightness on the device
    dartsnut.set_brightness(brightness)
    try:
        # Read the existing device info
        with open("./device.json", 'r') as file:
            device_info = json.load(file)
        # Update the device brightness
        device_info['brightness'] = str(brightness)
        # Write the updated info back to the file
        with open("./device.json", 'w') as file:
            json.dump(device_info, file)
    except FileNotFoundError:
        print(f"Device info file not found")
    except json.JSONDecodeError:
        print(f"Error decoding JSON from device info file")
    except Exception as e:
        print(f"An error occurred while updating device info: {e}")

# Function to set volume
def set_volume(volume):
    # Map the volume from (0,100) to (50,100)
    mapped_volume = int(50 + (volume / 100) * 50)
    # Set the volume on the device
    try:
        subprocess.run(
            ['amixer', '-c', '0', 'sset', 'PCM', f'{mapped_volume}%'],
            check=True,
            capture_output=True
        )
        try:
            # Read the existing device info
            with open("./device.json", 'r') as file:
                device_info = json.load(file)
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
def start_game(game_id):
    global game
    term_game_process(game)
    game = start_game_process(game_id)
    if game is not None:
        global pages, state
        term_widget_processes(pages)
        state = "in_game"
        return True
    else:
        # start game failed, go back to widget
        global reload_conf
        reload_conf = True
        return False

# Function to init the widgets
def init_widgets():
    global pages, page_index, page_freeze, locate_device_intv, reload_conf, game, game_index, game_list, state, page_tick
    # go back to menu if in game
    if state == "in_game":
        state = "menu"
    # terminate all existing widget and game processes
    term_widget_processes(pages)
    term_game_process(game)
    # Read configuration from conf.json
    with open("./apps/conf.json", "r") as config_file:
        pages = init_pages(json.load(config_file))
    #init variables   
    page_index = 0
    page_freeze = False
    locate_device_intv = 0
    reload_conf = False
    game_index = 0
    if game_list is None:
        game_list = []
    else:
        game_list.clear()
    page_tick = time.time()

# display the loading image
dartsnut.update_frame_buffer(loading_image)

# read device.info
with open("./device.json", 'r') as file:
    device_info = json.load(file)
# set the volume
set_volume(int(device_info.get('volume', "50")) )

#check if apps folder and apps/conf.json exist
if not os.path.isdir("./apps"):
    os.makedirs("./apps")
if not os.path.isfile("./apps/conf.json"):
    with open("./apps/conf.json", "w") as config_file:
        json.dump({
            "user": "",
            "date": "",
            "pages": [
                {
                    "uuid": "e7b8c2e2-4f3a-4b7e-9c1a-2d6e8f5a1b3c",
                    "title": "factory_tool",
                    "duration" : "60",
                    "combination" : "0",
                    "enabled" : True,
                    "widgets" : [{
                        "id": "factory_tool",
                        "position": [0,0,127,159],
                        "fields": {}
                    }]
                }
            ]
        }, config_file)

# start ble server
ble_thread = threading.Thread(target=start_ble_server, daemon=True)
ble_thread.start()

# start websocket server
websocket_thread = threading.Thread(target=start_websocket_server, args=(set_brightness,locate_device,reload_config,set_time_zone,get_widgets_framebuffer,start_game), daemon=True)
websocket_thread.start()

# Declare globals only if they have been defined previously
global_vars = [
    "pages", "page_index", "page_freeze", "locate_device_intv", "reload_conf",
    "game", "game_index", "game_list", "state", "page_tick", "setting_select_index"
]
for var in global_vars:
    if var in globals():
        globals()[var]
    else:
        # Optionally, initialize to None or suitable default if not declared
        globals()[var] = None

# init
state = "menu"
menu_select_index = 0
setting_select_index = 0
init_widgets()
    
# start the loop
while dartsnut.running:
    try:
        time.sleep(1/30)
        # locate device
        if locate_device_intv:
            buffer = bytearray([255] * (128 * 160 * 3))
            dartsnut.update_frame_buffer(buffer)
            locate_device_intv -= 1
        # reload configuration
        elif reload_conf:
            reload_conf = False
            # call init widgets
            init_widgets()
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
                text_bbox = draw.textbbox((0, 0), text, font=font12)
                draw.text(((64 - text_bbox[2]) / 2, 150), text, fill="white", font=font12)
                # render the menu to the screen
                dartsnut.update_frame_buffer(menu_image)
        # widget mode
        elif (state == "widget"):
            # check if the widgets process is running:
            if pages is None or len(pages) == 0:
                reload_conf = True
            else:
                if (len(pages) > 1) & (not page_freeze):
                    if (time.time() - page_tick > int(pages[page_index]["duration"])) or (not pages[page_index]["enabled"]):
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
                # load the widgets' frame buffer
                widget_img = Image.frombytes("RGB", (128, 160), bytes(pages[page_index]["framebuffer"]))
                # overlay the lock icon if the widget is freezing
                if page_freeze:
                    widget_img.paste(lock_widget_icon, (117, 117), lock_widget_icon.convert("RGBA"))
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
                # trigger reload
                reload_conf = True
            # render the game frame buffer
            elif game is not None:
                if game["shm"].buf[0] == 0:
                    dartsnut.update_frame_buffer(game["shm"].buf[1:])
                    game["shm"].buf[0] = 1
        # in settings
        elif (state == "settings"):
            # Draw the settings menu
            settings_image = Image.new("RGB", (128, 160), (0, 0, 0))
            draw = ImageDraw.Draw(settings_image)
            # Define settings items
            settings_items = [
                {"name": "Brightness", "type": "value"},
                {"name": "Volume", "type": "value"},
                {"name": "Network", "type": "info"},
                {"name": "Version", "type": "info"}
            ]
            # Get brightness and volume values
            try:
                with open("./device.json", 'r') as file:
                    device_info = json.load(file)
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
                draw.text((8, y + 8), item["name"], fill="white", font=font16)
                # Draw value/info
                if item["name"] == "Brightness":
                    value_str = f"{brightness}"
                    # Fixed positions for arrows and value
                    value_x = 80
                    arrow_left_x = value_x - 13
                    arrow_right_x = value_x + 23
                    if focused:
                        draw.text((arrow_left_x, y + 8), "<", fill="white", font=font16)
                        draw.text((arrow_right_x, y + 8), ">", fill="white", font=font16)
                        draw.text((value_x, y + 8), value_str, fill="white", font=font16)
                    else:
                        draw.text((value_x, y + 8), value_str, fill="white", font=font16)
                elif item["name"] == "Volume":
                    value_str = f"{volume}"
                    value_x = 80
                    arrow_left_x = value_x - 13
                    arrow_right_x = value_x + 23
                    if focused:
                        draw.text((arrow_left_x, y + 8), "<", fill="white", font=font16)
                        draw.text((arrow_right_x, y + 8), ">", fill="white", font=font16)
                        draw.text((value_x, y + 8), value_str, fill="white", font=font16)
                    else:
                        draw.text((value_x, y + 8), value_str, fill="white", font=font16)
                elif item["name"] == "Network":
                    draw.text((64, y + 8), ip_address, fill="white")
                elif item["name"] == "Version":
                    draw.text((64, y + 8), version, fill="white", font=font16)
            # Draw the settings icon at the bottom
            settings_image.paste(settings_icon, (24, 128), settings_icon.convert("RGBA"))
            text_bbox = draw.textbbox((0, 0), "Settings", font=font12)
            draw.text(((64 - text_bbox[2]) / 2, 144), "Settings", fill="white", font=font12)
            # Render settings to screen
            dartsnut.update_frame_buffer(settings_image)
        
        # read the buttons
        buttons = get_buttons_pressed()
        if (buttons["btn_a"]):
            # button A to enter menu item
            if state == "menu":
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
                page_freeze = ~page_freeze
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
                    try:
                        with open("./device.json", 'r') as file:
                            device_info = json.load(file)
                        brightness = max(int(device_info.get('brightness', "50")) - 10, 10)
                        set_brightness(brightness)
                        device_info['brightness'] = str(brightness)
                        # Update the device brightness in device.json
                        with open("./device.json", 'w') as file:
                            json.dump(device_info, file)
                    except Exception as e:
                        print(f"Error reading or updating device brightness: {e}")
                elif setting_select_index == 1:
                    # decrease volume
                    try:
                        with open("./device.json", 'r') as file:
                            device_info = json.load(file)
                        volume = max(int(device_info.get('volume', "50")) - 10, 0)
                        set_volume(volume)
                        # Update the device volume in device.json
                        device_info['volume'] = str(volume)
                        with open("./device.json", 'w') as file:
                            json.dump(device_info, file)
                    except Exception as e:
                        print(f"Error reading or updating device volume: {e}")
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
                    try:
                        with open("./device.json", 'r') as file:
                            device_info = json.load(file)
                        brightness = min(int(device_info.get('brightness', "50")) + 10, 100)
                        set_brightness(brightness)
                        device_info['brightness'] = str(brightness)
                        # Update the device brightness in device.json
                        with open("./device.json", 'w') as file:
                            json.dump(device_info, file)
                    except Exception as e:
                        print(f"Error reading or updating device brightness: {e}")
                elif setting_select_index == 1:
                    # increase volume
                    try:
                        with open("./device.json", 'r') as file:
                            device_info = json.load(file)
                        volume = min(int(device_info.get('volume', "50")) + 10, 100)
                        set_volume(volume)
                        # Update the device volume in device.json
                        device_info['volume'] = str(volume)
                        with open("./device.json", 'w') as file:
                            json.dump(device_info, file)
                    except Exception as e:
                        print(f"Error reading or updating device volume: {e}")
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
            with open("./device.json", 'r') as file:
                device_info = json.load(file)
            if device_info["model"] == "PixelBoard":
                # to toggle widget freeze in widget mode
                if state == "widget":
                    page_freeze = ~page_freeze
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
                        state = "in_game"
                    elif pages is not None and len(pages) > 0:
                        state = "widget"
                # if in game, reload config and go to menu
                else:
                    state = "menu"
        elif (buttons["btn_reserved"]):
            pass
            
        # render widgets
        if pages is not None and len(pages) > 0:
            for page in pages:
                for widget in page["widgets"]:
                    if widget["shm"].buf[0] == 0:
                        for widget in page["widgets"]:
                            shm_buf = widget["shm"].buf
                            x0, y0, x1, y1 = widget["widget"]["position"]
                            width = x1 - x0 + 1
                            height = y1 - y0 + 1
                            for y in range(height):
                                for x in range(width):
                                    src_idx = (y * width + x) * 3 + 1
                                    dst_idx = ((y0 + y) * 128 + (x0 + x)) * 3
                                    page["framebuffer"][dst_idx:dst_idx+3] = shm_buf[src_idx:src_idx+3]
                            shm_buf[0] = 1
    except Exception as e:
        print(f"Error in main loop: {e}")

