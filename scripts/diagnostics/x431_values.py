"""Offline X431 measurement decode + coding buffer preview. Never live/write."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .x431_formula import formula_from_record

FLAGS = {"executionEnabled": False, "liveVerified": False, "writePayload": None}

_LIB_REL = ".local/x431-re/2026-09-27-protocol/package/libPORSCHE_CALC.so"
_LIB_SHA_EXPECTED = "40a4bf4ebff5da4f5d3093e469287881a1aee876b483416ef0bdc8d9e8edc543"
_REPO = Path(__file__).resolve().parents[2]


def _library_meta() -> dict:
    p = _REPO / _LIB_REL
    if not p.is_file():
        return {
            "library": _LIB_REL,
            "librarySha256": _LIB_SHA_EXPECTED,
            "libraryPresent": False,
            "librarySha256Match": None,
        }
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return {
        "library": _LIB_REL,
        "librarySha256": h,
        "libraryPresent": True,
        "librarySha256Match": h == _LIB_SHA_EXPECTED,
    }


EVIDENCE = {
    **_library_meta(),
    "extract": "GetX_Modify@0x37a28: nbytes=ceil((bitOff+bitLen)/8); HighLow=0 reverse@0x37acc; BE int >> bitOffset & ((1<<bitLen)-1); sprintf@0x37c82",
    "highLowSwap": "GetX_Modify@0x37aae nbytes>=2 reverse when HighLow==0",
    "crossByte": "examples HL1 bl=12 off=4 ABCD->0xABC; bl=10 off=2->0x2F3; bl=16 off=4 ABCDEF->0xBCDE (not LSB stream)",
    "bitMask": "AND after extract; BitMask token hex (7F/0FFF)",
    "linear": "GetLinearResult@0x2845e (x*Xc-Xa)/Xb",
    "scaleTab": "GetSCALE_LINEAR@0x294b8 / GetTAB_INTP@0x2bc00 GetX_Modify + same algebra; GetTABResult@0x2888c extra vcvt.f32.f64 display",
    "bcd": "BCDtoDec@0x3734e packed BCD-P only; BCD-UP not implemented",
    "ascii": "fixed BitLength copy; MinMax ZERO terminator within MaxLength else overlong; missing NUL missing_terminator; Encoding ISO-8859-1/2",
    "signed": "GetLINEAR vcvt.f64.s32/u32; GetIc signed vs unsigned compare",
    "float32": "A_FLOAT32 IEEE BE bits; TAB display f32 round; pack overflow -> out_of_range; nan/inf -> nan",
    "compucode": "getParaObjectX@0x95f58 hex-concat/strtol; Formel54@0x964c8 uxth+%d@0x9655c; Formel21/119 lo*hi*k; Formel33 lo/hi*100; Formel5 (lo-100)*hi*0.1; EntryPoint=execute",
    "preview": "rawValue unsigned field bits; bits outside BitMask -> preview_raw_unrepresentable; post-decode raw must equal rawValue",
}

_SUPPORTED_KINDS = frozenset({"IDENTICAL", "LINEAR", "TEXTTABLE", "SCALE-LINEAR", "TAB-INTP", "COMPUCODE"})
_SIGNED = frozenset({"A_INT32", "A_INT16", "A_INT8"})
_UINT = frozenset({"A_UINT32", "A_UINT16", "A_UINT8"})
_WIDE = frozenset({"A_INT64", "A_UINT64"})
_NUMERIC = _SIGNED | _UINT
_FLOAT = frozenset({"A_FLOAT32"})
_BYTES = frozenset({"A_BYTEFIELD", "A_ASCIISTRING", "A_UNICODE2STRING"})
_ENC_OK = frozenset({"UNDEFINED", "2C"})
_ENC_BCD = frozenset({"BCD-P"})
_ENC_BCD_UNPROVEN = frozenset({"BCD", "BCD-UP"})
_ENC_STR = {
    "ISO-8859-1": "latin-1",
    "ISO-8859-2": "iso8859-2",
    "ASCII": "ascii",
}
_MAX_BYTES = 65535


def _flags(d: dict) -> dict:
    out = dict(d)
    out.update(FLAGS)
    return out


def _fail(reason: str, **extra) -> dict:
    return _flags({"ok": False, "reason": reason, **extra})


def _strict_int(x) -> int | None:
    if type(x) is not int:
        return None
    return x


def _signed_type(parsed: dict) -> bool:
    bdt = parsed.get("baseDataType") or ""
    enc = (parsed.get("encoding") or "Undefined").upper()
    return bdt in _SIGNED or enc == "2C"


def _is_bytes_type(parsed: dict) -> bool:
    bdt = parsed.get("baseDataType") or ""
    dt = parsed.get("dataType") or ""
    return bdt in _BYTES or dt in _BYTES


def _enc_reason(parsed: dict) -> str | None:
    enc = (parsed.get("encoding") or "Undefined").upper()
    kind = parsed.get("kind")
    bdt = parsed.get("baseDataType") or ""
    if enc in _ENC_BCD:
        return None if kind in {"IDENTICAL", "LINEAR", "SCALE-LINEAR", "TAB-INTP"} else "encoding_bcd"
    if enc in _ENC_BCD_UNPROVEN:
        return "encoding_bcd_unproven"
    if kind == "IDENTICAL" and _is_bytes_type(parsed):
        if bdt == "A_BYTEFIELD" or parsed.get("dataType") == "A_BYTEFIELD":
            return None if enc in _ENC_OK else "encoding_unproven"
        if enc in _ENC_STR:
            return None
        return "encoding_string_unproven"
    if enc in _ENC_OK:
        return None
    if bdt in _FLOAT and kind in {"IDENTICAL", "LINEAR", "SCALE-LINEAR", "TAB-INTP"}:
        return None
    if enc in _ENC_STR or enc.startswith(("ISO", "UTF", "ASCII", "UNICODE", "ANSI")):
        return "encoding_string_unproven"
    return "encoding_unproven"


def _display_name(record: dict) -> dict:
    name = record.get("name")
    labels = record.get("labels")
    raw_name = name if isinstance(name, str) else None
    if raw_name is not None and raw_name.startswith("SubIndexNum="):
        display = None
    else:
        display = raw_name
    texts: list[str] = []
    if isinstance(labels, list):
        for lab in labels:
            if isinstance(lab, dict) and "text" in lab:
                t = lab["text"]
                texts.append("" if t is None else str(t))
            elif isinstance(lab, str):
                texts.append(lab)
        if texts and display is None and not (raw_name and raw_name.startswith("SubIndexNum=")):
            display = texts[-1]
    elif isinstance(labels, str) and display is None:
        display = labels
    return {"displayName": display, "rawName": raw_name, "labelTexts": texts}


def _bcd_to_dec(buf: bytes) -> dict:
    if not buf:
        return {"ok": True, "value": 0}
    acc = 0
    for b in buf:
        hi, lo = b >> 4, b & 0xF
        if hi > 9 or lo > 9:
            return {"ok": False, "reason": "encoding_bcd_invalid"}
        acc = acc * 100 + hi * 10 + lo
    return {"ok": True, "value": acc}


def extract_raw(parsed: dict, data: bytes, byte_off: int, bit_off: int) -> dict:
    if not isinstance(data, (bytes, bytearray)):
        return {"ok": False, "reason": "malformed_input"}
    data = bytes(data)
    if byte_off < 0 or bit_off < 0 or bit_off > 7:
        return {"ok": False, "reason": "malformed_offset"}
    if _is_bytes_type(parsed) and parsed.get("kind") == "IDENTICAL":
        return _extract_bytes(parsed, data, byte_off, bit_off)
    bl = parsed.get("bitLength")
    if bl is None or bl <= 0:
        return {"ok": False, "reason": "malformed_bitlength"}
    if bl > 32 and not _is_bytes_type(parsed):
        return {"ok": False, "reason": "width_gt_32_unproven"}
    n = (bit_off + bl + 7) // 8
    if byte_off + n > len(data):
        return {"ok": False, "reason": "truncated"}
    hl = parsed.get("highLow")
    chunk = bytearray(data[byte_off : byte_off + n])
    if n >= 2:
        if hl not in (0, 1):
            return {"ok": False, "reason": "highlow_unproven"}
        if hl == 0:
            chunk.reverse()
    acc = int.from_bytes(chunk, "big")
    raw = (acc >> bit_off) & ((1 << bl) - 1)
    mask = parsed.get("bitMask") or 0
    if mask:
        raw &= mask
    span = "one_byte" if n == 1 else ("multibyte" if bit_off == 0 and bl % 8 == 0 else "cross_byte")
    return {"ok": True, "raw": raw, "span": span, "bytesUsed": n, "highLow": hl}


def _extract_bytes(parsed: dict, data: bytes, byte_off: int, bit_off: int) -> dict:
    if bit_off != 0:
        return {"ok": False, "reason": "bit_span_cross_byte"}
    bl = parsed.get("bitLength")
    term = parsed.get("termination")
    maxlen = parsed.get("maxLength")
    minlen = parsed.get("minLength")
    if bl is not None:
        if bl <= 0 or bl % 8:
            return {"ok": False, "reason": "malformed_bitlength"}
        n = bl // 8
        if n > _MAX_BYTES:
            return {"ok": False, "reason": "width_gt_32_unproven"}
        if byte_off + n > len(data):
            return {"ok": False, "reason": "truncated"}
        payload = data[byte_off : byte_off + n]
        return {"ok": True, "raw_bytes": payload, "span": "bytes", "bytesUsed": n}
    if term is None:
        return {"ok": False, "reason": "malformed_bitlength"}
    if byte_off > len(data):
        return {"ok": False, "reason": "truncated"}
    rest = data[byte_off:]
    cap = maxlen if maxlen is not None else _MAX_BYTES
    if cap > _MAX_BYTES:
        cap = _MAX_BYTES
    if term == "ZERO":
        z = rest.find(b"\x00")
        if z < 0:
            return {"ok": False, "reason": "missing_terminator"}
        if z > cap:
            return {"ok": False, "reason": "overlong"}
        payload = rest[:z]
    elif term == "END-OF-PDU":
        payload = rest[:cap]
    else:
        return {"ok": False, "reason": "malformed_formula"}
    if minlen is not None and len(payload) < minlen:
        return {"ok": False, "reason": "truncated"}
    return {"ok": True, "raw_bytes": payload, "span": "minmax", "bytesUsed": len(payload)}


def insert_raw(parsed: dict, original: bytes, byte_off: int, bit_off: int, raw_value: int) -> dict:
    bl = parsed.get("bitLength")
    if bl is None or bl <= 0 or bl > 32:
        return {"ok": False, "reason": "preview_not_single_byte_texttable"}
    if byte_off < 0 or bit_off < 0 or bit_off > 7:
        return {"ok": False, "reason": "malformed_offset"}
    n = (bit_off + bl + 7) // 8
    if byte_off + n > len(original):
        return {"ok": False, "reason": "truncated"}
    hl = parsed.get("highLow")
    chunk = bytearray(original[byte_off : byte_off + n])
    work = bytearray(chunk)
    if n >= 2:
        if hl not in (0, 1):
            return {"ok": False, "reason": "highlow_unproven"}
        if hl == 0:
            work.reverse()
    acc = int.from_bytes(work, "big")
    field = ((1 << bl) - 1) << bit_off
    put = raw_value & ((1 << bl) - 1)
    mask_f = parsed.get("bitMask") or 0
    if mask_f:
        m = (mask_f << bit_off) & field
        acc = (acc & ~m) | ((put & mask_f) << bit_off)
    else:
        acc = (acc & ~field) | (put << bit_off)
    out = acc.to_bytes(n, "big")
    if n >= 2 and hl == 0:
        out = bytes(reversed(out))
    after = bytearray(original)
    after[byte_off : byte_off + n] = out
    return {"ok": True, "after": bytes(after)}


def _sign_extend(raw: int, bit_length: int) -> int:
    sign = 1 << (bit_length - 1)
    return raw - (1 << bit_length) if raw & sign else raw


def _compare_value(parsed: dict, raw: int) -> int:
    if _signed_type(parsed) or parsed.get("signedLiterals"):
        return _sign_extend(raw, parsed["bitLength"])
    return raw


def _in_range(val: int, lo: int, hi: int) -> bool:
    return lo <= val <= hi


def _lookup_maps(val: int, rows: list[tuple[int, int, str]]) -> str | None:
    for lo, hi, tid in rows:
        if _in_range(val, lo, hi):
            return tid
    return None


def _enum_lookup(record: dict, tid: str) -> tuple[str | None, str]:
    et = record.get("enumText")
    if not isinstance(et, dict):
        return None, "absent"
    key = tid[2:] if tid.upper().startswith("0X") else tid
    for k in (key, key.upper(), key.lower(), "0x" + key, "0X" + key.upper()):
        if k in et:
            v = et[k]
            if v is None:
                return None, "absent"
            if v == "":
                return "", "empty"
            return str(v), "resolved"
    return None, "absent"


def _formel_key(parsed: dict) -> str:
    cf = parsed.get("codeFile") or (parsed.get("fields") or {}).get("CodeFile") or ""
    return str(cf).replace(".class", "").replace(".CLASS", "").strip().upper()


def _compucode_eval(parsed: dict, raw: int) -> dict:
    name = _formel_key(parsed)
    lo, hi = raw & 0xFF, (raw >> 8) & 0xFF
    if name == "FORMEL54":
        return {"ok": True, "value": raw & 0xFFFF, "numeric": True}
    if name == "FORMEL21":
        phys = Decimal(lo * hi) * Decimal("0.001")
        return {"ok": True, "value": lo * hi, "phys": phys, "numeric": True}
    if name == "FORMEL119":
        phys = Decimal(lo * hi) * Decimal("0.01")
        return {"ok": True, "value": lo * hi, "phys": phys, "numeric": True}
    if name == "FORMEL33":
        if hi == 0:
            return {"ok": False, "reason": "zero_denominator"}
        phys = (Decimal(lo) / Decimal(hi)) * Decimal(100)
        return {"ok": True, "value": lo, "phys": phys, "numeric": True}
    if name == "FORMEL5":
        phys = Decimal((lo - 100) * hi) * Decimal("0.1")
        return {"ok": True, "value": (lo - 100) * hi, "phys": phys, "numeric": True}
    return {"ok": False, "reason": "kind_COMPUCODE"}


def _linear_phys(value, xa, xb, xc) -> dict:
    if xb == 0:
        return {"ok": False, "reason": "zero_denominator"}
    try:
        dv = value if isinstance(value, Decimal) else Decimal(str(value))
        phys = (dv * xc - xa) / xb
    except (InvalidOperation, ZeroDivisionError):
        return {"ok": False, "reason": "nan"}
    if not phys.is_finite():
        return {"ok": False, "reason": "nan"}
    return {"ok": True, "phys": phys}


def _apply_bcd(parsed: dict, raw: int) -> dict:
    enc = (parsed.get("encoding") or "").upper()
    if enc not in _ENC_BCD:
        return {"ok": True, "raw": raw}
    bl = parsed["bitLength"]
    n = (bl + 7) // 8
    buf = raw.to_bytes(n, "big")
    d = _bcd_to_dec(buf)
    if not d["ok"]:
        return d
    return {"ok": True, "raw": d["value"]}


def classify_decode(parsed: dict, byte_off, bit_off) -> dict:
    if not parsed.get("ok"):
        return {"ok": False, "reason": parsed.get("reason") or "malformed_formula"}
    kind = parsed.get("kind")
    if kind not in _SUPPORTED_KINDS:
        return {"ok": False, "reason": f"kind_{kind or 'unknown'}"}
    bo, bi = _strict_int(byte_off), _strict_int(bit_off)
    if bo is None or bi is None:
        return {"ok": False, "reason": "missing_offset" if byte_off is None or bit_off is None else "malformed_offset"}
    if bo < 0 or bi < 0 or bi > 7:
        return {"ok": False, "reason": "malformed_offset"}
    bdt = parsed.get("baseDataType") or ""
    if bdt in _WIDE:
        return {"ok": False, "reason": "width_gt_32_unproven"}
    er = _enc_reason(parsed)
    if er:
        return {"ok": False, "reason": er}
    bytes_ident = kind == "IDENTICAL" and _is_bytes_type(parsed)
    bl = parsed.get("bitLength")
    if not bytes_ident:
        if bl is None or bl <= 0:
            return {"ok": False, "reason": "malformed_bitlength"}
        if bl > 32:
            return {"ok": False, "reason": "width_gt_32_unproven"}
        if kind == "TEXTTABLE":
            if bdt in {"A_ASCIISTRING", "A_UNICODE2STRING"}:
                return {"ok": False, "reason": "type_non_numeric"}
            if bdt not in _NUMERIC and bdt != "A_BYTEFIELD":
                return {"ok": False, "reason": "type_unproven"}
        elif bdt and bdt not in _NUMERIC and bdt not in _BYTES and bdt not in _FLOAT:
            return {"ok": False, "reason": "type_non_numeric"}
        if kind in {"LINEAR", "SCALE-LINEAR", "TAB-INTP"} and bdt and bdt not in _NUMERIC and bdt not in _FLOAT:
            return {"ok": False, "reason": "type_non_numeric"}
    if kind == "LINEAR":
        if parsed.get("xa") is None or parsed.get("xb") is None or parsed.get("xc") is None:
            return {"ok": False, "reason": "linear_coeff_missing"}
        if parsed["xb"] == 0:
            return {"ok": False, "reason": "zero_denominator"}
    if kind in {"SCALE-LINEAR", "TAB-INTP"}:
        segs = parsed.get("segments") or []
        if not segs:
            return {"ok": False, "reason": "malformed_coeff"}
        if any(s.get("xb") == 0 for s in segs):
            return {"ok": False, "reason": "zero_denominator"}
    if kind == "TEXTTABLE":
        if not parsed.get("maps") and not parsed.get("ic"):
            return {"ok": False, "reason": "texttable_empty"}
    if kind == "COMPUCODE":
        if _formel_key(parsed) not in {"FORMEL54", "FORMEL21", "FORMEL119", "FORMEL33", "FORMEL5"}:
            return {"ok": False, "reason": "kind_COMPUCODE"}
        if (parsed.get("entryPoint") or "") != "execute":
            return {"ok": False, "reason": "kind_COMPUCODE"}
    probe = bytes(min(_MAX_BYTES, max(64, (bo or 0) + 64)))
    ext = extract_raw(parsed, probe, bo, bi)
    if not ext["ok"] and ext["reason"] not in {"truncated"}:
        return {"ok": False, "reason": ext["reason"]}
    return {"ok": True, "reason": None, "kind": kind}


def classify_preview(parsed: dict, byte_off, bit_off) -> dict:
    d = classify_decode(parsed, byte_off, bit_off)
    if not d["ok"]:
        return {"ok": False, "reason": d["reason"]}
    kind = parsed.get("kind")
    bl = parsed.get("bitLength")
    bi = _strict_int(bit_off)
    if bl is None or bi is None or bl <= 0 or bl > 32:
        return {"ok": False, "reason": "preview_not_single_byte_texttable"}
    if kind == "TEXTTABLE":
        maps = parsed.get("maps") or []
        if not maps or any(lo != hi for lo, hi, _ in maps):
            return {"ok": False, "reason": "preview_range_enum"}
        return {"ok": True, "reason": None, "kind": "TEXTTABLE"}
    if kind == "IDENTICAL" and not _is_bytes_type(parsed):
        return {"ok": True, "reason": None, "kind": "IDENTICAL"}
    if kind == "LINEAR":
        return {"ok": False, "reason": "preview_inverse_ambiguous"}
    return {"ok": False, "reason": "preview_not_single_byte_texttable"}


def _decode_string(parsed: dict, payload: bytes) -> dict:
    enc = (parsed.get("encoding") or "Undefined").upper()
    bdt = parsed.get("baseDataType") or ""
    raw_hex = payload.hex().upper()
    if bdt == "A_BYTEFIELD" or (parsed.get("dataType") == "A_BYTEFIELD" and enc in _ENC_OK):
        return {"ok": True, "rawHex": raw_hex, "value": raw_hex, "text": None, "numeric": False}
    codec = _ENC_STR.get(enc)
    body = payload.split(b"\x00", 1)[0]
    if codec is None:
        return {"ok": False, "reason": "encoding_string_unproven"}
    try:
        text = body.decode(codec)
    except UnicodeDecodeError:
        return {"ok": False, "reason": "encoding_invalid", "rawHex": raw_hex}
    return {"ok": True, "rawHex": raw_hex, "value": text, "text": text, "numeric": False}


def _format_phys(parsed: dict, phys: Decimal, tab_f32: bool = False) -> tuple[str | None, str | None]:
    prec = parsed.get("precision")
    dt = (parsed.get("dataType") or "").upper()
    shown_src = phys
    if dt == "A_FLOAT32" or tab_f32:
        try:
            f32 = struct.unpack("<f", struct.pack("<f", float(phys)))[0]
        except (OverflowError, ValueError):
            return None, None
        shown_src = Decimal(str(f32))
    shown = format(shown_src, f".{prec}f") if prec is not None and prec >= 0 else format(shown_src, "f")
    return str(phys), shown


def decode_record(record: dict, data: bytes) -> dict:
    names = _display_name(record if isinstance(record, dict) else {})
    if not isinstance(record, dict):
        return _fail("malformed_input", **names)
    _text, parsed = formula_from_record(record)
    base = {
        **names,
        "formulaId": parsed.get("id_hex"),
        "formulaKind": parsed.get("kind"),
        "byteOffset": record.get("byteOffset"),
        "bitOffset": record.get("bitOffset"),
        "evidence": EVIDENCE,
    }
    if not parsed.get("ok"):
        return _fail(parsed.get("reason") or "malformed_formula", **base)
    byte_off = _strict_int(record.get("byteOffset"))
    bit_off = _strict_int(record.get("bitOffset"))
    clf = classify_decode(parsed, record.get("byteOffset"), record.get("bitOffset"))
    if not clf["ok"]:
        return _fail(clf["reason"], **base)
    ext = extract_raw(parsed, data, byte_off, bit_off)
    if not ext["ok"]:
        return _fail(ext["reason"], **base)
    if "raw_bytes" in ext:
        dec = _decode_string(parsed, ext["raw_bytes"])
        if not dec["ok"]:
            return _fail(dec["reason"], **base, rawHex=dec.get("rawHex"))
        return _flags({"ok": True, "reason": None, **base, **dec, "raw": ext["raw_bytes"].hex().upper()})
    raw = ext["raw"]
    bdt = parsed.get("baseDataType") or ""
    float32 = bdt == "A_FLOAT32" and parsed.get("bitLength") == 32
    if float32:
        f32 = struct.unpack(">f", raw.to_bytes(4, "big"))[0]
        if not math.isfinite(f32):
            return _fail("nan", **base, raw=raw)
        cmpv_num = None
        value = f32
    else:
        bcd = _apply_bcd(parsed, raw)
        if not bcd["ok"]:
            return _fail(bcd["reason"], **base, raw=raw)
        if (parsed.get("encoding") or "").upper() in _ENC_BCD:
            cmpv = bcd["raw"]
            value = bcd["raw"]
        else:
            cmpv = _compare_value(parsed, raw)
            value = cmpv if _signed_type(parsed) or parsed.get("signedLiterals") else raw
    if not float32:
        ic_id = _lookup_maps(cmpv, parsed.get("ic") or [])
        if ic_id:
            text, st = _enum_lookup(record, ic_id)
            return _fail(
                "invalid_reserved",
                **base,
                raw=raw,
                value=cmpv,
                icId=ic_id,
                icText=text,
                textStatus=st,
            )
        lo, hi = parsed.get("lower"), parsed.get("upper")
        kind = parsed["kind"]
        if kind not in {"SCALE-LINEAR", "TAB-INTP"}:
            if lo is not None and cmpv < lo:
                return _fail("out_of_bounds", **base, raw=raw, value=cmpv, lower=lo, upper=hi)
            if hi is not None and cmpv > hi:
                return _fail("out_of_bounds", **base, raw=raw, value=cmpv, lower=lo, upper=hi)
    else:
        kind = parsed["kind"]
        cmpv = value
    if kind == "IDENTICAL":
        return _flags({"ok": True, "reason": None, **base, "raw": raw, "value": value, "numeric": True})
    if kind == "COMPUCODE":
        ev = _compucode_eval(parsed, raw if not float32 else int(raw))
        if not ev["ok"]:
            return _fail(ev["reason"], **base, raw=raw)
        out = {"ok": True, "reason": None, **base, "raw": raw, "value": ev["value"], "numeric": True}
        if "phys" in ev:
            phys_s, shown = _format_phys(parsed, ev["phys"])
            if phys_s is None:
                return _fail("out_of_range", **base, raw=raw)
            out["phys"] = phys_s
            out["display"] = shown
            out["unit"] = record.get("unit")
        return _flags(out)
    if kind in {"LINEAR", "SCALE-LINEAR", "TAB-INTP"}:
        lin_x = value if float32 else value
        if kind == "LINEAR":
            lp = _linear_phys(lin_x, parsed["xa"], parsed["xb"], parsed["xc"])
        else:
            segs = parsed.get("segments") or []
            hit = next((s for s in segs if s["lower"] <= int(cmpv) <= s["upper"]), None) if not float32 else None
            if hit is None and not float32:
                return _fail("out_of_bounds", **base, raw=raw, value=cmpv)
            if float32:
                lp = _linear_phys(lin_x, parsed["xa"], parsed["xb"], parsed["xc"])
            else:
                lp = _linear_phys(lin_x, hit["xa"], hit["xb"], hit["xc"])
        if not lp["ok"]:
            return _fail(lp["reason"], **base, raw=raw)
        phys_s, shown = _format_phys(parsed, lp["phys"], tab_f32=(kind == "TAB-INTP"))
        if phys_s is None:
            return _fail("out_of_range", **base, raw=raw)
        return _flags(
            {
                "ok": True,
                "reason": None,
                **base,
                "raw": raw,
                "value": value,
                "phys": phys_s,
                "display": shown,
                "unit": record.get("unit"),
                "numeric": True,
            }
        )
    tid = _lookup_maps(cmpv, parsed.get("maps") or [])
    if tid is None:
        return _fail("texttable_unmapped", **base, raw=raw, value=value)
    text, st = _enum_lookup(record, tid)
    return _flags(
        {
            "ok": True,
            "reason": None,
            **base,
            "raw": raw,
            "value": value,
            "textId": tid,
            "text": text,
            "textStatus": st,
            "numeric": False,
        }
    )


def preview_coding(record: dict, original: bytes, raw_value) -> dict:
    names = _display_name(record if isinstance(record, dict) else {})
    if not isinstance(record, dict) or not isinstance(original, (bytes, bytearray)):
        return _fail("malformed_input", **names)
    original = bytes(original)
    _text, parsed = formula_from_record(record)
    base = {**names, "formulaId": parsed.get("id_hex"), "formulaKind": parsed.get("kind")}
    if not parsed.get("ok"):
        return _fail(parsed.get("reason") or "malformed_formula", **base)
    clf = classify_preview(parsed, record.get("byteOffset"), record.get("bitOffset"))
    if not clf["ok"]:
        return _fail(clf["reason"], **base)
    byte_off = _strict_int(record.get("byteOffset"))
    bit_off = _strict_int(record.get("bitOffset"))
    rv = _strict_int(raw_value)
    if rv is None:
        return _fail("malformed_raw_value", **base)
    bl = parsed["bitLength"]
    if rv < 0 or rv > (1 << bl) - 1:
        return _fail("preview_raw_not_allowed_singleton", **base, rawValue=rv)
    cmpv = _compare_value(parsed, rv)
    if _lookup_maps(cmpv, parsed.get("ic") or []):
        return _fail("invalid_reserved", **base, rawValue=rv, value=cmpv)
    lo, hi = parsed.get("lower"), parsed.get("upper")
    if lo is not None and cmpv < lo:
        return _fail("out_of_bounds", **base, rawValue=rv, value=cmpv)
    if hi is not None and cmpv > hi:
        return _fail("out_of_bounds", **base, rawValue=rv, value=cmpv)
    mask_f = parsed.get("bitMask") or 0
    if mask_f and (rv & ~mask_f):
        return _fail("preview_raw_unrepresentable", **base, rawValue=rv)
    if parsed.get("kind") == "TEXTTABLE":
        allowed = {a for a, b, _ in (parsed.get("maps") or []) if a == b}
        if cmpv not in allowed:
            return _fail("preview_raw_not_allowed_singleton", **base, rawValue=rv)
    ins = insert_raw(parsed, original, byte_off, bit_off, rv)
    if not ins["ok"]:
        return _fail(ins["reason"], **base)
    after = ins["after"]
    if len(after) != len(original):
        return _fail("malformed_input", **base)
    decoded = decode_record(record, after)
    if not decoded.get("ok"):
        return _fail(decoded.get("reason") or "preview_raw_unrepresentable", **base, rawValue=rv)
    if decoded.get("raw") != rv:
        return _fail("preview_raw_unrepresentable", **base, rawValue=rv)
    changed = bytes(a ^ b for a, b in zip(original, after, strict=True))
    return _flags(
        {
            "ok": True,
            "reason": None,
            **base,
            "beforeHex": original.hex().upper(),
            "afterHex": after.hex().upper(),
            "changedBitMaskHex": changed.hex().upper(),
            "byteOffset": byte_off,
            "bitOffset": bit_off,
            "rawValue": rv,
        }
    )


def _iter_pool_records(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            v = json.loads(line)
            pools = v.get("pool_records") or {}
            for cat, blob in pools.items():
                for rec in (blob or {}).get("records") or []:
                    yield v.get("profile_id") or v.get("name"), cat, rec
            yield v.get("profile_id") or v.get("name"), "_variant", None


def _tally(cats: dict, samples: dict, profile, cat, rec, parsed, reason: str):
    cats.setdefault(cat, Counter())[reason] += 1
    key = f"{cat}:{reason}"
    if len(samples.setdefault(key, [])) < 3:
        samples[key].append(
            {
                "profile": profile,
                "at": rec.get("at"),
                "kind": parsed.get("kind") or "none",
                "formulaId": parsed.get("id_hex"),
                "byteOffset": rec.get("byteOffset"),
                "bitOffset": rec.get("bitOffset"),
            }
        )


def audit_variants(path: Path) -> dict:
    dec_cats: dict[str, Counter] = {}
    prev_cats: dict[str, Counter] = {}
    dec_samples: dict[str, list] = {}
    prev_samples: dict[str, list] = {}
    kinds: Counter = Counter()
    named: Counter = Counter()
    ledger: dict[str, dict] = {}
    n_var = 0
    for profile, cat, rec in _iter_pool_records(path):
        if rec is None:
            n_var += 1
            continue
        _text, parsed = formula_from_record(rec)
        kinds[f"{cat}:{parsed.get('kind') or 'none'}"] += 1
        if parsed.get("kind") == "COMPUCODE":
            named[parsed.get("codeFile") or "?"] += 1
        d = classify_decode(parsed, rec.get("byteOffset"), rec.get("bitOffset"))
        p = classify_preview(parsed, rec.get("byteOffset"), rec.get("bitOffset"))
        dr = "supported" if d["ok"] else (d.get("reason") or "unsupported")
        pr = "supported" if p["ok"] else (p.get("reason") or "unsupported")
        _tally(dec_cats, dec_samples, profile, cat, rec, parsed, dr)
        _tally(prev_cats, prev_samples, profile, cat, rec, parsed, pr)
        if not d["ok"]:
            txt = parsed.get("text") or ""
            key = f"{cat}:{dr}:{txt}"
            slot = ledger.setdefault(
                key,
                {
                    "category": cat,
                    "reason": dr,
                    "n": 0,
                    "formula": txt,
                    "kind": parsed.get("kind"),
                    "formulaId": parsed.get("id_hex"),
                    "profile": profile,
                    "at": rec.get("at"),
                    "sourceAvailability": "local-formula+libPORSCHE_CALC.so" if parsed.get("ok") else "parse-fail",
                    "next": "physical-capture" if parsed.get("ok") else "formula-syntax",
                },
            )
            slot["n"] += 1
    unique = sorted(ledger.values(), key=lambda r: (-r["n"], r["reason"], r["formula"][:80]))
    reason_counts = Counter(f"{u['category']}:{u['reason']}" for u in unique)
    return _flags(
        {
            "variants": n_var,
            "decode": {"perCategory": {k: dict(v) for k, v in dec_cats.items()}, "samples": dec_samples},
            "preview": {"perCategory": {k: dict(v) for k, v in prev_cats.items()}, "samples": prev_samples},
            "formulaKinds": dict(kinds),
            "compucodeNamed": dict(named),
            "unsupportedUnique": unique,
            "unsupportedUniqueReasonCounts": dict(reason_counts),
            "evidence": EVIDENCE,
        }
    )


def _parse_hex(s: str) -> bytes:
    h = s.strip().replace(" ", "")
    if not h or len(h) % 2:
        raise ValueError("odd hex")
    return bytes.fromhex(h)


def _load_record(variants: Path, profile_id: str, at: int | None, offset: int | None) -> dict:
    with variants.open(encoding="utf-8") as fh:
        for line in fh:
            v = json.loads(line)
            pid = v.get("profile_id") or v.get("name")
            if pid != profile_id:
                continue
            for cat, blob in (v.get("pool_records") or {}).items():
                for rec in (blob or {}).get("records") or []:
                    if at is not None and rec.get("at") == at:
                        rec = dict(rec)
                        rec["_category"] = cat
                        return rec
                    if offset is not None and rec.get("at") == offset:
                        rec = dict(rec)
                        rec["_category"] = cat
                        return rec
            raise FileNotFoundError(f"record not found profile={profile_id}")
    raise FileNotFoundError(f"profile not found: {profile_id}")


class _P(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps(_fail("cli_error", detail=message), ensure_ascii=False))
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    p = _P(
        prog="python -m scripts.diagnostics.x431_values",
        description="Offline X431 decode/preview. executionEnabled=false. No ECU I/O.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode", help="decode explicit response data bytes")
    d.add_argument("--variants", required=True)
    d.add_argument("--profile-id", required=True)
    d.add_argument("--at", type=int, default=None)
    d.add_argument("--record-offset", type=int, default=None)
    d.add_argument("--data-hex", required=True)
    pr = sub.add_parser("preview", help="coding buffer preview only")
    pr.add_argument("--variants", required=True)
    pr.add_argument("--profile-id", required=True)
    pr.add_argument("--at", type=int, default=None)
    pr.add_argument("--record-offset", type=int, default=None)
    pr.add_argument("--data-hex", required=True)
    pr.add_argument("--raw-value", type=int, required=True)
    au = sub.add_parser("audit", help="coverage over variants.jsonl")
    au.add_argument("--variants", required=True)
    au.add_argument("--out", default=None)
    args = p.parse_args(argv)
    try:
        if args.cmd == "audit":
            report = audit_variants(Path(args.variants))
            text = json.dumps(report, ensure_ascii=False, indent=2)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
            print(text)
            return 0
        rec = _load_record(Path(args.variants), args.profile_id, args.at, args.record_offset)
        data = _parse_hex(args.data_hex)
        if args.cmd == "decode":
            out = decode_record(rec, data)
        else:
            out = preview_coding(rec, data, args.raw_value)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 2
    except FileNotFoundError as e:
        print(json.dumps(_fail("cli_not_found", detail=str(e)), ensure_ascii=False))
        return 2
    except ValueError as e:
        print(json.dumps(_fail("cli_bad_hex", detail=str(e)), ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps(_fail("cli_error", detail=type(e).__name__), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
