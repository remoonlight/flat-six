"""Offline measurement row fields + disabled UDS read-candidate. executionEnabled=false."""
from __future__ import annotations

import struct

ROW16 = 16
ROW19 = 19
_BIT_SEM = "lsb_within_one_byte_proven_zero_based"
_SID31_NOTE = (
    "native COMM.OdxSendUdsReadSidPidCmdStand@1390c/13918 sets SID 0x22@1391a; "
    "candidate is 22+PID, not RoutineControl 31"
)


def _fail(status: str, **extra) -> dict:
    base = {
        "ok": False,
        "status": status,
        "rawSID": None,
        "wireSID": None,
        "PID": None,
        "byteOffset": None,
        "bitOffset": None,
        "nameID": None,
        "formulaID": None,
        "disabledreadrequestcandidate": None,
        "bit_semantics": _BIT_SEM,
        "executionEnabled": False,
    }
    base.update(extra)
    return base


def _payload_record(hex_s: str | None, cand_status: str, *, alias31: bool = False) -> dict:
    rec = {
        "executionEnabled": False,
        "liveApproved": False,
        "payload_hex": hex_s,
        "status": cand_status,
    }
    if alias31:
        rec["rawSID"] = 0x31
        rec["wireSID"] = 0x22
        rec["explanation"] = _SID31_NOTE
    return rec


def _candidate(raw_sid: int, pid: int) -> tuple[int | None, dict]:
    if raw_sid == 0x21:
        if pid > 0xFF:
            return None, _payload_record(None, "sid21_wide_pid_unsupported")
        return 0x21, _payload_record(bytes([0x21, pid]).hex().upper(), "ok")
    if raw_sid == 0x22:
        if pid > 0xFFFF:
            return None, _payload_record(None, "pid_overflow_u16")
        return 0x22, _payload_record(bytes([0x22, (pid >> 8) & 0xFF, pid & 0xFF]).hex().upper(), "ok")
    if raw_sid == 0x31:
        if pid > 0xFFFF:
            return 0x22, _payload_record(None, "pid_overflow_u16", alias31=True)
        hx = bytes([0x22, (pid >> 8) & 0xFF, pid & 0xFF]).hex().upper()
        return 0x22, _payload_record(hx, "ok", alias31=True)
    return None, _payload_record(None, "unsupported_sid")


def parse_measurement_fields(raw: bytes, format_flag: int = 0) -> dict:
    if not isinstance(raw, (bytes, bytearray)):
        return _fail("malformed_input")
    raw = bytes(raw)
    if format_flag in (1, 2, 3):
        if len(raw) != ROW19:
            return _fail("format_mismatch", expected_len=ROW19, actual_len=len(raw), format_flag=format_flag)
        sid_w, fp = 4, 4
        raw_sid = struct.unpack_from("<I", raw, 0)[0]
    elif format_flag == 0:
        if len(raw) != ROW16:
            return _fail("format_mismatch", expected_len=ROW16, actual_len=len(raw), format_flag=format_flag)
        sid_w, fp = 1, 1
        raw_sid = raw[0]
    else:
        return _fail("unsupported_header_format", format_flag=format_flag, actual_len=len(raw))

    pid = struct.unpack_from("<I", raw, fp)[0]
    name_id = struct.unpack_from("<I", raw, fp + 4)[0]
    byte_off = struct.unpack_from("<H", raw, fp + 8)[0]
    bit_off = raw[fp + 10]
    formula_id = struct.unpack_from("<I", raw, fp + 11)[0]
    wire, cand = _candidate(raw_sid, pid)
    row_ok = cand["status"] == "ok"
    return {
        "ok": row_ok,
        "status": cand["status"],
        "rawSID": raw_sid,
        "wireSID": wire,
        "PID": pid,
        "byteOffset": byte_off,
        "bitOffset": bit_off,
        "nameID": f"{name_id:08X}",
        "formulaID": f"{formula_id:08X}",
        "disabledreadrequestcandidate": cand,
        "bit_semantics": _BIT_SEM,
        "executionEnabled": False,
        "format_flag": format_flag,
        "sid_width": sid_w,
        "row_len": len(raw),
    }
