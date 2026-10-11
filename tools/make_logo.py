#!/usr/bin/env python3
"""Turn a single-colour logo on a white background into a transparent PNG that
sits cleanly on the dashboard's dark logo slide.

  python3 tools/make_logo.py assets/logo_source.png assets/logo_lcd.png             # drop the wordmark
  python3 tools/make_logo.py assets/logo_source.png assets/logo_lcd.png --keep-text

* Alpha comes from each pixel's distance to white relative to the ink colour, so
  anti-aliased edges stay smooth; every visible pixel is repainted in the ink colour.
* The ink colour is the most common clearly non-white colour (override with --color).
* By default the last block of rows separated by an empty gap (usually a
  wordmark under the mark) is cut off, because the slide draws the title in Inter.
"""
import argparse
from collections import Counter

from PIL import Image


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--color", help="ink colour as RRGGBB (default: auto)")
    ap.add_argument("--keep-text", action="store_true", help="keep the bottom wordmark")
    a = ap.parse_args()

    im = Image.open(a.src).convert("RGB")
    w, h = im.size
    px = list(im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata())
    if a.color:
        ink = tuple(int(a.color[i:i + 2], 16) for i in (0, 2, 4))
    else:
        ink = Counter(c for c in px if sum(255 - v for v in c) > 150).most_common(1)[0][0]
    dref = sum((255 - v) ** 2 for v in ink) ** 0.5
    alpha = []
    for c in px:
        v = round(255 * sum((255 - x) ** 2 for x in c) ** 0.5 / dref)
        alpha.append(0 if v < 10 else min(255, v))

    bottom = h
    if not a.keep_text:
        rows = [any(alpha[y * w + x] > 40 for x in range(w)) for y in range(h)]
        blocks, start = [], None
        for y, r in enumerate(rows + [False]):
            if r and start is None:
                start = y
            if not r and start is not None:
                blocks.append((start, y))
                start = None
        if len(blocks) > 1:
            bottom = blocks[-2][1]

    out = Image.new("RGBA", (w, h), ink + (0,))
    mask = Image.new("L", (w, h))
    mask.putdata(alpha)
    out.putalpha(mask)
    out = out.crop((0, 0, w, bottom))
    out = out.crop(out.getchannel("A").getbbox())
    out.save(a.dst)
    print(f"ink #{''.join(f'{v:02x}' for v in ink)}, saved {out.size[0]}x{out.size[1]} -> {a.dst}")


if __name__ == "__main__":
    main()
