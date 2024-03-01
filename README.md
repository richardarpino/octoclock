# octoclock
RPI Pico W based python script to colour a neopixel ring based on data from the Octopus Energy Agile API

# Motivation
- As a household who pay a fixed amount per kilowatt for our electricity
- We want to have a clear and easy way for everyone in the house to see when the 'best' or 'least worst' time is to turn something on or set a timer
- So that we can change our habits with when we use "big ticket" electricity items before we switch to a tariff with dynamic pricing

# Prototype
![Prototype of Octoclock](https://github.com/richardarpino/octoclock/blob/main/IMG_20240226_140126.jpg)

## Dependencies
- [urequests](https://pypi.org/project/micropython-urequests/) - request library which support https
- [pi_pico_neopixel](https://github.com/blaz-r/pi_pico_neopixel) - implementation of protocol to control the neopixels
