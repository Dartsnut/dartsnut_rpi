#remove shm from resource tracker
from multiprocessing import shared_memory, resource_tracker
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

import argparse
import sys
parser = argparse.ArgumentParser(description="Dartsnut")
parser.add_argument(
    "--params",
    type=str,
    default="{}",
    help="JSON string for widget parameters"
)
parser.add_argument(
    "--shm",
    type=str,
    default="pdishm",
    help="Shared memory name"
)
args = parser.parse_args()
#load the parameters
try:
    import json
    widget_params = json.loads(args.params)
except json.JSONDecodeError as e:
    print(f"Error decoding JSON: {e}")
    sys.exit(1)
#load the shared memory for display
try:
    shm = shared_memory.SharedMemory(name=args.shm, create=False)
except FileNotFoundError:
    print(f"Shared memory file '{args.shm}' not found.")
    sys.exit(1)

def update_frame_buffer(frame):
    """Update the shared memory buffer with the given image or buffer."""
    if isinstance(frame, bytearray):
        image_bytes = frame
    elif hasattr(frame, 'tobytes'):
        image_bytes = frame.tobytes()
    else:
        raise TypeError("frame must be a bytearray or have a 'tobytes' method")
    
    shm_buffer = shm.buf
    if (shm_buffer[0] == 2):
        return False
    elif (shm_buffer[0] == 1):
        shm_buffer[1:len(image_bytes)+1] = image_bytes
        shm_buffer[0] = 0
        return True
    else:
        return False

# map the input shared memory
try:
    shm_pdo = shared_memory.SharedMemory(name="pdoshm", create=False)
except FileNotFoundError:
    print(f"Shared memory file 'pdoshm' not found.")
    sys.exit(1)

shm_pdo_buf = shm_pdo.buf
# get the darts from the shared memory
def get_darts():
    darts = []
    for i in range(12):
        x = shm_pdo_buf[i*4+1] + (shm_pdo_buf[i*4+2] << 8)
        y = shm_pdo_buf[i*4+3] + (shm_pdo_buf[i*4+4] << 8)
        if (x != 0xffff) & (y != 0xffff):
            if (y <= 1300):
                y_mapped = 127
            elif (y >= 39300):
                y_mapped = 0
            else:
                y_mapped = 127 - (y - 1300) // 299
            
            if (x <= 1800):
                x_mapped = 0
            elif (x >= 39800):
                x_mapped = 127
            else:
                x_mapped = (x - 1800) // 299
            darts.append([x_mapped, y_mapped])
        else:
            darts.append([-1, -1])
    return darts
# get the button from the shared memory
def get_buttons():
    return shm_pdo_buf[0]
# set brightness
def set_brightness(brightness):
    if (10 <= brightness <= 100):
        shm_pdo_buf[49] = brightness