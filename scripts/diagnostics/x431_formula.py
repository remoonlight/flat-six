"""Strict X431 formula text parser. No eval. Fail closed."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_KIND = re.compile(r"^([A-Z][A-Z0-9-]*)\s*:")
_KV = re.compile(r"([A-Za-z][A-Za-z0-9]*)=(.*?)(?=,(?:[A-Za-z][A-Za-z0-9]*=)|;|$)")
_HEX_LIT = r"0x-[0-9A-Fa-f]+|0x[0-9A-Fa-f]+|-?\d+|[0-9A-Fa-f]+"
_MAP = re.compile(rf"\[({_HEX_LIT})\]->(0x[0-9A-Fa-f]+)")
_RANGE = re.compile(rf"\[({_HEX_LIT}),({_HEX_LIT})\]->(0x[0-9A-Fa-f]+)")
_HEX_NEG = re.compile(r"^0x-([0-9A-Fa-f]+)$", re.I)
_HEX_POS = re.compile(r"^0x([0-9A-Fa-f]+)$", re.I)
_DEC_INT = re.compile(r"^-?\d+$")
_BARE_HEX = re.compile(r"^[0-9A-Fa-f]+$")
_KNOWN_KINDS = frozenset(
    {"IDENTICAL", "LINEAR", "TEXTTABLE", "SCALE-LINEAR", "TAB-INTP", "COMPUCODE"}
)
_PIECEWISE = frozenset({"SCALE-LINEAR", "TAB-INTP"})
_SEG_KEYS = frozenset({"Lower", "Upper", "Xa", "Xb", "Xc"})
_MAX_DEC_DIGITS = 24
_MAX_DEC_ADJ = 40
_MAX_PREC = 18
_TERMINATIONS = frozenset({"ZERO", "END-OF-PDU"})


def _num_token(tok: str) -> tuple[int | None, str]:
    s = tok.strip()
    if not s:
        return None, "empty"
    if s.lower() == "null":
        return None, "null"
    m = _HEX_NEG.fullmatch(s)
    if m:
        return -int(m.group(1), 16), "ok"
    m = _HEX_POS.fullmatch(s)
    if m:
        return int(m.group(1), 16), "ok"
    if _DEC_INT.fullmatch(s):
        if len(s.lstrip("-")) > 16:
            return None, "bad"
        return int(s, 10), "ok"
    if _BARE_HEX.fullmatch(s) and re.search(r"[A-Fa-f]", s):
        return int(s, 16), "ok"
    return None, "bad"


def _mask_token(tok: str) -> tuple[int | None, str]:
    s = tok.strip()
    if not s:
        return None, "empty"
    if s.lower() == "null":
        return None, "null"
    m = _HEX_POS.fullmatch(s)
    if m:
        return int(m.group(1), 16), "ok"
    if _BARE_HEX.fullmatch(s):
        return int(s, 16), "ok"
    return None, "bad"


def _map_token(tok: str) -> tuple[int | None, str]:
    s = tok.strip()
    n, st = _num_token(s)
    if st == "ok":
        return n, st
    if _BARE_HEX.fullmatch(s):
        return int(s, 16), "ok"
    return None, "bad"


def _dec_token(tok: str) -> tuple[Decimal | None, str]:
    s = (tok or "").strip()
    if not s:
        return None, "empty"
    if s.lower() == "null":
        return None, "null"
    if s.lower() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
        return None, "nonfinite"
    if re.search(r"[eE]", s):
        try:
            exp = int(s.lower().split("e", 1)[1])
        except ValueError:
            return None, "bad"
        if abs(exp) > 40:
            return None, "exponent"
    compact = s.replace("-", "").replace("+", "").replace(".", "")
    compact = re.split(r"[eE]", compact, maxsplit=1)[0]
    if len(compact) > _MAX_DEC_DIGITS:
        return None, "precision"
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None, "bad"
    if not d.is_finite():
        return None, "nonfinite"
    if abs(d.adjusted()) > _MAX_DEC_ADJ:
        return None, "exponent"
    return d, "ok"


def _tid(tok: str) -> str:
    s = tok.strip().upper()
    if s.startswith("0X"):
        s = s[2:]
    return s


def _overlap_conflict(rows: list[tuple[int, int, str]]) -> bool:
    for i, (a0, a1, ta) in enumerate(rows):
        for b0, b1, tb in rows[i + 1 :]:
            if ta == tb:
                continue
            if a0 <= b1 and b0 <= a1:
                return True
    return False


def parse_formula_text(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "reason": "malformed_formula", "kind": None}
    m = _KIND.match(text.strip())
    if not m:
        return {"ok": False, "reason": "malformed_formula", "kind": None}
    kind = m.group(1)
    if kind not in _KNOWN_KINDS:
        return {"ok": False, "reason": f"kind_{kind}", "kind": kind}
    body = text.strip()[m.end() :]
    fields: dict[str, str] = {}
    maps: list[tuple[int, int, str]] = []
    ic: list[tuple[int, int, str]] = []
    segments: list[dict] = []
    in_ic = False
    for sec in [p.strip() for p in body.split(";") if p.strip()]:
        if sec.startswith("Ic:") or sec.startswith("IC:"):
            in_ic = True
            sec = sec.split(":", 1)[1]
        dest = ic if in_ic else maps
        for rm in _RANGE.finditer(sec):
            a, sa = _map_token(rm.group(1))
            b, sb = _map_token(rm.group(2))
            if sa != "ok" or sb != "ok" or a is None or b is None:
                return {"ok": False, "reason": "malformed_formula", "kind": kind}
            if a > b:
                return {"ok": False, "reason": "malformed_range_reversed", "kind": kind}
            dest.append((a, b, _tid(rm.group(3))))
        rest = _RANGE.sub("", sec)
        for sm in _MAP.finditer(rest):
            a, st = _map_token(sm.group(1))
            if st != "ok" or a is None:
                return {"ok": False, "reason": "malformed_formula", "kind": kind}
            dest.append((a, a, _tid(sm.group(2))))
        rest = _MAP.sub("", rest)
        if kind in {"IDENTICAL", "COMPUCODE"}:
            if rest.startswith("Standar,"):
                rest = rest[len("Standar,") :]
            elif rest.startswith("MinMax,"):
                rest = rest[len("MinMax,") :]
                fields.setdefault("LengthInfo", "MinMax")
        sec_kv: dict[str, str] = {}
        if re.search(r"\[|->", rest):
            return {"ok": False, "reason": "malformed_mapping", "kind": kind}
        for km in _KV.finditer(rest):
            key, val = km.group(1), km.group(2).strip().rstrip(",").strip()
            if key in sec_kv and sec_kv[key] != val:
                return {"ok": False, "reason": "duplicate_key", "kind": kind}
            sec_kv[key] = val
        if _KV.sub("", rest).strip(", \t\r\n"):
            return {"ok": False, "reason": "malformed_formula", "kind": kind}
        if kind in _PIECEWISE and not in_ic and any(k in sec_kv for k in _SEG_KEYS):
            segments.append(dict(sec_kv))
            for key, val in sec_kv.items():
                if key in _SEG_KEYS:
                    continue
                if key in fields and fields[key] != val:
                    return {"ok": False, "reason": "duplicate_key", "kind": kind}
                fields[key] = val
        else:
            for key, val in sec_kv.items():
                if key in fields and fields[key] != val:
                    return {"ok": False, "reason": "duplicate_key", "kind": kind}
                fields[key] = val
    if _overlap_conflict(maps) or _overlap_conflict(ic):
        return {"ok": False, "reason": "malformed_map_overlap", "kind": kind}

    def _req_int(key: str, default_absent):
        if key not in fields:
            return default_absent, "absent"
        n, st = _num_token(fields[key])
        if st == "null":
            return None, "null"
        if st != "ok":
            return None, "bad"
        return n, "ok"

    bit_length, st_bl = _req_int("BitLength", None)
    if "BitLength" in fields and st_bl != "ok":
        return {"ok": False, "reason": "malformed_bitlength", "kind": kind}
    bit_mask = 0
    if "BitMask" in fields:
        bit_mask, st_bm = _mask_token(fields["BitMask"])
        if st_bm == "null":
            bit_mask = 0
        elif st_bm != "ok":
            return {"ok": False, "reason": "malformed_bitmask", "kind": kind}
    high_low, st_hl = _req_int("HighLow", None)
    if "HighLow" in fields and st_hl not in {"ok", "null"}:
        return {"ok": False, "reason": "malformed_highlow", "kind": kind}

    def _bound_from(src: dict, key: str):
        if key not in src:
            return None, "absent"
        n, st = _num_token(src[key])
        if st == "null":
            return None, "null"
        if st != "ok":
            return None, "bad"
        return n, "ok"

    lower, st_lo = _bound_from(fields, "Lower")
    if "Lower" in fields and st_lo not in {"ok", "null", "absent"}:
        return {"ok": False, "reason": "malformed_bound", "kind": kind}
    if st_lo == "null":
        lower = None
    upper, st_up = _bound_from(fields, "Upper")
    if "Upper" in fields and st_up not in {"ok", "null", "absent"}:
        return {"ok": False, "reason": "malformed_bound", "kind": kind}
    if st_up == "null":
        upper = None
    if lower is not None and upper is not None and lower > upper:
        return {"ok": False, "reason": "malformed_range_reversed", "kind": kind}

    xa = xb = xc = None
    for key, attr in (("Xa", "xa"), ("Xb", "xb"), ("Xc", "xc")):
        if key not in fields:
            continue
        d, st = _dec_token(fields[key])
        if st != "ok":
            return {"ok": False, "reason": "malformed_coeff", "kind": kind}
        if attr == "xa":
            xa = d
        elif attr == "xb":
            xb = d
        else:
            xc = d

    parsed_segs: list[dict] = []
    if kind in _PIECEWISE:
        for raw_seg in segments:
            lo, st = _bound_from(raw_seg, "Lower")
            hi, su = _bound_from(raw_seg, "Upper")
            if st not in {"ok"} or su not in {"ok"} or lo is None or hi is None:
                return {"ok": False, "reason": "malformed_bound", "kind": kind}
            if lo > hi:
                return {"ok": False, "reason": "malformed_range_reversed", "kind": kind}
            coeffs = {}
            for key, attr in (("Xa", "xa"), ("Xb", "xb"), ("Xc", "xc")):
                if key not in raw_seg:
                    return {"ok": False, "reason": "malformed_coeff", "kind": kind}
                d, stc = _dec_token(raw_seg[key])
                if stc != "ok":
                    return {"ok": False, "reason": "malformed_coeff", "kind": kind}
                coeffs[attr] = d
            parsed_segs.append({"lower": lo, "upper": hi, **coeffs})
        if not parsed_segs:
            return {"ok": False, "reason": "malformed_coeff", "kind": kind}

    radix = 10
    if "Radix" in fields:
        radix, st_r = _num_token(fields["Radix"])
        if st_r != "ok" or radix not in (2, 10, 16):
            return {"ok": False, "reason": "malformed_radix", "kind": kind}
    precision = None
    if "Precision" in fields:
        precision, st_p = _num_token(fields["Precision"])
        if st_p == "null":
            precision = None
        elif st_p != "ok" or precision is None or precision < 0 or precision > _MAX_PREC:
            return {"ok": False, "reason": "malformed_precision", "kind": kind}

    min_length = max_length = None
    if "MinLength" in fields:
        min_length, st = _num_token(fields["MinLength"])
        if st != "ok" or min_length is None or min_length < 0:
            return {"ok": False, "reason": "malformed_formula", "kind": kind}
    if "MaxLength" in fields:
        max_length, st = _num_token(fields["MaxLength"])
        if st != "ok" or max_length is None or max_length < 0:
            return {"ok": False, "reason": "malformed_formula", "kind": kind}
    if min_length is not None and max_length is not None and min_length > max_length:
        return {"ok": False, "reason": "malformed_formula", "kind": kind}
    termination = fields.get("Termination")
    if termination is not None and termination.upper() not in _TERMINATIONS:
        return {"ok": False, "reason": "malformed_formula", "kind": kind}

    bdt = fields.get("BaseDataType") or fields.get("DataType")
    dt = fields.get("DataType")
    if bdt is not None and not re.fullmatch(r"A_[A-Z0-9]+", bdt):
        return {"ok": False, "reason": "malformed_formula", "kind": kind}
    if dt is not None and not re.fullmatch(r"A_[A-Z0-9]+", dt):
        return {"ok": False, "reason": "malformed_formula", "kind": kind}

    signed_literals = any(a < 0 or b < 0 for a, b, _ in maps + ic)
    if lower is not None and lower < 0:
        signed_literals = True
    if upper is not None and upper < 0:
        signed_literals = True
    for seg in parsed_segs:
        if seg["lower"] < 0 or seg["upper"] < 0:
            signed_literals = True

    return {
        "ok": True,
        "reason": None,
        "kind": kind,
        "fields": fields,
        "maps": maps,
        "ic": ic,
        "xa": xa,
        "xb": xb,
        "xc": xc,
        "segments": parsed_segs,
        "bitLength": bit_length,
        "bitMask": 0 if bit_mask is None else bit_mask,
        "highLow": high_low,
        "lower": lower,
        "upper": upper,
        "baseDataType": bdt,
        "dataType": dt,
        "encoding": fields.get("Encoding") or "Undefined",
        "radix": radix,
        "precision": precision,
        "unit": fields.get("Unit"),
        "signedLiterals": signed_literals,
        "minLength": min_length,
        "maxLength": max_length,
        "termination": termination.upper() if isinstance(termination, str) else None,
        "lengthInfo": fields.get("LengthInfo"),
        "codeFile": fields.get("CodeFile"),
        "entryPoint": fields.get("EntryPoint"),
    }


def formula_from_record(record: dict) -> tuple[str | None, dict]:
    raw = record.get("formula")
    text = None
    fid = None
    if isinstance(raw, dict):
        text = raw.get("text")
        fid = raw.get("id_hex") or record.get("formulaID") or record.get("formulaId")
    elif isinstance(raw, str):
        text = raw
        fid = record.get("formulaID") or record.get("formulaId")
    if not isinstance(text, str) or not text:
        return None, {"ok": False, "reason": "missing_formula", "kind": None, "id_hex": fid}
    parsed = parse_formula_text(text)
    parsed["id_hex"] = fid
    parsed["text"] = text
    return text, parsed
