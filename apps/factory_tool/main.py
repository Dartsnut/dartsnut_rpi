import re
from PIL import Image, ImageDraw
import time
import numpy
import time
from pydartsnut import Dartsnut

dartsnut = Dartsnut()

dart_mode = False
currentImage = Image.new("RGB",(128,160))
draw = ImageDraw.Draw(currentImage)
colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
for i in range(128):
    for j in range(160):
        color = colors[(i + j) % 3]
        draw.point((i, j), fill=color)

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

try:
    while True:
        time.sleep(0.1)
        buttons = dartsnut.get_buttons()
        if (buttons["btn_a"]):
            dart_mode = False
            draw = ImageDraw.Draw(currentImage)
            draw.rectangle([(0, 0), currentImage.size], fill="#ffffff")
        elif (buttons["btn_b"]):
            dart_mode = False
            draw = ImageDraw.Draw(currentImage)
            draw.rectangle([(0, 0), currentImage.size], fill="#ff0000")
        elif (buttons["btn_up"]):
            dart_mode = False
            draw = ImageDraw.Draw(currentImage)
            draw.rectangle([(0, 0), currentImage.size], fill="#00ff00")
        elif (buttons["btn_left"]):
            dart_mode = False
            draw = ImageDraw.Draw(currentImage)
            draw.rectangle([(0, 0), currentImage.size], fill="#0000ff")
        elif (buttons["btn_right"]):
            dart_mode = False
            draw = ImageDraw.Draw(currentImage)
            for i in range(128):
                for j in range(160):
                    r = int((i / 127) * 255)
                    g = int((j / 159) * 255)
                    b = int(((i + j) / (127 + 159)) * 255)
                    draw.point((i, j), fill=(r, g, b))
        elif (buttons["btn_down"]):
            dart_mode = False
            draw = ImageDraw.Draw(currentImage)
            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
            for i in range(128):
                for j in range(160):
                    color = colors[(i + j) % 3]
                    draw.point((i, j), fill=color)
        elif (buttons["btn_home"]):
            dart_mode = True
        elif (buttons["btn_reserved"]):
            import subprocess
            device = find_usb_audio_device("USB")
            if device:
                subprocess.Popen(["aplay", "-D", device, "1.wav"])
            else:
                print("USB audio device not found!")
        elif dart_mode:
            #for dart test
            currentImage.paste((0,0,0),(0,0,128,160))
            darts = dartsnut.get_darts()
            for idx, dart in enumerate(darts):
                if (dart != [-1,-1]):
                    _, _, w, h = draw.textbbox((0, 0), str(idx), font_size=12)
                    draw.text((dart[0]-w/2, dart[1]-h/2), str(idx), (255,255,255), font_size=12)

        dartsnut.update_frame_buffer(currentImage)
        
except KeyboardInterrupt:
    print("Exiting...")
