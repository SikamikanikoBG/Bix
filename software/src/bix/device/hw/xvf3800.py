"""reSpeaker XVF3800 control over USB vendor requests (LED ring, direction of arrival).

Protocol from Seeed's python_control (respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY):
write: bmRequestType vendor|device|OUT, bRequest 0, wValue=cmdid, wIndex=resid
read:  bmRequestType vendor|device|IN,  bRequest 0, wValue=0x80|cmdid, wIndex=resid,
       length = payload + 1 (first byte is a status byte)
GPO servicer resid 20: LED_EFFECT 12, LED_BRIGHTNESS 13, LED_SPEED 15,
LED_COLOR 16, DOA_VALUE 18 (2 × uint16: angle 0-359, speech flag).

On Linux the user running Bix needs access to the USB device; deploy/linux
installs a udev rule (idVendor 2886, idProduct 001a).
"""

from __future__ import annotations

import logging
import struct
import threading

log = logging.getLogger(__name__)

VID, PID = 0x2886, 0x001A
GPO = 20
LED_EFFECT, LED_BRIGHTNESS, LED_SPEED, LED_COLOR, DOA_VALUE = 12, 13, 15, 16, 18
EFFECT_OFF, EFFECT_BREATH, EFFECT_RAINBOW, EFFECT_SOLID, EFFECT_DOA, EFFECT_RING = 0, 1, 2, 3, 4, 5


class XVF3800:
    def __init__(self) -> None:
        import usb.core
        import usb.util

        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise RuntimeError("XVF3800 не е свързан по USB.")
        self.dev = dev
        self.usb = usb
        self._lock = threading.Lock()

    def _write(self, cmdid: int, payload: bytes) -> None:
        u = self.usb.util
        with self._lock:
            self.dev.ctrl_transfer(u.CTRL_OUT | u.CTRL_TYPE_VENDOR | u.CTRL_RECIPIENT_DEVICE, 0, cmdid, GPO, payload, 1000)

    def _read(self, cmdid: int, length: int) -> bytes:
        u = self.usb.util
        with self._lock:
            data = self.dev.ctrl_transfer(u.CTRL_IN | u.CTRL_TYPE_VENDOR | u.CTRL_RECIPIENT_DEVICE, 0, 0x80 | cmdid, GPO, length + 1, 1000)
        return bytes(data[1:])

    def effect(self, effect: int) -> None:
        self._write(LED_EFFECT, bytes([effect]))

    def brightness(self, value: int) -> None:
        self._write(LED_BRIGHTNESS, bytes([max(0, min(255, value))]))

    def color(self, rgb: int) -> None:
        self._write(LED_COLOR, struct.pack("<I", rgb))

    def speed(self, value: int) -> None:
        self._write(LED_SPEED, bytes([max(0, min(255, value))]))

    def doa(self) -> tuple[int, bool]:
        angle, speech = struct.unpack("<HH", self._read(DOA_VALUE, 4))
        return angle, bool(speech)
