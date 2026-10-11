#!/usr/bin/env python3
"""Rotating system dashboard for the darkFlash DE360 LCD.

Collectors read the sensors every `sample_interval` seconds and keep a short
history; lcd_pages.py draws the pages; the rotator walks the page list from
config.yaml and an alert override pins the alert page while a temperature is
at or above its limit (released `hysteresis` degC below it). Frames go out
through the CRT/DRA protocol in de360_lcd.py.
"""
import argparse
import logging
import os
import signal
import socket
import sys
import time
from collections import deque
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import psutil  # noqa: E402
import yaml  # noqa: E402
from PIL import Image  # noqa: E402

import lcd_pages as pages  # noqa: E402
from de360_lcd import Lcd, find_hidraw  # noqa: E402

log = logging.getLogger("de360-dash")

# NVML clock-event reasons that mean the GPU is slowing down for heat or power
THROTTLE_MASK = 0x08 | 0x20 | 0x40 | 0x80   # HW slowdown, SW thermal, HW thermal, power brake


# ------------------------------------------------------------- collectors --
class CpuTemps:
    """k10temp via hwmon sysfs (the same files lm-sensors reads), no subprocess."""

    def __init__(self):
        self.files = None

    def _find(self):
        for hw in os.listdir("/sys/class/hwmon"):
            base = f"/sys/class/hwmon/{hw}"
            try:
                with open(f"{base}/name") as f:
                    if f.read().strip() != "k10temp":
                        continue
            except OSError:
                continue
            files = {}
            for n in os.listdir(base):
                if n.startswith("temp") and n.endswith("_label"):
                    with open(f"{base}/{n}") as f:
                        files[f.read().strip()] = f"{base}/{n[:-6]}_input"
            return files
        raise RuntimeError("k10temp not found in /sys/class/hwmon")

    def read(self):
        if self.files is None:
            self.files = self._find()
        out = {}
        try:
            for label, path in self.files.items():
                with open(path) as f:
                    out[label] = int(f.read()) / 1000
        except OSError:
            self.files = None
            raise
        return out


class Gpus:
    def __init__(self):
        self.nv = None
        self.next_try = 0.0

    def _init(self):
        import pynvml
        pynvml.nvmlInit()
        self.nv = pynvml
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i)
                        for i in range(pynvml.nvmlDeviceGetCount())]
        self.names = [pynvml.nvmlDeviceGetName(h).replace("NVIDIA ", "").replace("GeForce ", "")
                      for h in self.handles]
        log.info("NVML: %s", ", ".join(self.names) or "no GPUs")

    def _opt(self, fn, *a, default=0):
        try:
            return fn(*a)
        except self.nv.NVMLError:
            return default

    def read(self):
        if self.nv is None:
            if time.monotonic() < self.next_try:
                return []
            try:
                self._init()
            except Exception as e:  # missing library, driver not loaded, ...
                log.warning("NVML unavailable: %s", e)
                self.nv, self.next_try = None, time.monotonic() + 60
                return []
        nv, out = self.nv, []
        try:
            for i, h in enumerate(self.handles):
                mem = nv.nvmlDeviceGetMemoryInfo(h)
                reasons = self._opt(nv.nvmlDeviceGetCurrentClocksEventReasons, h)
                out.append({
                    "index": i, "name": self.names[i],
                    "temp": nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU),
                    "util": nv.nvmlDeviceGetUtilizationRates(h).gpu,
                    "power": self._opt(nv.nvmlDeviceGetPowerUsage, h) / 1000,
                    "power_limit": self._opt(nv.nvmlDeviceGetEnforcedPowerLimit, h, default=1000) / 1000,
                    "vram_used": mem.used / 2**30, "vram_total": mem.total / 2**30,
                    "fan": self._opt(nv.nvmlDeviceGetFanSpeed, h),
                    "clock_ghz": self._opt(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_GRAPHICS) / 1000,
                    "throttling": bool(reasons & THROTTLE_MASK),
                })
        except nv.NVMLError as e:
            log.warning("NVML read failed: %s", e)
            self.nv, self.next_try = None, time.monotonic() + 10
            return []
        return out


