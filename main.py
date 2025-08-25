from multiprocessing import shared_memory
import subprocess
import os
import signal
import time
import json
import threading
import base64
import tempfile
from PIL import Image
import io
from python_ble.ble_server import start_ble_server
from python_websocket.websocket_server import start_websocket_server, screen_buffer, preview_buffer
from dartsnut import set_brightness, update_frame_buffer, get_buttons

# Function to set PR_SET_PDEATHSIG
def set_pdeathsig():
    import ctypes
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)

# function to process the widget's fields
def process_widget_fields(widget_id, widget_fields_parameter):
    params = widget_fields_parameter.copy()
    # read the app's conf.json file
    conf_path = os.path.join(os.getcwd(), "apps", widget_id, "conf.json")
    with open(conf_path, "r") as f:
        conf = json.load(f)
        for field in conf["fields"]:
            # if there is files type in the field, decode the base64 data
            if field["type"] == "files":
                # get the widget_fields_parameter with the field["id"]
                if params.get(field["id"]) is not None:
                    files = []
                    # read the file data
                    for file in params.get(field["id"]):
                        file_data_array = file.split(",")
                        file_data_b64 = file_data_array[1]
                        file_data = base64.b64decode(file_data_b64)
                        # write the file data into a named temp file
                        with tempfile.NamedTemporaryFile(delete=False, suffix="."+file_data_array[0]) as tmp_file:
                            tmp_file.write(file_data)
                            tmp_file_path = tmp_file.name
                            files.append(tmp_file_path)
                    # replace the file field with the file paths
                    params[field["id"]] = files
    return params

def start_page_process(page):
    widgets = []
    for widget_index in range(len(page["widgets"])):
        widget = page["widgets"][widget_index]
        # check if the widget exists
        widget_path = os.path.join(os.getcwd(), "apps", widget["id"])
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
                if (page_uuid == "0"):
                    command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "default.py")]
                else:
                    command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "apps/", widget["id"], "main.py")]
                command.extend(["--params", json.dumps(process_widget_fields(widget["id"], widget["fields"]))])
                command.extend(["--shm", shm_name])
                process = subprocess.Popen(
                    command,
                    cwd=os.path.join("./apps/", widget["id"]),
                    preexec_fn=set_pdeathsig
                )
                # Pause the process right after starting
                os.kill(process.pid, signal.SIGSTOP)  
                # add the widget to the widgets list
                widgets.append({"process":process, "shm": shm, "widget": widget})
            except Exception as e:
                print(f"Error starting widget {widget['id']}: {e}")
                # If the widget fails to start, we don't add it to the page
                continue
    # if at lease one widget is valid, add the page
    if len(widgets) > 0:
        return {"widgets" : widgets, "duration" : page["duration"], "uuid" : page["uuid"]}
    else:
        return None

def start_game_process(game):
    game_path = os.path.join(os.getcwd(), "apps", game["id"])
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
            # if the uuid is "0", it is a default widget
            command = [os.path.join(os.getcwd(), "venv0/bin/python"), os.path.join(os.getcwd(), "apps/", game["id"], "main.py")]
            command.extend(["--shm", shm_name])
            process = subprocess.Popen(
                command,
                cwd=os.path.join("./apps/", game["id"]),
                preexec_fn=set_pdeathsig
            )
            return {"process":process, "shm": shm}
        except Exception as e:
            print(f"Error starting game {game['id']}: {e}")
            # If the widget fails to start, we don't add it to the page
    return None

def term_game_process(game):
    if game is not None:
        try:
            os.kill(game["process"].pid, signal.SIGCONT)
            os.kill(game["process"].pid, signal.SIGTERM)
            game["shm"].close()
            game["shm"].unlink()
            game.clear()
        except Exception as e:
            print(f"Error terminating game: {e}")
    return None

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
                                    images.append(bytearray(img.tobytes()))
                                conf["preview"] = images
                            game_list.append(conf)
                except Exception as e:
                    print(f"Error loading game config for {name}: {e}")
    return game_list

