#!/bin/bash
sudo service klipper stop
./scripts/flash-sdcard.sh /dev/serial/by-id/usb-Klipper_lpc1769_1D80FF1527903CAFB8C66D5CC62000F5-if00 generic-lpc1769
sudo service klipper start