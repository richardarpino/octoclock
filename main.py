import time
import os
import network
from machine import Pin, SPI
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
orange = (255, 75, 0)
yellow = (255, 125, 0)
green = (0, 255, 0)
blue = (0, 0, 255)
indigo = (100, 0, 90)
violet = (200, 0, 100)
pixel_off = (0, 0, 0)

# Dictionary of wifi networks Octoclock with connect to
dict_of_wifi = {
    "Brockenhurst92_Guest": "iwantwifinow!"
}

#  octopus URLs
BASE_URL="https://api.octopus.energy"
PRODUCT_CODE = "AGILE-18-02-21"
TARIFF_CODE = "E-1R-%s-J" % (PRODUCT_CODE)
TARIFF_URL = "%s/v1/products/%s/electricity-tariffs/%s" % (BASE_URL, PRODUCT_CODE, TARIFF_CODE)
STANDARD_RATES_URL = "%s/standard-unit-rates/" % (TARIFF_URL)

# variables used in main loop
upcoming_prices = [] #cache of the upcoming prices
prices_look_ahead = 12 #number of prices to display from cache, also triggers refresh when cache contains less than this number

# Anything that between good_price and high_price lights with orange
# Anything lower or equal to zero lights with violet
amazing_price = 7 # blue light threshold - anything lower or equal to this lights with blue
good_price = 14 #green light threshold - anything lower or equal to this lights with green
high_price = 28 #red light threshold - anything greater or equal to this lights with red

# Initialise neopixels
spi = SPI(0, baudrate=10000000, polarity=1, phase=0, sck=Pin(2), mosi=Pin(3))
ss = Pin(5, Pin.OUT)
strip = Neopixel(24, 0, 0, "GRB")
#strip.brightness(0.75) - needs investigating, seems to be a bit on/off but not graduated

def displayError(pixels: Neopixel):
    pixels.fill(red)
    pixels.show()

def displayDownloading(pixels: Neopixel):
    pixels.fill(violet)
    pixels.show()
    time.sleep(1)
    
def displayConnecting(pixels: Neopixel):
    pixels.fill(blue)
    pixels.show()
    time.sleep(2)

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
        strip.clear()
        strip.show()
    return reconnected

def calc_pixel_location(time: str):
    price_hour = int(time[11:13])
    price_minute = int(time[14:16])
    if(price_hour >= 12):
        price_hour -= 12
    if(price_minute > 0):
        price_minute = 1
    return (price_hour * 2) + price_minute

def calc_pixel_colour(price: float):
    pixel_colour = yellow
    if(price >= high_price):
        pixel_colour = red
    elif(price <= 0):
        pixel_colour = violet
    elif(price <= amazing_price):
        pixel_colour = blue
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
    maxindex = min(12, len(prices))
    strip.clear()
    for idx, price in enumerate(prices[:maxindex]):
        print(price)
        set_price_pixel(pixels, price)
        print("-"*40)
    pixels.show()

def set_price_pixel(pixels: Neopixels, price: dict):
    pixel_location = calc_pixel_location(price["valid_from"])
    pixel_colour = calc_pixel_colour(price["value_inc_vat"])
    pixels.set_pixel(pixel_location, pixel_colour)
    print("Lighting pixel %s with %s which is for %s at price %s" % (pixel_location, pixel_colour, price["valid_from"], price["value_inc_vat"]))
    
def clear_price_pixel(pixels: Neopixels, price: dict):
    pixel_location = calc_pixel_location(price["valid_from"])
    pixels.set_pixel(pixel_location, pixel_off)
    print("Switching off pixel %s which is for %s at price %s" % (pixel_location, price["valid_from"], price["value_inc_vat"]))
    
while True:
    try:
        # connect/reconnect to SSID
        reconnected = connectToWifi()

        current_datetime = time.localtime()
        current_mins = int(current_datetime[4])
            
        if current_mins < 30:
            current_mins = 0
        else:
            current_mins = 30

        target_datetime = "%s-%02d-%02dT%02d:%02d:00Z" % (current_datetime[0], current_datetime[1], current_datetime[2], current_datetime[3], current_mins )

        print("Target Time = %s" % (target_datetime))
        
        if(len(upcoming_prices) > 0 and target_datetime != upcoming_prices[0]["valid_from"]):
            latest_price_index = min(12, len(upcoming_prices))
            set_price_pixel(strip, upcoming_prices[latest_price_index])
            clear_price_pixel(strip, upcoming_prices[0])
            strip.show()
            upcoming_prices.pop(0)
            
        if(len(upcoming_prices) < prices_look_ahead or reconnected):
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
        
