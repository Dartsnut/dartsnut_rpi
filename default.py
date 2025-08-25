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

def handle_sigcont(signum, frame):
    if(signum == signal.SIGCONT):
        set_frame_buffer(numpy.asarray(currentImage).tobytes())
# Register the SIGCONT handler
signal.signal(signal.SIGCONT, handle_sigcont)

#remove shm from resource tracker
def remove_shm_from_resource_tracker():
    """Monkey-patch multiprocessing.resource_tracker so SharedMemory won't be tracked

    More details at: https://bugs.python.org/issue38119
    """

    def fix_register(name, rtype):
        if rtype == "shared_memory":
            return
        return resource_tracker._resource_tracker.register(name, rtype)
    resource_tracker.register = fix_register

    def fix_unregister(name, rtype):
        if rtype == "shared_memory":
            return
        return resource_tracker._resource_tracker.unregister(name, rtype)
    resource_tracker.unregister = fix_unregister

    if "shared_memory" in resource_tracker._CLEANUP_FUNCS:
        del resource_tracker._CLEANUP_FUNCS["shared_memory"]
remove_shm_from_resource_tracker()

def set_frame_buffer(fb):
    global buf
    if (buf[0] == 0):
        size = len(fb)
        buf[1:size+1] = fb
        buf[0] = 1

argv_json = json.loads(sys.argv[1])
#load the shared memory
shm_name = sys.argv[2]
shm = shared_memory.SharedMemory(name=shm_name, create=False)
buf = shm.buf

currentImage = Image.new("RGB",(128,128))

draw = ImageDraw.Draw(currentImage)
draw.text((0, 0), "default widget", (255,255,255), font_size=14)

set_frame_buffer(numpy.asarray(currentImage).tobytes())

try:
    while True:
        time.sleep(1)
        
except KeyboardInterrupt:
    print("default widget exiting...")