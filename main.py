import time
import os
import network
from machine import Pin, SPI
import math
from utime import sleep_ms
import ntptime
import sys
import machine
import json
import struct
import socket

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

# Dictionary of wifi networks Octoclock will connect to
dict_of_wifi = {
    "your_wifi_ssid": "your_password"
}

# Octopus URLs
BASE_URL="https://api.octopus.energy"
PRODUCT_CODE = "AGILE-24-10-01"
TARIFF_CODE = "E-1R-%s-J" % (PRODUCT_CODE)
TARIFF_URL = "%s/v1/products/%s/electricity-tariffs/%s" % (BASE_URL, PRODUCT_CODE, TARIFF_CODE)
STANDARD_RATES_URL = "%s/standard-unit-rates/" % (TARIFF_URL)

# Variables used in main loop
upcoming_prices = [] # cache of the upcoming prices
prices_look_ahead = 16 # number of prices to display from cache, also triggers refresh when cache contains less than this number

# Price thresholds
amazing_price = 7.25 # blue light threshold - anything lower or equal to this lights with blue
good_price = 14.5 # green light threshold - anything lower or equal to this lights with green
high_price = 29 # red light threshold - anything greater or equal to this lights with red

# WiFi management variables
wlan = None
last_wifi_check = 0
wifi_check_interval = 300  # Check WiFi every 5 minutes instead of constantly
last_keepalive = 0
keepalive_interval = 300  # Send keepalive every 5 minutes
gateway_ip = None
wifi_retry_count = 0
max_wifi_retries = 3

# Timezone configuration
TIMEZONE = "Europe/London"  # Change this for different locations
TIMEZONE_API_BASE = "https://timeapi.io/api/TimeZone/zone"

# Timezone handling - will be populated from API
TIME_ZONE_PARAMS = {}
last_timezone_update = 0
timezone_update_interval = 86400  # Update once per day (24 hours)

# Initialise neopixels
spi = SPI(0, baudrate=10000000, polarity=1, phase=0, sck=Pin(2), mosi=Pin(3))
ss = Pin(5, Pin.OUT)
strip = Neopixel(24, 0, 0, "GRB")

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

def parse_timezone_offset(offset_data):
    """Parse timezone offset from timeapi.io object format"""
    if isinstance(offset_data, dict) and 'seconds' in offset_data:
        total_seconds = offset_data['seconds']
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        sign = 1 if total_seconds >= 0 else -1
        return abs(hours), abs(minutes), sign
    
    # Fallback to GMT if format is unexpected
    return 0, 0, 1

def fetch_timezone_data():
    """Fetch timezone data from timeapi.io"""
    try:
        url = f"{TIMEZONE_API_BASE}?timeZone={TIMEZONE}"
        response = requests.get(url)
        
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")
        
        tz_data = response.json()
        response.close()
        
        # Parse offset from timeapi.io format
        offset_data = tz_data.get('currentUtcOffset')
        offset_hours, offset_mins, offset_multiplier = parse_timezone_offset(offset_data)
        
        # Create standard UTC offset string
        sign_str = '+' if offset_multiplier >= 0 else '-'
        utc_offset_str = f"{sign_str}{abs(offset_hours):02d}:{abs(offset_mins):02d}"
        
        timezone_params = {
            "offset_hours": offset_hours,
            "offset_mins": offset_mins,
            "offset_multiplier": offset_multiplier,
            "utc_offset": utc_offset_str,
            "dst": tz_data.get('isDayLightSavingActive', False),
            "timezone": tz_data.get('timeZone', TIMEZONE),
            "abbreviation": 'BST' if tz_data.get('isDayLightSavingActive', False) else 'GMT'
        }
        
        print(f"Timezone updated: {timezone_params['abbreviation']} (UTC{timezone_params['utc_offset']})")
        return timezone_params
        
    except Exception as e:
        print(f"Timezone fetch failed: {e}")
        return None

def update_timezone_params():
    """Update timezone parameters from API if needed, with fallback to cached data"""
    global TIME_ZONE_PARAMS, last_timezone_update
    
    current_time = time.time()
    current_datetime = time.localtime()
    
    # Check if we need to update (once per day at 2am, or if params are empty)
    should_update = (
        len(TIME_ZONE_PARAMS) == 0 or  # No cached data
        (current_datetime[3] == 2 and current_datetime[4] < 5 and  # It's 2am-ish
         (current_time - last_timezone_update) > timezone_update_interval)  # Haven't updated today
    )
    
    if should_update:
        new_params = fetch_timezone_data()
        if new_params:
            TIME_ZONE_PARAMS = new_params
            last_timezone_update = current_time
        elif len(TIME_ZONE_PARAMS) == 0:
            # Fallback to GMT if we have no data at all
            print(f"Using GMT fallback for timezone (requested: {TIMEZONE})")
            TIME_ZONE_PARAMS = {
                "offset_hours": 0,
                "offset_mins": 0,
                "offset_multiplier": 1,
                "utc_offset": "+00:00",
                "dst": False,
                "timezone": TIMEZONE,
                "abbreviation": "GMT"
            }
            last_timezone_update = current_time

