import sys
import struct
import binascii
import threading
import queue
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.core.window import Window
from kivy.utils import platform

# --- PROTOKÓŁ IPIXEL COLOR (BAJTY) ---
WRITE_UUID_STR  = "0000fa02-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID_STR = "0000fa03-0000-1000-8000-00805f9b34fb"
CCCD_UUID_STR   = "00002902-0000-1000-8000-00805f9b34fb"

BLE_CHUNK = 244
WINDOW_SIZE = 12 * 1024

def _u16(n): return struct.pack("<H", n)
def _u32(n): return struct.pack("<I", n)
def crc32(b): return binascii.crc32(b) & 0xFFFFFFFF

def short(cmd, typ, *params):
    body = bytes([0, cmd, typ, *params])
    return bytes([len(body) + 1]) + body

def proto_brightness(level): return short(0x04, 0x80, max(0, min(100, int(level))))
def proto_power(on): return short(0x07, 0x01, 1 if on else 0)
def proto_set_time(now=None):
    n = now or datetime.now()
    return short(0x01, 0x80, n.hour, n.minute, n.second, 0)
def proto_clock_mode():
    n = datetime.now()
    yy = n.year % 100
    dow = n.isoweekday() # 1=pon .. 7=niedz
    return short(0x06, 0x01, 1, 1, 1, yy, n.month, n.day, dow)

def glyph_from_image(im_char):
    """Konwertuje znak 8x16 lub 16x16 z PIL na bajty LSB-first zgodnie z protokołem."""
    w, h = im_char.size
    pixels = im_char.load()
    out = bytearray()
    for y in range(h):
        for b in range(0, w, 8):
            byte_val = 0
            for k in range(8):
                if b + k < w:
                    px = pixels[b + k, y]
                    is_on = 1 if (isinstance(px, int) and px > 128) or (isinstance(px, tuple) and sum(px[:3]) > 200) else 0
                    byte_val |= (is_on << k)
            out.append(byte_val)
    return bytes(out)

def generate_text_payload(text, rgb=(0, 255, 128), anim=1, speed=80):
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font = ImageFont.load_default()

    blocks = []
    for char in text:
        im = Image.new("1", (8, 16), 0)
        draw = ImageDraw.Draw(im)
        draw.text((0, 0), char, font=font, fill=1)
        bitmap = glyph_from_image(im)
        
        # char_block: OPCODE 0x00 dla 16px wysokości, 8px szerokości
        block = bytes([0x00, rgb[0], rgb[1], rgb[2]]) + bitmap
        blocks.append(block)

    if not blocks:
        return []

    # Odwrócenie bloków przy przewijaniu w prawo
    if anim == 2:
        blocks.reverse()

    props = bytes([0x00, 0x01, 0x01, anim, speed, 0, *rgb, 0, 0, 0, 0])
    payload = bytes([len(blocks)]) + props + b"".join(blocks)

    # Budowanie okna transportowego
    size = _u32(len(payload))
    crc = _u32(crc32(payload))
    frame = bytes([0x00, 0x01, 0x00]) + size + crc + bytes([0x00, 0x00]) + payload
    return [_u16(len(frame) + 2) + frame]


