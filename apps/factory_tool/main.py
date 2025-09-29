import re
from PIL import Image, ImageDraw
import time
import numpy
import time
from pydartsnut import Dartsnut
import json
import threading
import os
from evdev import InputDevice, categorize, ecodes

dartsnut = Dartsnut()

with open("/home/rpi/dartsnut_rpi/device.json", "r") as f:
    device_config = json.load(f)

dart_mode = False
currentImage = Image.new("RGB",(128,160))
draw = ImageDraw.Draw(currentImage)
colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
for i in range(128):
    for j in range(160):
        color = colors[(i + j) % 3]
        draw.point((i, j), fill=color)
pattern_index = 5

def find_usb_audio_device(name_hint="USB"):
    # Get device list
    result = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        # Example line: card 1: Device [USB LCS audio], device 0: USB Audio [USB Audio]
        if name_hint in line:
            match = re.search(r'card (\d+):.*device (\d+):', line)
            if match:
                card = match.group(1)
                device = match.group(2)
                return f"hw:{card},{device}"
    return None

def barcode_listener():
    barcode = ""
    while dartsnut.running:
        try:
            event_devices = [d for d in os.listdir('/dev/input') if d.startswith('event')]
            input_devices = []
            for event_dev in event_devices:
                dev_path = f'/dev/input/{event_dev}'
                try:
                    dev_obj = InputDevice(dev_path)
                    name = dev_obj.name
                    if "PIXELDARTS" in name or "vc4" in name:
                        continue
                    input_devices.append(dev_path)
                except Exception as e:
                    continue
            if not input_devices:
                time.sleep(1)
                continue
            event_path = input_devices[-1]
            dev = InputDevice(event_path)
            for event in dev.read_loop():
                if event.type == ecodes.EV_KEY and event.value == 1:  # Key down
                    key_event = categorize(event)
                    keycode = key_event.keycode
                    if isinstance(keycode, list):
                        keycode = keycode[0]
                    if keycode == 'KEY_ENTER':
                        device_config["serial"] = barcode
                        with open("/home/rpi/dartsnut_rpi/device.json", "w") as f:
                            json.dump(device_config, f)
                        barcode = ""
                    elif keycode.startswith('KEY_'):
                        char = keycode[4:]
                        # Only allow single alphanumeric characters, ignore modifier keys including CAPSLOCK
                        if char.isdigit() or (len(char) == 1 and char.isalpha()):
                            barcode += char.upper()
        except Exception as e:
            print(f"Error reading barcode scanner: {e}")
            time.sleep(1)

# Start the barcode listener thread
barcode_thread = threading.Thread(target=barcode_listener, args=(), daemon=False)
barcode_thread.start()

old_buttons = {}
burning_intv = 0
dart_color_table = [(0,0,255),(255,0,0),(0,255,0),(255,255,0),(0,0,255),(255,0,0),(0,255,0),(255,255,0),(0,0,255),(255,0,0),(0,255,0),(255,255,0)]
dart_circle_table = [(0,0,255),(255,0,0),(0,0,255),(255,0,0),(0,0,255),(255,0,0),(0,0,255),(255,0,0),(0,0,255),(255,0,0),(0,0,255),(255,0,0)]

while dartsnut.running:
    time.sleep(0.05)
    currentImage.paste((0,0,0),(0,0,128,160))

    # read the buttons
    buttons = dartsnut.get_buttons()
    if device_config["model"] == "PixelBoard":
        if buttons["btn_home"] and not old_buttons.get("btn_home", False):
            pattern_index = (pattern_index + 1) % 6
    else:
        if buttons["btn_a"]:
            pattern_index = 5
        elif buttons["btn_b"]:
            pattern_index = 4
            burning_intv = 0
        elif buttons["btn_up"]:
            pattern_index = 0
        elif buttons["btn_left"]:
            pattern_index = 1
        elif buttons["btn_right"]:
            pattern_index = 2
        elif buttons["btn_down"]:
            pattern_index = 3
        elif buttons["btn_home"]:
            pattern_index = 6
            
    if pattern_index == 0:
        draw.rectangle([(0, 0), currentImage.size], fill="#ffffff")
        _, _, w, h = draw.textbbox((0, 0), device_config["serial"], font_size=20)
        draw.text(((128-w)/2, (128-h)/2), device_config["serial"], (0,0,0), font_size=20)
    elif pattern_index == 1:
        draw.rectangle([(0, 0), currentImage.size], fill="#ff0000")
    elif pattern_index == 2:
        draw.rectangle([(0, 0), currentImage.size], fill="#00ff00")
    elif pattern_index == 3:
        draw.rectangle([(0, 0), currentImage.size], fill="#0000ff")
    elif pattern_index == 4:
        burning_intv += 1
        if burning_intv > 120:
            burning_intv = 0
        if burning_intv // 40 == 0:
            draw.rectangle([(0, 0), currentImage.size], fill="#ff0000")
        elif burning_intv // 40 == 1:
            draw.rectangle([(0, 0), currentImage.size], fill="#00ff00")
        else:
            draw.rectangle([(0, 0), currentImage.size], fill="#0000ff")
    elif pattern_index == 5:
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        for i in range(128):
            for j in range(160):
                color = colors[(i + j) % 3]
                draw.point((i, j), fill=color)

    # draw the darts
    darts = dartsnut.get_darts()
    for idx, dart in enumerate(darts):
        if (dart != [-1,-1]):
            _, _, w, h = draw.textbbox((0, 0), str(idx), font_size=12)
            # Draw black outline for the text
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx != 0 or dy != 0:
                        draw.text((dart[0]-w/2+dx, dart[1]-h/2+dy), str(idx), (0,0,0), font_size=12)
            # Draw the colored text on top
            draw.text((dart[0]-w/2, dart[1]-h/2), str(idx), dart_color_table[idx], font_size=12)
            # Draw black outline first
            draw.ellipse(
                [
                    (dart[0] - 10, dart[1] - 10),
                    (dart[0] + 10, dart[1] + 10)
                ],
                outline=(0, 0, 0),
                width=4
            )
            # Draw colored outline on top
            draw.ellipse(
                [
                    (dart[0] - 10, dart[1] - 10),
                    (dart[0] + 10, dart[1] + 10)
                ],
                outline=dart_circle_table[idx],
                width=2
            )

    if buttons["btn_reserved"] and not old_buttons.get("btn_reserved", False):
        import subprocess
        device = find_usb_audio_device("USB")
        if device:
            subprocess.Popen(["aplay", "-D", device, "1.wav"])
        else:
            print("USB audio device not found!")

    old_buttons = buttons.copy()
    dartsnut.update_frame_buffer(currentImage)
        
barcode_thread.join(timeout=1)
print("Exiting...")