def get_gateway_ip():
    """Get the gateway IP address from network config"""
    global gateway_ip
    if wlan and wlan.isconnected():
        config = wlan.ifconfig()
        gateway_ip = config[2]  # Gateway is the 3rd element
        print(f"Gateway IP: {gateway_ip}")
        return gateway_ip
    return None

def send_keepalive():
    """Send ARP request to gateway to keep connection alive"""
    global last_keepalive
    
    if not gateway_ip:
        return False
        
    try:
        # Simple ping to gateway - more reliable than ARP on Pico W
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        # Send a small UDP packet to gateway (will likely be dropped but keeps connection alive)
        s.sendto(b'keepalive', (gateway_ip, 1234))
        s.close()
        last_keepalive = time.time()
        print("Keepalive sent to gateway")
        return True
    except Exception as e:
        print(f"Keepalive failed: {e}")
        return False

def check_wifi_connection():
    """Check WiFi connection less frequently and handle reconnection"""
    global last_wifi_check, wifi_retry_count
    
    current_time = time.time()
    
    # Only check WiFi every wifi_check_interval seconds
    if current_time - last_wifi_check < wifi_check_interval:
        return True
    
    last_wifi_check = current_time
    
    if wlan and wlan.isconnected():
        # Only log occasionally to reduce spam
        if (current_time - last_wifi_check) < 5:  # Only log on the actual check
            print("WiFi still connected")
        wifi_retry_count = 0  # Reset retry count on successful check
        return True
    else:
        print("WiFi disconnected, attempting reconnection...")
        return reconnect_wifi()

def reconnect_wifi():
    """Attempt to reconnect to WiFi with exponential backoff"""
    global wifi_retry_count, wlan
    
    wifi_retry_count += 1
    
    if wifi_retry_count > max_wifi_retries:
        print(f"Max WiFi retries ({max_wifi_retries}) exceeded, will try again later")
        return False
    
    try:
        if not wlan:
            wlan = network.WLAN(network.STA_IF)
        
        if not wlan.active():
            wlan.active(True)
            time.sleep(2)
        
        # Scan and connect to known networks
        ssid_list = wlan.scan()
        for network_info in ssid_list:
            network_name = network_info[0].decode('utf-8')
            if network_name in dict_of_wifi:
                print(f'Attempting to connect to {network_name}')
                displayConnecting(strip)
                
                wlan.connect(network_name, dict_of_wifi[network_name])
                
                # Wait for connection with timeout
                for _ in range(20):  # 10 second timeout
                    if wlan.isconnected():
                        print('WiFi reconnected successfully')
                        print('Network config:', wlan.ifconfig())
                        
                        # Update NTP time
                        try:
                            ntptime.settime()
                            print("NTP time updated")
                        except Exception as e:
                            print(f"NTP update failed: {e}")
                        
                        # Get gateway IP and update timezone on initial connection
                        get_gateway_ip()
                        update_timezone_params()
                        
                        strip.clear()
                        strip.show()
                        wifi_retry_count = 0
                        return True
                    time.sleep(0.5)
                
                print(f'Failed to connect to {network_name}')
                break
        
        print('No known networks found or connection failed')
        return False
        
    except Exception as e:
        print(f"WiFi reconnection error: {e}")
        return False

def connectToWifi():
    """Initial WiFi connection setup"""
    global wlan
    
    wlan = network.WLAN(network.STA_IF)
    
    if not wlan.active():
        wlan.active(True)
        time.sleep(2)
    
    if wlan.isconnected():
        print('Already connected to WiFi')
        print('Network config:', wlan.ifconfig())
        get_gateway_ip()
        update_timezone_params()
        return False
    
    # Initial connection attempt
    success = reconnect_wifi()
    if not success:
        raise Exception("Could not connect to WiFi using all known network credentials")
    
    return True

def calc_pixel_location(time_str: str):
    """Calculate pixel location on clock face from UTC time string"""
    # Apply timezone offset to UTC time
    price_hour = int(time_str[11:13]) + (TIME_ZONE_PARAMS["offset_hours"] * TIME_ZONE_PARAMS["offset_multiplier"])
    price_minute = int(time_str[14:16]) + (TIME_ZONE_PARAMS["offset_mins"] * TIME_ZONE_PARAMS["offset_multiplier"])
    
    # Handle day overflow
    if price_hour >= 24:
        price_hour -= 24
    elif price_hour < 0:
        price_hour += 24
    
    # Convert to 12-hour format
    if price_hour >= 12:
        price_hour -= 12
    
    # Convert minutes to half-hour slots (0 or 1)
    minute_slot = 1 if price_minute >= 30 else 0
    
    return (price_hour * 2) + minute_slot

def calc_pixel_colour(price: float):
    """Calculate pixel colour based on price thresholds"""
    segment = round(((high_price - good_price) / 3), 2)
    
    # Default graduated colors between good and high price
    if price >= (high_price - segment):
        pixel_colour = orange
    elif price >= (high_price - (segment * 2)):
        pixel_colour = yellow
    else:
        pixel_colour = chartreuse
    
    # Override with threshold colors
    if price >= high_price:
        pixel_colour = red
    elif price <= 0:
        pixel_colour = blue
    elif price <= amazing_price:
        pixel_colour = cyan
    elif price <= good_price:
        pixel_colour = green
    
    return pixel_colour

