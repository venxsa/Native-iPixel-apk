[app]
title = Pixel Matrix Controller
package.name = pixelmatrix
package.domain = org.matrix

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf

version = 1.0

# PyJNIus zapewnia mostek do Bluetooth Androida, Pillow do renderowania liter
requirements = python3,kivy,pyjnius,pillow

android.permissions = BLUETOOTH, BLUETOOTH_ADMIN, BLUETOOTH_SCAN, BLUETOOTH_CONNECT, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION

android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True
android.api = 33
android.minapi = 21
android.ndk = 25b

[buildozer]
log_level = 2
warn_on_root = 1
