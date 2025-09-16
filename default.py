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

qr_img = Image.open("qrcode.png")

dartsnut.update_frame_buffer(qr_img)

try:
    while True:
        time.sleep(10)
        
except KeyboardInterrupt:
    print("default widget exiting...")