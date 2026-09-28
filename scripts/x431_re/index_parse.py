#!/usr/bin/env python3
"""Parse first-level YZJM name/offset indexes; reject truncated/malformed."""
from __future__ import annotations

import struct


def parse_name_offset_index(data: bytes, extra_u8: bool, label: str) -> dict:
    if len(data) < 1:
        raise ValueError(f"{label}: truncated count")
    count = data[0]
    if count == 0:
        raise ValueError(f"{label}: empty index")
    off = 1
    extra = None
    if extra_u8:
        if len(data) < 2:
            raise ValueError(f"{label}: truncated extra")
        extra = data[1]
        off = 2
    entries = []
    for i in range(count):
        if off + 2 > len(data):
            raise ValueError(f"{label}: truncated len at entry {i}")
        nlen = struct.unpack_from("<H", data, off)[0]
        off += 2
        if nlen < 1 or nlen > 256:
            raise ValueError(f"{label}: bad nlen {nlen} at entry {i}")
        if off + nlen + 4 > len(data):
            raise ValueError(f"{label}: truncated name/off at entry {i}")
        raw_name = data[off : off + nlen]
        if b"\x00" not in raw_name:
            raise ValueError(f"{label}: name not NUL-terminated at entry {i}")
        name = raw_name.split(b"\x00", 1)[0].decode("ascii")
        off += nlen
        rec_off = struct.unpack_from("<I", data, off)[0]
        off += 4
        entries.append({"i": i, "name": name, "nlen": nlen, "off": rec_off})
    return {
        "kind": "u8_count_u16len_cstr_u32off",
        "extra_u8": extra,
        "count": count,
        "consumed": off,
        "entries": entries,
        "label": label,
    }


def parse_dsn_index(data: bytes) -> dict:
    """u8 count, then N x (u8 id, u16le nlen, cstr, u32le off)."""
    if len(data) < 1:
        raise ValueError("DSN: truncated count")
    count = data[0]
    if count == 0:
        raise ValueError("DSN: empty index")
    off = 1
    entries = []
    for i in range(count):
        if off + 3 > len(data):
            raise ValueError(f"DSN: truncated id/len at {i}")
        eid = data[off]
        off += 1
        nlen = struct.unpack_from("<H", data, off)[0]
        off += 2
        if nlen < 1 or nlen > 256:
            raise ValueError(f"DSN: bad nlen {nlen} at {i}")
        if off + nlen + 4 > len(data):
            raise ValueError(f"DSN: truncated name at {i}")
        raw_name = data[off : off + nlen]
        if b"\x00" not in raw_name:
            raise ValueError(f"DSN: name not NUL-terminated at {i}")
        name = raw_name.split(b"\x00", 1)[0].decode("ascii")
        off += nlen
        rec_off = struct.unpack_from("<I", data, off)[0]
        off += 4
        entries.append({"i": i, "id": eid, "name": name, "nlen": nlen, "off": rec_off})
    return {
        "kind": "u8_count_u8id_u16len_cstr_u32off",
        "count": count,
        "consumed": off,
        "entries": entries,
        "label": "DSN",
    }


def parse_9x1_index(data: bytes) -> dict:
    return parse_name_offset_index(data, extra_u8=False, label="9X1")


def parse_l2_u16unk_id_name_off(data: bytes, start: int, limit: int) -> dict:
    """DSN family block: repeat u16le unk, u8 id, u16le nlen, cstr, u32le off until limit."""
    off = start
    entries = []
    while off + 9 <= limit:
        unk = struct.unpack_from("<H", data, off)[0]
        eid = data[off + 2]
        nlen = struct.unpack_from("<H", data, off + 3)[0]
        if nlen < 1 or nlen > 128 or off + 9 + nlen > limit:
            break
        raw = data[off + 5 : off + 5 + nlen]
        if b"\x00" not in raw:
            break
        try:
            name = raw.split(b"\x00", 1)[0].decode("ascii")
        except UnicodeDecodeError:
            break
        rec_off = struct.unpack_from("<I", data, off + 5 + nlen)[0]
        entries.append({"unk": unk, "id": eid, "name": name, "off": rec_off, "at": off})
        off += 9 + nlen
    if not entries:
        raise ValueError("empty L2")
    return {"kind": "u16unk_u8id_u16len_cstr_u32off", "count": len(entries), "consumed": off - start, "entries": entries}


def parse_l2_u32count_id_name_off_extra(data: bytes, start: int) -> dict:
    """9X1 ECU block: u32le count, u8 id0, then N x (u16le nlen, cstr, u32le off, u32le pad)."""
    if start + 5 > len(data):
        raise ValueError("truncated L2 count")
    count = struct.unpack_from("<I", data, start)[0]
    if count < 1 or count > 4096:
        raise ValueError(f"bad L2 count {count}")
    eid0 = data[start + 4]
    off = start + 5
    entries = []
    for i in range(count):
        if off + 10 > len(data):
            raise ValueError(f"truncated L2 entry {i}")
        nlen = struct.unpack_from("<H", data, off)[0]
        if nlen < 1 or nlen > 128 or off + 2 + nlen + 8 > len(data):
            raise ValueError(f"bad L2 nlen {nlen} at {i}")
        raw = data[off + 2 : off + 2 + nlen]
        if b"\x00" not in raw:
            raise ValueError(f"L2 name not NUL at {i}")
        name = raw.split(b"\x00", 1)[0].decode("ascii")
        rec_off = struct.unpack_from("<I", data, off + 2 + nlen)[0]
        pad = struct.unpack_from("<I", data, off + 2 + nlen + 4)[0]
        entries.append({"i": i, "id0": eid0, "name": name, "off": rec_off, "pad": pad})
        off += 2 + nlen + 8
    return {"kind": "u32count_u8id_then_u16len_cstr_u32off_u32pad", "count": count, "consumed": off - start, "entries": entries}
