"""Prefix/suffix field parse. No send, no write payload."""
from __future__ import annotations

import struct


def parse_prefix7(prefix: bytes) -> dict:
    if len(prefix) != 7:
        return {"ok": False, "error": f"prefix_len_{len(prefix)}"}
    sid = prefix[0]
    companion = struct.unpack_from("<H", prefix, 1)[0]
    pid = struct.unpack_from("<I", prefix, 3)[0]
    return {
        "ok": True,
        "readSID": sid,
        "companionSID": companion,
        "pid": pid,
        "error": None,
    }


def derived_read_request(prefix: bytes) -> dict:
    """Native SendUdsReadSidPidCmd: 1A/21 one PID byte; 22 two PID bytes high-first.

    Zero PID is valid metadata. Overflow / unsupported SID rejected (no silent truncate).
    """
    p = parse_prefix7(prefix)
    if not p["ok"]:
        return {**p, "request_hex": None, "status": "malformed_prefix"}
    sid, pid = p["readSID"], p["pid"]
    if sid in (0x1A, 0x21):
        if pid > 0xFF:
            return {**p, "request_hex": None, "status": "pid_overflow_u8"}
        req = bytes([sid, pid]).hex().upper()
        return {**p, "request_hex": req, "status": "ok"}
    if sid == 0x22:
        if pid > 0xFFFF:
            return {**p, "request_hex": None, "status": "pid_overflow_u16"}
        req = bytes([0x22, (pid >> 8) & 0xFF, pid & 0xFF]).hex().upper()
        return {**p, "request_hex": req, "status": "ok"}
    return {**p, "request_hex": None, "status": "unsupported_sid"}


def parse_coding_suffix8(suf: bytes) -> dict:
    if len(suf) != 8:
        return {"ok": False, "error": f"suffix_len_{len(suf)}"}
    property_b = suf[0]
    byte_off = struct.unpack_from("<H", suf, 1)[0]
    bit_off = suf[3]
    formula_id = struct.unpack_from("<I", suf, 4)[0]
    return {
        "ok": True,
        "propertyByte": property_b,
        "property_status": "unresolved",
        "byteOffset": byte_off,
        "bitOffset": bit_off,
        "bit_semantics": "lsb_within_one_byte_proven",
        "formula_id": formula_id,
        "formula_id_hex": f"{formula_id:08X}",
    }


def parse_identity_suffix7(suf: bytes) -> dict:
    if len(suf) != 7:
        return {"ok": False, "error": f"suffix_len_{len(suf)}"}
    formula_id = struct.unpack_from("<I", suf, 3)[0]
    return {
        "ok": True,
        "head_hex": suf[:3].hex(),
        "head_status": "unresolved_identity_suffix_head",
        "formula_id": formula_id,
        "formula_id_hex": f"{formula_id:08X}",
    }
