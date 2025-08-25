from PIL import Image, ImageDraw
import signal
import sys
import time
import numpy
import asyncio
import json
import base64
import time
from io import BytesIO
from dartsnut import widget_params, get_darts, update_frame_buffer

default_params = {
    "text": "Guess which half?",
}

# Ensure all parameters are defined, otherwise load default values
for key, value in default_params.items():
    if key not in widget_params:
        widget_params[key] = value

currentImage = Image.new("RGB",(128,64))

draw = ImageDraw.Draw(currentImage)
draw.text((0, 0), widget_params["text"], (255,255,255), font_size=14)

update_frame_buffer(currentImage)

try:
    while True:
        time.sleep(1)
        update_frame_buffer(currentImage)
        
except KeyboardInterrupt:
    print("simple_half_demo exiting...")