import re
from PIL import Image, ImageDraw
import time
import numpy
import time
from pydartsnut import Dartsnut
import json

dartsnut = Dartsnut()

with open("/home/rpi/dartsnut_rpi/device.json", "r") as f:
    device_config = json.load(f)

# if device_config["model"] == "PixelBoard":
#     GPIO.setmode(GPIO.BCM)
#     GPIO.setup(3, GPIO.IN)
#     GPIO.setup(14, GPIO.IN)
# else:
#     pass

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

old_buttons = {}
burning_intv = 0
try:
    while True:
        time.sleep(0.05)
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
            #for dart test
            currentImage.paste((0,0,0),(0,0,128,160))
            darts = dartsnut.get_darts()
            for idx, dart in enumerate(darts):
                if (dart != [-1,-1]):
                    _, _, w, h = draw.textbbox((0, 0), str(idx), font_size=12)
                    draw.text((dart[0]-w/2, dart[1]-h/2), str(idx), (255,255,255), font_size=12)

        if buttons["btn_reserved"] and not old_buttons.get("btn_reserved", False):
            import subprocess
            device = find_usb_audio_device("USB")
            if device:
                subprocess.Popen(["aplay", "-D", device, "1.wav"])
            else:
                print("USB audio device not found!")

        old_buttons = buttons.copy()
        dartsnut.update_frame_buffer(currentImage)
        
except KeyboardInterrupt:
    print("Exiting...")
