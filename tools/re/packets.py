#!/usr/bin/env python3
"""Recover HID packet templates from SDLibrary1.dll.

  objdump -d --no-show-raw-insn -M intel SDLibrary1.dll > sdl.asm
  python3 packets.py SDLibrary1.dll sdl.asm | grep -A1 @SDDevice@@

The vendor builds every packet byte by byte with QByteArray::operator[](idx) = val,
which compiles to `mov r8d,<idx>` ... `mov dl,<val>` (or `movzx edx,<reg>` for a
runtime value). This walks each exported function and prints idx:value pairs, e.g.

  ?sendHeartbeatPack@SDDevice@@UEAAXXZ
      0:<BYTE> 1:C 2:R 3:T 6:C 7:O 8:N 9:N 10:E 11:C 12:T

`send*` functions put the HID report ID at index 0; `add*`/`get*` ones don't.
"""
import re
import sys

from pe import PE


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    starts = PE(sys.argv[1]).exports
    out, cur, idx = {}, None, None
    with open(sys.argv[2]) as f:
        for line in f:
            m = re.match(r"\s+([0-9a-f]+):\s+(.*)", line)
            if not m:
                continue
            a, ins = int(m.group(1), 16), m.group(2)
            if a in starts:
                cur, idx = starts[a], None
                out.setdefault(cur, [])
            if cur is None:
                continue
            if (mm := re.match(r"mov\s+r8d,0x([0-9a-f]+)$", ins)):
                idx = int(mm.group(1), 16)
            elif re.match(r"xor\s+r8d,r8d", ins):
                idx = 0
            elif idx is not None and (mm := re.match(r"mov\s+dl,0x([0-9a-f]+)$", ins)):
                v = int(mm.group(1), 16)
                out[cur].append((idx, chr(v) if 32 < v < 127 else hex(v)))
                idx = None
            elif idx is not None and re.match(r"xor\s+edx,edx", ins):
                out[cur].append((idx, "0"))
                idx = None
            elif idx is not None and (mm := re.match(r"movzx\s+edx,(\S+)", ins)):
                out[cur].append((idx, f"<{mm.group(1)}>"))
                idx = None
    for name, pairs in out.items():
        if any(len(v) == 1 and v.isalpha() for _, v in pairs):
            print(name)
            print("   ", " ".join(f"{i}:{v}" for i, v in pairs[:60]))


if __name__ == "__main__":
    main()
