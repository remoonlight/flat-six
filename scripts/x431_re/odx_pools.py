"""Five ODX pools. Native GetOdxSysAdd: 40a250 ident, 254 meas, 258 coding, 25c routines, 260 DTC."""
from __future__ import annotations

import re
import struct

RE_ID = re.compile(r"0x([0-9A-Fa-f]{8})(?![0-9A-Fa-f])")
RE_HEX_LIT = re.compile(r"0x([0-9A-Fa-f]+)(?![0-9A-Fa-f])", re.I)

POOL_NAMES = ("identity", "measurement", "coding", "routine", "dtc")
TOKEN_FIELDS = ("0x40a250", "0x40a254", "0x40a258", "0x40a25c", "0x40a260")


def _need(blob: bytes, off: int, n: int, what: str, limit: int | None = None) -> None:
    hi = len(blob) if limit is None else min(len(blob), limit)
    if off < 0 or n < 0 or off + n > hi:
        raise ValueError(f"truncated {what} at {off}+{n} limit={hi}")


def parse_five_pointers(blob: bytes, variant_off: int) -> dict:
    """Allow zero = missing pool. Non-zero pointers must lie after the header."""
    _need(blob, variant_off, 20, "five pointers")
    ptrs = list(struct.unpack_from("<5I", blob, variant_off))
    prev_nz = variant_off
    named = {}
    tokens = {}
    for i, p in enumerate(ptrs):
        if p == 0:
            named[POOL_NAMES[i]] = 0
            tokens[TOKEN_FIELDS[i]] = 0
            continue
        if p <= variant_off or p >= len(blob):
            raise ValueError(f"pointer {i} out of range {p}")
        if p < prev_nz:
            raise ValueError(f"pointers not increasing at {i}")
        prev_nz = p
        named[POOL_NAMES[i]] = p
        tokens[TOKEN_FIELDS[i]] = p
    return {
        "variant_off": variant_off,
        "consumed": 20,
        "pointers": ptrs,
        "named": named,
        "token_fields": tokens,
    }


def pool_end(ptrs: list[int], i: int, next_variant: int) -> int | None:
    if ptrs[i] == 0:
        return None
    for q in ptrs[i + 1 :]:
        if q:
            return q
    return next_variant


def parse_id_chain_table(blob: bytes, start: int, end: int, suffix: int, label: str) -> dict:
    """u32 count, then N x (7-byte prefix, u16le nlen, cstr, suffix). count 0 is empty."""
    _need(blob, start, 4, f"{label} count", end)
    count = struct.unpack_from("<I", blob, start)[0]
    if count == 0:
        if start + 4 != end:
            raise ValueError(f"{label}: count0 leftover {start + 4}..{end}")
        return {
            "kind": f"u32count_raw7_u16len_cstr_suf{suffix}",
            "label": label,
            "start": start,
            "end": start + 4,
            "count": 0,
            "records": [],
            "status": "empty",
        }
    if count > 4096:
        raise ValueError(f"{label}: bad count {count}")
    off = start + 4
    recs = []
    for i in range(count):
        _need(blob, off, 7 + 2 + suffix, f"{label} rec {i}", end)
        prefix = blob[off : off + 7]
        nlen = struct.unpack_from("<H", blob, off + 7)[0]
        if nlen < 2 or nlen > 256:
            raise ValueError(f"{label}: bad nlen {nlen} at {i}")
        _need(blob, off + 9, nlen + suffix, f"{label} body {i}", end)
        raw = blob[off + 9 : off + 9 + nlen]
        if b"\x00" not in raw:
            raise ValueError(f"{label}: name not NUL at {i}")
        text = raw.split(b"\x00", 1)[0].decode("ascii")
        suf = blob[off + 9 + nlen : off + 9 + nlen + suffix]
        ids = [int(x, 16) for x in RE_ID.findall(text)]
        recs.append(
            {
                "i": i,
                "at": off,
                "request_prefix_hex": prefix.hex(),
                "prefix_b0": prefix[0],
                "chain": text,
                "ids": ids,
                "id_hex": [f"{x:08X}" for x in ids],
                "suffix_hex": suf.hex(),
                "suffix_status": "candidate_until_native_proven",
            }
        )
        off += 9 + nlen + suffix
    pad = end - off
    if off != end:
        tail = blob[off:end]
        if not (0 < pad <= 3 and end == len(blob) and tail == b"\x00" * pad):
            raise ValueError(f"{label}: end {off} != pool end {end}")
    return {
        "kind": f"u32count_raw7_u16len_cstr_suf{suffix}",
        "label": label,
        "start": start,
        "end": off,
        "pool_end": end,
        "trailing_pad": pad if off != end else 0,
        "count": count,
        "records": recs,
        "status": "decoded",
    }