def default_iface():
    with open("/proc/net/route") as f:
        for line in f.readlines()[1:]:
            fields = line.split()
            if fields[1] == "00000000":
                return fields[0]
    return None


def cpu_model():
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                name = line.split(":", 1)[1].strip().replace("AMD ", "")
                return name.split(" with ")[0].rsplit(" ", 2)[0] if "-Core" in name else name
    return "CPU"


class Collector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cpu_temps = CpuTemps()
        self.gpus = Gpus()
        self.model = cpu_model()
        self.host = socket.gethostname()
        self.hist = {}
        self.hist_len = cfg["history"]
        self.net_prev = None
        psutil.cpu_percent(percpu=True)        # prime the per-thread counters

    def _push(self, key, value):
        self.hist.setdefault(key, deque(maxlen=self.hist_len)).append(value)

    def _net(self):
        iface = self.cfg["net_iface"]
        if iface == "auto":
            iface = default_iface()
        ip = "—"
        rx = tx = 0.0
        if iface:
            for a in psutil.net_if_addrs().get(iface, []):
                if a.family == socket.AF_INET:
                    ip = a.address
                    break
            c = psutil.net_io_counters(pernic=True).get(iface)
            now = time.monotonic()
            if c and self.net_prev and self.net_prev[0] == iface:
                dt = now - self.net_prev[1]
                rx = max(0, c.bytes_recv - self.net_prev[2].bytes_recv) / dt
                tx = max(0, c.bytes_sent - self.net_prev[2].bytes_sent) / dt
            self.net_prev = (iface, now, c) if c else None
        return {"iface": iface or "offline", "ip": ip, "rx_bps": rx, "tx_bps": tx}

    def sample(self):
        t = self.cpu_temps.read()
        threads = psutil.cpu_percent(percpu=True)
        freqs = psutil.cpu_freq(percpu=True)
        vm = psutil.virtual_memory()
        disks = []
        for dk in self.cfg["disks"]:
            try:
                u = psutil.disk_usage(dk["path"])
                disks.append({"label": dk["label"], "used_gb": u.used / 1e9, "total_gb": u.total / 1e9})
            except OSError:
                pass
        snap = {
            "time": time.strftime("%H:%M"),
            "host": self.host,
            "uptime_s": time.time() - psutil.boot_time(),
            "loadavg": os.getloadavg(),
            "cpu": {"model": self.model, "tctl": t["Tctl"],
                    "ccd": [t.get("Tccd1", t["Tctl"]), t.get("Tccd2", t["Tctl"])],
                    "clock_ghz": sum(f.current for f in freqs) / len(freqs) / 1000,
                    "load": sum(threads) / len(threads), "threads": threads},
            "gpus": self.gpus.read(),
            "mem": {"used": (vm.total - vm.available) / 2**30, "total": vm.total / 2**30},
            "disks": disks,
            "net": self._net(),
        }
        self._push("cpu_temp", snap["cpu"]["tctl"])
        for g in snap["gpus"]:
            self._push(f"gpu{g['index']}_temp", g["temp"])
        return snap


