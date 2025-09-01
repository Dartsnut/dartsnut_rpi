from PIL import Image, ImageDraw, ImageFont
from pydartsnut import Dartsnut
import datetime
import requests
import asyncio
import time

dartsnut = Dartsnut()
#read the city from params
city_name = dartsnut.widget_params.get("city", "")
temperature_unit = dartsnut.widget_params.get("unit", "celsius")
forecast_days = int(dartsnut.widget_params.get("forecast", "3"))
weather_data = None

# Create a blank image (128x160, black background)
currentImage = Image.new("RGB", (128, 160), "black")

if city_name != "":
    while True:
        try:
            resp = requests.get(
                "https://secure.geonames.org/searchJSON",
                params={"q": city_name, "maxRows": 1, "username": "sz1jrh"},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if data["geonames"]:
                    lat = data["geonames"][0]["lat"]
                    lng = data["geonames"][0]["lng"]
                    city = data["geonames"][0]["name"]
                else:
                    lat = None
                    lng = None
                    city = "Unknown"
                break
        except Exception as e:
            print(f"Error fetching location data: {e}")
        # Wait a bit before retrying
        time.sleep(2)
else:
    lat = None
    lng = None
    city = "Unknown"

def draw_weather(city="", lat=None, lng=None, forecast="0", unit="celsius", weather_data=None):
    """Draw the weather info on the image and return it."""
    img = Image.new("RGB", (128, 160), "black")
    draw = ImageDraw.Draw(img)

    if city == "Unknown" or lat is None or lng is None:
        # Display "City not found" message
        msg = "City not found"
        text_bbox = draw.textbbox((0, 0), msg, font_size=16)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        text_x = (128 - text_w) // 2
        text_y = (128 - text_h) // 2
        draw.text((text_x, text_y), msg, fill="red", font_size=16)
    else:
        try:
            weather_resp = requests.get(
                "https://api.openweathermap.org/data/2.5/forecast",
                params={
                    "lat": lat,
                    "lon": lng,
                    "appid": "61dff7bfe5173fde076c317c2de2a234",
                },
                timeout=5
            )
            # load weather data
            if weather_resp.status_code == 200:
                weather_data = weather_resp.json()
                if forecast == 0 :
                    weather = weather_data["list"][0]
                    main = weather["main"]
                    if unit == "celsius":
                        temp = int(main["temp"] - 273.15)
                        temp_str = f"{temp}°C"
                    elif unit == "fahrenheit":
                        temp = int((main["temp"] - 273.15) * 9 / 5 + 32)
                        temp_str = f"{temp}°F"
                    else:
                        temp = int(main["temp"])
                        temp_str = f"{temp}K"
                    weather_icon = weather["weather"][0]["icon"]
                    icon_url = f"http://openweathermap.org/img/wn/{weather_icon}@2x.png"
                    icon_resp = requests.get(icon_url, stream=True, timeout=5)
                    if icon_resp.status_code == 200:
                        icon_img = Image.open(icon_resp.raw).resize((64, 64))
                        img.paste(icon_img, (32, 20), icon_img.convert("RGBA"))

                    # Draw temperature
                    temp_bbox = draw.textbbox((0, 0), temp_str, font_size=20)
                    temp_w = temp_bbox[2] - temp_bbox[0]
                    draw.text(((128-temp_w) // 2, 80), temp_str, font_size=20, fill="white")

                    # Draw city name at the bottom
                    city_bbox = draw.textbbox((0, 0), city, font_size=12)
                    city_w = city_bbox[2] - city_bbox[0]
                    city_x = 0 + (64 - city_w) // 2
                    city_y = 128
                    draw.text((city_x, city_y), city, fill="lightgray", font_size=12)
                elif forecast == 3 :
                    # Extract 3-day forecast for noon time (12:00)
                    timezone_shift = weather_data["city"]["timezone"]
                    forecasts = []
                    for entry in weather_data["list"]:
                        # Convert UTC timestamp to local time
                        local_dt = datetime.datetime.fromtimestamp(entry["dt"], datetime.timezone.utc) + datetime.timedelta(seconds=timezone_shift)
                        # Check if time is noon (11:00)
                        if local_dt.hour == 11 and local_dt.date() != datetime.datetime.now().date():
                            forecasts.append((local_dt.date(), entry))
                            if len(forecasts) == 3:
                                break

                    # Draw 3-day forecast
                    # Draw forecast icons and temps for 3 days
                    for i, (date, entry) in enumerate(forecasts):
                        # Date (show weekday and month-day)
                        weekday = date.strftime("%a")
                        month_day = date.strftime("%m-%d")
                        draw.text((i * 42 + 8, 10), weekday, font_size=12, fill="lightgray")
                        draw.text((i * 42 + 8, 24), month_day, font_size=12, fill="lightgray")
                        # Weather icon
                        weather_icon = entry["weather"][0]["icon"]
                        icon_url = f"http://openweathermap.org/img/wn/{weather_icon}.png"
                        icon_resp = requests.get(icon_url, stream=True, timeout=5)
                        if icon_resp.status_code == 200:
                            icon_img = Image.open(icon_resp.raw).resize((32, 32))
                            img.paste(icon_img, (i * 42 + 5, 45), icon_img.convert("RGBA"))
                        # Temperature
                        if unit == "celsius":
                            temp = int(entry["main"]["temp"] - 273.15)
                            temp_str = f"{temp}°C"
                        elif unit == "fahrenheit":
                            temp = int((entry["main"]["temp"] - 273.15) * 9 / 5 + 32)
                            temp_str = f"{temp}°F"
                        else:
                            temp = int(entry["main"]["temp"])
                            temp_str = f"{temp}K"
                        draw.text((i * 42 + 8, 80), temp_str, font_size=14, fill="white")

                    # Draw current weather and city name at the bottom (0,128,63,159)
                    current = weather_data["list"][0]
                    current_icon = current["weather"][0]["icon"]
                    current_icon_url = f"http://openweathermap.org/img/wn/{current_icon}.png"
                    current_icon_resp = requests.get(current_icon_url, stream=True, timeout=5)
                    if current_icon_resp.status_code == 200:
                        current_icon_img = Image.open(current_icon_resp.raw).resize((20, 20))
                        img.paste(current_icon_img, (18, 128), current_icon_img.convert("RGBA"))

                    # Draw city name at the bottom
                    city_bbox = draw.textbbox((0, 0), city, font_size=12)
                    city_w = city_bbox[2] - city_bbox[0]
                    city_x = 0 + (64 - city_w) // 2
                    city_y = 142
                    draw.text((city_x, city_y), city, fill="lightgray", font_size=12)
                elif forecast == 5 :
                    # Extract 5-day forecast for noon time (12:00)
                    timezone_shift = weather_data["city"]["timezone"]
                    forecasts = []
                    for entry in weather_data["list"]:
                        local_dt = datetime.datetime.fromtimestamp(entry["dt"], datetime.timezone.utc) + datetime.timedelta(seconds=timezone_shift)
                        # Check if time is noon (11:00)
                        if local_dt.hour == 11 and local_dt.date() != datetime.datetime.now().date():
                            forecasts.append((local_dt.date(), entry))
                            if len(forecasts) == 5:
                                break

                    # Draw 5-day forecast vertically
                    for i, (date, entry) in enumerate(forecasts):
                        y_offset = i * 24 + 12
                        # Date (show weekday and month-day)
                        weekday = date.strftime("%a")
                        draw.text((8, y_offset), weekday, font_size=16, fill="lightgray")
                        # Weather icon
                        weather_icon = entry["weather"][0]["icon"]
                        icon_url = f"http://openweathermap.org/img/wn/{weather_icon}.png"
                        icon_resp = requests.get(icon_url, stream=True, timeout=5)
                        if icon_resp.status_code == 200:
                            icon_img = Image.open(icon_resp.raw).resize((24, 24))
                            img.paste(icon_img, (50, y_offset-4), icon_img.convert("RGBA"))
                        # Temperature
                        if unit == "celsius":
                            temp = int(entry["main"]["temp"] - 273.15)
                            temp_str = f"{temp}°C"
                        elif unit == "fahrenheit":
                            temp = int((entry["main"]["temp"] - 273.15) * 9 / 5 + 32)
                            temp_str = f"{temp}°F"
                        else:
                            temp = int(entry["main"]["temp"])
                            temp_str = f"{temp}K"
                        draw.text((80, y_offset), temp_str, font_size=16, fill="white")

                    # Draw current weather and city name at the bottom
                    current = weather_data["list"][0]
                    current_icon = current["weather"][0]["icon"]
                    current_icon_url = f"http://openweathermap.org/img/wn/{current_icon}.png"
                    current_icon_resp = requests.get(current_icon_url, stream=True, timeout=5)
                    if current_icon_resp.status_code == 200:
                        current_icon_img = Image.open(current_icon_resp.raw).resize((20, 20))
                        img.paste(current_icon_img, (18, 128), current_icon_img.convert("RGBA"))

                    city_bbox = draw.textbbox((0, 0), city, font_size=12)
                    city_w = city_bbox[2] - city_bbox[0]
                    city_x = 0 + (64 - city_w) // 2
                    city_y = 142
                    draw.text((city_x, city_y), city, fill="lightgray", font_size=12)    
        except Exception as e:
            print(f"Error fetching weather data: {e}")
            return
    currentImage.paste(img)

async def refresh_weather_periodically():
    while True:
        draw_weather(city=city, lat=lat, lng=lng, forecast=forecast_days, unit=temperature_unit)
        await asyncio.sleep(600)  # Refresh every 10 minutes

async def main_loop():
    # Start the background weather refresh task
    asyncio.create_task(refresh_weather_periodically())
    while True:
        dartsnut.update_frame_buffer(currentImage)
        await asyncio.sleep(0.5)

try:
    asyncio.run(main_loop())
except KeyboardInterrupt:
    print("simple_demo exiting...")
