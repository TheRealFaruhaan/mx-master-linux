"""Minimal HID++ 2.0 over hidraw: find the mouse, call features, read events."""
import errno
import os
import select
import time
from pathlib import Path

LONG = 0x11
LONG_LEN = 20
SHORT = 0x10
DAEMON_SW_ID = 0x0B  # tags our requests so replies can be told apart from events
APP_SW_ID = 0x0C
BT_INDEX = 0xFF  # a Bluetooth device answers on index 0xFF

ROOT = 0x0000
FEATURE_SET = 0x0001
DEVICE_NAME = 0x0005
BATTERY_STATUS = 0x1000
WIRELESS_STATUS = 0x1D4B
REPROG_CONTROLS_V4 = 0x1B04
ADJUSTABLE_DPI = 0x2201
SMART_SHIFT = 0x2110
HIRES_WHEEL = 0x2121
THUMB_WHEEL = 0x2150

FEATURE_NAMES = {
    0x0000: "ROOT", 0x0001: "FEATURE_SET", 0x0003: "DEVICE_FW_VERSION",
    0x0005: "DEVICE_NAME", 0x0020: "CONFIG_CHANGE", 0x1000: "BATTERY_STATUS",
    0x1814: "CHANGE_HOST", 0x1815: "HOSTS_INFO", 0x1B04: "REPROG_CONTROLS_V4",
    0x1D4B: "WIRELESS_STATUS", 0x1DF3: "EQUAD_DJ_DEBUG", 0x2110: "SMART_SHIFT",
    0x2121: "HIRES_WHEEL", 0x2150: "THUMB_WHEEL", 0x2201: "ADJUSTABLE_DPI",
    0x00C2: "DFUCONTROL_SIGNED", 0x1E00: "ENABLE_HIDDEN", 0x1803: "GPIO_ACCESS",
    0x1806: "CONFIG_DEVICE_PROPS", 0x1813: "OOBSTATE", 0x18A1: "LED_SW_CONTROL",
    0x1E22: "SPI_DIRECT_ACCESS", 0x1EB0: "TDE_ACCESS", 0x1F03: "?",
}

ERRORS = {
    1: "unknown", 2: "invalid argument", 3: "out of range", 4: "hardware error",
    5: "logitech internal", 6: "invalid feature index", 7: "invalid function",
    8: "busy", 9: "unsupported",
}


class HidppError(Exception):
    pass


def find_devices():
    """Yield (hidraw path, name) for Logitech devices that speak HID++ (long reports)."""
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        uevent = (node / "device" / "uevent").read_text()
        info = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        if ":0000046D:" not in info.get("HID_ID", ""):
            continue
        descriptor = (node / "device" / "report_descriptor").read_bytes()
        if bytes([0x85, LONG]) in descriptor:
            yield f"/dev/{node.name}", info.get("HID_NAME", "")


class Device:
    def __init__(self, path, index=BT_INDEX, sw_id=DAEMON_SW_ID):
        self.path = path
        self.index = index
        self.sw_id = sw_id
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.features = {}  # feature id -> index
        self.pending_events = []

    def close(self):
        os.close(self.fd)

    def fileno(self):
        return self.fd

    def _write(self, feature_index, function, params):
        msg = bytes([LONG, self.index, feature_index, (function << 4) | self.sw_id]) + bytes(params)
        os.write(self.fd, msg.ljust(LONG_LEN, b"\0"))

    def read_report(self, timeout):
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        try:
            data = os.read(self.fd, 64)
        except OSError as e:
            if e.errno == errno.EAGAIN:
                return None
            raise
        if data and data[0] in (LONG, SHORT) and data[1] == self.index:
            return data
        return b""  # some other report (plain mouse input); ignore

    def call(self, feature_index, function, params=(), timeout=2.0):
        self._write(feature_index, function, params)
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            data = self.read_report(left)
            if not data:
                continue
            if data[2] == 0xFF and data[3] == feature_index and data[4] & 0x0F == self.sw_id:
                raise HidppError(ERRORS.get(data[5], f"error {data[5]}"))
            if data[2] == feature_index and data[3] == (function << 4) | self.sw_id:
                return data[4:]
            self.pending_events.append(data)
        raise HidppError("timeout")

    def feature(self, feature_id):
        """Index of a feature on this device, or None when it does not have it."""
        if feature_id not in self.features:
            reply = self.call(0x00, 0, feature_id.to_bytes(2, "big"))
            self.features[feature_id] = reply[0] or None
        return self.features[feature_id]

    def request(self, feature_id, function, params=()):
        index = self.feature(feature_id)
        if index is None:
            raise HidppError(f"no feature {feature_id:#06x}")
        return self.call(index, function, params)

    def protocol(self):
        reply = self.call(0x00, 1, (0, 0, 0x5A))
        return reply[0], reply[1]

    def all_features(self):
        count = self.request(FEATURE_SET, 0)[0]
        found = []
        for i in range(1, count + 1):
            reply = self.request(FEATURE_SET, 1, (i,))
            fid = int.from_bytes(reply[0:2], "big")
            self.features[fid] = i
            found.append((i, fid, reply[2], reply[3]))
        return found

    def name(self):
        length = self.request(DEVICE_NAME, 0)[0]
        name = b""
        while len(name) < length:
            name += self.request(DEVICE_NAME, 1, (len(name),))[: length - len(name)]
        return name.decode(errors="replace")

    def dpi(self):
        return int.from_bytes(self.request(ADJUSTABLE_DPI, 2, (0,))[1:3], "big")

    def default_dpi(self):
        return int.from_bytes(self.request(ADJUSTABLE_DPI, 2, (0,))[3:5], "big")

    def set_dpi(self, dpi):
        self.request(ADJUSTABLE_DPI, 3, (0, dpi >> 8, dpi & 0xFF))

    def dpi_range(self):
        """(lowest, highest, step), or a plain list of the DPI values the sensor takes."""
        raw = self.request(ADJUSTABLE_DPI, 1, (0,))[1:]
        values = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
        values = values[: values.index(0)] if 0 in values else values
        if len(values) == 3 and values[1] >= 0xE000:  # low, step marker, high
            return values[0], values[2], values[1] - 0xE000
        return values

    def battery(self):
        """(percent, charging) from BATTERY_STATUS."""
        r = self.request(BATTERY_STATUS, 0)
        return r[0], r[2] in (1, 2, 3)  # recharging, almost full, full