# ------------------------------------------------------------------ alert --
class Alerts:
    """Latching over-temperature alerts with hysteresis."""

    def __init__(self, cfg):
        a, th = cfg["alert"], cfg["thresholds"]
        self.cpu_limit = a.get("cpu_limit", th["cpu"][2])
        self.gpu_limit = a.get("gpu_limit", th["gpu"][2])
        self.hyst = a.get("hysteresis", 5)
        self.active = {}                                  # key -> since "HH:MM"

    def update(self, snap):
        """Return kwargs for page_alert for the worst active alert, or None."""
        cands = [("cpu", "CPU", snap["cpu"]["tctl"], self.cpu_limit, "Tctl")]
        multi = len(snap["gpus"]) > 1
        for g in snap["gpus"]:
            cands.append((f"gpu{g['index']}", f"GPU {g['index']}" if multi else "GPU", g["temp"],
                          self.gpu_limit, "clocks throttling" if g["throttling"] else "above limit"))
        worst = None
        for key, what, value, limit, detail in cands:
            if key in self.active:
                if value < limit - self.hyst:
                    log.info("alert cleared: %s %.0f °C", what, value)
                    del self.active[key]
            elif value >= limit:
                log.warning("alert: %s %.0f °C >= %.0f °C", what, value, limit)
                self.active[key] = time.strftime("%H:%M")
            if key in self.active and (worst is None or value - limit > worst["value"] - worst["limit"]):
                worst = {"what": what, "value": value, "limit": limit,
                         "clear_below": limit - self.hyst, "since": self.active[key], "detail": detail}
        return worst


# ---------------------------------------------------------------- rotator --
def expand_pages(cfg, gpu_count):
    out = []
    for e in cfg["pages"]:
        if e["page"] == "gpu":
            out += [("gpu", e["seconds"], i) for i in range(gpu_count)]
        else:
            out.append((e["page"], e["seconds"], None))
    return out


def render(name, arg, snap, hist, idx, count, cfg):
    if name == "overview":
        return pages.page_overview(snap, hist, idx, count)
    if name == "cpu":
        return pages.page_cpu(snap, hist, idx, count)
    if name == "gpu":
        return pages.page_gpu(snap, hist, arg, idx, count)
    if name == "system":
        return pages.page_system(snap, hist, idx, count)
    if name == "logo":
        text = {k: cfg[f"logo_{k}"] for k in ("title", "subtitle") if f"logo_{k}" in cfg}
        return pages.page_logo(snap, hist, cfg["logo"], idx, count, **text)
    raise ValueError(f"unknown page {name!r}")


def to_jpeg(img, cfg):
    if cfg["rotate"]:
        img = img.rotate(-cfg["rotate"])
    buf = BytesIO()
    img.save(buf, "JPEG", quality=cfg["jpeg_quality"])
    return buf.getvalue()


class Screen:
    """Owns the HID device: (re)connects, heartbeats, skips identical frames.

    The DE360 firmware drops off USB and re-enumerates when it receives no frame
    for about 8-11 s (the CONNECT heartbeat does not count), so an identical frame
    is skipped only while the last one went out less than `max_frame_gap` s ago.
    """

    def __init__(self, cfg):
        self.cfg, self.lcd, self.last_jpeg, self.last_beat = cfg, None, None, 0.0
        self.last_frame = 0.0
        self.max_gap = cfg.get("max_frame_gap", 1.5)

    def send(self, jpeg):
        try:
            if self.lcd is None:
                path = find_hidraw()
                if not path:
                    raise OSError("DE360 LCD (1800:3006) not found")
                self.lcd = Lcd(path)
                log.info("opened %s", path)
                self.lcd.wake()
                self.lcd.brightness(self.cfg["brightness"])
                self.last_jpeg = None
            now = time.monotonic()
            if now - self.last_beat >= 10:
                self.lcd.heartbeat()
                self.last_beat = now
            if jpeg != self.last_jpeg or now - self.last_frame >= self.max_gap:
                self.lcd.frame(jpeg)
                self.last_jpeg, self.last_frame = jpeg, now
            self.lcd.drain()
        except OSError as e:
            log.warning("LCD: %s", e)
            if self.lcd is not None:
                try:
                    self.lcd.close()
                except OSError:
                    pass
            self.lcd = None


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    base = os.path.dirname(os.path.abspath(path))
    logo = os.path.join(base, cfg.get("logo", "logo_lcd.png"))
    if not os.path.exists(logo):            # running from a git checkout: ../assets/
        here = os.path.dirname(os.path.realpath(__file__))
        logo = os.path.join(here, "..", "assets", os.path.basename(logo))
    cfg["logo"] = logo
    pages.CPU_T = tuple(cfg["thresholds"]["cpu"])
    pages.GPU_T = tuple(cfg["thresholds"]["gpu"])
    return cfg


