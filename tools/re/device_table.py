#!/usr/bin/env python3
"""List the USB VID:PID -> device-type table compiled into DFSpaceStation.exe.

  objdump -d --no-show-raw-insn -M intel DFSpaceStation.exe > dfs.asm
  python3 device_table.py DFSpaceStation.exe dfs.asm

The table is built inline as { QString type; int vid; int pid; } entries:
  lea rcx,[rip+..] ; "D92"      <- QString::fromAscii_helper(name, len)
  ...
  mov DWORD PTR [rbp+0x108],0x1800   <- vid
  mov DWORD PTR [rbp+0x10c],0x3012   <- pid
Each type name maps to a C++ class "<type>DeviceOperation" (the DE360 is "DE").
"""
import re
import sys

from pe import PE


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pe = PE(sys.argv[1])
    # keep only names that have a "<name>DeviceOperation" class (drops unrelated strings)
    classes = set(re.findall(rb"\.\?AV(\w+)DeviceOperation@@", pe.data))
    name, imms = None, []
    seen = set()
    with open(sys.argv[2]) as f:
        for line in f:
            if (m := re.search(r"lea\s+rcx,\[rip\+0x[0-9a-f]+\]\s+# (0x[0-9a-f]+)", line)):
                s = pe.describe(int(m.group(1), 16))
                name, imms = (s.strip("'") if s and s.startswith("'") else None), []
            elif name and (m := re.search(r"mov\s+DWORD PTR \[rbp[+-]0x[0-9a-f]+\],0x([0-9a-f]+)$", line)):
                imms.append(int(m.group(1), 16))
                if len(imms) == 2:
                    vid, pid = imms
                    if (name.encode() in classes and 0 < vid < 0x10000 and 0 < pid < 0x10000
                            and (name, vid, pid) not in seen):
                        seen.add((name, vid, pid))
                        print(f"{vid:04x}:{pid:04x}  {name}")
                    name = None


if __name__ == "__main__":
    main()
