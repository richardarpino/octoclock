import time
import os
import network
from machine import Pin, SPI, RTC
import math
from utime import sleep_ms
import ntptime
import sys
import machine

# urequests - https://pypi.org/project/micropython-urequests/ (request library which support https)
import urequests as requests
# pi_pico_neopixel - https://github.com/blaz-r/pi_pico_neopixel (implementation of protocol to control the neopixels)
from neopixel import Neopixel

# Neopixel colours
red = (255, 0, 0)
orange = (210, 80, 25)
yellow = (210, 210, 50)
chartreuse = (100, 255, 50)
green = (0, 255, 0)
cyan = (0, 200, 255)
blue = (0, 0, 255)
indigo = (100, 0, 255)
violet = (255, 125, 125)
white = (255, 255, 255)
pixel_off = (0, 0, 0)


# Dictionary of wifi networks Octoclock with connect to
dict_of_wifi = {
    "ExampleSSID": "ExamplePassword!"
}

#  octopus URLs
BASE_URL="https://api.octopus.energy"
PRODUCT_CODE = "AGILE-FLEX-22-11-25"
TARIFF_CODE = "E-1R-%s-J" % (PRODUCT_CODE)
TARIFF_URL = "%s/v1/products/%s/electricity-tariffs/%s" % (BASE_URL, PRODUCT_CODE, TARIFF_CODE)
STANDARD_RATES_URL = "%s/standard-unit-rates/" % (TARIFF_URL)
TIME_ZONE_URL = "http://worldtimeapi.org/api/timezone/Europe/London"
TIME_ZONE_PARAMS = {}

# variables used in main loop
upcoming_prices = [] #cache of the upcoming prices
prices_look_ahead = 16 #number of prices to display from cache, also triggers refresh when cache contains less than this number

# Anything that between good_price and high_price lights with a colour between red and green (orange, yellow, chartreuse) depending on the price
# Anything lower or equal to zero lights with violet
amazing_price = 7.25 # blue light threshold - anything lower or equal to this lights with blue
good_price = 14.5 #green light threshold - anything lower or equal to this lights with green
high_price = 29 #red light threshold - anything greater or equal to this lights with red


# Initialise neopixels
spi = SPI(0, baudrate=10000000, polarity=1, phase=0, sck=Pin(2), mosi=Pin(3))
ss = Pin(5, Pin.OUT)
strip = Neopixel(24, 0, 0, "GRB")
#strip.brightness(0.75) - needs investigating, seems to be a bit on/off but not graduated

# strip.set_pixel(0, red)
# strip.set_pixel(1, orange)
# strip.set_pixel(2, yellow)
# strip.set_pixel(3, chartreuse)
# strip.set_pixel(4, green)
# strip.set_pixel(5, cyan)
# strip.set_pixel(6, blue)
# strip.set_pixel(7, indigo)
# strip.set_pixel(8, violet)
# strip.set_pixel(9, white)
# 
# strip.show()
# time.sleep(100)

def displayError(pixels: Neopixel):
    pixels.fill(red)
    pixels.show()

def displayDownloading(pixels: Neopixel):
    pixels.fill(green)
    pixels.show()
    time.sleep(1)
    
def displayConnecting(pixels: Neopixel):
    pixels.fill(blue)
    pixels.show()
    time.sleep(2)

def getTimeZoneOffsets():
    # Patch the timezone information by getting the offset from http://worldtimeapi.org/
    time_zone_info = requests.get(TIME_ZONE_URL).json()
    offset_sign = time_zone_info["utc_offset"][0:1]
    time_zone_info["offset_hours"] = int(time_zone_info["utc_offset"][2:3])
    time_zone_info["offset_mins"] = int(time_zone_info["utc_offset"][5:6])
    time_zone_info["offset_multiplier"] = 1
    if offset_sign == "-":
        time_zone_info["offset_multiplier"] *= -1
    return time_zone_info

# connectToWifi heavily influence by https://sungkhum.medium.com/robust-wifi-connection-script-for-a-esp8266-in-micropython-239c12fae0de
# different board with different characterics but same problem I saw with very infrequent wifi usage on RP2040 (seems to be related to
# keeping the wifi connect alive/connected)
def connectToWifi():
    wlan = network.WLAN(network.STA_IF)
    reconnected = False
    if not wlan.active():
        wlan.active(True)
    i = 1
    if not wlan.isconnected():
        for _ in range(10):
            #check available WiFi and try to connect
            #to WiFi specified in dict_of_wifi if available
            ssid = wlan.scan()
            for x in ssid:
                for wifi_ssid in dict_of_wifi:
                    if wifi_ssid in str(x):
                        displayConnecting(strip)
                        wlan.connect(wifi_ssid, dict_of_wifi[wifi_ssid])
                        print('Trying ' + str(wifi_ssid))
                        time.sleep(10)
                        reconnected = True
                        break
                    else:
                        pass
            i += 1
            if wlan.isconnected():
                print('Connected to wifi')
                break
            time.sleep(10)
        else:
            print('Failed to connect to wifi')
            raise Exception("Could not connect to Wifi using all known network credentials")
    print('network config:', wlan.ifconfig())
    if(reconnected):
        # Sort out time with NTP (otherwise board will default to a weird date and time, which is inconvenient)
        ntptime.settime()
        TIME_ZONE_PARAMS = getTimeZoneOffsets()
        strip.clear()
        strip.show()
    return reconnected

