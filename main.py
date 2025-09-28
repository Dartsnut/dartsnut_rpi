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
from PIL import Image
import io
from python_ble.ble_server import start_ble_server
from python_websocket.websocket_server import start_websocket_server
from pydartsnut import Dartsnut

dartsnut = Dartsnut()

# Load the loading image
loading_image = Image.open("./loading.png")

# Function to set PR_SET_PDEATHSIG
def set_pdeathsig():
    import ctypes
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)

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
                    # if the uuid is "0", it is a default widget
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
        return {"widgets" : widgets, "duration" : page["duration"], "uuid" : page["uuid"], "loading": True, "framebuffer": bytearray(128 * 160 * 3), "enabled": page.get("enabled", True)}
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
            os.kill(g["process"].pid, signal.SIGTERM)
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
                    os.kill(widget["process"].pid, signal.SIGTERM)
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
    # Declare globals only if they have been defined previously
    global_vars = [
        "pages", "page_index", "page_freeze", "locate_device_intv", "reload_conf",
        "game", "game_index", "game_list", "state", "page_tick"
    ]
    for var in global_vars:
        if var in globals():
            globals()[var]
        else:
            # Optionally, initialize to None or suitable default if not declared
            globals()[var] = None
    global pages, page_index, page_freeze, locate_device_intv, reload_conf, game, game_index, game_list, state, page_tick
    # init the state machine
    state = "widget" # widget, game_select, in_game
    # Read configuration from conf.json
    if pages is None:
        pages = []
    else:
        term_widget_processes(pages)
    with open("./apps/conf.json", "r") as config_file:
        pages = init_pages(json.load(config_file))
    #init variables   
    page_index = 0
    page_freeze = False
    locate_device_intv = 0
    reload_conf = False
    term_game_process(game)
    game_index = 0
    if game_list is None:
        game_list = []
    else:
        game_list.clear()
    # reset the xvfb mode
    dartsnut.shm_buffer[0] = 1
    page_tick = time.time()

# display the loading image
dartsnut.update_frame_buffer(loading_image)

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

# init the widgets
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
        # normal mode
        elif (state == "widget"):
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
            for page in pages:
                if all(widget["shm"].buf[0] == 0 for widget in page["widgets"]):
                    page["loading"] = False
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
                elif page["loading"]:
                    # if the current page is still loading, show the loading image
                    page["framebuffer"] = loading_image.tobytes()    
            #render the current page to the screen
            dartsnut.update_frame_buffer(pages[page_index]["framebuffer"])
        # game selecting page
        elif (state == "game_select"):
            # draw the game preview to the screen
            if time.time() - page_tick > 5:
                game_preview_index += 1
                if (game_preview_index >= len(game_list[game_index]["preview"])):
                    game_preview_index = 0
                page_tick = time.time()
            dartsnut.update_frame_buffer(game_list[game_index]["preview"][game_preview_index])
        # in game
        elif (state == "in_game"):
            # check if the game object is not None
            if game is None:
                # trigger reload
                reload_conf = True
            # Check if the game process is still running
            elif game["process"].poll() is not None:
                # trigger reload
                reload_conf = True
            # render the game frame buffer
            elif game is not None:
                if game["shm"].buf[0] == 0:
                    dartsnut.update_frame_buffer(game["shm"].buf[1:])
                    game["shm"].buf[0] = 1

        # read the buttons
        buttons = get_buttons_pressed()
        if (buttons["btn_a"]):
            # button A to toggle widget freeze in widget mode
            if state == "widget":
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
            # if in game_select, go back to widget
            if (state == "game_select"):
                state = "widget"
        elif (buttons["btn_left"]):
            # button LEFT to go to previous page in widget mode
            if state == "widget":
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
        elif (buttons["btn_right"]):
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
        # elif (buttons["btn_up"]):
        #     # button UP to increase brightness if not in game
        #     if state != "in_game":
        #         try:
        #             with open("./device.json", 'r') as file:
        #                 device_info = json.load(file)
        #             brightness = min(int(device_info.get('brightness', "50")) + 10, 100)
        #             set_brightness(brightness)
        #         except Exception as e:
        #             print(f"Error reading or updating device brightness: {e}")
        # elif (buttons["btn_down"]):
        #     # button DOWN to decrease brightness if not in game
        #     if state != "in_game":
        #         try:
        #             with open("./device.json", 'r') as file:
        #                 device_info = json.load(file)
        #             brightness = max(int(device_info.get('brightness', "50")) - 10, 10)
        #             set_brightness(brightness)
        #         except Exception as e:
        #             print(f"Error reading or updating device brightness: {e}")
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
            else:
                # if in widget mode, show the game select
                if (state == "widget"):
                    # load the game list
                    game_list = load_game_list()
                    # if there is at least one game
                    if (len(game_list) > 0) :
                        state = "game_select"
                        game_index = 0
                        game_preview_index = 0
                        page_tick = time.time()
                # if in game select, go back to widget
                elif (state == "game_select"):
                    state = "widget"
                # if in game, trigger reload
                elif (state == "in_game"):
                    reload_conf = True
        elif (buttons["btn_reserved"]):
            pass
            
    except Exception as e:
        print(f"Error in main loop: {e}")

