"""Minimal PE32+ reader used by the reverse-engineering helpers (no third-party deps).

Gives: image base, VA <-> file offset, import table (IAT slot VA -> 'dll!symbol'),
export table (VA -> symbol) and printable strings at a VA.
"""
import struct


class PE:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = d = f.read()
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        nsec = struct.unpack_from("<H", d, pe + 6)[0]
        optsz = struct.unpack_from("<H", d, pe + 20)[0]
        self.opt = opt = pe + 24
        self.base = struct.unpack_from("<Q", d, opt + 24)[0]
        self.sections = []                                   # (rva, raw_size, raw_offset)
        for i in range(nsec):
            o = opt + optsz + 40 * i
            _vs, va, rs, ro = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((va, rs, ro))
        self._imports = self._exports = None

    # -- addressing --------------------------------------------------------
    def rva2off(self, rva):
        for va, rs, ro in self.sections:
            if va <= rva < va + rs:
                return ro + rva - va
        return None

    def off2rva(self, off):
        for va, rs, ro in self.sections:
            if ro <= off < ro + rs:
                return va + off - ro
        return None

    def cstr(self, off):
        return self.data[off:self.data.index(b"\0", off)].decode("latin1")

    # -- tables ------------------------------------------------------------
    @property
    def imports(self):
        if self._imports is None:
            d, imp = self.data, {}
            o = self.rva2off(struct.unpack_from("<I", d, self.opt + 112 + 8)[0])
            while o is not None:
                oft, _, _, name, ft = struct.unpack_from("<IIIII", d, o)
                o += 20
                if not ft:
                    break
                dll, t, k = self.cstr(self.rva2off(name)), self.rva2off(oft or ft), 0
                while True:
                    e = struct.unpack_from("<Q", d, t + 8 * k)[0]
                    if not e:
                        break
                    sym = f"#{e & 0xFFFF}" if e >> 63 else self.cstr(self.rva2off(e & 0x7FFFFFFF) + 2)
                    imp[self.base + ft + 8 * k] = f"{dll}!{sym}"
                    k += 1
            self._imports = imp
        return self._imports

    @property
    def exports(self):
        if self._exports is None:
            d, exp = self.data, {}
            rva = struct.unpack_from("<I", d, self.opt + 112)[0]
            if rva:
                e = self.rva2off(rva)
                _nf, nn, af, an, ao = struct.unpack_from("<IIIII", d, e + 20)
                for i in range(nn):
                    n = struct.unpack_from("<I", d, self.rva2off(an) + 4 * i)[0]
                    ordi = struct.unpack_from("<H", d, self.rva2off(ao) + 2 * i)[0]
                    f = struct.unpack_from("<I", d, self.rva2off(af) + 4 * ordi)[0]
                    exp[self.base + f] = self.cstr(self.rva2off(n))
            self._exports = exp
        return self._exports

    def describe(self, va):
        """Import name, or a printable ASCII / UTF-16 string at `va`, else None."""
        if va in self.imports:
            return self.imports[va]
        off = self.rva2off(va - self.base) if va >= self.base else None
        if off is None:
            return None
        chunk = self.data[off:off + 200]
        a = chunk.split(b"\0")[0]
        if len(a) >= 2 and all(32 <= c < 127 or c in (9, 10, 13) for c in a):
            return repr(a.decode())
        if len(chunk) > 3 and chunk[1] == 0 and 32 <= chunk[0] < 127:
            u = chunk.decode("utf-16le", "ignore").split("\0")[0]
            if len(u) >= 2:
                return "L" + repr(u)
        return None