def parse_flagged_ptr_index(blob: bytes, start: int, pool_end: int, label: str) -> dict:
    _need(blob, start, 1, f"{label} count", pool_end)
    count = blob[start]
    if count == 0:
        if start + 1 != pool_end:
            raise ValueError(f"{label}: count0 leftover")
        return {
            "kind": "u8count_u32gag_u32ptr_u8flag_then_groups",
            "label": label,
            "start": start,
            "end": start + 1,
            "count": 0,
            "groups": [],
            "status": "empty",
        }
    if count > 64:
        raise ValueError(f"{label}: bad count {count}")
    off = start + 1
    recs = []
    prev_ptr = off
    for i in range(count):
        _need(blob, off, 9, f"{label} idx {i}", pool_end)
        gag, ptr = struct.unpack_from("<II", blob, off)
        flag = blob[off + 8]
        if ptr < start or ptr >= pool_end:
            raise ValueError(f"{label}: ptr {ptr} outside pool")
        if ptr < prev_ptr:
            raise ValueError(f"{label}: group ptr not monotonic {ptr} < {prev_ptr}")
        recs.append({"i": i, "gag_id": gag, "gag_hex": f"{gag:08X}", "payload_off": ptr, "flag": flag})
        prev_ptr = ptr
        off += 9
    if recs[0]["payload_off"] != off:
        raise ValueError(f"{label}: first payload {recs[0]['payload_off']} != index_end {off}")
    groups = []
    for i, r in enumerate(recs):
        nxt = recs[i + 1]["payload_off"] if i + 1 < len(recs) else pool_end
        groups.append({**r, **parse_group_payload(blob, r["payload_off"], nxt, label, i)})
    if groups[-1]["end"] != pool_end:
        raise ValueError(f"{label}: last group end {groups[-1]['end']} != {pool_end}")
    return {
        "kind": "u8count_u32gag_u32ptr_u8flag_then_groups",
        "label": label,
        "start": start,
        "end": pool_end,
        "count": count,
        "index_end": off,
        "groups": groups,
        "status": "decoded",
    }


