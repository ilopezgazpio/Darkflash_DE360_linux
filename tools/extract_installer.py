#!/usr/bin/env python3
"""Extract the payload of an Advanced Installer self-extracting EXE (no Windows needed).

Usage:  python3 extract_installer.py darkFlash_DE240-DE360_Driver.exe OUTDIR

Layout found in the darkFlash installer (Advanced Installer 15.x bootstrapper):

  [PE stub][embedded files ...][file table][footer "...ADVINSTSFX"][Authenticode cert]

Footer, counted back from the 'ADVINSTSFX' magic:
  magic - 0x38  u32  number of files
  magic - 0x2c  u32  absolute file offset of the file table
Each file-table entry:
  u32 a, u32 b, u32 flags, u32 size, u32 offset, u32 name_len, UTF-16LE name[name_len]
  flags & 2  ->  the first 0x200 bytes are XOR 0xFF (the MSI and the CAB use this)

The MSI holds the install logic, the CAB (DF-Space-Station-Installer_Windows1.cab)
holds the application files; unpack it with `7z x file.cab`.
"""
import os
import struct
import sys


def entries(data):
    magic = data.rfind(b"ADVINSTSFX")
    if magic < 0:
        sys.exit("not an Advanced Installer SFX (no ADVINSTSFX footer)")
    count = struct.unpack_from("<I", data, magic - 0x38)[0]
    pos = struct.unpack_from("<I", data, magic - 0x2C)[0]
    for _ in range(count):
        _a, _b, flags, size, offset, name_len = struct.unpack_from("<6I", data, pos)
        pos += 24
        name = data[pos:pos + 2 * name_len].decode("utf-16-le")
        pos += 2 * name_len
        yield name, flags, size, offset


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    for name, flags, size, offset in entries(data):
        blob = data[offset:offset + size]
        if flags & 2:
            blob = bytes(b ^ 0xFF for b in blob[:0x200]) + blob[0x200:]
        safe = os.path.basename(name.replace("\\", "/"))
        with open(os.path.join(out, safe), "wb") as f:
            f.write(blob)
        print(f"{size:>11}  flags={flags}  {safe}")


if __name__ == "__main__":
    main()