def download_latest_prices(url: str):
    """Download latest prices with better error handling"""
    displayDownloading(strip)
    print(f"Fetching data from {url}")
    print("-" * 40)
    
    try:
        response = requests.get(url)
        price_info = response.json()
        
        # Find current price index
        current_price_index = None
        for index, price in enumerate(price_info["results"]):
            if price["valid_from"] == target_datetime:
                current_price_index = index
                break
        
        if current_price_index is None:
            print("Warning: Current time slot not found in price data")
            current_price_index = 0
        
        # Get upcoming prices (reversed order - newest first)
        upcoming_price_indices = range(current_price_index, max(0, current_price_index - prices_look_ahead), -1)
        upcoming_prices = []
        
        for price_index in upcoming_price_indices:
            if price_index < len(price_info["results"]):
                upcoming_prices.append(price_info["results"][price_index].copy())
        
        response.close()
        print(f"Downloaded {len(upcoming_prices)} price entries")
        return upcoming_prices
        
    except Exception as e:
        print(f"Failed to download prices: {e}")
        displayError(strip)
        time.sleep(2)
        strip.clear()
        strip.show()
        return []

def redraw_prices(pixels: Neopixel, prices: list):
    """Redraw all price pixels on the clock"""
    maxindex = min(prices_look_ahead, len(prices))
    strip.clear()
    
    for idx, price in enumerate(prices[:maxindex]):
        print(f"Price {idx}: {price['valid_from']} = {price['value_inc_vat']}p")
        set_price_pixel(pixels, price)
        print("-" * 40)
    
    pixels.show()

def set_price_pixel(pixels: Neopixel, price: dict):
    """Set a single price pixel"""
    pixel_location = calc_pixel_location(price["valid_from"])
    pixel_colour = calc_pixel_colour(price["value_inc_vat"])
    
    print(f"Lighting pixel {pixel_location} with {pixel_colour} for {price['valid_from']} at {price['value_inc_vat']}p")
    pixels.set_pixel(pixel_location, pixel_colour)
    
def clear_price_pixel(pixels: Neopixel, price: dict):
    """Clear a single price pixel"""
    pixel_location = calc_pixel_location(price["valid_from"])
    pixels.set_pixel(pixel_location, pixel_off)
    print(f"Clearing pixel {pixel_location} for {price['valid_from']} at {price['value_inc_vat']}p")

# Main loop
while True:
    try:
        # Check WiFi connection (only every 5 minutes)
        wifi_connected = check_wifi_connection()
        
        # Send keepalive if needed (only every 5 minutes)
        current_time = time.time()
        if wifi_connected and (current_time - last_keepalive) > keepalive_interval:
            send_keepalive()
        
        # Get current time
        current_datetime = time.localtime()
        current_mins = current_datetime[4]
        
        # Update timezone daily at 2am (and on startup if empty)
        if current_datetime[3] == 2 and current_mins < 5:
            update_timezone_params()
        
        # Round to nearest half hour for price lookup
        if current_mins < 30:
            current_mins = 0
        else:
            current_mins = 30

        target_datetime = f"{current_datetime[0]}-{current_datetime[1]:02d}-{current_datetime[2]:02d}T{current_datetime[3]:02d}:{current_mins:02d}:00Z"

        # Only log target time when something interesting happens
        if len(upcoming_prices) == 0 or (len(upcoming_prices) > 0 and target_datetime != upcoming_prices[0]["valid_from"]):
            print(f"Target Time from data = {target_datetime} (UTC), {len(upcoming_prices)} in cache")
        
        # Update pixel display if time has moved to next slot
        if len(upcoming_prices) > 0 and target_datetime != upcoming_prices[0]["valid_from"]:
            # Add new pixel for future price if available
            if len(upcoming_prices) > prices_look_ahead:
                set_price_pixel(strip, upcoming_prices[prices_look_ahead])
            
            # Clear the old current price pixel
            clear_price_pixel(strip, upcoming_prices[0])
            strip.show()
            
            # Remove the old price from cache
            upcoming_prices.pop(0)
            
        # Download new prices if cache is low and we're connected, or on 0/30 minute marks
        if wifi_connected and ((len(upcoming_prices) < prices_look_ahead and current_mins in (0, 30)) or len(upcoming_prices) == 0):
            new_prices = download_latest_prices(STANDARD_RATES_URL)
            if new_prices:  # Only update if download was successful
                upcoming_prices = new_prices
                redraw_prices(strip, upcoming_prices)
            
        # Main loop delay - back to 30s since we're not constantly checking WiFi
        time.sleep(30)
        
    except Exception as e:
        print("Error:")
        sys.print_exception(e)
        displayError(strip)
        print("Resetting microcontroller in 10 seconds")
        time.sleep(10)
        machine.reset()