def run(cfg):
    col, alerts, screen = Collector(cfg), Alerts(cfg), Screen(cfg)
    snap = alert = None
    page_i, page_until = -1, 0.0
    next_sample = last_frame = 0.0
    dirty = False
    while True:
        now = time.monotonic()
        if now >= next_sample:
            try:
                snap = col.sample()
                alert = alerts.update(snap)
                dirty = True
            except Exception as e:
                log.warning("sensor read failed: %s", e)
            next_sample = max(next_sample + cfg["sample_interval"], now)
        if snap is not None and alert is None and now >= page_until:
            plist = expand_pages(cfg, len(snap["gpus"]))
            page_i = (page_i + 1) % len(plist)
            page_until = now + plist[page_i][1]
            dirty = True
        if dirty and now - last_frame >= cfg["min_frame_interval"]:
            try:
                if alert:
                    img = pages.page_alert(snap, col.hist, **alert)
                else:
                    plist = expand_pages(cfg, len(snap["gpus"]))
                    page_i %= len(plist)
                    name, _, arg = plist[page_i]
                    img = render(name, arg, snap, col.hist, page_i, len(plist), cfg)
                screen.send(to_jpeg(img, cfg))
            except Exception:
                log.exception("rendering failed")
                page_until = 0.0                       # skip to the next page
            last_frame, dirty = now, False
        wake = [next_sample]
        if alert is None:
            wake.append(page_until)
        if dirty:
            wake.append(last_frame + cfg["min_frame_interval"])
        time.sleep(max(0.05, min(wake) - time.monotonic()))


def test_pattern():
    from PIL import ImageDraw
    w = h = pages.W
    img = Image.new("RGB", (w, h), "black")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w - 1, h - 1), outline=(255, 0, 0))
    d.rectangle((4, 4, w - 5, h - 5), outline=(0, 255, 0))
    d.ellipse((0, 0, w - 1, h - 1), outline=(0, 128, 255), width=2)
    for x, y, a in ((10, 10, "lt"), (w - 10, 10, "rt"), (10, h - 10, "lb"), (w - 10, h - 10, "rb")):
        d.text((x, y), "TL TR BL BR".split()[["lt", "rt", "lb", "rb"].index(a)], fill="white",
               anchor=a, font=pages.font("bold", 14).font_variant(size=28))
    d.text((w / 2, h / 2), f"{w}x{h}", fill="white", anchor="mm", font=pages.font("bold", 20).font_variant(size=48))
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.yaml"))
    ap.add_argument("--preview", metavar="DIR", help="render every page from live sensors into DIR and exit")
    ap.add_argument("--test-pattern", action="store_true", help="show a border/corner test image and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.test_pattern:
        Screen(cfg).send(to_jpeg(test_pattern(), cfg))
        return
    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        col = Collector(cfg)
        time.sleep(1)
        snap = col.sample()
        plist = expand_pages(cfg, len(snap["gpus"]))
        for i, (name, _, arg) in enumerate(plist):
            render(name, arg, snap, col.hist, i, len(plist), cfg).save(
                f"{args.preview}/{i + 1}_{name}{'' if arg is None else arg}.png")
        a = Alerts(cfg)
        pages.page_alert(snap, col.hist, what="CPU", value=snap["cpu"]["tctl"], limit=a.cpu_limit,
                         clear_below=a.cpu_limit - a.hyst, since=time.strftime("%H:%M"),
                         detail="Tctl").save(f"{args.preview}/9_alert.png")
        print("rendered", sorted(os.listdir(args.preview)))
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    run(cfg)


if __name__ == "__main__":
    main()
