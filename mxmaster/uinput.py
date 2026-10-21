"""Virtual input devices through /dev/uinput (keyboard + buttons, and a touchpad), with no extra libraries."""
import fcntl
import os
import struct
import time

from .keycodes import CODES

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
REL_X, REL_Y, REL_WHEEL, REL_HWHEEL = 0x00, 0x01, 0x08, 0x06
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
BTN_LEFT, BTN_TOOL_FINGER, BTN_TOUCH = 0x110, 0x145, 0x14A
BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP, BTN_TOOL_QUINTTAP = 0x14D, 0x14E, 0x14F, 0x148
FINGER_TOOLS = {1: BTN_TOOL_FINGER, 2: BTN_TOOL_DOUBLETAP, 3: BTN_TOOL_TRIPLETAP, 4: BTN_TOOL_QUADTAP, 5: BTN_TOOL_QUINTTAP}
INPUT_PROP_POINTER, INPUT_PROP_BUTTONPAD = 0x00, 0x02
BUS_VIRTUAL = 0x06

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E
UI_ABS_SETUP = 0x401C5504

KEY_MAX = 0x2FF


class _Device:
    def _open(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)

    def _create(self, name, product):
        setup = struct.pack("HHHH80sI", BUS_VIRTUAL, 0x046D, product, 1, name.encode()[:79], 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.2)  # let the compositor pick the new device up before the first event

    def close(self):
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)

    def _emit(self, etype, code, value):
        now = time.time()
        os.write(self.fd, struct.pack("llHHi", int(now), int(now % 1 * 1e6), etype, code, value))

    def _sync(self):
        self._emit(EV_SYN, SYN_REPORT, 0)


class VirtualInput(_Device):
    def __init__(self, name="mx-master-linux virtual input"):
        self._open()
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in sorted(set(CODES.values())):
            if code <= KEY_MAX:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
        for rel in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, rel)
        self._create(name, 0x0001)

    def press(self, keys):
        """Press keys in order, then release them in reverse: a chord like Super+Page_Up."""
        codes = [CODES[k] for k in keys]
        for code in codes:
            self._emit(EV_KEY, code, 1)
            self._sync()
        for code in reversed(codes):
            self._emit(EV_KEY, code, 0)
            self._sync()

    def hold(self, keys, down):
        for code in [CODES[k] for k in keys]:
            self._emit(EV_KEY, code, 1 if down else 0)
        self._sync()


class VirtualTouchpad(_Device):
    """A multi-finger touchpad. Holding fingers down and moving them makes the desktop
    run its own touchpad swipe, which follows the motion (workspaces, overview)."""

    RESOLUTION = 40  # units per mm
    WIDTH, HEIGHT = 300, 200  # mm; big enough that a long drag never reaches an edge
    SLOTS = 5
    SPACING = 15  # mm between fingers

    def __init__(self, name="mx-master-linux virtual touchpad"):
        self._open()
        self.max_x = self.WIDTH * self.RESOLUTION
        self.max_y = self.HEIGHT * self.RESOLUTION
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in (BTN_LEFT, BTN_TOUCH, *FINGER_TOOLS.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        for code, maximum in ((ABS_X, self.max_x), (ABS_Y, self.max_y),
                              (ABS_MT_POSITION_X, self.max_x), (ABS_MT_POSITION_Y, self.max_y),
                              (ABS_MT_SLOT, self.SLOTS - 1), (ABS_MT_TRACKING_ID, 0xFFFF)):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            resolution = self.RESOLUTION if code in (ABS_X, ABS_Y, ABS_MT_POSITION_X, ABS_MT_POSITION_Y) else 0
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("H2xiiiiii", code, 0, 0, maximum, 0, 0, resolution))
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_POINTER)
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_BUTTONPAD)
        self._create(name, 0x0002)
        self.fingers = 0
        self.tracking_id = 0
        self.x = self.y = 0.0

    @property
    def touching(self):
        return self.fingers > 0

    def _frame(self):
        spacing = self.SPACING * self.RESOLUTION
        for slot in range(self.fingers):
            x = int(min(max(self.x + (slot - (self.fingers - 1) / 2) * spacing, 0), self.max_x))
            y = int(min(max(self.y + (spacing / 3 if slot % 2 else 0), 0), self.max_y))
            self._emit(EV_ABS, ABS_MT_SLOT, slot)
            self._emit(EV_ABS, ABS_MT_POSITION_X, x)
            self._emit(EV_ABS, ABS_MT_POSITION_Y, y)
            if slot == 0:
                self._emit(EV_ABS, ABS_X, x)
                self._emit(EV_ABS, ABS_Y, y)
        self._sync()

    def begin(self, fingers):
        self.fingers = max(2, min(self.SLOTS, fingers))
        self.x, self.y = self.max_x / 2, self.max_y / 2
        for slot in range(self.fingers):
            self.tracking_id = (self.tracking_id + 1) & 0xFFFF
            self._emit(EV_ABS, ABS_MT_SLOT, slot)
            self._emit(EV_ABS, ABS_MT_TRACKING_ID, self.tracking_id)
        self._emit(EV_KEY, BTN_TOUCH, 1)
        self._emit(EV_KEY, FINGER_TOOLS[self.fingers], 1)
        self._frame()

    def move(self, dx_mm, dy_mm):
        if self.touching:
            self.x += dx_mm * self.RESOLUTION
            self.y += dy_mm * self.RESOLUTION
            self._frame()

    def end(self):
        if not self.touching:
            return
        for slot in range(self.fingers):
            self._emit(EV_ABS, ABS_MT_SLOT, slot)
            self._emit(EV_ABS, ABS_MT_TRACKING_ID, -1)
        self._emit(EV_KEY, BTN_TOUCH, 0)
        self._emit(EV_KEY, FINGER_TOOLS[self.fingers], 0)
        self._sync()
        self.fingers = 0
