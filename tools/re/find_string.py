#!/usr/bin/env python3
"""Print the virtual address of every ASCII string containing TEXT, so it can be
grepped for in objdump output (`grep '# 0x<va>' dfs.asm` = code that uses it).

  python3 find_string.py DFSpaceStation.exe DEDeviceOperation
"""
import re
import sys

from pe import PE


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pe = PE(sys.argv[1])
    d = pe.data
    for m in re.finditer(re.escape(sys.argv[2].encode()), d):
        st = m.start()
        while st > 0 and 32 <= d[st - 1] < 127:
            st -= 1
        rva = pe.off2rva(st)
        if rva is not None:
            print(f"{pe.base + rva:#x}  {pe.cstr(st)[:120]}")


if __name__ == "__main__":
    main()
