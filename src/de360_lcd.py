#!/usr/bin/env python3
"""Show CPU temperature on the darkFlash DE240/DE360 AIO pump LCD (USB 1800:3006).

Protocol (reverse engineered from DF Space Station 5.1.17: DFSpaceStation.exe
class DEDeviceOperation + SDLibrary1.dll class SDDevice, MiraBox "CRT" protocol):

  * Every HID output report is 1024 bytes, no report ID (hidraw gets a leading 0x00).
  * Commands start with b"CRT\\0\\0" + 3-letter command, zero padded to 1024:
        DIS  wake screen        byte[10] = 1
        LIG  brightness         byte[10] = 0..100
        CONNECT  heartbeat
  * A frame is a 480x480 JPEG (the app uses quality 70) preceded by a 32-byte header:
        0  b"CRT\\0\\0DRA"
        8  u32 BE  total length = 32 + len(jpeg)
        12 u8      key = 0xB1
        13 u16 BE  width, 15 height, 17 x, 19 y   (all 0 for a full-screen frame)
    header+jpeg are concatenated and cut into 1024-byte reports, the last one zero padded.
"""
import argparse
import json
import logging
import os
import select
import signal
import struct
import subprocess
import sys
import time
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

VID, PID = 0x1800, 0x3006
REPORT_LEN = 1024
SIZE = 480
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

log = logging.getLogger("de360")


def find_hidraw():
    for name in sorted(os.listdir("/sys/class/hidraw")):
        try:
            with open(f"/sys/class/hidraw/{name}/device/uevent") as f:
                uevent = f.read()
        except OSError:
            continue
        if f"HID_ID=0003:{VID:08X}:{PID:08X}" in uevent:
            return f"/dev/{name}"
    return None


class Lcd:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)

    def close(self):
        os.close(self.fd)

    def _write(self, report):
        assert len(report) <= REPORT_LEN
        os.write(self.fd, b"\x00" + report.ljust(REPORT_LEN, b"\x00"))

    def command(self, cmd, args=b""):
        # args start at byte 8; e.g. DIS/LIG put their value at byte 10
        self._write(b"CRT\x00\x00" + cmd + args)

    def wake(self):
        self.command(b"DIS", b"\x00\x00\x01")

    def brightness(self, level):
        self.command(b"LIG", b"\x00\x00" + bytes([max(0, min(100, level))]))

    def heartbeat(self):
        self.command(b"CONNECT")

    def frame(self, jpeg):
        header = b"CRT\x00\x00DRA" + struct.pack(">IB", 32 + len(jpeg), 0xB1)
        data = header.ljust(32, b"\x00") + jpeg
        for i in range(0, len(data), REPORT_LEN):
            self._write(data[i:i + REPORT_LEN])

    def drain(self):
        """Read and log any input reports so the device's queue never fills."""
        while select.select([self.fd], [], [], 0)[0]:
            data = os.read(self.fd, 512)
            if not data:
                break
            log.debug("in: %s", data.rstrip(b"\x00")[:64])


def cpu_temp(chip_prefix, label):
    out = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=5).stdout
    for chip, feats in json.loads(out).items():
        if chip.startswith(chip_prefix) and label in feats:
            for key, val in feats[label].items():
                if key.endswith("_input"):
                    return float(val)
    raise RuntimeError(f"no {label} on {chip_prefix}* in `sensors -j`")


def temp_color(t):
    # green -> yellow -> red between 40 and 90 C
    stops = [(40, (0, 200, 120)), (65, (255, 200, 0)), (90, (255, 40, 40))]
    if t <= stops[0][0]:
        return stops[0][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
    return stops[-1][1]


class Renderer:
    def __init__(self, rotate):
        self.rotate = rotate
        self.big = ImageFont.truetype(FONT_BOLD, 150)
        self.unit = ImageFont.truetype(FONT_BOLD, 48)
        self.label = ImageFont.truetype(FONT, 44)

    def render(self, temp, quality=80):
        img = Image.new("RGB", (SIZE, SIZE), "black")
        d = ImageDraw.Draw(img)
        color = temp_color(temp)
        box = (20, 20, SIZE - 20, SIZE - 20)
        # 270-degree gauge from 0 to 100 C, open at the bottom
        d.arc(box, 135, 405, fill=(40, 40, 40), width=26)
        frac = max(0.0, min(1.0, temp / 100))
        if frac > 0:
            d.arc(box, 135, 135 + 270 * frac, fill=color, width=26)
        c = SIZE // 2
        d.text((c, 120), "CPU", font=self.label, fill=(200, 200, 200), anchor="mm")
        num = f"{temp:.0f}"
        d.text((c - 10, c + 10), num, font=self.big, fill=color, anchor="mm")
        w = d.textlength(num, font=self.big)
        d.text((c - 10 + w / 2 + 4, c - 40), "°C", font=self.unit, fill=color, anchor="lm")
        if self.rotate:
            img = img.rotate(-self.rotate)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()


def run(args):
    renderer = Renderer(args.rotate)
    lcd = None
    last_shown = None
    last_frame = last_beat = 0.0
    while True:
        try:
            if lcd is None:
                path = find_hidraw()
                if not path:
                    raise OSError("DE360 LCD (1800:3006) not found")
                lcd = Lcd(path)
                log.info("opened %s", path)
                lcd.wake()
                lcd.brightness(args.brightness)
                lcd.heartbeat()
                last_shown, last_beat = None, time.monotonic()
            temp = cpu_temp(args.chip, args.label)
            shown = round(temp)
            now = time.monotonic()
            # redraw on change, and periodically so the screen never falls back to its demo
            if shown != last_shown or now - last_frame >= args.refresh:
                lcd.frame(renderer.render(temp))
                last_shown, last_frame = shown, now
            if now - last_beat >= args.heartbeat:
                lcd.heartbeat()
                last_beat = now
            lcd.drain()
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as e:
            log.warning("%s; retrying in 5 s", e)
            if lcd is not None:
                try:
                    lcd.close()
                except OSError:
                    pass
                lcd = None
            time.sleep(5)
            continue
        time.sleep(args.interval)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--interval", type=float, default=1.0, help="seconds between sensor reads")
    p.add_argument("--refresh", type=float, default=5.0, help="max seconds between frames")
    p.add_argument("--heartbeat", type=float, default=10.0, help="seconds between CONNECT packets")
    p.add_argument("--brightness", type=int, default=80, help="0-100")
    p.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270))
    p.add_argument("--chip", default="k10temp", help="sensors chip name prefix")
    p.add_argument("--label", default="Tctl", help="sensors feature label")
    p.add_argument("--test-image", metavar="FILE", help="write one rendered JPEG to FILE and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    if args.test_image:
        with open(args.test_image, "wb") as f:
            f.write(Renderer(args.rotate).render(cpu_temp(args.chip, args.label)))
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    run(args)


if __name__ == "__main__":
    main()
