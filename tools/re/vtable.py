#!/usr/bin/env python3
"""Find the vtable of an MSVC C++ class through its RTTI and list its slots.

  python3 vtable.py DFSpaceStation.exe DEDeviceOperation
  python3 vtable.py SDLibrary1.dll SDDevice          # exported slots get their names

Chain: ".?AV<Class>@@" type descriptor -> Complete Object Locator (signature 1,
self-RVA check) -> the pointer to the COL sits right before the vtable.
Comparing the vtables of two sibling classes shows which methods a class overrides.
"""
import re
import struct
import sys

from pe import PE


def vtables(pe, name):
    d = pe.data
    td_rva = pe.off2rva(d.index(f".?AV{name}@@".encode()) - 0x10)
    for m in re.finditer(re.escape(struct.pack("<I", td_rva)), d):
        col = m.start() - 12
        sig, off, _cd, _td, _chd, self_rva = struct.unpack_from("<6I", d, col)
        if sig != 1 or self_rva != pe.off2rva(col):
            continue
        for v in re.finditer(re.escape(struct.pack("<Q", pe.base + self_rva)), d):
            vt, slots = v.start() + 8, []
            while True:
                f = struct.unpack_from("<Q", d, vt + 8 * len(slots))[0]
                if not pe.base < f < pe.base + 0x8000000:
                    break
                slots.append(f)
            yield off, pe.base + pe.off2rva(vt), slots


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pe = PE(sys.argv[1])
    for off, va, slots in vtables(pe, sys.argv[2]):
        print(f"{sys.argv[2]}  this-offset {off}  vtable {va:#x}")
        for i, f in enumerate(slots):
            print(f"  [{i:3}] +{8 * i:#05x}  {f:#x}  {pe.exports.get(f, '')}")


if __name__ == "__main__":
    main()
