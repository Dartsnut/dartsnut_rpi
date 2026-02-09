"""Load and expose all images, fonts, and loading animation used by states and main loop."""
import time
from PIL import Image, ImageFont


# Loading animation state (mutable)
_loading_frame_index = 0
_loading_frame_last_update = time.time()
_loading_frame_duration = 0.1  # 10 fps


def _load_loading_frames():
    """Load loading sprite sheets and extract frames."""
    global loading_frames, loading_frames_big
    sheet = Image.open("./loading_sprite.png")
    w = sheet.size[0] // 7
    h = sheet.size[1]
    loading_frames = [
        sheet.crop((i * w, 0, (i + 1) * w, h)) for i in range(7)
    ]
    sheet_big = Image.open("./loading_sprite_big.png")
    w_big = sheet_big.size[0] // 7
    h_big = sheet_big.size[1]
    loading_frames_big = [
        sheet_big.crop((i * w_big, 0, (i + 1) * w_big, h_big)) for i in range(7)
    ]


_load_loading_frames()


def get_current_loading_frame():
    """Return the current small loading frame (advances animation)."""
    global _loading_frame_index, _loading_frame_last_update
    now = time.time()
    if now - _loading_frame_last_update >= _loading_frame_duration:
        _loading_frame_index = (_loading_frame_index + 1) % 7
        _loading_frame_last_update = now
    return loading_frames[_loading_frame_index]


def get_current_loading_frame_big():
    """Return the current big loading frame (advances animation)."""
    global _loading_frame_index, _loading_frame_last_update
    now = time.time()
    if now - _loading_frame_last_update >= _loading_frame_duration:
        _loading_frame_index = (_loading_frame_index + 1) % 7
        _loading_frame_last_update = now
    return loading_frames_big[_loading_frame_index]


def create_loading_image():
    """Create a 128x160 image with loading sprites (big top, small bottom)."""
    frame_big = get_current_loading_frame_big()
    frame_small = get_current_loading_frame()
    if frame_big.mode != "RGB":
        frame_big = frame_big.convert("RGB")
    if frame_small.mode != "RGB":
        frame_small = frame_small.convert("RGB")
    img = Image.new("RGB", (128, 160), (0, 0, 0))
    img.paste(frame_big, (0, 32))
    img.paste(frame_small, (0, 128))
    return img


# Images (loaded once)
logo_image = Image.open("./logo.png").resize((128, 128))
identify_image = Image.open("./identify.png")
game_icon = Image.open("./game_icon.png")
settings_icon = Image.open("./settings_icon.png")
widget_icon = Image.open("./widget_icon.png")
lock_widget_icon = Image.open("./lock_widget_icon.png")
wifi_disconnect_icon = Image.open("./wifi_disconnect_icon.png")
internet_disconnect_icon = Image.open("./internet_disconnect_icon.png")
game_select_image = Image.open("./game_sel.png")

# Fonts
font8 = ImageFont.load("./dartsnut-6X8.pil")
font16 = ImageFont.truetype("./Micro5.ttf", size=16)
font24 = ImageFont.truetype("./Micro5.ttf", size=24)
