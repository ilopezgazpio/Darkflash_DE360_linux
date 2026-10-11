#!/usr/bin/env python3
"""Annotate `objdump -d -M intel` output with the strings and imports it references.

  objdump -d --no-show-raw-insn -M intel DFSpaceStation.exe > dfs.asm
  python3 annotate.py DFSpaceStation.exe dfs.asm 0x14003a1e0 0x14003a330

Every rip-relative reference (`# 0x14...`) gets `;; 'string'` or `;; dll!symbol`.
"""
import re
import sys

from pe import PE


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    pe = PE(sys.argv[1])
    lo, hi = int(sys.argv[3], 16), int(sys.argv[4], 16)
    with open(sys.argv[2]) as f:
        for line in f:
            m = re.match(r"\s+([0-9a-f]+):", line)
            if not m:
                continue
            a = int(m.group(1), 16)
            if a < lo:
                continue
            if a > hi:
                break
            ref = re.search(r"# (0x[0-9a-f]+)", line)
            desc = pe.describe(int(ref.group(1), 16)) if ref else None
            print(line.rstrip() + (f"   ;; {desc}" if desc else ""))


if __name__ == "__main__":
    main()