def init_pages(config):
    pages = []
    # Start the processes based on the configuration
    for page in config["pages"]:
        if (page["enabled"]):
            page_process = start_page_process(page)
            if page_process is not None:
                pages.append(page_process)
    # if no page is valid, add a default page
    if len(pages) == 0:
        page_process = start_page_process({
            "uuid": "0",
            "title": "default widget",
            "duration" : "0",
            "combination" : "0",
            "enabled" : True,
            "widgets" : [{
                "id": "0",
                "position": [0,0,127,127],
                "fields": {}
            }]
        })
        if page_process is not None:
            pages.append(page_process)
    return pages

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

def process_message(message):
    if message["action"] == "locate_device":
        process_message.locate_device_intv = 180
    elif message["action"] == "set_brightness":
        brightness = message.get("brightness", -1)
        if 10 <= brightness <= 100:
            process_message.set_brightness = brightness
        else:
            process_message.set_brightness = -1
    elif message["action"] == "reload_conf":
        process_message.reload_conf = True
    elif message["action"] == "preview_widget":
        config = message.get("config")
        if config:
            process_message.preview = True
            process_message.preview_conf = config
            process_message.preview_uuid = message.get("uuid", "0")
        else:
            process_message.preview = False
            process_message.preview_conf = None
    elif message["action"] == "websocket_disconnected":
        # If the websocket is disconnected, reset the preview
            process_message.preview = False
            process_message.preview_conf = None
process_message.locate_device_intv = 0
process_message.reload_conf = False
process_message.preview = False
process_message.preview_conf = None
process_message.preview_uuid = "0"
process_message.set_brightness = -1  # Default value, -1 means not set

def get_buttons_pressed():
    # Get the buttons from the shared memory
    buttons = get_buttons()
    # Decode the buttons into individual bits
    button_states = {
        "btn_a" : bool(buttons & 0b00000001),
        "btn_b" : bool(buttons & 0b00000010),
        "btn_left" : bool(buttons & 0b00000100),
        "btn_up" : bool(buttons & 0b00001000),
        "btn_right" : bool(buttons & 0b00010000),
        "btn_down" : bool(buttons & 0b00100000),
        "btn_home" : bool(buttons & 0b01000000),
        "btn_reserved" : bool(buttons & 0b10000000)
    }
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

