"""Read-only look at what the mouse supports. Changes nothing on the device."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mxmaster import hidpp  # noqa: E402

devices = list(hidpp.find_devices())
if not devices:
    sys.exit("No Logitech HID++ device found")
path, hid_name = devices[0]
print(f"{path}: {hid_name}")
dev = hidpp.Device(path)
print("HID++ protocol %d.%d" % dev.protocol())
print("Name:", dev.name())

print("\nFeatures:")
for index, fid, ftype, version in dev.all_features():
    print(f"  [{index:2}] {fid:#06x} {hidpp.FEATURE_NAMES.get(fid, ''):22} type={ftype:#04x} v{version}")

if dev.feature(hidpp.REPROG_CONTROLS_V4):
    print("\nControls (REPROG_CONTROLS_V4):")
    count = dev.request(hidpp.REPROG_CONTROLS_V4, 0)[0]
    for i in range(count):
        r = dev.request(hidpp.REPROG_CONTROLS_V4, 1, (i,))
        cid, task = int.from_bytes(r[0:2], "big"), int.from_bytes(r[2:4], "big")
        flags, pos, group, gmask, flags2 = r[4], r[5], r[6], r[7], r[8]
        rep = dev.request(hidpp.REPROG_CONTROLS_V4, 2, r[0:2])
        caps = [n for b, n in [(0x01, "mouse"), (0x02, "fkey"), (0x04, "hotkey"), (0x08, "fntog"),
                               (0x10, "reprog"), (0x20, "divert"), (0x40, "persist"), (0x80, "virtual")] if flags & b]
        if flags2 & 0x01:
            caps.append("rawXY")
        print(f"  cid={cid:#06x} task={task:#06x} group={group} gmask={gmask:#04x} "
              f"{','.join(caps)}  now: flags={rep[2]:#04x} remap={int.from_bytes(rep[3:5], 'big'):#06x}")

if dev.feature(hidpp.ADJUSTABLE_DPI):
    r = dev.request(hidpp.ADJUSTABLE_DPI, 2, (0,))
    print(f"\nDPI: {int.from_bytes(r[1:3], 'big')} (default {int.from_bytes(r[3:5], 'big')})")
    r = dev.request(hidpp.ADJUSTABLE_DPI, 1, (0,))
    print("DPI list raw:", r[1:].hex(" "))

if dev.feature(hidpp.SMART_SHIFT):
    r = dev.request(hidpp.SMART_SHIFT, 0)
    print(f"\nSmartShift: mode={r[0]} (1 free, 2 ratchet) auto_disengage={r[1]} default={r[2]}")

if dev.feature(hidpp.BATTERY_STATUS):
    r = dev.request(hidpp.BATTERY_STATUS, 0)
    print(f"\nBattery: {r[0]}% next={r[1]}% status={r[2]}")

if dev.feature(hidpp.THUMB_WHEEL):
    r = dev.request(hidpp.THUMB_WHEEL, 0)
    print("\nThumb wheel info raw:", r[:8].hex(" "))
    r = dev.request(hidpp.THUMB_WHEEL, 1)
    print("Thumb wheel status raw:", r[:4].hex(" "))