def calc_pixel_location(time: str):
    # Incoming prices will be raw from Octopus and will be in UTC, so we need to apply the timezone offset so it represents the correct time according to where the clock is located
    price_hour = int(time[11:13]) + (TIME_ZONE_PARAMS["offset_hours"] * TIME_ZONE_PARAMS["offset_multiplier"])
    # Because our time correction can push us into the next day, check and subtract 24 hours to rebase the time to the next day
    if price_hour >= 24:
        price_hour -= 24
    price_minute = int(time[14:16]) + (TIME_ZONE_PARAMS["offset_mins"] * TIME_ZONE_PARAMS["offset_multiplier"])
    if(price_hour >= 12):
        price_hour -= 12
    if(price_minute > 0):
        price_minute = 1
    return (price_hour * 2) + price_minute

def calc_pixel_colour(price: float):
    segment = round(((high_price - good_price) / 3), 2)
    # Segment the difference between high and good price and graduate that evenly across 3 colours - this is the 'default' colour and can be overridden by the next block
    if(price >= (high_price - segment)):
        pixel_colour = orange
    elif(price >= (high_price - (segment * 2))):
        pixel_colour = yellow
    else:
        pixel_colour = chartreuse
    if(price >= high_price):
        pixel_colour = red
    elif(price <= 0):
        pixel_colour = blue
    elif(price <= amazing_price):
        pixel_colour = cyan
    elif(price <= good_price):
        pixel_colour = green
    return pixel_colour

def download_latest_prices(url: str):
    displayDownloading(strip)
    print("Fetching data from %s" % url)
    print("-" * 40)
    response = requests.get(url)
    price_info = response.json()
    current_price_index = next((index for (index, price) in enumerate(price_info["results"]) if price["valid_from"] == target_datetime))
    upcoming_price_indices = range(current_price_index, 0, -1)                
    upcoming_prices = []
    for price_index in upcoming_price_indices:
        upcoming_prices.append(price_info["results"][price_index].copy())
    del(price_info)
    response.close()
    return upcoming_prices

def redraw_prices(pixels: Neopixels, prices: list):
    maxindex = min(prices_look_ahead, len(prices))
    strip.clear()
    for idx, price in enumerate(prices[:maxindex]):
        print(price)
        set_price_pixel(pixels, price)
        print("-"*40)
    pixels.show()

def set_price_pixel(pixels: Neopixels, price: dict):
    pixel_location = calc_pixel_location(price["valid_from"])
    pixel_colour = calc_pixel_colour(price["value_inc_vat"])
    print("Lighting pixel %s with %s which is for %s (TZ %s mins) at price %s" % (pixel_location, pixel_colour, price["valid_from"], ((TIME_ZONE_PARAMS["offset_mins"] + (TIME_ZONE_PARAMS["offset_hours"] * 60)) * TIME_ZONE_PARAMS["offset_multiplier"]), price["value_inc_vat"]))
    pixels.set_pixel(pixel_location, pixel_colour)
    
def clear_price_pixel(pixels: Neopixels, price: dict):
    pixel_location = calc_pixel_location(price["valid_from"])
    pixels.set_pixel(pixel_location, pixel_off)
    print("Switching off pixel %s which is for %s at price %s" % (pixel_location, price["valid_from"], price["value_inc_vat"]))
    
while True:
    try:
        # connect/reconnect to SSID
        force_redraw = connectToWifi()

        current_datetime = time.localtime()
        current_mins = int(current_datetime[4])
        
        current_offset = TIME_ZONE_PARAMS.get("utc_offset", None)
        # Recheck the timezone params each day at midnight or is empty (only found in development with soft restarts)
        if (current_datetime[3] == 0 and current_mins == 0) or len(TIME_ZONE_PARAMS) == 0:
            TIME_ZONE_PARAMS = getTimeZoneOffsets()
            # If the offset has changed then we need to force a re-draw of all the pixels
            if current_offset != TIME_ZONE_PARAMS.get("utc_offset", None):
                force_redraw = True
        
        if current_mins < 30:
            current_mins = 0
        else:
            current_mins = 30

        target_datetime = "%s-%02d-%02dT%02d:%02d:00Z" % (current_datetime[0], current_datetime[1], current_datetime[2], current_datetime[3], current_mins )

        print("Target Time from data = %s (which is UTC)" % (target_datetime))
        
        if(len(upcoming_prices) > 0 and target_datetime != upcoming_prices[0]["valid_from"]):
            latest_price_index = min(prices_look_ahead, len(upcoming_prices))
            set_price_pixel(strip, upcoming_prices[latest_price_index])
            clear_price_pixel(strip, upcoming_prices[0])
            strip.show()
            upcoming_prices.pop(0)
            
        if(len(upcoming_prices) < prices_look_ahead or force_redraw):
            upcoming_prices = download_latest_prices(STANDARD_RATES_URL)
            redraw_prices(strip, upcoming_prices)
            
        #  delays for 30s
        time.sleep(30)
    # pylint: disable=broad-except
    except Exception as e:
        print("Error:\n", sys.print_exception(e))
        displayError(strip)
        print("Resetting microcontroller in 10 seconds")
        time.sleep(10)
        machine.reset()