def parse_group_payload(blob: bytes, start: int, end: int, label: str, gi: int) -> dict:
    if end < start:
        raise ValueError(f"{label}[{gi}]: negative span")
    _need(blob, start, 5, f"{label}[{gi}] count", end)
    n = struct.unpack_from("<I", blob, start)[0]
    if n < 1 or n > 4096:
        raise ValueError(f"{label}[{gi}]: bad n {n}")
    format_flag = blob[start + 4]
    packed = start + 5 + n * 16
    if packed == end:
        rows = []
        off = start + 5
        for i in range(n):
            _need(blob, off, 16, f"{label}[{gi}] row {i}", end)
            raw = blob[off : off + 16]
            name_id = struct.unpack_from("<I", raw, 5)[0]
            formula_id = struct.unpack_from("<I", raw, 12)[0]
            rows.append(
                {
                    "i": i,
                    "at": off,
                    "raw_hex": raw.hex(),
                    "dstream_name_id": name_id,
                    "dstream_name_id_hex": f"{name_id:08X}",
                    "express_id": formula_id,
                    "express_id_hex": f"{formula_id:08X}",
                    "u32_at_0_4": list(struct.unpack_from("<I", raw, 0))
                    + list(struct.unpack_from("<I", raw, 4)),
                }
            )
            off += 16
        return {
            "payload_kind": "u32count_u8_then_16byte_rows",
            "header_hex": blob[start : start + 5].hex(),
            "format_flag": format_flag,
            "item_count": n,
            "end": end,
            "rows": rows,
        }
    _need(blob, start + 4, n * 4, f"{label}[{gi}] ptrs", end)
    ptrs = [struct.unpack_from("<I", blob, start + 4 + 4 * i)[0] for i in range(n)]
    if ptrs[0] != start + 4 + n * 4:
        raise ValueError(f"{label}[{gi}]: first leaf {ptrs[0]} != table_end")
    leaves = []
    prev = start
    for i, p in enumerate(ptrs):
        if p < start or p > end:
            raise ValueError(f"{label}[{gi}]: leaf ptr {p}")
        if p < prev:
            raise ValueError(f"{label}[{gi}]: leaf ptr not monotonic")
        nxt = ptrs[i + 1] if i + 1 < len(ptrs) else end
        _need(blob, p, nxt - p, f"{label}[{gi}] leaf {i}", end)
        chunk = blob[p:nxt]
        text = chunk.decode("latin1")
        ids = [int(x, 16) for x in RE_ID.findall(text)]
        raw_hex_lits = ["0x" + m.group(1).upper() for m in RE_HEX_LIT.finditer(text) if len(m.group(1)) != 8]
        leaves.append(
            {
                "i": i,
                "at": p,
                "end": nxt,
                "size": nxt - p,
                "raw_hex": chunk.hex(),
                "ids": ids,
                "id_hex": [f"{x:08X}" for x in ids],
                "raw_hex_lits": raw_hex_lits,
            }
        )
        prev = p
    if leaves[-1]["end"] != end:
        raise ValueError(f"{label}[{gi}]: last leaf end {leaves[-1]['end']} != {end}")
    return {
        "payload_kind": "u32count_u32leafptrs_id_leaves",
        "item_count": n,
        "end": end,
        "leaf_ptrs": ptrs,
        "leaves": leaves,
    }


def parse_dtc_labels(blob: bytes, start: int, end: int, label: str) -> dict:
    """u32 count, u32 service header, N × (u32 numeric, u32 TEXTID, u16 nlen, name, u8 statusKind)."""
    _need(blob, start, 8, f"{label} hdr", end)
    count, sid = struct.unpack_from("<II", blob, start)
    if count == 0:
        if start + 8 != end:
            raise ValueError(f"{label}: count0 leftover")
        return {
            "kind": "u32count_u32sid_then_u32dtc_u32textid_u16len_name_u8statusKind",
            "label": label,
            "start": start,
            "end": start + 8,
            "count": 0,
            "service_header": sid,
            "records": [],
            "status": "empty",
        }
    if count > 10000:
        raise ValueError(f"{label}: bad count {count}")
    off = start + 8
    recs = []
    for i in range(count):
        _need(blob, off, 11, f"{label} rec {i}", end)
        numeric, text_id = struct.unpack_from("<II", blob, off)
        nlen = struct.unpack_from("<H", blob, off + 8)[0]
        if nlen < 1 or nlen > 128:
            raise ValueError(f"{label}: bad nlen {nlen} at {i}")
        _need(blob, off + 10, nlen + 1, f"{label} name {i}", end)
        raw = blob[off + 10 : off + 10 + nlen]
        if b"\x00" in raw:
            code = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
        else:
            code = raw.decode("latin1", "replace")
        status_kind = blob[off + 10 + nlen]
        recs.append(
            {
                "i": i,
                "at": off,
                "numeric_dtc": numeric,
                "numeric_dtc_hex": f"{numeric:08X}",
                "text_id": text_id,
                "text_id_hex": f"{text_id:08X}",
                "code": code,
                "statusKind": status_kind,
                "note": "statusKind is table field, not live status byte",
            }
        )
        off += 11 + nlen
    pad = end - off
    if off != end:
        tail = blob[off:end]
        if not (0 < pad <= 3 and end == len(blob) and tail == b"\x00" * pad):
            raise ValueError(f"{label}: end {off} != {end}")
    return {
        "kind": "u32count_u32sid_then_u32dtc_u32textid_u16len_name_u8statusKind",
        "label": label,
        "start": start,
        "end": off,
        "pool_end": end,
        "trailing_pad": pad if off != end else 0,
        "count": count,
        "service_header": sid,
        "service_header_hex": f"{sid:08X}",
        "records": recs,
        "status": "decoded",
    }

