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
import fcntl
import struct
import array

dartsnut = Dartsnut()

with open("/home/rpi/dartsnut_rpi/device.json", "r") as f:
    device_config = json.load(f)

dart_mode = False
currentImage = Image.new("RGB",(128,160))
traceOverlay = Image.new("RGBA",(128,160))
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
                    print(keycode)
                    if isinstance(keycode, list):
                        keycode = keycode[0]
                    if keycode == 'KEY_ENTER':
                        device_config["serial"] = barcode
                        with open("/home/rpi/dartsnut_rpi/device.json", "w") as f:
                            json.dump(device_config, f)
                        barcode = ""
                    elif keycode.startswith('KEY_'):
                        char = keycode[4:]
                        if char.isdigit():
                            barcode += char
                        elif len(char) == 1 and char.isalpha():
                            barcode += char.upper()
        except Exception as e:
            print(f"Error reading barcode scanner: {e}")
            time.sleep(1)

def get_emr_version():
    try:
        # Open the hidraw device
        device_path = '/dev/hidraw1'
        fd = open(device_path, 'rb+', buffering=0)

        # HIDIOCGFEATURE ioctl - construct it properly for your architecture
        # _IOC_READ = 2, _IOC_WRITE = 1
        # For HIDIOCGFEATURE: _IOR('H', 0x07, struct)
        # On 64-bit systems: 0xC0404807, on 32-bit: 0xC0204807
        # Let's calculate it dynamically
        _IOC_NRBITS = 8
        _IOC_TYPEBITS = 8
        _IOC_SIZEBITS = 14
        _IOC_DIRBITS = 2

        _IOC_NRSHIFT = 0
        _IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
        _IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
        _IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

        _IOC_READ = 2

        def _IOC(dir, type_char, nr, size):
            return (dir << _IOC_DIRSHIFT) | (ord(type_char) << _IOC_TYPESHIFT) | \
                (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)

        def _IOR(type_char, nr, size):
            return _IOC(_IOC_READ, type_char, nr, size)

        report_id = 0x04
        length = 8

        # Prepare buffer - use array instead of bytearray
        buf = array.array('B', [report_id] + [0] * (length - 1))

        # Construct HIDIOCGFEATURE with the correct size
        HIDIOCGFEATURE = _IOR('H', 0x07, len(buf))

        result = fcntl.ioctl(fd, HIDIOCGFEATURE, buf, True)  # True = mutate buffer
        ascii_string = ''.join(chr(b) for b in buf if 32 <= b < 127)
        return ascii_string
    except Exception as e:
        print(f"Error getting feature report: {e}")
        return None


# Start the barcode listener thread
barcode_thread = threading.Thread(target=barcode_listener, args=(), daemon=False)
barcode_thread.start()

# Read emr version
emr_version = get_emr_version()

old_buttons = {}
burning_intv = 0
dart_color_table = [(0,0,255),(255,0,0),(0,255,0),(255,255,0),(0,0,255),(255,0,0),(0,255,0),(255,255,0),(0,0,255),(255,0,0),(0,255,0),(255,255,0)]

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
            traceOverlay.paste((0,0,0,0), (0,0,128,160))

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
    elif pattern_index == 6:
        # draw the emr version
        if emr_version:
            # Define the box for the "emr_version" text
            box = (0, 144, 63, 159)
            # Get the bounding box of the text to center it
            _, _, w, h = draw.textbbox((0, 0), emr_version, font_size=12)
            # Calculate position to center the text in the box
            x = box[0] + (box[2] - box[0] - w) / 2
            y = box[1] + (box[3] - box[1] - h) / 2
            # Draw the text
            draw.text((x, y), emr_version, fill=(255, 255, 255), font_size=12)

    # draw the darts
    darts = dartsnut.get_darts()
    for idx, dart in enumerate(darts):
        if (dart != [-1,-1]):
            _, _, w, h = draw.textbbox((0, 0), str(idx), font_size=16)
            # Draw black outline for the text
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx != 0 or dy != 0:
                        draw.text((dart[0]-w/2+dx, dart[1]-h/2+dy), str(idx), (0,0,0), font_size=16)
            # Draw the colored text on top
            draw.text((dart[0]-w/2, dart[1]-h/2), str(idx), dart_color_table[idx], font_size=16)
            # Draw black outline first
            draw.ellipse(
                [
                    (dart[0] - 12, dart[1] - 12),
                    (dart[0] + 12, dart[1] + 12)
                ],
                outline=(0, 0, 0),
                width=4
            )
            # Draw colored outline on top
            draw.ellipse(
                [
                    (dart[0] - 12, dart[1] - 12),
                    (dart[0] + 12, dart[1] + 12)
                ],
                outline=dart_color_table[idx],
                width=2
            )
            # draw the trace
            trace_draw = ImageDraw.Draw(traceOverlay)
            trace_draw.rectangle(
                [
                    (dart[0] - 1, dart[1] - 1),
                    (dart[0] + 1, dart[1] + 1)
                ],
                fill=dart_color_table[idx]
            )
    
    # check if all 12 darts are present
    all_present = all(dart != [-1, -1] for dart in darts)
    if all_present:
        # Define the box for the "OK" text
        box = (0, 128, 63, 143)
        # Get the bounding box of the text to center it
        _, _, w, h = draw.textbbox((0, 0), "12OK", font_size=16)
        # Calculate position to center the text in the box
        x = box[0] + (box[2] - box[0] - w) / 2
        y = box[1] + (box[3] - box[1] - h) / 2
        # Draw black border by drawing the text at the surrounding offsets
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), "12OK", fill=(0, 0, 0), font_size=16)
        # Draw the white text on top
        draw.text((x, y), "12OK", fill=(255, 255, 255), font_size=16)
    #check if only 014589 darts are present
    target_indices = {0, 1, 4, 5, 8, 9}
    only_targets_present = all(
        (dart != [-1, -1]) if i in target_indices else (dart == [-1, -1])
        for i, dart in enumerate(darts)
    )

    if only_targets_present:
        # Define the box for the "6OK" text
        box = (0, 128, 63, 143)
        # Get the bounding box of the text to center it
        _, _, w, h = draw.textbbox((0, 0), "6OK", font_size=16)
        # Calculate position to center the text in the box
        x = box[0] + (box[2] - box[0] - w) / 2
        y = box[1] + (box[3] - box[1] - h) / 2
        # Draw black border by drawing the text at the surrounding offsets
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), "6OK", fill=(0, 0, 0), font_size=16)
        # Draw the white text on top
        draw.text((x, y), "6OK", fill=(255, 255, 255), font_size=16)

    # draw the trace overlay
    currentImage.paste(traceOverlay, (0, 0), traceOverlay)

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