# --- NATYWNY STEROWNIK BLE (ANDROID + DESKTOP FALLBACK) ---
class BLEController:
    def __init__(self, log_callback):
        self.log = log_callback
        self.is_connected = False
        self.ack_event = threading.Event()
        self.write_queue = queue.Queue()
        self.is_android = platform == "android"

        if self.is_android:
            self._init_android()
        else:
            self._init_desktop()

    # --- OBSŁUGA ANDROID (PyJNIus) ---
    def _init_android(self):
        from jnius import autoclass, PythonJavaClass, java_method

        self.UUID = autoclass('java.util.UUID')
        self.BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
        self.BluetoothGatt = autoclass('android.bluetooth.BluetoothGatt')
        self.BluetoothGattDescriptor = autoclass('android.bluetooth.BluetoothGattDescriptor')
        self.PythonActivity = autoclass('org.kivy.android.PythonActivity')

        parent = self

        class GattCallback(PythonJavaClass):
            __javainterfaces__ = ['android/bluetooth/BluetoothGattCallback']
            __javacontext__ = 'app'

            @java_method('(Landroid/bluetooth/BluetoothGatt;II)V')
            def onConnectionStateChange(self, gatt, status, newState):
                if newState == 2: # STATE_CONNECTED
                    parent.log("Połączono z GATT. Odkrywanie usług...")
                    gatt.discoverServices()
                elif newState == 0: # STATE_DISCONNECTED
                    parent.log("Rozłączono.")
                    parent.is_connected = False

            @java_method('(Landroid/bluetooth/BluetoothGatt;I)V')
            def onServicesDiscovered(self, gatt, status):
                parent.log("Usługi wykryte! Konfiguracja Notify...")
                parent.gatt = gatt
                service = gatt.getService(parent.UUID.fromString("0000fa00-0000-1000-8000-00805f9b34fb"))
                if not service:
                    # Szukamy bezpośrednio po liście serwisów
                    for s in gatt.getServices().toArray():
                        if s.getCharacteristic(parent.UUID.fromString(WRITE_UUID_STR)):
                            service = s
                            break

                if service:
                    parent.write_char = service.getCharacteristic(parent.UUID.fromString(WRITE_UUID_STR))
                    parent.notify_char = service.getCharacteristic(parent.UUID.fromString(NOTIFY_UUID_STR))

                    # Włączenie notify na fa03
                    gatt.setCharacteristicNotification(parent.notify_char, True)
                    desc = parent.notify_char.getDescriptor(parent.UUID.fromString(CCCD_UUID_STR))
                    if desc:
                        desc.setValue(parent.BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                        gatt.writeDescriptor(desc)
                    
                    parent.is_connected = True
                    parent.log("Gotowy do wysyłania!")

            @java_method('(Landroid/bluetooth/BluetoothGatt;Landroid/bluetooth/BluetoothGattCharacteristic;)V')
            def onCharacteristicChanged(self, gatt, characteristic):
                val = bytes(characteristic.getValue())
                if len(val) == 5 and val[0] == 0x05:
                    parent.ack_event.set()

            @java_method('(Landroid/bluetooth/BluetoothGatt;Landroid/bluetooth/BluetoothGattCharacteristic;I)V')
            def onCharacteristicWrite(self, gatt, characteristic, status):
                pass

        self.callback = GattCallback()

    def connect(self, mac):
        if self.is_android:
            adapter = self.BluetoothAdapter.getDefaultAdapter()
            if not adapter or not adapter.isEnabled():
                self.log("Błąd: Bluetooth jest wyłączony!")
                return
            device = adapter.getRemoteDevice(mac)
            context = self.PythonActivity.mActivity
            self.log(f"Łączenie z {mac}...")
            device.connectGatt(context, False, self.callback, 2) # 2 = TRANSPORT_LE
        else:
            threading.Thread(target=self._desktop_connect, args=(mac,), daemon=True).start()

    # --- OBSŁUGA DESKTOP (Bleak Fallback) ---
    def _init_desktop(self):
        self.bleak_client = None

    def _desktop_connect(self, mac):
        import asyncio
        from bleak import BleakClient

        async def run():
            try:
                self.log(f"[PC] Łączenie z {mac}...")
                self.bleak_client = BleakClient(mac)
                await self.bleak_client.connect()
                
                def on_notify(_, d):
                    if len(d) == 5 and d[0] == 0x05:
                        self.ack_event.set()

                await self.bleak_client.start_notify(NOTIFY_UUID_STR, on_notify)
                self.is_connected = True
                self.log("[PC] Połączono pomyślnie!")
            except Exception as e:
                self.log(f"[PC Błąd]: {e}")

        asyncio.run(run())

    # --- WYSYŁANIE PAKIETÓW ---
    def send_packet(self, data, wait_ack=True):
        if not self.is_connected:
            self.log("Błąd: Brak połączenia!")
            return

        threading.Thread(target=self._send_thread, args=(data, wait_ack), daemon=True).start()

    def _send_thread(self, data, wait_ack):
        windows = data if isinstance(data, list) else [data]
        
        for w in windows:
            self.ack_event.clear()
            for i in range(0, len(w), BLE_CHUNK):
                chunk = w[i:i+BLE_CHUNK]
                if self.is_android:
                    self.write_char.setValue(chunk)
                    self.write_char.setWriteType(2) # 2 = WRITE_TYPE_DEFAULT (with response)
                    self.gatt.writeCharacteristic(self.write_char)
                else:
                    import asyncio
                    asyncio.run(self.bleak_client.write_gatt_char(WRITE_UUID_STR, chunk, response=True))
                time.sleep(0.01)

            if wait_ack:
                got_ack = self.ack_event.wait(timeout=6.0)
                if not got_ack:
                    self.log("Brak ACK (Timeout)")
                else:
                    self.log("Odebrano ACK ✔")


# --- INTERFEJS GRAFICZNY (KIVY) ---
class PixelMatrixApp(App):
    def build(self):
        Window.clearcolor = (0.1, 0.1, 0.12, 1)
        self.ble = BLEController(self.update_log)

        root = BoxLayout(orientation='vertical', padding=15, spacing=10)

        # 1. Połączenie
        conn_box = BoxLayout(size_hint_y=None, height=45, spacing=10)
        self.mac_input = TextInput(text="55:F6:46:82:91:EC", multiline=False, size_hint_x=0.7)
        btn_conn = Button(text="Połącz", background_color=(0, 0.7, 1, 1), size_hint_x=0.3)
        btn_conn.bind(on_press=lambda _: self.ble.connect(self.mac_input.text.strip()))
        conn_box.add_widget(self.mac_input)
        conn_box.add_widget(btn_conn)
        root.add_widget(conn_box)

        # 2. Status / Logi
        self.lbl_status = Label(text="Status: Niepołączono", size_hint_y=None, height=30, color=(0.8, 0.8, 0.8, 1))
        root.add_widget(self.lbl_status)

        # 3. Pole wpisywania tekstu
        self.txt_input = TextInput(text="Cześć!", multiline=False, size_hint_y=None, height=45)
        root.add_widget(self.txt_input)

        # 4. Wybór animacji i wysyłka tekstu
        text_ctrl_box = BoxLayout(size_hint_y=None, height=45, spacing=10)
        self.anim_spinner = Spinner(
            text="Przewijanie w lewo",
            values=("Statyczny", "Przewijanie w lewo", "Przewijanie w prawo"),
            size_hint_x=0.6
        )
        btn_send_text = Button(text="Wyślij Tekst", background_color=(0, 0.8, 0.4, 1), size_hint_x=0.4)
        btn_send_text.bind(on_press=self.on_send_text)
        text_ctrl_box.add_widget(self.anim_spinner)
        text_ctrl_box.add_widget(btn_send_text)
        root.add_widget(text_ctrl_box)

        # 5. Jasność
        bright_box = BoxLayout(size_hint_y=None, height=40, spacing=10)
        bright_box.add_widget(Label(text="Jasność:", size_hint_x=0.3))
        self.slider = Slider(min=0, max=100, value=60, size_hint_x=0.7)
        self.slider.bind(on_touch_up=self.on_brightness_change)
        bright_box.add_widget(self.slider)
        root.add_widget(bright_box)

        # 6. Szybkie akcje (Zasilanie, Zegar)
        btn_grid = GridLayout(cols=2, spacing=10, size_hint_y=None, height=90)
        
        btn_on = Button(text="Włącz Ekran", background_color=(0.2, 0.6, 0.2, 1))
        btn_on.bind(on_press=lambda _: self.ble.send_packet(proto_power(True)))
        
        btn_off = Button(text="Wyłącz Ekran", background_color=(0.6, 0.2, 0.2, 1))
        btn_off.bind(on_press=lambda _: self.ble.send_packet(proto_power(False)))

        btn_clock = Button(text="Tryb Zegara", background_color=(0.8, 0.5, 0.1, 1))
        btn_clock.bind(on_press=lambda _: self.ble.send_packet(proto_clock_mode()))

        btn_sync_time = Button(text="Synchronizuj Czas", background_color=(0.5, 0.2, 0.8, 1))
        btn_sync_time.bind(on_press=lambda _: self.ble.send_packet(proto_set_time()))

        btn_grid.add_widget(btn_on)
        btn_grid.add_widget(btn_off)
        btn_grid.add_widget(btn_clock)
        btn_grid.add_widget(btn_sync_time)
        root.add_widget(btn_grid)

        return root

    def on_send_text(self, _):
        anim_map = {"Statyczny": 0, "Przewijanie w lewo": 1, "Przewijanie w prawo": 2}
        anim_id = anim_map.get(self.anim_spinner.text, 1)
        windows = generate_text_payload(self.txt_input.text, rgb=(0, 255, 128), anim=anim_id)
        if windows:
            self.update_log("Wysyłanie tekstu...")
            self.ble.send_packet(windows, wait_ack=True)

    def on_brightness_change(self, instance, touch):
        if instance.collide_point(*touch.pos):
            self.ble.send_packet(proto_brightness(int(instance.value)))

    def update_log(self, text):
        Clock.schedule_once(lambda _: setattr(self.lbl_status, 'text', f"Status: {text}"))

if __name__ == '__main__':
    PixelMatrixApp().run()
