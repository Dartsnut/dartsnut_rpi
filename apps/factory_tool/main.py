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
