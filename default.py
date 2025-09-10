from multiprocessing import shared_memory, resource_tracker
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
from pydartsnut import Dartsnut

dartsnut = Dartsnut()

currentImage = Image.new("RGB",(128,128))

draw = ImageDraw.Draw(currentImage)
draw.text((0, 0), "default widget", (255,255,255), font_size=14)

dartsnut.update_frame_buffer(currentImage)

try:
    while True:
        time.sleep(1)
        
except KeyboardInterrupt:
    print("default widget exiting...")