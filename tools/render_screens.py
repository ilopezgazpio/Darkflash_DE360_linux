#!/usr/bin/env python3
"""Render every dashboard page from the mock snapshot in lcd_pages.mock() into
docs/screens/ (the images used in the README). No hardware or sensors needed.

  python3 tools/render_screens.py [OUTDIR]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import lcd_pages as P  # noqa: E402


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screens")
    os.makedirs(out, exist_ok=True)
    snap, hist = P.mock()
    n = 5
    shots = {
        "1_overview.png": P.page_overview(snap, hist, 0, n),
        "2_cpu.png": P.page_cpu(snap, hist, 1, n),
        "3_gpu.png": P.page_gpu(snap, hist, 0, 2, n),
        "4_system.png": P.page_system(snap, hist, 3, n),
        "5_logo.png": P.page_logo(snap, hist, os.path.join(ROOT, "assets", "logo_lcd.png")),
        "6_alert.png": P.page_alert(snap, hist),
    }
    snap2, hist2 = P.mock(two_gpus=True)
    shots["7_overview_2gpu.png"] = P.page_overview(snap2, hist2, 0, n + 1)
    for name, img in shots.items():
        img.save(os.path.join(out, name), optimize=True)
    print("rendered", ", ".join(shots), "->", out)


if __name__ == "__main__":
    main()
