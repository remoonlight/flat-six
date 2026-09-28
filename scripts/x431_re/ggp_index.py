"""GGP directory parse. No vendor blobs; CLI supplies the GGP path."""
from __future__ import annotations

import struct

try:
    from .rijndael256 import decrypt_32
except ImportError:
    from rijndael256 import decrypt_32


def checksum_ok(buf: bytes) -> bool:
    if len(buf) < 2:
        return False
    return (sum(buf[:-1]) & 0xFF) == buf[-1]


def parse_header(raw: bytes) -> dict:
    if len(raw) < 12:
        raise ValueError("truncated ggp")
    if raw[:3] != b"GGP":
        raise ValueError("magic")
    hdr_size = struct.unpack_from("<I", raw, 7)[0]
    if hdr_size < 13 or hdr_size > len(raw):
        raise ValueError(f"hdr_size {hdr_size}")
    hdr = raw[:hdr_size]
    if not checksum_ok(hdr):
        raise ValueError(f"checksum fail size={hdr_size}")
    count = hdr[0xB]
    dir_off = 0x0C
    stride = 0x48
    if count < 1 or dir_off + count * stride > hdr_size:
        raise ValueError(f"directory overflow count={count}")
    entries = []
    for i in range(count):
        base = dir_off + i * stride
        rec = hdr[base : base + stride]
        name = decrypt_32(rec[:32])
        nul = name.find(b"\x00")
        name_s = name[: nul if nul >= 0 else 32]
        off, sz = struct.unpack_from("<II", rec, 0x40)
        if off > len(raw) or sz > len(raw) or off + sz > len(raw):
            raise ValueError(f"gag range {off}+{sz}")
        entries.append(
            {
                "i": i,
                "name_ascii": name_s.decode("latin1", "replace"),
                "offset": off,
                "size": sz,
            }
        )
    return {"hdr_size": hdr_size, "count": count, "entries": entries, "file_size": len(raw)}
