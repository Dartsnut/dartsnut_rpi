import sys
import json
import base64
import os
import cv2
from PIL import Image
from io import BytesIO
import numpy
import time
import signal
from dartsnut import widget_params, get_darts, update_frame_buffer

#init the image
currentImage = Image.new("RGB",(128,128))

#resolve the media data
image_array = []
if (widget_params.get("files","") != ""):
    for file_path in widget_params["files"]:
        with open(file_path, "rb") as f:
            file_ext = os.path.splitext(file_path)[1][1:].lower()
            file_data = f.read()
            if file_ext in ["jpg", "bmp", "png"]:
                image = Image.open(BytesIO(file_data)).resize((128,128))
                image_array.append({"fps":0.2, "images":[image]})
            elif file_ext == "mp4":
                images = []
                video_data = base64.b64decode(file_data)
                video_bytes = numpy.frombuffer(video_data, numpy.uint8)
                cap = cv2.VideoCapture()
                cap.open(cv2.imdecode(video_bytes, cv2.IMREAD_UNCHANGED))
                success, frame = cap.read()
                while success:
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((128, 128))
                    images.append(img)
                    success, frame = cap.read()
                cap.release()
                image_array.append({"fps":cap.get(cv2.CAP_PROP_FPS), "images":images})
            elif file_ext == "gif":
                images = []
                gif = Image.open(BytesIO(file_data))
                for frame in range(gif.n_frames):
                    gif.seek(frame)
                    img = gif.convert("RGB").resize((128, 128))
                    images.append(img)
                if "duration" in gif.info:
                    # duration is in milliseconds per frame
                    duration_ms = gif.info["duration"]
                    if duration_ms > 0:
                        fps = 1000 / duration_ms
                    else:
                        fps = 30
                else:
                    fps = 30  # Unknown FPS
                image_array.append({"fps": fps, "images": images})
            # delete the temp file
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Failed to delete temp file {file_path}: {e}")

#start the loop
if (len(image_array) > 0):
    image_index = 0
    frame_index = 0
    try:
        while True:
            currentImage.paste(image_array[image_index]["images"][frame_index], (0, 0))
            frame_index += 1
            if frame_index >= len(image_array[image_index]["images"]):
                frame_index = 0
                image_index += 1
                if image_index >= len(image_array):
                    image_index = 0
            update_frame_buffer(currentImage)
            time.sleep(1 / image_array[image_index]["fps"])
            
    except KeyboardInterrupt:
        print("simple_demo exiting...")