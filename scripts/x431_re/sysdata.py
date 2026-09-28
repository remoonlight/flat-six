#!/usr/bin/env python3
"""PORSCHE_SYS_DATA.BIN logical parse. Research-only; writePayload=null."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILE_LOADER = REPO_ROOT / ".local/x431-re/2026-09-27-decode/file-loader"
EXPECTED_BIN_SHA = "9102da18191fcc2128f0507ffafc7359c77a8efd7a004c2b19f7c4854fc79f43"
FAMILY_9X1 = 13
MAX_VARIANTS = 64
MAX_FAMILIES = 64
MAX_SYSTEMS = 64
MAX_SUBSYS = 64
MAX_PTRS = 32
MAX_PARAMS = 32
CMD_LEN_MAX = 16
SLOT_TOKEN = ("TOKEN+0x604", "TOKEN+0x608", "TOKEN+0x60c", "TOKEN+0x610", "TOKEN+0x614", "TOKEN+0x618")

DISABLED = {"executionEnabled": False, "liveVerified": False, "writePayload": None}


class CodecUnavailable(RuntimeError):
    """Private yzjm codec missing; fail closed, no decrypt bypass."""


def _codec():
    if not (FILE_LOADER / "yzjm_codec.py").is_file():
        raise CodecUnavailable(f"FILE_LOADER missing: {FILE_LOADER.as_posix()}")
    if str(FILE_LOADER) not in sys.path:
        sys.path.insert(0, str(FILE_LOADER))
    from yzjm_codec import TABLE_PATH, fread_ex, parse_yzjm_fopenex, transform_words  # noqa: E402

    return TABLE_PATH, fread_ex, parse_yzjm_fopenex, transform_words


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def decrypt_logical(raw: bytes) -> tuple[dict, bytes]:
    TABLE_PATH, fread_ex, parse_yzjm_fopenex, transform_words = _codec()
    hdr = parse_yzjm_fopenex(raw)
    keys = transform_words(
        TABLE_PATH.read_bytes(),
        hdr["brand"].encode("ascii"),
        hdr["version"].encode("ascii"),
        hdr["field_u32"],
    )
    payload = fread_ex(raw, 0, len(raw) - hdr["payload_off"], keys, header_off=hdr["payload_off"])
    return hdr, payload


def recover_logical(bin_path: Path, out_path: Path) -> dict:
    raw = bin_path.read_bytes()
    sha = sha256_bytes(raw)
    if sha != EXPECTED_BIN_SHA:
        return {"ok": False, "class": "not-confirmed", "reason": "bin hash mismatch", "sha256": sha, "expect": EXPECTED_BIN_SHA}
    try:
        hdr, payload = decrypt_logical(raw)
    except CodecUnavailable as e:
        return {"ok": False, "class": "missing-external-source", "stop": "FILE_LOADER yzjm_codec", "reason": str(e), "sha256": sha}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    return {
        "ok": True,
        "class": "static-source-confirmed",
        "sha256": sha,
        "logicalSha256": sha256_bytes(payload),
        "logicalBytes": len(payload),
        "header": {k: hdr[k] for k in ("brand", "version", "payload_off", "field_u32")},
        "scratch": str(out_path),
        "filename": "PORSCHE_SYS_DATA.BIN",
        "codec": {"class": "private-local-yzjm", "loader": FILE_LOADER.as_posix(), "noAccessBypass": True},
        **DISABLED,
    }


COMMON_COMM_KINDS = {0x10, 0x30, 0x50}
SPECIAL_COMM_KINDS = {1, 2, 4, 0x20, 0x60}


def _need(blob: bytes, off: int, n: int = 1) -> None:
    if off < 0:
        raise ValueError(f"negative offset {off}")
    if off + n > len(blob):
        raise ValueError(f"oob {off}+{n}")


def _u32(blob: bytes, off: int) -> int:
    _need(blob, off, 4)
    return struct.unpack_from("<I", blob, off)[0]


def _i32(blob: bytes, off: int) -> int:
    _need(blob, off, 4)
    return struct.unpack_from("<i", blob, off)[0]


def _file_ptr(blob: bytes, off: int) -> int:
    signed = _i32(blob, off)
    if signed < 0:
        raise ValueError(f"negative offset {signed} at {off}")
    ptr = _u32(blob, off)
    if ptr >= len(blob):
        raise ValueError(f"ptr oob {ptr}")
    return ptr


def parse_family_index(blob: bytes) -> list[dict]:
    if not blob:
        raise ValueError("empty")
    n = blob[0]
    if n > MAX_FAMILIES or 1 + 5 * n > len(blob):
        raise ValueError(f"bad family count {n}")
    rows = []
    o = 1
    for _ in range(n):
        fid = blob[o]
        off = _file_ptr(blob, o + 1)
        rows.append({"familyId": fid, "off": off})
        o += 5
    return rows


def parse_cmd(blob: bytes, off: int) -> dict | None:
    """FromAddrGetCommand: SID, b1, n, n payload bytes; wire omits n. n=0xFF unsupported."""
    if off < 0:
        raise ValueError(f"negative offset {off}")
    if off + 3 > len(blob):
        return None
    sid, b1, n = blob[off], blob[off + 1], blob[off + 2]
    if n == 0xFF:
        return {
            "off": off,
            "sid": sid,
            "b1": b1,
            "payloadLen": n,
            "class": "unsupported-structured",
            "reason": "n=0xFF sentinel; no huge read",
            **DISABLED,
        }
    if n > CMD_LEN_MAX or off + 3 + n > len(blob):
        return {"off": off, "class": "not-confirmed", "reason": "cmd length oob", "sid": sid, "n": n}
    payload = blob[off + 3 : off + 3 + n]
    wire = bytes([sid, b1]) + payload
    role = "sessionPrecondition" if sid == 0x10 else "dtcRead" if sid in (0x18, 0x19) else "dtcClear" if sid == 0x14 else "other"
    return {
        "off": off,
        "sid": sid,
        "b1": b1,
        "payloadLen": n,
        "payloadHex": payload.hex().upper(),
        "tableHex": blob[off : off + 3 + n].hex().upper(),
        "wireHex": wire.hex().upper(),
        "role": role,
        "class": "static-source-confirmed",
        **DISABLED,
    }


def parse_dtc_table(blob: bytes, off: int) -> dict:
    if off < 0:
        raise ValueError(f"negative offset {off}")
    if off + 12 > len(blob):
        raise ValueError(f"dtc table oob {off}")
    style = blob[off]
    u32_at_1 = _u32(blob, off + 1)
    meta = blob[off + 5 : off + 11]
    read_count = blob[off + 11]
    if read_count > MAX_PTRS:
        raise ValueError(f"readCount {read_count}")
    pos = off + 12
    reads = []
    for _ in range(read_count):
        ptr = _file_ptr(blob, pos)
        pos += 4
        reads.append(None if ptr == 0 else parse_cmd(blob, ptr))
    if pos + 2 > len(blob):
        raise ValueError("clear header oob")
    clear_type = blob[pos]
    clear_count = blob[pos + 1]
    pos += 2
    if clear_count > MAX_PTRS:
        raise ValueError(f"clearCount {clear_count}")
    clears = []
    for _ in range(clear_count):
        ptr = _file_ptr(blob, pos)
        pos += 4
        clears.append(None if ptr == 0 else parse_cmd(blob, ptr))
    if pos + 2 > len(blob):
        raise ValueError("otherCount oob")
    other_count = struct.unpack_from("<H", blob, pos)[0]
    pos += 2
    if other_count > MAX_PTRS:
        raise ValueError(f"otherCount {other_count}")
    others = []
    for _ in range(other_count):
        others.append(_file_ptr(blob, pos))
        pos += 4
    session = [c for c in reads if c and c.get("role") == "sessionPrecondition"]
    dtc_reads = [c for c in reads if c and c.get("role") == "dtcRead"]
    clear_cmds = [c for c in clears if c and c.get("sid") == 0x14]
    return {
        "off": off,
        "end": pos,
        "style": style,
        "u32AtPlus1": u32_at_1,
        "metadataHex": meta.hex().upper(),
        "readCount": read_count,
        "read": reads,
        "sessionPrecondition": session,
        "dtcReadSequence": dtc_reads,
        "dtcRead": dtc_reads[-1] if dtc_reads else None,
        "clearType": clear_type,
        "clearCount": clear_count,
        "clear": clears,
        "dtcClear": clear_cmds[-1] if clear_cmds else None,
        "otherCount": other_count,
        "otherPointers": others,
        "class": "static-source-confirmed",
        **DISABLED,
    }


def parse_subsys(blob: bytes, off: int, *, target_names: set[str] | None = None) -> dict:
    if off < 0:
        raise ValueError(f"negative offset {off}")
    if off >= len(blob):
        raise ValueError(f"subsys oob {off}")
    count = blob[off]
    if count > MAX_SUBSYS:
        raise ValueError(f"subsys count {count}")
    pos = off + 1
    variants = []
    for _ in range(count):
        if pos + 2 > len(blob):
            raise ValueError("subsys strlen oob")
        slen = struct.unpack_from("<H", blob, pos)[0]
        pos += 2
        if slen < 1 or pos + slen + 24 > len(blob):
            raise ValueError(f"malformed string length {slen} at {pos}")
        raw = blob[pos : pos + slen]
        if raw[-1] != 0:
            raise ValueError("string length must include NUL")
        if b"\x00" in raw[:-1]:
            raise ValueError("embedded NUL in name")
        name = raw[:-1].decode("latin1")
        pos += slen
        slots = []
        for _i in range(6):
            signed = _i32(blob, pos)
            if signed < 0:
                raise ValueError(f"negative offset {signed} at {pos}")
            ptr = _u32(blob, pos)
            if ptr and ptr >= len(blob):
                raise ValueError(f"ptr oob {ptr}")
            slots.append(ptr)
            pos += 4
        dtc = parse_dtc_table(blob, slots[2]) if slots[2] else None
        bind = None if target_names is None else name in target_names
        variants.append(
            {
                "name": name,
                "slots": slots,
                "slotTokens": list(SLOT_TOKEN),
                "dtcTableOff": slots[2] or None,
                "dtc": dtc,
                "baseSys": name.endswith("BaseSys"),
                "bindTarget": bind,
                "fittedClaim": False,
                "class": "static-source-confirmed",
                **DISABLED,
            }
        )
    first = variants[0]["name"] if variants else None
    return {
        "off": off,
        "count": count,
        "end": pos,
        "name": first,
        "variants": variants,
        "class": "static-source-confirmed",
        **DISABLED,
    }


def parse_comm(blob: bytes, off: int, end: int) -> dict:
    if off < 0 or end < 0:
        raise ValueError(f"negative offset {off}/{end}")
    if off >= end or off >= len(blob):
        raise ValueError(f"comm oob {off}")
    kind = blob[off]
    token = {
        "TOKEN+0x5fc": {"size": 4, "src": "family-link commOff POINTER", "valueHex": f"{off:08X}", "insn": "0x1573a"},
        "TOKEN+0x600": {"size": 4, "src": "family-link serviceOff POINTER", "valueHex": f"{end:08X}", "insn": "0x1574a"},
        "TOKEN+0x63d": {"size": 1, "src": "blob[commOff] protocolKind", "value": kind, "insn": "0x1575a"},
    }
    if kind in SPECIAL_COMM_KINDS:
        span = blob[off:end]
        return {
            "off": off,
            "end": end,
            "protocolKind": kind,
            "token": token,
            "rawHex": span.hex().upper(),
            "class": "unsupported-structured",
            "reason": "kind diverted before common 0x1595e (1/2/4/0x20/0x60)",
            "derived11bit": {"class": "unsupported-structured", "formula": None},
            **DISABLED,
        }
    if kind not in COMMON_COMM_KINDS:
        span = blob[off:end]
        return {
            "off": off,
            "end": end,
            "protocolKind": kind,
            "token": token,
            "rawHex": span.hex().upper(),
            "class": "unsupported-structured",
            "reason": "kind not in common branch 0x1595e (10/30/50)",
            "derived11bit": {"class": "unsupported-structured", "formula": None},
            **DISABLED,
        }
    _need(blob, off, 3)
    if off + 3 > end:
        raise ValueError(f"kind header oob {off}")
    token641 = blob[off + 1]
    pair_count = blob[off + 2]
    token["TOKEN+0x641"] = {"size": 1, "src": "commOff+1", "value": token641, "insn": "0x15960"}
    token["TOKEN+0x64e"] = {"size": 1, "src": "commOff+2 pairCount", "value": pair_count, "insn": "0x15972"}
    if pair_count < 1 or pair_count > 8:
        raise ValueError(f"pairCount {pair_count}")
    pos = off + 3
    pairs = []
    for _ in range(pair_count):
        if pos + 9 > end:
            raise ValueError("pair header oob")
        stored_tx, stored_rx = _u32(blob, pos), _u32(blob, pos + 4)
        pos += 8
        mode = blob[pos]
        pos += 1
        extra = None
        if mode == 5:
            if pos + 4 > end:
                raise ValueError("mode5 extra oob")
            extra = _u32(blob, pos)
            pos += 4
        if pos >= end:
            raise ValueError("paramCount oob")
        param_count = blob[pos]
        pos += 1
        if param_count > MAX_PARAMS:
            raise ValueError(f"paramCount {param_count}")
        params = []
        for _p in range(param_count):
            if pos + 4 > end:
                raise ValueError("param oob")
            params.append(_u32(blob, pos))
            pos += 4
        pairs.append(
            {
                "storedTx": stored_tx,
                "storedRx": stored_rx,
                "storedTxHex": f"{stored_tx:08X}",
                "storedRxHex": f"{stored_rx:08X}",
                "mode": mode,
                "mode5Extra": extra,
                "paramCount": param_count,
                "params": params,
                "role": None,
                "derived11bit": {
                    "txHex": f"{stored_tx >> 5:03X}",
                    "rxHex": f"{stored_rx >> 5:03X}",
                    "formula": "stored_u32_le>>5",
                    "hardwareConversionNativeConfirmed": False,
                    "class": "derived-candidate",
                    "notCaptureObserved": True,
                    "notLiveQualified": True,
                },
            }
        )
    if pos >= end:
        raise ValueError("trailCount oob")
    trail_count = blob[pos]
    pos += 1
    if trail_count > MAX_PARAMS:
        raise ValueError(f"trailCount {trail_count}")
    trail = []
    for _ in range(trail_count):
        if pos + 4 > end:
            raise ValueError("trail oob")
        trail.append(_u32(blob, pos))
        pos += 4
    three = []
    for _ in range(3):
        if pos + 4 > end:
            raise ValueError("trailing three u32 oob")
        three.append(_u32(blob, pos))
        pos += 4
    multi = pair_count > 1
    derived = {
        "formula": "stored_u32_le>>5",
        "hardwareConversionNativeConfirmed": False,
        "class": "derived-candidate",
        "notCaptureObserved": True,
        "notLiveQualified": True,
        "selectedPairUnknown": multi,
        "pairChoice": {"class": "unproven-runtime", "selectedPair": None},
        "pairs": [{"txHex": x["derived11bit"]["txHex"], "rxHex": x["derived11bit"]["rxHex"], "mode": x["mode"]} for x in pairs],
    }
    if not multi:
        derived["txHex"] = pairs[0]["derived11bit"]["txHex"]
        derived["rxHex"] = pairs[0]["derived11bit"]["rxHex"]
        derived["storedTx"] = pairs[0]["storedTx"]
        derived["storedRx"] = pairs[0]["storedRx"]
        derived["storedTxHex"] = pairs[0]["storedTxHex"]
        derived["storedRxHex"] = pairs[0]["storedRxHex"]
    return {
        "off": off,
        "end": end,
        "consumedEnd": pos,
        "protocolKind": kind,
        "token": token,
        "pairCount": pair_count,
        "pairs": pairs,
        "selectedPairUnknown": multi,
        "pairChoice": {"class": "unproven-runtime", "selectedPair": None},
        "trailCount": trail_count,
        "trail": trail,
        "trailingThree": three,
        "derived11bit": derived,
        "timings": {
            "class": "timing-labels-not-identified",
            "note": "common 0x1595e..0x15b66 param/trail u32s; P2/S3 names not labeled; pair role unproven",
        },
        "class": "static-source-confirmed",
        "notNamedUds": kind != 0x10,
        **DISABLED,
    }


def parse_systems(blob: bytes, table_off: int, *, target_names: set[str] | None = None) -> list[dict]:
    if table_off < 0:
        raise ValueError(f"negative offset {table_off}")
    _need(blob, table_off, 1)
    n = blob[table_off]
    if n > MAX_SYSTEMS or table_off + 1 > len(blob):
        raise ValueError(f"bad system count {n}")
    pos = table_off + 1
    out = []
    for _ in range(n):
        if pos + 3 > len(blob):
            raise ValueError("system truncated")
        key, cnt = struct.unpack_from("<HB", blob, pos)
        pos += 3
        if cnt > MAX_VARIANTS:
            raise ValueError(f"variantCount {cnt}")
        links = []
        for vi in range(cnt):
            if pos + 8 > len(blob):
                raise ValueError("link truncated")
            comm_off, svc_off = struct.unpack_from("<II", blob, pos)
            pos += 8
            if comm_off >= len(blob) or svc_off >= len(blob) or comm_off >= svc_off:
                raise ValueError(f"link oob {comm_off} {svc_off}")
            links.append(
                {
                    "index": vi,
                    "commOff": comm_off,
                    "serviceOff": svc_off,
                    "comm": parse_comm(blob, comm_off, svc_off),
                    "service": parse_subsys(blob, svc_off, target_names=target_names),
                    "class": "static-source-confirmed",
                    "selection": "GetCommData r1/sl variant index; GetSubSys exact name bind; not a physical-fit claim",
                    "fittedClaim": False,
                    **DISABLED,
                }
            )
        out.append(
            {
                "systemKey": key,
                "familyId": key >> 8,
                "ecuId": key & 0xFF,
                "variantCount": cnt,
                "links": links,
                "class": "static-source-confirmed",
                **DISABLED,
            }
        )
    return out


def find_named_variant(sys_rec: dict | None, name: str) -> dict | None:
    if not sys_rec or not name:
        return None
    for ln in sys_rec.get("links") or []:
        for v in ((ln.get("service") or {}).get("variants") or []):
            if v.get("name") == name:
                return {**v, "linkIndex": ln.get("index"), "comm": ln.get("comm")}
    return None


def iter_subsys_variants(sys_rec: dict | None):
    if not sys_rec:
        return
    for ln in sys_rec.get("links") or []:
        for v in ((ln.get("service") or {}).get("variants") or []):
            yield ln, v


def parse_payload(
    blob: bytes,
    *,
    family_id: int = FAMILY_9X1,
    include_other_families: bool = False,
    target_names: set[str] | None = None,
) -> dict:
    families = parse_family_index(blob)
    row = next((r for r in families if r["familyId"] == family_id), None)
    if row is None:
        return {"ok": False, "reason": f"family {family_id} absent", "familyCount": len(families)}
    systems = parse_systems(blob, row["off"], target_names=target_names)
    other = [r["familyId"] for r in families if r["familyId"] != family_id]
    kind_counts: dict[int, int] = {}
    link_count = 0
    for s in systems:
        for ln in s.get("links") or []:
            link_count += 1
            k = (ln.get("comm") or {}).get("protocolKind")
            kind_counts[k] = kind_counts.get(k, 0) + 1
    return {
        "ok": True,
        "class": "static-source-confirmed",
        "familyCount": len(families),
        "familyIdQueried": family_id,
        "otherFamilyIdsPresentNotQueried": other if not include_other_families else [],
        "systemCount": len(systems),
        "linkCount": link_count,
        "kindCounts": kind_counts,
        "getCommData": {
            "commonBranch": "0x1595e",
            "kinds": [0x10, 0x30, 0x50],
            "specialDiverted": [1, 2, 4, 0x20, 0x60],
            "pairLoop": "each pair: u32 tx, u32 rx, u8 mode, mode5 extra u32, u8 paramCount, params; then trail after all pairs",
            "class": "static-source-confirmed",
        },
        "systems": systems,
        "byEcuId": {s["ecuId"]: s for s in systems},
        "xmlDriveLinks": {
            "nativeOpens": "PORSCHE_DRIVELINKS.BIN",
            "va": "0x5ba75",
            "mode": "rb 0x5ba4c",
            "presentInTree": False,
            "family13UsesSysData": True,
            "class": "missing-external-source",
            "stop": "PORSCHE_DRIVELINKS.BIN absent (checked); family-13 GetCommData uses PORSCHE_SYS_DATA.BIN",
        },
        "getDtcData": {
            "file": "PORSCHE_SYS_DATA.BIN",
            "layout": "style u8, u32+1, meta +5..+10, readCount u8+11, read u32[], clearType, clearCount, clear u32[], u16 otherCount, array",
            "TOKEN+0x60c": "GetSubSys slot2 DTC table file offset",
            "TOKEN+0x6860": "destination buffer in TOKEN, not a SYS file offset",
            "FromAddrGetCommand": "SID,b1,n,payload[n]; wire=SID+b1+payload; n=0 ok; n=0xFF unsupported",
            "class": "static-source-confirmed",
        },
        **DISABLED,
    }


def _drivelinks_present(bin_path: Path) -> bool:
    pkg = bin_path.parent
    cands = [
        pkg / "PORSCHE_DRIVELINKS.BIN",
        pkg.parent / "supplement" / "PORSCHE_DRIVELINKS.BIN",
        pkg.parent / "PORSCHE_DRIVELINKS.BIN",
    ]
    return any(p.is_file() for p in cands)


def load_sysdata(bin_path: Path | None, scratch: Path | None = None, target_names: list[str] | None = None) -> dict:
    if bin_path is None or not bin_path.is_file():
        return {"present": False, "checked": False, "class": "missing-external-source", "stop": "PORSCHE_SYS_DATA.BIN"}
    names = set(target_names) if target_names is not None else None
    rec = recover_logical(bin_path, scratch) if scratch else None
    if rec and not rec.get("ok"):
        return {"present": True, "checked": False, **rec}
    raw = bin_path.read_bytes()
    sha = sha256_bytes(raw)
    if sha != EXPECTED_BIN_SHA:
        return {"present": True, "checked": False, "class": "not-confirmed", "reason": "bin hash mismatch", "sha256": sha}
    try:
        hdr, payload = decrypt_logical(raw)
    except CodecUnavailable as e:
        return {
            "present": True,
            "checked": False,
            "class": "missing-external-source",
            "stop": "FILE_LOADER yzjm_codec",
            "reason": str(e),
            "codec": {"noAccessBypass": True, "loader": FILE_LOADER.as_posix()},
        }
    parsed = parse_payload(payload, target_names=names)
    parsed["xmlDriveLinks"]["presentInTree"] = _drivelinks_present(bin_path)
    if parsed["xmlDriveLinks"]["presentInTree"]:
        parsed["xmlDriveLinks"]["class"] = "static-source-confirmed"
    parsed.update(
        {
            "present": True,
            "checked": True,
            "binSha256": sha,
            "logicalSha256": sha256_bytes(payload),
            "logicalBytes": len(payload),
            "recover": rec,
            "header": {k: hdr[k] for k in ("brand", "version", "payload_off")},
            "fileLoader": FILE_LOADER.as_posix(),
            "codec": {"class": "private-local-yzjm", "noAccessBypass": True},
        }
    )
    return parsed


def capture_vs_static(sys: dict) -> dict:
    by = sys.get("byEcuId") or {}
    eu5 = find_named_variant(by.get(1), "SDI9_1_981_3_4L_EU5")
    a71 = find_named_variant(by.get(9), "CAN_CAN_Gateway_A7_1")

    def _final(v):
        dtc = (v or {}).get("dtc") or {}
        return ((dtc.get("dtcRead") or {}).get("wireHex"), [c.get("wireHex") for c in (dtc.get("read") or []) if c])

    dme_final, dme_seq = _final(eu5)
    gw_final, gw_seq = _final(a71)
    return {
        "dmeBoundName": "SDI9_1_981_3_4L_EU5",
        "gwBoundName": "CAN_CAN_Gateway_A7_1",
        "dmeTableOff": (eu5 or {}).get("dtcTableOff"),
        "gwTableOff": (a71 or {}).get("dtcTableOff"),
        "dmeReadSequence": dme_seq,
        "gwReadSequence": gw_seq,
        "dmeStaticWires": [dme_final] if dme_final else [],
        "gwStaticWires": [gw_final] if gw_final else [],
        "dmeCaptureHex": "1800FF00",
        "gwCaptureHex": "190208",
        "dmeExact": dme_final == "1800FF00",
        "gwExact": gw_final == "190208",
        "dmeHasAdjacent19022C": "19022C" in (dme_seq or []),
        "gwHasAdjacent1800FF00": "1800FF00" in (gw_seq or []),
        "gateIndependent": True,
        "class": "static-source-confirmed",
        "dmeVariantIndexNotEu5Guess": True,
        "unionedModesForbidden": True,
        "fittedClaim": False,
    }


def canonical_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
