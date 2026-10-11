#!/usr/bin/env python3
"""
LCD dashboard pages for the darkFlash DE360 screen (480x480).

Each page is a function  page(snap, hist) -> PIL.Image (RGB, 480x480).
`snap` is the latest sensor snapshot, `hist` holds short histories for
sparklines (~150 samples = 5 min at one per 2 s). The `mock()` function at
the bottom shows the exact schema the collectors must fill.

Thresholds, sizes and colors are constants near the top. The panel is 480x480
(verified with the --test-pattern of de360_dash.py: full square, no cropping).

This module only DRAWS. Collecting sensor data and sending frames to the
screen live in the existing service; call a page function, then hand the
image to the existing send routine.

Run `python3 lcd_pages.py` to render every page with mock data into ./out/.

Fonts: Inter (Ubuntu package `fonts-inter`), falls back to DejaVu.
"""
from __future__ import annotations

import math
import os
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- geometry --
W = H = 480            # native screen size (verified on the DE360 panel)
S = 2                  # supersampling factor (draw at 2x, downscale = smooth arcs)
M = 24                 # outer margin


def p(v: float) -> int:
    """Design units (480 grid) -> canvas pixels."""
    return int(round(v * S))


# ------------------------------------------------------------------ theme --
# Dark surface + ink from a validated dark data-viz palette.
BG = (16, 17, 20)
HAIR = (44, 44, 42)           # hairlines, gauge tracks
TRACK = (38, 39, 43)
INK = (240, 239, 236)         # primary text
INK2 = (195, 194, 183)        # secondary text
MUTED = (137, 135, 129)       # labels, axes

# Status palette: reserved for state, always shipped with a word or icon.
GOOD = (12, 163, 12)
WARN = (250, 178, 25)
SERIOUS = (236, 131, 90)
CRIT = (208, 59, 59)

# Sequential blue ramp (dark mode: near-zero recedes toward the surface).
SEQ = [(13, 54, 107), (24, 79, 149), (37, 106, 191), (57, 135, 229),
       (109, 167, 236), (158, 197, 244), (205, 226, 251)]

# Thresholds in degC: (warning, serious, critical). Tune in the service config.
CPU_T = (75, 85, 92)          # Ryzen 9000 runs to 95 by design
GPU_T = (75, 83, 88)          # RTX 4090 starts pulling clocks around 83

# Type scale (design px). The panel is ~52 mm wide, so 1 px = 0.11 mm:
# nothing below 18 px, or it stops being readable from a chair.
T_LABEL, T_SMALL, T_PILL, T_TITLE, T_VALUE = 18, 20, 18, 24, 34

FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu"]
_FONT_FILES = {
    "display": ["InterDisplay-Bold.otf", "DejaVuSans-Bold.ttf"],
    "bold": ["Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
    "semi": ["Inter-SemiBold.otf", "DejaVuSans-Bold.ttf"],
    "medium": ["Inter-Medium.otf", "DejaVuSans.ttf"],
    "regular": ["Inter-Regular.otf", "DejaVuSans.ttf"],
    "mono": ["DejaVuSansMono.ttf"],
}
_font_cache: dict = {}


def font(kind: str, size: float) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _font_cache:
        for name in _FONT_FILES[kind]:
            for d in FONT_DIRS:
                path = os.path.join(d, name)
                if os.path.exists(path):
                    _font_cache[key] = ImageFont.truetype(path, p(size))
                    break
            if key in _font_cache:
                break
        else:
            _font_cache[key] = ImageFont.load_default(p(size))
    return _font_cache[key]


# ---------------------------------------------------------------- helpers --
def blend(c, base=BG, a=0.25):
    return tuple(int(base[i] + (c[i] - base[i]) * a) for i in range(3))


def status(v, t):
    """-> (color, word) for a temperature against (warn, serious, crit)."""
    if v >= t[2]:
        return CRIT, "HOT"
    if v >= t[1]:
        return SERIOUS, "HIGH"
    if v >= t[0]:
        return WARN, "WARM"
    return GOOD, "NORMAL"


def seq_color(frac):
    frac = max(0.0, min(1.0, frac))
    pos = frac * (len(SEQ) - 1)
    i = min(int(pos), len(SEQ) - 2)
    f = pos - i
    a, b = SEQ[i], SEQ[i + 1]
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def new_canvas(bg=BG):
    img = Image.new("RGB", (p(W), p(H)), bg)
    return img, ImageDraw.Draw(img)


def finish(img):
    return img.resize((W, H), Image.LANCZOS)


def text(d, xy, s, f, fill=INK, anchor="ls"):
    d.text((p(xy[0]), p(xy[1])), s, font=f, fill=fill, anchor=anchor)


def text_w(s, f):
    l, _, r, _ = f.getbbox(s, anchor="ls")
    return (r - l) / S


def cap_h(f):
    return -f.getbbox("8", anchor="ls")[1] / S


def number_with_unit(d, cx, baseline, num, unit, fnum, funit, fill=INK, ufill=INK2,
                     align="center"):
    """Big number with a small top-aligned unit, e.g. 61 + °C."""
    w1, w2, gap = text_w(num, fnum), text_w(unit, funit), 4
    if align == "center":
        x = cx - (w1 + gap + w2) / 2
    elif align == "left":
        x = cx
    else:
        x = cx - (w1 + gap + w2)
    text(d, (x, baseline), num, fnum, fill)
    text(d, (x + w1 + gap, baseline - cap_h(fnum) + cap_h(funit)), unit, funit, ufill)


def round_rect(d, box, r, fill=None, outline=None, width=1):
    x0, y0, x1, y1 = box
    d.rounded_rectangle((p(x0), p(y0), p(x1), p(y1)), radius=p(r), fill=fill,
                        outline=outline, width=p(width) if outline else 0)


def bar(d, x, y, w, h, frac, color, track=TRACK):
    frac = max(0.0, min(1.0, frac))
    round_rect(d, (x, y, x + w, y + h), h / 2, fill=track)
    if frac > 0:
        fw = max(h, w * frac)
        round_rect(d, (x, y, x + fw, y + h), h / 2, fill=color)


def arc_gauge(d, cx, cy, r, width, frac, color, ticks=()):
    """270-degree gauge, opening at the bottom, rounded ends."""
    start, sweep = 135, 270
    box = (p(cx - r), p(cy - r), p(cx + r), p(cy + r))
    d.arc(box, start, start + sweep, fill=TRACK, width=p(width))
    for a in (start, start + sweep):          # rounded track ends
        _dot(d, cx, cy, r - width / 2, a, width / 2, TRACK)
    frac = max(0.0, min(1.0, frac))
    if frac > 0:
        end = start + sweep * frac
        d.arc(box, start, end, fill=color, width=p(width))
        _dot(d, cx, cy, r - width / 2, start, width / 2, color)
        _dot(d, cx, cy, r - width / 2, end, width / 2, color)
    for tf in ticks:                           # threshold ticks outside the arc
        a = math.radians(start + sweep * tf)
        r0, r1 = r + 3, r + 11
        d.line((p(cx + r0 * math.cos(a)), p(cy + r0 * math.sin(a)),
                p(cx + r1 * math.cos(a)), p(cy + r1 * math.sin(a))),
               fill=MUTED, width=p(2))


def _dot(d, cx, cy, r, ang, rad, color):
    a = math.radians(ang)
    x, y = cx + r * math.cos(a), cy + r * math.sin(a)
    d.ellipse((p(x - rad), p(y - rad), p(x + rad), p(y + rad)), fill=color)


def sparkline(d, x, y, w, h, vals, lo, hi, dot_color, guide=None):
    """Trend line in secondary ink; the last point carries the status color."""
    if len(vals) < 2:
        return
    span = max(hi - lo, 1e-6)

    def pt(i, v):
        return (x + w * i / (len(vals) - 1),
                y + h - h * (max(lo, min(hi, v)) - lo) / span)

    pts = [pt(i, v) for i, v in enumerate(vals)]
    poly = [(p(a), p(b)) for a, b in pts] + [(p(x + w), p(y + h)), (p(x), p(y + h))]
    d.polygon(poly, fill=blend(INK2, a=0.08))
    if guide is not None and lo < guide < hi:   # dashed warning line
        gy = pt(0, guide)[1]
        xx = x
        while xx < x + w:
            d.line((p(xx), p(gy), p(min(xx + 6, x + w)), p(gy)), fill=blend(WARN, a=0.55),
                   width=p(1.5))
            xx += 11
    d.line([(p(a), p(b)) for a, b in pts], fill=INK2, width=p(2), joint="curve")
    lx, ly = pts[-1]
    d.ellipse((p(lx - 6), p(ly - 6), p(lx + 6), p(ly + 6)), fill=BG)
    d.ellipse((p(lx - 4.5), p(ly - 4.5), p(lx + 4.5), p(ly + 4.5)), fill=dot_color)


def status_pill(d, x, y, color, word, center=False):
    """Colored dot + word: status never relies on color alone. y = baseline."""
    f = font("semi", T_PILL)
    tw = text_w(word, f)
    dot = 12
    total = dot + 7 + tw
    x0 = x - total / 2 if center else x
    cy = y - cap_h(f) / 2
    d.ellipse((p(x0), p(cy - dot / 2), p(x0 + dot), p(cy + dot / 2)), fill=color)
    text(d, (x0 + dot + 7, y), word, f, INK2)
    return total


def header(d, title, sub, snap, page_idx=None, page_count=None):
    ft = font("bold", T_TITLE)
    text(d, (M, 46), title, ft, INK)
    if sub:
        text(d, (M + text_w(title, ft) + 12, 46), sub, font("regular", T_SMALL), MUTED)
    text(d, (W - M, 46), snap["time"], font("medium", T_SMALL + 2), INK2, anchor="rs")
    d.line((p(M), p(62), p(W - M), p(62)), fill=HAIR, width=p(1))
    if page_idx is not None and page_count:
        n, gap = page_count, 16
        x0 = W / 2 - gap * (n - 1) / 2
        for i in range(n):
            r = 4 if i == page_idx else 3
            c = INK2 if i == page_idx else HAIR
            d.ellipse((p(x0 + i * gap - r), p(465 - r), p(x0 + i * gap + r), p(465 + r)), fill=c)


def stat(d, x, y, label, value, unit="", fv=T_VALUE, align="left"):
    """Uppercase label above a value. y = value baseline."""
    flab, fval, funit = font("medium", T_LABEL), font("semi", fv), font("medium", max(T_LABEL, fv * 0.55))
    anchor = "ls" if align == "left" else "rs"
    text(d, (x, y - cap_h(fval) - 12), label.upper(), flab, MUTED, anchor=anchor)
    if align == "left":
        text(d, (x, y), value, fval, INK)
        if unit:
            text(d, (x + text_w(value, fval) + 5, y), unit, funit, INK2)
    else:
        uw = text_w(unit, funit) + 5 if unit else 0
        text(d, (x - uw, y), value, fval, INK, anchor="rs")
        if unit:
            text(d, (x, y), unit, funit, INK2, anchor="rs")


def fmt_rate(bps):
    for u, k in (("GB/s", 1e9), ("MB/s", 1e6), ("kB/s", 1e3)):
        if bps >= k:
            return f"{bps / k:.1f}", u
    return f"{bps:.0f}", "B/s"


def fmt_size(gb):
    return (f"{gb / 1000:.2f}", "TB") if gb >= 1000 else (f"{gb:.0f}", "GB")


def fmt_uptime(s):
    d_, h = divmod(int(s) // 3600, 24)
    m = (int(s) // 60) % 60
    return f"{d_} d {h} h" if d_ else f"{h} h {m} min"


def _gauge_block(d, value, th, sublabel, cx=124, cy=176, r=96):
    """Shared left half of the CPU and GPU pages."""
    col, word = status(value, th)
    lo, hi = 20, 100
    arc_gauge(d, cx, cy, r, 16, (value - lo) / (hi - lo), col,
              ticks=[(t - lo) / (hi - lo) for t in (th[0], th[2])])
    number_with_unit(d, cx, cy + 24, f"{value:.0f}", "°C", font("display", 70), font("semi", 24))
    text(d, (cx, cy + 56), sublabel, font("medium", T_LABEL), MUTED, anchor="ms")
    status_pill(d, cx, cy + 102, col, word, center=True)
    return col


# ------------------------------------------------------------------ pages --
def page_overview(snap, hist, page_idx=None, page_count=None):
    """CPU + every GPU as rows: big temp, two stats, 5-min trend."""
    img, d = new_canvas()
    header(d, snap["host"], None, snap, page_idx, page_count)
    rows = [("CPU", snap["cpu"]["tctl"], CPU_T, hist["cpu_temp"],
             [("Load", f"{snap['cpu']['load']:.0f}", "%"),
              ("Clock", f"{snap['cpu']['clock_ghz']:.2f}", "GHz")])]
    for g in snap["gpus"]:
        rows.append((f"GPU {g['index']}" if len(snap["gpus"]) > 1 else "GPU", g["temp"], GPU_T,
                     hist.get(f"gpu{g['index']}_temp", []),
                     [("Util", f"{g['util']:.0f}", "%"), ("Power", f"{g['power']:.0f}", "W")]))
    top, bottom = 66, 450
    rh = (bottom - top) / len(rows)
    compact = len(rows) > 2
    big = 64 if compact else 90
    fv = 28 if compact else 32
    for i, (label, temp, th, series, stats) in enumerate(rows):
        y0 = top + i * rh
        if i:
            d.line((p(M), p(y0), p(W - M), p(y0)), fill=HAIR, width=p(1))
        col, word = status(temp, th)
        fl = font("semi", 22)
        lab_y = y0 + 36
        text(d, (M, lab_y), label, fl, INK2)
        status_pill(d, M + text_w(label, fl) + 12, lab_y, col, word)
        num_y = lab_y + 12 + cap_h(font("display", big))
        number_with_unit(d, M - 3, num_y, f"{temp:.0f}", "°C",
                         font("display", big), font("semi", big * 0.3), align="left")
        rx = 222 if compact else 206
        sy = lab_y + 2 + cap_h(font("semi", fv)) + (14 if compact else 22)
        for k, (lab, val, unit) in enumerate(stats):
            stat(d, rx + k * 128, sy, lab, val, unit, fv=fv)
        sp_top = sy + 12
        sp_h = (y0 + rh - 14) - sp_top
        if sp_h > 16:
            sparkline(d, rx, sp_top, W - M - rx, sp_h, series, 20, 100, col, guide=th[0])
    return finish(img)


def page_cpu(snap, hist, page_idx=None, page_count=None):
    c = snap["cpu"]
    img, d = new_canvas()
    header(d, "CPU", c["model"], snap, page_idx, page_count)
    _gauge_block(d, c["tctl"], CPU_T, "Tctl")
    x = 262
    stat(d, x, 130, "CCD1 · V-Cache", f"{c['ccd'][0]:.0f}", "°C", fv=36)
    stat(d, x, 206, "CCD2", f"{c['ccd'][1]:.0f}", "°C", fv=36)
    stat(d, x, 282, "Clock avg", f"{c['clock_ghz']:.2f}", "GHz", fv=36)

    # thread heatmap: two 4x4 blocks, one per chiplet
    gy = 322
    text(d, (M, gy), "THREADS", font("medium", T_LABEL), MUTED)
    text(d, (M + text_w("THREADS", font("medium", T_LABEL)) + 10, gy), f"{c['load']:.0f}% avg",
         font("semi", T_SMALL), INK2)
    fl = font("medium", T_LABEL)               # ramp legend: 0 .. 100 %
    lx = W - M - text_w("100%", fl) - 8 - 70
    text(d, (lx - 7, gy), "0", fl, MUTED, anchor="rs")
    for k in range(7):
        d.rectangle((p(lx + k * 10), p(gy - 13), p(lx + k * 10 + 9), p(gy)), fill=seq_color(k / 6))
    text(d, (W - M, gy), "100%", fl, MUTED, anchor="rs")
    loads = c["threads"]
    half = len(loads) // 2
    # Linux numbering on a 16-core SMT part: cores 0-15, then SMT siblings 16-31.
    # CCD1 = cores 0-7 + siblings 16-23 ; CCD2 = cores 8-15 + siblings 24-31.
    blocks = [list(range(0, 8)) + list(range(half, half + 8)),
              list(range(8, 16)) + list(range(half + 8, half + 16))]
    gap_b, gap_c = 20, 5
    bw = (W - 2 * M - gap_b) / 2
    cw, ch = (bw - 3 * gap_c) / 4, 19
    top = gy + 12
    for b, idxs in enumerate(blocks):
        bx = M + b * (bw + gap_b)
        for k, t in enumerate(idxs):
            rr, cc = divmod(k, 4)
            x0, y0 = bx + cc * (cw + gap_c), top + rr * (ch + gap_c)
            round_rect(d, (x0, y0, x0 + cw, y0 + ch), 4, fill=seq_color(loads[t] / 100))
        text(d, (bx, 450), ["CCD1", "CCD2"][b], font("medium", T_LABEL), MUTED)
    return finish(img)


def page_gpu(snap, hist, gpu_i=0, page_idx=None, page_count=None):
    g = snap["gpus"][gpu_i]
    img, d = new_canvas()
    title = f"GPU {g['index']}" if len(snap["gpus"]) > 1 else "GPU"
    header(d, title, g["name"], snap, page_idx, page_count)
    col = _gauge_block(d, g["temp"], GPU_T, "core")
    x = 262
    stat(d, x, 130, "Util", f"{g['util']:.0f}", "%", fv=36)
    stat(d, x, 206, "Clock", f"{g['clock_ghz']:.2f}", "GHz", fv=36)
    stat(d, x, 282, "Fan", f"{g['fan']:.0f}", "%", fv=36)

    def labeled_bar(y, label, value_txt, frac):
        text(d, (M, y), label, font("medium", T_LABEL), MUTED)
        text(d, (W - M, y), value_txt, font("semi", T_SMALL), INK, anchor="rs")
        bar(d, M, y + 10, W - 2 * M, 12, frac, SEQ[4])

    labeled_bar(322, "POWER", f"{g['power']:.0f} / {g['power_limit']:.0f} W", g["power"] / g["power_limit"])
    labeled_bar(374, "VRAM", f"{g['vram_used']:.1f} / {g['vram_total']:.0f} GB", g["vram_used"] / g["vram_total"])
    text(d, (M, 444), "5 MIN", font("medium", T_LABEL), MUTED)
    sparkline(d, M + 74, 412, W - 2 * M - 74, 36, hist.get(f"gpu{g['index']}_temp", []),
              20, 100, col, guide=GPU_T[0])
    return finish(img)


def page_system(snap, hist, page_idx=None, page_count=None):
    img, d = new_canvas()
    header(d, "System", None, snap, page_idx, page_count)
    text(d, (M, 114), snap["host"], font("bold", 40), INK)
    w = status_pill(d, M, 150, GOOD, "ONLINE")
    text(d, (M + w + 14, 150), f"up {fmt_uptime(snap['uptime_s'])}", font("medium", T_SMALL), INK2)
    la = snap["loadavg"]
    text(d, (W - M, 150), f"load {la[0]:.1f}", font("medium", T_SMALL), INK2, anchor="rs")

    rows = [("RAM", snap["mem"]["used"], snap["mem"]["total"])]
    rows += [(dk["label"], dk["used_gb"], dk["total_gb"]) for dk in snap["disks"]]
    y = 196
    for lab, used, total in rows[:4]:
        uv, uu = fmt_size(used)
        tv, tu = fmt_size(total)
        val = f"{uv} {uu} / {tv} {tu}" if uu != tu else f"{uv} / {tv} {tu}"
        text(d, (M, y), lab, font("semi", T_SMALL), INK)
        text(d, (W - M, y), val, font("medium", T_SMALL), INK2, anchor="rs")
        frac = used / total
        bar(d, M, y + 9, W - 2 * M, 10, frac, SEQ[4] if frac < 0.85 else WARN)
        y += 52
    d.line((p(M), p(386), p(W - M), p(386)), fill=HAIR, width=p(1))
    n = snap["net"]
    text(d, (M, 418), n["iface"], font("medium", T_LABEL), MUTED)
    text(d, (W - M, 418), n["ip"], font("mono", T_SMALL), INK, anchor="rs")
    rv, ru = fmt_rate(n["rx_bps"])
    tv, tu = fmt_rate(n["tx_bps"])
    text(d, (M, 448), "traffic", font("medium", T_LABEL), MUTED)
    text(d, (W - M, 448), f"↓ {rv} {ru}    ↑ {tv} {tu}", font("medium", T_SMALL), INK2, anchor="rs")
    return finish(img)


def page_logo(snap, hist, logo_path="logo.png", page_idx=None, page_count=None,
              title="MURGILDU", subtitle="3D Graphics Laboratory · EHU"):
    img, d = new_canvas()
    box = 250
    x0, y0 = (W - box) / 2, 64
    if logo_path and os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((p(box), p(box)), Image.LANCZOS)
        img.paste(logo, (p(W / 2) - logo.width // 2, p(y0 + box / 2) - logo.height // 2), logo)
    else:                                       # placeholder slot for the mockup
        _dashed_round_rect(d, (x0, y0, x0 + box, y0 + box), 28, MUTED)
        text(d, (W / 2, y0 + box / 2 - 2), "lab logo", font("semi", 24), INK2, anchor="ms")
        text(d, (W / 2, y0 + box / 2 + 28), "logo.png", font("mono", T_LABEL), MUTED, anchor="ms")
    f = font("bold", 40)
    word, spacing = title, 7
    total = sum(text_w(ch, f) for ch in word) + spacing * (len(word) - 1)
    x = W / 2 - total / 2
    for ch in word:
        text(d, (x, 384), ch, f, INK)
        x += text_w(ch, f) + spacing
    text(d, (W / 2, 420), subtitle, font("regular", T_SMALL), INK2, anchor="ms")
    return finish(img)


def _dashed_round_rect(d, box, r, color):
    x0, y0, x1, y1 = box
    dash, gap = 10, 8
    for a0, a1, fixed, horiz in ((x0 + r, x1 - r, y0, True), (x0 + r, x1 - r, y1, True),
                                 (y0 + r, y1 - r, x0, False), (y0 + r, y1 - r, x1, False)):
        t = a0
        while t < a1:
            e = min(t + dash, a1)
            seg = (p(t), p(fixed), p(e), p(fixed)) if horiz else (p(fixed), p(t), p(fixed), p(e))
            d.line(seg, fill=color, width=p(2))
            t += dash + gap
    for (cx, cy, st) in ((x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270),
                         (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90)):
        d.arc((p(cx - r), p(cy - r), p(cx + r), p(cy + r)), st + 20, st + 70, fill=color, width=p(2))


def page_alert(snap, hist, what="GPU 0", value=91.0, limit=88.0, clear_below=83.0,
               since="11:42", detail="clocks throttling"):
    """Pinned while a threshold is exceeded; rotation pauses."""
    bg = (34, 12, 13)
    img, d = new_canvas(bg)
    d.rectangle((0, 0, p(W), p(10)), fill=CRIT)
    cx, ty, s = W / 2, 54, 78                  # warning triangle
    tri = [(cx, ty), (cx + s / 2, ty + s * 0.87), (cx - s / 2, ty + s * 0.87)]
    d.polygon([(p(a), p(b)) for a, b in tri], fill=CRIT)
    text(d, (cx, ty + s * 0.87 - 9), "!", font("bold", 44), bg, anchor="ms")
    text(d, (W / 2, 184), f"{what} too hot".upper(), font("bold", 28), INK, anchor="ms")
    number_with_unit(d, W / 2, 310, f"{value:.0f}", "°C", font("display", 128), font("semi", 36))
    text(d, (W / 2, 356), f"Limit {limit:.0f} °C · {detail}", font("medium", T_SMALL), INK2, anchor="ms")
    text(d, (W / 2, 386), f"Since {since}", font("medium", T_SMALL), INK2, anchor="ms")
    d.line((p(M), p(410), p(W - M), p(410)), fill=blend(CRIT, bg, 0.5), width=p(1))
    text(d, (W / 2, 444), f"Rotation paused until < {clear_below:.0f} °C", font("medium", T_LABEL),
         MUTED, anchor="ms")
    return finish(img)


# -------------------------------------------------------------- mock data --
def _history(start, plateau, n=150, rise=40, noise=0.8, seed=1):
    import random
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        base = start + (plateau - start) * (1 - math.exp(-i / rise))
        out.append(base + rnd.uniform(-noise, noise))
    return out


def mock(two_gpus=False):
    import random
    rnd = random.Random(7)
    threads = [rnd.uniform(4, 22) for _ in range(32)]
    for t in (0, 1, 2, 3, 4, 5, 16, 17, 18, 19, 20, 21):   # dataloader workers on CCD1
        threads[t] = rnd.uniform(62, 98)
    for t in (8, 9, 24):
        threads[t] = rnd.uniform(35, 55)
    snap = {
        "time": "11:24",
        "host": "workstation",
        "uptime_s": 2 * 86400 + 14 * 3600 + 7 * 60,
        "loadavg": [6.2, 5.8, 4.1],
        "cpu": {"model": "Ryzen 9 9950X3D", "tctl": 61, "ccd": [58, 52], "clock_ghz": 5.08,
                "load": sum(threads) / len(threads), "threads": threads},
        "gpus": [{"index": 0, "name": "RTX 4090", "temp": 71, "util": 98, "power": 412,
                  "power_limit": 450, "vram_used": 18.2, "vram_total": 24.0, "fan": 64,
                  "clock_ghz": 2.61}],
        "mem": {"used": 21.6, "total": 62.0},
        "disks": [{"label": "/home", "used_gb": 183, "total_gb": 3480},
                  {"label": "scratch1", "used_gb": 1310, "total_gb": 1780},
                  {"label": "scratch2", "used_gb": 93, "total_gb": 1780}],
        "net": {"iface": "enp5s0", "ip": "192.168.1.42", "rx_bps": 1.24e6, "tx_bps": 8.4e4},
    }
    hist = {"cpu_temp": _history(34, 61, seed=2), "gpu0_temp": _history(36, 71, seed=3)}
    if two_gpus:
        snap["gpus"].append({"index": 1, "name": "RTX 4090", "temp": 77, "util": 97, "power": 418,
                             "power_limit": 450, "vram_used": 18.4, "vram_total": 24.0, "fan": 71,
                             "clock_ghz": 2.58})
        hist["gpu1_temp"] = _history(36, 77, seed=4)
    return snap, hist


if __name__ == "__main__":
    os.makedirs("out", exist_ok=True)
    snap, hist = mock()
    pages = ["overview", "cpu", "gpu", "system", "logo"]
    n = len(pages)
    page_overview(snap, hist, 0, n).save("out/1_overview.png")
    page_cpu(snap, hist, 1, n).save("out/2_cpu.png")
    page_gpu(snap, hist, 0, 2, n).save("out/3_gpu.png")
    page_system(snap, hist, 3, n).save("out/4_system.png")
    page_logo(snap, hist).save("out/5_logo.png")
    page_alert(snap, hist).save("out/6_alert.png")
    snap2, hist2 = mock(two_gpus=True)
    page_overview(snap2, hist2, 0, n + 1).save("out/7_overview_2gpu.png")
    print("rendered:", sorted(os.listdir("out")))
