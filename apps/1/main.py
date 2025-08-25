from PIL import Image, ImageDraw
import signal
import sys
import time
import numpy
import json
import base64
import time
import os
from io import BytesIO
from dartsnut import widget_params, get_darts, update_frame_buffer

default_params = {
    "file": "",
    "text": "Hello, World!",
    "color": (255, 255, 255),
    "dropdown": "0",
    "toggle": True,
    "number": 42,
    "slider": 50,
    "checkbox": ["A", "B"]
}

# Ensure all parameters are defined, otherwise load default values
for key, value in default_params.items():
    if key not in widget_params:
        widget_params[key] = value

currentImage = Image.new("RGB",(128,128))

if (widget_params.get("files","") != ""):
    if len(widget_params["files"]) > 0:
        with open(widget_params["files"][0], "rb") as f:
            file_ext = os.path.splitext(widget_params["files"][0])[1][1:].lower()
            file_data = f.read()
            if file_ext in ["jpg", "bmp", "png"]:
                image = Image.open(BytesIO(file_data)).resize((128,128))
                currentImage.paste(image, (0, 0))
        # delete the temp file
        try:
            os.remove(widget_params["files"][0])
        except Exception as e:
            print(f"Failed to delete temp file {file_path}: {e}")

draw = ImageDraw.Draw(currentImage)
draw.text((0, 0), widget_params["text"], widget_params["color"], font_size=14)
draw.text((0, 15), "dropdown: "+widget_params["dropdown"], widget_params["color"], font_size=14)
draw.text((0, 30), "toggle: "+str(widget_params["toggle"]), widget_params["color"], font_size=14)
draw.text((0, 45), "number: "+str(widget_params["number"]), widget_params["color"], font_size=14)
draw.text((0, 60), "slider: "+str(widget_params["slider"]), widget_params["color"], font_size=14)
draw.text((0, 75), "checkbox: ", widget_params["color"], font_size=14)
for i, item in enumerate(widget_params["checkbox"]):
    draw.text((i * 8 + 75, 75), f"{item}", widget_params["color"], font_size=14)

update_frame_buffer(currentImage)

try:
    while True:
        time.sleep(1)
        update_frame_buffer(currentImage)
        
except KeyboardInterrupt:
    print("simple_demo exiting...")