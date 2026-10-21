"""Diverts the thumb button for a few seconds and logs what the mouse sends.

The divert is temporary (not saved on the mouse) and is undone on exit.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mxmaster import hidpp  # noqa: E402

THUMB = 0x00C3
DIVERT, DIVERT_VALID, RAW_XY, RAW_XY_VALID = 0x01, 0x02, 0x10, 0x20
seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30

path, _ = next(hidpp.find_devices())
dev = hidpp.Device(path)
reprog = dev.feature(hidpp.REPROG_CONTROLS_V4)
cid = THUMB.to_bytes(2, "big")
dev.request(hidpp.REPROG_CONTROLS_V4, 3, cid + bytes([DIVERT | DIVERT_VALID | RAW_XY | RAW_XY_VALID, 0, 0]))
print(f"Thumb button diverted for {seconds:.0f}s", flush=True)

held = False
dx = dy = 0
start = time.monotonic()
try:
    while time.monotonic() - start < seconds:
        data = dev.read_report(0.5)
        if not data:
            continue
        if data[2] != reprog:
            print(f"{time.monotonic() - start:6.2f} other HID++ report: {data[:8].hex(' ')}", flush=True)
            continue
        function = data[3] >> 4
        t = time.monotonic() - start
        if function == 0:
            cids = [int.from_bytes(data[i:i + 2], "big") for i in range(4, 12, 2)]
            now_held = THUMB in cids
            if now_held and not held:
                dx = dy = 0
                print(f"{t:6.2f} thumb down", flush=True)
            elif held and not now_held:
                print(f"{t:6.2f} thumb up   total dx={dx} dy={dy}", flush=True)
            held = now_held
        elif function == 1:
            mx = int.from_bytes(data[4:6], "big", signed=True)
            my = int.from_bytes(data[6:8], "big", signed=True)
            dx += mx
            dy += my
            print(f"{t:6.2f}   move {mx:+4} {my:+4}   sum {dx:+5} {dy:+5}", flush=True)
finally:
    dev.request(hidpp.REPROG_CONTROLS_V4, 3, cid + bytes([DIVERT_VALID | RAW_XY_VALID, 0, 0]))
    print("Thumb button restored", flush=True)