print("start...")
try:
    #start ble server
    ble_thread = threading.Thread(target=start_ble_server, args=(process_message,), daemon=True)
    ble_thread.start()
    #start websocket server
    websocket_thread = threading.Thread(target=start_websocket_server, args=(process_message,), daemon=True)
    websocket_thread.start()
    #init
    state = "widget" # widget, game_select, in_game
    # Read configuration from conf.json
    pages = []
    with open("./apps/conf.json", "r") as config_file:
        pages = init_pages(json.load(config_file))
    page_tick = time.time()
    page_index = 0
    page_freeze = False
    preview_pages = None
    preview_conf = None
    preview_uuid = "0"
    game = None
    game_index = 0
    game_list = []
    #start the first page
    for widget in pages[0]["widgets"]:
        os.kill(widget["process"].pid, signal.SIGCONT)
    #start the loop
    while True:
        time.sleep(1/60)
        # set brightness
        if (process_message.set_brightness != -1):
            set_brightness(process_message.set_brightness)
            try:
                # Read the existing device info
                with open("./device.json", 'r') as file:
                    device_info = json.load(file)
                # Update the device brightness
                device_info['brightness'] = process_message.set_brightness
                # Write the updated info back to the file
                with open("./device.json", 'w') as file:
                    json.dump(device_info, file)
            except FileNotFoundError:
                print(f"Device info file not found")
            except json.JSONDecodeError:
                print(f"Error decoding JSON from device info file")
            except Exception as e:
                 print(f"An error occurred while updating device info: {e}")
            process_message.set_brightness = -1
        # locate device
        if (process_message.locate_device_intv > 0):
            buffer = bytearray([255] * (128 * 160 * 3))
            update_frame_buffer(buffer)
            process_message.locate_device_intv -= 1
        # reload configuration
        elif (process_message.reload_conf):
            process_message.reload_conf = False
            # load a loading screen, todo
            buffer = bytearray(128 * 160 * 3)
            update_frame_buffer(buffer)
            # init state
            state = "widget"
            # quit preview
            term_widget_processes(preview_pages)
            process_message.preview = False
            process_message.preview_conf = None
            process_message.preview_uuid = "0"
            preview_pages = None
            preview_conf = None
            preview_uuid = "0"
            # quit game
            term_game_process(game)
            game = None
            game_index = 0
            game_list.clear()
            # init pages
            term_widget_processes(pages)
            with open("./apps/conf.json", "r") as config_file:
                pages = init_pages(json.load(config_file))
            page_tick = time.time()
            page_index = 0
            #start the first page
            for widget in pages[0]["widgets"]:
                os.kill(widget["process"].pid, signal.SIGCONT)
        # preview
        elif (process_message.preview):
            preview_uuid = process_message.preview_uuid
            if (preview_conf != process_message.preview_conf):
                # load a loading screen, todo
                buffer = bytearray(128 * 160 * 3)
                update_frame_buffer(buffer)
                # first time preview, start the preview pages
                if preview_conf is None:
                    preview_buffer.clear()
                    # quit widgets
                    term_widget_processes(pages)
                    # quit game
                    term_game_process(game)
                    game = None
                    # init preview pages
                    preview_pages = init_pages(process_message.preview_conf)
                    # start all pages in preview
                    for page in preview_pages:
                        preview_buffer.append({"uuid": page["uuid"], "buffer": bytearray(128 * 160 * 3)})
                        for widget in page["widgets"]:
                            os.kill(widget["process"].pid, signal.SIGCONT)
                # if the preview configuration has changed
                else:
                    old_pages = {page["uuid"]: page for page in preview_conf.get("pages", [])}
                    new_pages = {page["uuid"]: page for page in process_message.preview_conf.get("pages", [])}
                    # compare the changes
                    added = [new_pages[uuid] for uuid in new_pages if uuid not in old_pages]
                    removed = [old_pages[uuid] for uuid in old_pages if uuid not in new_pages]
                    modified = [new_pages[uuid] for uuid in new_pages if uuid in old_pages and new_pages[uuid] != old_pages[uuid]]
                    # for added pages, start the precesses
                    for page in added:
                        page_process = start_page_process(page)
                        if page_process is not None:
                            # continue if the page is None
                            for widget in page_process["widgets"]:
                                os.kill(widget["process"].pid, signal.SIGCONT)
                            preview_pages.append(page_process)
                            preview_buffer.append({"uuid": page["uuid"], "buffer": bytearray(128 * 160 * 3)})
                    # for removed pages, terminate the processes and remove from preview_pages
                    for removed_page in removed:
                        # Find the page in preview_pages by uuid and remove it
                        for i, p in enumerate(preview_pages):
                            if p["uuid"] == removed_page["uuid"]:
                                term_widget_processes([p])
                                preview_pages.pop(i)
                        # also remove from preview_buffer
                        for i, p in enumerate(preview_buffer):
                            if p["uuid"] == removed_page["uuid"]:
                                preview_buffer.pop(i)
                    # for modified pages, update the processes
                    for page in modified:
                        page_exists = False
                        for i, p in enumerate(preview_pages):
                            if p["uuid"] == page["uuid"]:
                                page_exists = True
                                term_widget_processes([p])
                                modified_page_process = start_page_process(page)
                                if modified_page_process is not None:
                                    for widget in modified_page_process["widgets"]:
                                        os.kill(widget["process"].pid, signal.SIGCONT)
                                    preview_pages[i] = modified_page_process
                                    # also clear the preview_buffer
                                    for pi, pp in enumerate(preview_buffer):
                                        if pp["uuid"] == page["uuid"]:
                                            preview_buffer[i]["buffer"] = bytearray(128 * 160 * 3)
                                else:
                                    preview_pages.pop(i)
                                    # also remove from preview_buffer
                                    for pi, pp in enumerate(preview_buffer):
                                        if pp["uuid"] == page["uuid"]:
                                            preview_buffer.pop(pi)
                        if not page_exists:
                            modified_page_process = start_page_process(page)
                            if modified_page_process is not None:
                                for widget in modified_page_process["widgets"]:
                                    os.kill(widget["process"].pid, signal.SIGCONT)
                                preview_pages.append(modified_page_process)
                                preview_buffer.append({"uuid": page["uuid"], "buffer": bytearray(128 * 160 * 3)})
                preview_conf = process_message.preview_conf
            # render the preview pages
            for page in preview_pages:
                if all(widget["shm"].buf[0] == 0 for widget in page["widgets"]):
                    buffer = bytearray(128 * 160 * 3)
                    for widget in page["widgets"]:
                        shm_buf = widget["shm"].buf
                        x0, y0, x1, y1 = widget["widget"]["position"]
                        width = x1 - x0 + 1
                        height = y1 - y0 + 1
                        for y in range(height):
                            for x in range(width):
                                src_idx = (y * width + x) * 3 + 1
                                dst_idx = ((y0 + y) * 128 + (x0 + x)) * 3
                                buffer[dst_idx:dst_idx+3] = shm_buf[src_idx:src_idx+3]
                        shm_buf[0] = 1
                    # update the preview_buffer with the same uuids
                    for i, p in enumerate(preview_buffer):
                        if p["uuid"] == page["uuid"]:
                            preview_buffer[i]["buffer"][:] = buffer
                    # update the screen_buffer with the same uuids
                    if page["uuid"] == preview_uuid:
                        screen_buffer[:] = buffer
                        update_frame_buffer(buffer)       
        # normal mode
        elif (state == "widget"):
            #if in preview, terminate it
            if (preview_pages is not None):
                # trigger reload
                process_message.reload_conf = True
            #if not in preview, continue the normal page loop
            else:
                if (len(pages) > 1) & (not page_freeze):
                    if (int(pages[page_index]["duration"]) == 0):
                        # if the duration is 0, stop the loop
                        page_tick = time.time()
                    elif (time.time() - page_tick > int(pages[page_index]["duration"])):
                        for widget in pages[page_index]["widgets"]:
                            os.kill(widget["process"].pid, signal.SIGSTOP)
                        page_index += 1
                        if (page_index >= len(pages)):
                            page_index = 0
                        for widget in pages[page_index]["widgets"]:
                            os.kill(widget["process"].pid, signal.SIGCONT)
                        page_tick = time.time()   
                if all(widget["shm"].buf[0] == 0 for widget in pages[page_index]["widgets"]):
                    buffer = bytearray(128 * 160 * 3)
                    for widget in pages[page_index]["widgets"]:
                        shm_buf = widget["shm"].buf
                        x0, y0, x1, y1 = widget["widget"]["position"]
                        width = x1 - x0 + 1
                        height = y1 - y0 + 1
                        for y in range(height):
                            for x in range(width):
                                src_idx = (y * width + x) * 3 + 1
                                dst_idx = ((y0 + y) * 128 + (x0 + x)) * 3
                                buffer[dst_idx:dst_idx+3] = shm_buf[src_idx:src_idx+3]
                        shm_buf[0] = 1
                    screen_buffer[:] = buffer
                    update_frame_buffer(buffer)
        # game selecting page
        elif (state == "game_select"):
            # draw the game preview to the screen
            if time.time() - page_tick > 5:
                game_preview_index += 1
                if (game_preview_index >= len(game_list[game_index]["preview"])):
                    game_preview_index = 0
                page_tick = time.time()
            screen_buffer[:] = game_list[game_index]["preview"][game_preview_index]
            update_frame_buffer(game_list[game_index]["preview"][game_preview_index])
        # in game
        elif (state == "in_game"):
            # check if the game object is not None
            if game is None:
                # trigger reload
                process_message.reload_conf = True
            # Check if the game process is still running
            elif game["process"].poll() is not None:
                # trigger reload
                process_message.reload_conf = True
            # render the game frame buffer
            elif game is not None:
                if game["shm"].buf[0] == 0:
                    screen_buffer[:] = game["shm"].buf[1:]
                    update_frame_buffer(game["shm"].buf[1:])
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
                term_widget_processes(pages)
                if game is not None:
                    term_game_process(game)
                    game = None
                game = start_game_process(game_list[game_index])
                if game is not None:
                    state = "in_game"
        elif (buttons["btn_b"]):
            # if in game_select, go back to widget
            if (state == "game_select"):
                state = "widget"
        elif (buttons["btn_left"]):
            # button LEFT to go to previous page in widget mode
            if state == "widget":
                if preview_pages is None and len(pages) > 1:
                    for widget in pages[page_index]["widgets"]:
                        os.kill(widget["process"].pid, signal.SIGSTOP)
                    page_index -= 1
                    if page_index < 0:
                        page_index = len(pages) - 1
                    for widget in pages[page_index]["widgets"]:
                        os.kill(widget["process"].pid, signal.SIGCONT)
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
                if preview_pages is None and len(pages) > 1:
                    for widget in pages[page_index]["widgets"]:
                        os.kill(widget["process"].pid, signal.SIGSTOP)
                    page_index += 1
                    if page_index >= len(pages):
                        page_index = 0
                    for widget in pages[page_index]["widgets"]:
                        os.kill(widget["process"].pid, signal.SIGCONT)
                    page_tick = time.time()
            # button RIGHT to select next game in game select
            elif state == "game_select":
                game_index += 1
                if game_index >= len(game_list):
                    game_index = 0
                game_preview_index = 0
                page_tick = time.time()
        elif (buttons["btn_up"]):
            # button UP to increase brightness if not in game
            if state != "in_game":
                try:
                    with open("./device.json", 'r') as file:
                        device_info = json.load(file)
                    brightness = min(int(device_info.get('brightness', "50")) + 10, 100)
                    # Update the device brightness
                    device_info['brightness'] = str(brightness)
                    # Write the updated info back to the file
                    with open("./device.json", 'w') as file:
                        json.dump(device_info, file)
                    set_brightness(brightness)
                except Exception as e:
                    print(f"Error reading or updating device brightness: {e}")
        elif (buttons["btn_down"]):
            # button DOWN to decrease brightness if not in game
            if state != "in_game":
                try:
                    with open("./device.json", 'r') as file:
                        device_info = json.load(file)
                    brightness = max(int(device_info.get('brightness', "50")) - 10, 10)
                    # Update the device brightness
                    device_info['brightness'] = str(brightness)
                    # Write the updated info back to the file
                    with open("./device.json", 'w') as file:
                        json.dump(device_info, file)
                    set_brightness(brightness)
                except Exception as e:
                    print(f"Error reading or updating device brightness: {e}")
        elif (buttons["btn_home"]):
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
                else:
                    game_list.clear()
            # if in game select, go back to widget
            elif (state == "game_select"):
                state = "widget"
            # if in game, trigger reload
            elif (state == "in_game"):
                process_message.reload_conf = True
        elif (buttons["btn_reserved"]):
            pass
except KeyboardInterrupt:
    print("main exiting...")
    term_widget_processes(pages)



# https://cdn.nba.com/logos/nba/{teamId}/primary/L/logo.svg
