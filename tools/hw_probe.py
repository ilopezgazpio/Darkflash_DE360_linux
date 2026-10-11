#!/usr/bin/env python3
"""Probe the limits of the DE360 screen. Stop the service first
(`sudo systemctl stop de360-lcd`); two writers garble frames.

  python3 tools/hw_probe.py size [KB ...]        frames of increasing size (default 20..60 KB)
  python3 tools/hw_probe.py rate [SECONDS ...]   stream 28 KB frames at these intervals, 30 s each
  python3 tools/hw_probe.py gap  [SECONDS ...]   stream, then go silent this long

Each step reports OK, or RESET when the device dropped off USB (re-enumerated).
Results on the tested DE360: 61 KB frames OK, 0.1 s interval OK, silence of 8 s OK,
10 s -> reset (see README section 6.1).
"""
import os
import random
import sys
import time
from io import BytesIO

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from PIL import Image, ImageDraw  # noqa: E402

from de360_lcd import Lcd, find_hidraw  # noqa: E402


def jpeg_of_size(target, label):
    """480x480 JPEG close to `target` bytes: dark background + a noise band."""
    rnd = random.Random(1)
    noise = bytes(rnd.getrandbits(8) for _ in range(480 * 480 * 3))
    lo, hi, data = 0, 480, b""
    for _ in range(14):
        rows = (lo + hi) // 2
        img = Image.new("RGB", (480, 480), (16, 17, 20))
        if rows:
            img.paste(Image.frombytes("RGB", (480, rows), noise[:480 * rows * 3]), (0, 120))
        ImageDraw.Draw(img).text((20, 30), label, fill="white", font_size=60)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=80)
        data = buf.getvalue()
        if len(data) < target:
            lo = rows + 1
        else:
            hi = rows
    return data


def open_lcd():
    path = find_hidraw()
    while not path:
        time.sleep(1)
        path = find_hidraw()
    lcd = Lcd(path)
    lcd.wake()
    return lcd, path


def finish(lcd, path, err, what):
    try:
        lcd.close()
    except OSError:
        pass
    alive = err is None and find_hidraw() == path
    print(f"{what}: {'OK' if alive else f'RESET ({err})' if err else 'RESET'}", flush=True)
    if not alive:
        time.sleep(6)                     # let it re-enumerate


def probe_size(args):
    for kb in [float(a) for a in args] or [20, 22, 24, 26, 28, 30, 32, 36, 40, 50, 60]:
        frame = jpeg_of_size(int(kb * 1000), f"{kb:g} KB")
        lcd, path, err = *open_lcd(), None
        try:
            for _ in range(4):
                lcd.frame(frame)
                lcd.drain()
                time.sleep(2)
        except OSError as e:
            err = e
        finish(lcd, path, err, f"{len(frame):6} B ({-(-(len(frame) + 32) // 1024)} reports)")


def probe_rate(args):
    frames = [jpeg_of_size(28000, "A"), jpeg_of_size(28000, "B")]
    for iv in [float(a) for a in args] or [2.0, 1.0, 0.5, 0.25, 0.1]:
        lcd, path, err = *open_lcd(), None
        t0, n = time.monotonic(), 0
        try:
            while time.monotonic() - t0 < 30:
                ts = time.monotonic()
                lcd.frame(frames[n % 2])
                lcd.drain()
                n += 1
                time.sleep(max(0, iv - (time.monotonic() - ts)))
        except OSError as e:
            err = e
        finish(lcd, path, err, f"interval {iv:4.2f} s, {n} frames")


def probe_gap(args):
    frame = jpeg_of_size(20000, "gap")
    for gap in [float(a) for a in args] or [4, 6, 8, 10, 12]:
        lcd, path, err = *open_lcd(), None
        try:
            for _ in range(3):
                lcd.frame(frame)
                time.sleep(1)
        except OSError as e:
            err = e
        t = time.monotonic()
        while err is None and time.monotonic() - t < gap + 1 and find_hidraw() == path:
            time.sleep(0.05)
        finish(lcd, path, err, f"silence {gap:4.1f} s")


def main():
    probes = {"size": probe_size, "rate": probe_rate, "gap": probe_gap}
    if len(sys.argv) < 2 or sys.argv[1] not in probes:
        sys.exit(__doc__)
    probes[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
