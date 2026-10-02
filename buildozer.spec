[app]
title = Pixel Matrix Controller
package.name = pixelmatrix
package.domain = org.matrix

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf

version = 1.0

# PyJNIus zapewnia mostek do Bluetooth Androida, Pillow do renderowania liter
requirements = python3,kivy,pyjnius,pillow

# Uprawnienia wymagane do Bluetooth i lokalizacji (BLE) na Androidzie
android.permissions = BLUETOOTH, BLUETOOTH_ADMIN, BLUETOOTH_SCAN, BLUETOOTH_CONNECT, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION

# Architektura procesorów smartfonów
android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True

# Automatyczna akceptacja licencji Android SDK na GitHub Actions
android.accept_sdk_license = True

# Wersje SDK, NDK i Build Tools
android.api = 33
android.minapi = 21
android.ndk = 25b
android.build_tools_version = 33.0.2

[buildozer]
log_level = 2
warn_on_root = 1
