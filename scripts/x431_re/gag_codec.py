"""GAG record decode (IdSearch). Copied method, no ELF/SO dependency."""
from __future__ import annotations

import struct

XOR15 = bytes.fromhex("263483985318932959387257033845")


def bcd4_to_float(b: bytes) -> float:
    acc = 0.0
    for x in b:
        hi, lo = x >> 4, x & 0xF
        if hi > 9 or lo > 9:
            raise ValueError(f"not bcd {x:#x}")
        acc = acc * 100.0 + (hi * 10 + lo)
    return acc / 10000.0


def xor_table(blob: bytes, version: float) -> bytearray:
    out = bytearray(blob)
    if version > 5.0:
        for i, x in enumerate(out):
            out[i] = x ^ XOR15[i % 15]
    return out


def gag_decrypt_high_c(buf: bytearray, ctx: bytearray) -> None:
    sl = 0x89
    n = len(buf)
    if n < 1:
        return
    last = (buf[n - 1] ^ ctx[sl]) & 0xFF
    buf[n - 1] = last
    r6 = last
    for i in range(1, n):
        idx = (i + r6) % 0x86
        buf[i - 1] ^= ctx[sl + idx]


def gag_decrypt_high_b(buf: bytearray, ctx: bytearray) -> None:
    for i in range(len(buf)):
        idx = (i + 0x12) % 0x9C
        buf[i] ^= ctx[0x73 + idx]


def gag_decrypt_modetable(buf: bytearray, mode: int, ctx: bytearray) -> None:
    if mode == 0xC:
        gag_decrypt_high_c(buf, ctx)
    elif mode == 0xB:
        gag_decrypt_high_b(buf, ctx)
    else:
        raise ValueError(mode)


def gag_decrypt_mode1(buf: bytearray, ctx: bytearray, sec: bytes, nsec: int) -> None:
    if not sec or nsec < 1:
        for i in range(len(buf)):
            buf[i] = ctx[0x10 + buf[i]]
        return
    b0 = buf[0]
    buf[0] = ctx[0x10 + ((sec[0] ^ b0) & 0xFF)]
    for i in range(1, len(buf)):
        x = sec[i % nsec] ^ buf[i]
        buf[i] = ctx[0x10 + (x & 0xFF)]


def encrypt_number_strict(buf: bytearray, ctx: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] ^= ctx[0x87 + i]


def parse_text_gag(gag: bytes) -> dict:
    if len(gag) < 7 + 256 + 4 + 2 + 5:
        raise ValueError("truncated gag header")
    if gag[:3] not in (b"LAH", b"WCX"):
        raise ValueError(f"magic {gag[:3]!r}")
    ver = bcd4_to_float(gag[3:7])
    stream = xor_table(gag[7:263], ver)
    if len(set(stream)) != 256:
        raise ValueError("table256 not a permutation")
    inv = bytearray(256)
    for i, b in enumerate(stream):
        inv[b] = i
    pos = 263
    ver2 = bcd4_to_float(gag[pos : pos + 4])
    pos += 4
    nsec = struct.unpack_from(">H", gag, pos)[0]
    pos += 2
    if nsec > 0x1F4:
        raise ValueError(f"nsec {nsec}")
    if pos + nsec + 5 > len(gag):
        raise ValueError("truncated gag section")
    sec = gag[pos : pos + nsec]
    pos += nsec
    ctx = bytearray(0x200)
    ctx[0x10 : 0x10 + 256] = inv
    hdr = bytearray(gag[pos : pos + 5])
    gag_decrypt_modetable(hdr, 0xC, ctx)
    count = (hdr[0] << 24) | (hdr[1] << 16) | (hdr[2] << 8) | hdr[3]
    keylen = hdr[4]
    if keylen < 1 or keylen > 64:
        raise ValueError(f"keylen {keylen}")
    if count < 0 or count > 2_000_000:
        raise ValueError(f"count {count}")
    rec_size = keylen + 4
    index_end = pos + 5 + count * rec_size
    if index_end > len(gag):
        raise ValueError(f"truncated index end={index_end} size={len(gag)}")
    return {
        "ver": ver,
        "count": count,
        "keylen": keylen,
        "index_off": pos,
        "index_end": index_end,
        "sec": sec,
        "nsec": nsec,
        "ctx": ctx,
        "gag": gag,
    }


def decode_record(st: dict, i: int) -> tuple[bytes, bytes] | None:
    gag, ctx, sec = st["gag"], st["ctx"], st["sec"]
    keylen, count, nsec = st["keylen"], st["count"], st["nsec"]
    rec_size = keylen + 4
    if i < 0 or i >= count:
        return None
    base = st["index_off"] + 5 + i * rec_size
    if base + rec_size > len(gag):
        raise ValueError(f"index row truncated i={i}")
    key = bytes(gag[base : base + keylen])
    offb = bytearray(gag[base + keylen : base + rec_size])
    gag_decrypt_modetable(offb, 0xB, ctx)
    text_off = (offb[0] << 24) | (offb[1] << 16) | (offb[2] << 8) | offb[3]
    if text_off + 2 > len(gag):
        raise ValueError(f"payload offset {text_off} i={i}")
    szb = bytearray(gag[text_off : text_off + 2])
    gag_decrypt_modetable(szb, 0xC, ctx)
    n = ((szb[0] << 8) | szb[1]) - 2
    if n < 0 or n > 0xFFFF or text_off + 2 + n > len(gag):
        raise ValueError(f"payload size {n} off={text_off} i={i}")
    body = bytearray(gag[text_off + 2 : text_off + 2 + n])
    gag_decrypt_mode1(body, ctx, sec, nsec)
    return key, bytes(body)
