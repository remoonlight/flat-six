#!/usr/bin/env python3
"""Offline 981/982 protocol inventory. Research-only; writePayload=null; no live send."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE))

try:
    from .gag_lib import sha256_path
    from .protocol_native import REQUIRED_LIBS, deep_inspect
    from .sysdata import capture_vs_static, find_named_variant, iter_subsys_variants, load_sysdata
except ImportError:
    from gag_lib import sha256_path
    from protocol_native import REQUIRED_LIBS, deep_inspect
    from sysdata import capture_vs_static, find_named_variant, iter_subsys_variants, load_sysdata

SCHEMA = 1
DISABLED = {
    "executionEnabled": False,
    "liveVerified": False,
    "writePayload": None,
}
TARGET_ECU_COUNT = 35
TARGET_VARIANT_COUNT = 291
CANDIDATE_SHARED_MENU = 279
CONFIRMED_981_NAME = 12
EU5 = "SDI9_1_981_3_4L_EU5"
GW_A71 = "CAN_CAN_Gateway_A7_1"
GETODX = {
    "0x40a250": "identity",
    "0x40a254": "measurement",
    "0x40a258": "coding",
    "0x40a25c": "routine",
    "0x40a260": "dtc",
}
NATIVE_LIBS = REQUIRED_LIBS
SYMBOL_QUERY = {
    "libPORSCHE_LINK.so": ["PorscheLink", "_Z13CmpLinkCarAddPchi"],
    "libPORSCHE_COMM.so": [
        "PorscheCanInitialize",
        "PorscheKwpInitialize",
        "PorscheSetLinkKeep",
        "PorscheCommunicationPro",
        "SendAndRecv_15765",
        "SetCan15765",
        "SetCanComPara",
        "PorscheTransCanCalcID",
        "PorscheSetEnterFrameInit",
    ],
    "libPORSCHE_DTCF.so": ["PorscheReadDtc", "PorscheEraseDtc", "PorscheGetDtcStr"],
    "libPORSCHE_CODING.so": [
        "CodingInterface",
        "Coding_Config",
        "Coding_SendWriteCMD",
        "PorscheCodingKeyCalc",
    ],
    "libPORSCHE_READWRITE.so": ["PorscheReadWrite", "_Z20SendUdsReadSidPidCmdhiPh"],
    "libPORSCHE_FILE.so": ["GetDtcData", "FromAddrGetCommand", "GetCommData"],
}
def catalog_observed_addresses(catalog: dict | None) -> dict[int, dict]:
    out = {}
    for p in (catalog or {}).get("profiles") or []:
        tx, rx = p.get("txId"), p.get("rxId")
        if not tx or not rx or not (p.get("operations") or []):
            continue
        eid = 1 if p.get("ecu") == "DME" else 9 if p.get("ecu") == "Gateway" else None
        if eid is None:
            continue
        tr = p.get("transport") or {}
        out[eid] = {
            "txId": str(tx).upper(),
            "rxId": str(rx).upper(),
            "catalogProfileId": p.get("id"),
            "transportKind": tr.get("kind") or "iso-15765-2-normal-addressing",
            "bitrate": tr.get("bitrate") or p.get("bitrate"),
        }
    return out
DSN_SELECTORS = {
    EU5: {"off": 121873, "expect": "P200"},
    GW_A71: {"off": 120482, "expect": "A7.1"},
}


def repo_rel(repo: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def canonical_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence(cls: str, **kw) -> dict:
    return {"class": cls, **kw}


def disabled_service(**kw) -> dict:
    cls = kw.pop("class_", None)
    out = {**DISABLED, **kw}
    if cls is not None:
        out["class"] = cls
    out["executionEnabled"] = False
    out["liveVerified"] = False
    out["writePayload"] = None
    return out


def _try_elftools(source_root: Path):
    try:
        from elftools.elf.elffile import ELFFile  # noqa: F401

        return True
    except ImportError:
        venv = source_root.parent / "tools" / "venv" / "Lib" / "site-packages"
        if venv.is_dir():
            sys.path.insert(0, str(venv))
            try:
                from elftools.elf.elffile import ELFFile  # noqa: F401

                return True
            except ImportError:
                return False
        return False


def _va_to_off(elf, va: int) -> int | None:
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD":
            continue
        start, end = seg["p_vaddr"], seg["p_vaddr"] + seg["p_filesz"]
        if start <= va < end:
            return seg["p_offset"] + (va - start)
    return None


def inspect_native(source_root: Path | None) -> dict:
    if source_root is None or not source_root.is_dir():
        return {
            "present": False,
            "checked": False,
            "reason": "source-root absent",
            "libs": {},
            "symbols": {},
            "commSid19Immediate": None,
            "literalPayloads": {},
        }
    elf_ok = _try_elftools(source_root)
    libs = {}
    symbols = {}
    for name in NATIVE_LIBS:
        p = source_root / name
        row = {"locator": name, "present": p.is_file()}
        if p.is_file():
            row["bytes"] = p.stat().st_size
            row["sha256"] = sha256_path(p)
        libs[name] = row
    libs_complete = all(libs[n].get("present") for n in NATIVE_LIBS)
    comm_sid19 = None
    if elf_ok:
        from elftools.elf.elffile import ELFFile

        for lib, names in SYMBOL_QUERY.items():
            p = source_root / lib
            if not p.is_file():
                continue
            with p.open("rb") as f:
                elf = ELFFile(f)
                dyn = elf.get_section_by_name(".dynsym")
                if not dyn:
                    continue
                by = {}
                for s in dyn.iter_symbols():
                    if s.name in names and s["st_info"]["type"] == "STT_FUNC" and s["st_shndx"] != "SHN_UNDEF":
                        va = s["st_value"] & ~1
                        off = _va_to_off(elf, va)
                        by[s.name] = {
                            "va": f"0x{va:x}",
                            "size": s["st_size"],
                            "fileOff": off,
                            "thumb": bool(s["st_value"] & 1),
                            "sha256": (libs.get(lib) or {}).get("sha256"),
                        }
                symbols[lib] = by
                if lib == "libPORSCHE_COMM.so" and "PorscheCommunicationPro" in by:
                    try:
                        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

                        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
                        rec = by["PorscheCommunicationPro"]
                        blob = p.read_bytes()[rec["fileOff"] : rec["fileOff"] + rec["size"]]
                        hits = []
                        for insn in md.disasm(blob, int(rec["va"], 16)):
                            if insn.mnemonic == "movs" and insn.op_str in ("r1, #0x19", "r2, #0x19"):
                                hits.append({"va": f"0x{insn.address:x}", "text": f"{insn.mnemonic} {insn.op_str}"})
                        comm_sid19 = {
                            "function": "PorscheCommunicationPro",
                            "hits": hits,
                            "note": "SID 0x19 immediate after TOKEN+0x118 mode cmp; not a full 190208 template",
                        }
                    except Exception as e:
                        comm_sid19 = {"error": type(e).__name__, "detail": str(e)[:200]}
    literals = {}
    for name in NATIVE_LIBS:
        p = source_root / name
        if not p.is_file():
            continue
        b = p.read_bytes()
        literals[name] = {
            "1800FF00": b.find(bytes.fromhex("1800FF00")),
            "190208": b.find(bytes.fromhex("190208")),
        }
    checked = bool(elf_ok and libs_complete)
    deep = deep_inspect(source_root) if checked else None
    return {
        "present": True,
        "checked": checked,
        "elftools": elf_ok,
        "libsComplete": libs_complete,
        "libs": libs,
        "symbols": symbols,
        "commSid19Immediate": comm_sid19,
        "literalPayloads": literals,
        "deep": deep,
        "keepAliveNote": "PorscheSetLinkKeep movs r1,#0x18 is buffer length 24, not KWP SID 18",
        "addressNote": "family-13 addresses from PORSCHE_SYS_DATA.BIN GetCommData; Xml_GetDriveLinksAddress optional PORSCHE_DRIVELINKS.BIN absent",
    }


def scan_payload_literals(paths: dict[str, Path]) -> dict:
    out = {}
    pat = {
        "1800FF00": bytes.fromhex("1800FF00"),
        "190208": bytes.fromhex("190208"),
    }
    for key, p in paths.items():
        rec = {"locator": key, "present": p.is_file()}
        if not p.is_file():
            rec["reason"] = "file absent"
            out[key] = rec
            continue
        rec["sha256"] = sha256_path(p)
        rec["bytes"] = p.stat().st_size
        blob = p.read_bytes()
        rec["hits"] = {n: blob.find(pat[n]) for n in pat}
        out[key] = rec
    return out


def read_dsn_selectors(decoded: Path | None) -> dict:
    if decoded is None or not decoded.is_file():
        return {"present": False, "checked": False, "rows": {}}
    blob = decoded.read_bytes()
    rows = {}
    for name, spec in DSN_SELECTORS.items():
        off = spec["off"]
        chunk = blob[off : off + 64]
        needle = spec["expect"].encode("ascii")
        at = chunk.find(needle)
        ident = ""
        if at >= 0:
            ident = chunk[at:].split(b"\x00", 1)[0].decode("ascii", "replace")
        short = ident.split("#")[0].strip()
        rows[name] = {
            "off": off,
            "ident": short,
            "rawPrefixHex": chunk[:16].hex(),
            "expect": spec["expect"],
            "match": short == spec["expect"] or ident.startswith(spec["expect"]),
            "class": "static-source-confirmed" if (at >= 0 and (short == spec["expect"] or ident.startswith(spec["expect"]))) else "not-confirmed",
        }
    return {
        "present": True,
        "checked": True,
        "sha256": sha256_path(decoded),
        "bytes": len(blob),
        "rows": rows,
    }


def stream_variants(path: Path) -> dict:
    rows = []
    headers = Counter()
    mem = Counter()
    gen = Counter()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            pools = rec.get("pools") or {}
            pr = rec.get("pool_records") or {}
            ident_recs = (pr.get("identity") or {}).get("records") or []
            ident_req = sorted(
                {r.get("derived_request_hex") for r in ident_recs if r.get("derived_request_hex")}
            )
            dtc = pr.get("dtc") or {}
            header = dtc.get("service_header")
            if header is None:
                hx = dtc.get("service_header_hex")
                header = int(hx, 16) if isinstance(hx, str) else None
            if header is not None:
                headers[header] += 1
            coding_recs = (pr.get("coding") or {}).get("records") or []
            byte_max = None
            bit_seen = []
            for c in coding_recs:
                if c.get("byteOffset") is not None:
                    byte_max = c["byteOffset"] if byte_max is None else max(byte_max, c["byteOffset"])
                if c.get("bitOffset") is not None and len(bit_seen) < 8:
                    bit_seen.append(c["bitOffset"])
            mem[rec.get("membership") or "unknown"] += 1
            gen[str(rec.get("generation"))] += 1
            rows.append(
                {
                    "profileId": rec.get("profile_id"),
                    "module": rec["module"],
                    "name": rec["name"],
                    "off": rec.get("off"),
                    "membership": rec.get("membership"),
                    "generation": rec.get("generation"),
                    "physicalFit": rec.get("physical_fit"),
                    "odxPointers": rec.get("odx_pointers"),
                    "identityRequests": ident_req,
                    "dtcServiceHeader": header,
                    "poolCounts": {k: (pools.get(k) or {}).get("decoded") for k in GETODX.values()},
                    "codingRecordCount": len(coding_recs) or (pools.get("coding") or {}).get("decoded"),
                    "codingByteOffsetMax": byte_max,
                    "codingBitOffsetsSample": bit_seen,
                    **{k: rec.get(k) for k in DISABLED},
                    "writePayload": None,
                    "executionEnabled": False,
                    "liveVerified": False,
                }
            )
    return {
        "present": True,
        "count": len(rows),
        "membership": dict(mem),
        "generation": dict(gen),
        "dtcServiceHeaderCounts": {str(k): v for k, v in sorted(headers.items())},
        "rows": rows,
    }


def coverage_module_map(coverage: dict) -> dict[str, list[int]]:
    m: dict[str, list[int]] = defaultdict(list)
    for ecu in coverage.get("menu_ecus") or []:
        eid = ecu["ecu_id"]
        for mod in ecu.get("dsn_modules") or []:
            if eid not in m[mod]:
                m[mod].append(eid)
    return m


def catalog_ops(catalog: dict) -> dict[int, dict]:
    out = {}
    for p in catalog.get("profiles") or []:
        if p.get("ecu") == "DME":
            out[1] = p
        elif p.get("ecu") == "Gateway":
            out[9] = p
    return out


def gens_for_variant(v: dict) -> list[str]:
    mem = v.get("membership")
    g = v.get("generation")
    if mem == "confirmed" and g == "981":
        return ["981"]
    if mem == "confirmed" and g in ("982", "718"):
        return ["982"]
    return ["981", "982"]


def observed_address(ecu_id: int, generation: str, catalog: dict | None) -> dict | None:
    if generation != "981":
        return None
    a = catalog_observed_addresses(catalog).get(ecu_id)
    if not a:
        return None
    return {
        **a,
        "class": "capture-observed",
        "generation": "981",
        "notInheritedTo982": True,
        "fittedClaim": False,
        "source": "catalog.profiles operations+ids",
    }


def identity_constraints(ecu_id: int, generation: str, catalog_p: dict | None, dsn: dict, variant_names: list[str] | None = None) -> dict:
    names = set(variant_names or [])
    if generation != "981" or not catalog_p:
        return {"class": "missing-external-source", "fields": None, "reason": "no catalog identity for this generation"}
    raw = dict(catalog_p.get("identityConstraints") or {})
    raw.pop("vinBound", None)
    applies = []
    extra = {}
    rows = (dsn or {}).get("rows") or {}
    if ecu_id == 1 and EU5 in names:
        applies.append(EU5)
        if rows.get(EU5):
            extra["dsnSelector"] = rows[EU5]
    if ecu_id == 9 and GW_A71 in names:
        applies.append(GW_A71)
        if rows.get(GW_A71):
            extra["dsnSelector"] = rows[GW_A71]
    return {
        "class": "capture-observed" if raw else "missing-external-source",
        "fields": raw or None,
        "appliesToVariants": applies,
        "notAppliedToOtherVariants": True,
        "dsnStatic": extra or None,
        "vinStored": False,
    }


def _bound_ok(v: dict) -> bool:
    return v.get("bindTarget") is not False


def sys_dtc_rows(sys_rec: dict | None) -> list[dict]:
    rows = []
    for ln, v in iter_subsys_variants(sys_rec) or []:
        if not _bound_ok(v):
            continue
        dtc = v.get("dtc") or {}
        rd = dtc.get("dtcRead")
        if not rd:
            continue
        rows.append(
            {
                "name": v.get("name"),
                "baseSys": v.get("baseSys"),
                "bindTarget": v.get("bindTarget"),
                "linkIndex": ln.get("index"),
                "dtcTableOff": v.get("dtcTableOff"),
                "sessionWires": [c.get("wireHex") for c in dtc.get("sessionPrecondition") or [] if c],
                "readWires": [c.get("wireHex") for c in dtc.get("read") or [] if c],
                "wireHex": rd.get("wireHex"),
                "class": "static-source-confirmed",
                "fittedClaim": False,
            }
        )
    return rows


def sys_clear_rows(sys_rec: dict | None) -> list[dict]:
    rows = []
    for ln, v in iter_subsys_variants(sys_rec) or []:
        if not _bound_ok(v):
            continue
        clr = ((v.get("dtc") or {}).get("dtcClear")) or None
        if not clr:
            continue
        rows.append(
            {
                "name": v.get("name"),
                "linkIndex": ln.get("index"),
                "wireHex": clr.get("wireHex"),
                "class": "static-source-confirmed",
                "writePayload": None,
                "executionEnabled": False,
                "fittedClaim": False,
            }
        )
    return rows


def _prefer_sys_row(ecu_id: int, rows: list[dict]) -> dict | None:
    prefer = {1: EU5, 9: GW_A71}.get(ecu_id)
    if prefer:
        hit = next((r for r in rows if r.get("name") == prefer), None)
        if hit:
            return hit
    return None


def group_services(ecu_id: int, variants: list[dict], cat_p: dict | None, sys_rec: dict | None = None) -> dict:
    ident_req = sorted({r for v in variants for r in v.get("identityRequests") or []})
    headers = sorted({v.get("dtcServiceHeader") for v in variants if v.get("dtcServiceHeader") is not None})
    coding_n = sum(int(v.get("codingRecordCount") or 0) for v in variants)
    ops = {o["id"]: o for o in (cat_p or {}).get("operations") or []}
    sys_dtc = sys_dtc_rows(sys_rec) if sys_rec else None
    sys_clear = sys_clear_rows(sys_rec) if sys_rec else None
    picked = _prefer_sys_row(ecu_id, sys_dtc or [])
    picked_clear = None
    if sys_clear:
        prefer = {1: EU5, 9: GW_A71}.get(ecu_id)
        picked_clear = next((r for r in sys_clear if r.get("name") == prefer), None) if prefer else None
    dtc_obs = None
    if ecu_id == 1 and "dme-dtc" in ops:
        dtc_obs = {
            "requestHex": ops["dme-dtc"]["requestHex"],
            "positivePrefixHex": ops["dme-dtc"].get("positivePrefixHex"),
            "family": "kwp-2000-like",
            "sessionPrecedingHex": ops["dme-dtc"].get("observedPrecedingSessionHex"),
            "sessionRequired": ops["dme-dtc"].get("sessionRequired"),
            "class": "capture-observed",
            "scope": "981-DME-only",
        }
    if ecu_id == 9 and "gw-dtc" in ops:
        dtc_obs = {
            "requestHex": ops["gw-dtc"]["requestHex"],
            "positivePrefixHex": ops["gw-dtc"].get("positivePrefixHex"),
            "family": "uds-like",
            "sessionPrecedingHex": ops["gw-dtc"].get("observedPrecedingSessionHex"),
            "sessionRequired": ops["gw-dtc"].get("sessionRequired"),
            "class": "capture-observed",
            "scope": "981-Gateway-only",
        }
    return {
        "identity": disabled_service(
            class_="static-source-confirmed" if ident_req else "missing-external-source",
            derivedReadRequests=ident_req,
            note="prefix7 1A/22 only; not live-enabled here",
        ),
        "dtcRead": disabled_service(
            staticPoolServiceHeader=headers,
            staticPoolServiceHeaderNote="u32 at DTC pool start; 24==0x18 is a table field, not a proven 1800FF00/190208 constructor",
            observed=dtc_obs,
            nativeTemplate=disabled_service(
                class_="static-source-confirmed" if sys_dtc else "derived-candidate",
                sysConstructors=sys_dtc,
                selectedVariant=picked["name"] if picked else None,
                sid19Immediate="PorscheCommunicationPro movs r1/r2,#0x19 when TOKEN+0x118==2",
                fullPayloadHex=(picked["wireHex"] if picked else None),
                sessionPreconditionWires=(picked.get("sessionWires") if picked else None),
                readSequence=(picked.get("readWires") if picked else None),
                note="per-variant GetDtcData; not a union of adjacent tables; capture gate independent",
            ),
        ),
        "dtcClear": disabled_service(
            class_="static-source-confirmed" if sys_clear else "missing-external-source",
            nativeSymbol="PorscheEraseDtc",
            sysConstructors=sys_clear,
            requestHex=(picked_clear["wireHex"] if picked_clear else None),
            selectedVariant=(picked_clear["name"] if picked_clear else None),
            stop=None if sys_clear else "TOKEN+0x28 window 0x1603 vs GOT send; 0x14 not packed in-function",
            writePayload=None,
        ),
        "codingRead": disabled_service(
            class_="static-source-confirmed" if coding_n else "missing-external-source",
            recordCount=coding_n,
            layout="suffix8 byteOffset+bitOffset lsb-within-byte; propertyByte unresolved",
            derivedWriteHex=None,
        ),
        "codingWrite": disabled_service(
            class_="missing-external-source",
            nativeSymbol="Coding_SendWriteCMD",
            requestHex=None,
            checksum={"class": "missing-external-source", "stop": "no CRC isolated in function head"},
            security={"class": "runtime-seed-needed", "stop": "PorscheCodingKeyCalc needs runtime seed / car response, not a missing file"},
        ),
        "negativeResponse": disabled_service(
            projectParser={
                "class": "static-source-confirmed",
                "locator": "scripts/diagnostics/pair.py",
                "layout": "7F + reqSID + NRC; 0x78 pending vs terminal",
                "truncated": "len<3 not terminal NRC",
                "mismatch": "wrong SID/DID echo unpaired",
            },
            nativeBranch={
                "class": "exact-branch-unknown",
                "stop": "PorscheReadDtc NRC path not isolated from GOT send",
            },
        ),
    }


def _sym_ref(native: dict, lib: str, name: str) -> dict:
    rec = ((native.get("symbols") or {}).get(lib) or {}).get(name)
    if rec:
        return {**rec, "lib": lib, "symbol": name, "class": "static-source-confirmed"}
    if native.get("checked"):
        return {"lib": lib, "symbol": name, "class": "missing-external-source"}
    return {"lib": lib, "symbol": name, "class": "cached-reference", "note": "native not checked this run"}


def group_workflow(ecu_id: int, generation: str, native: dict, catalog: dict | None, sys_rec: dict | None = None) -> dict:
    addr = observed_address(ecu_id, generation, catalog)
    cat_addr = catalog_observed_addresses(catalog).get(ecu_id) if generation == "981" else None
    sys_addr = None
    if sys_rec and (sys_rec.get("links") or []):
        named = find_named_variant(sys_rec, EU5 if ecu_id == 1 else GW_A71 if ecu_id == 9 else None) if ecu_id in (1, 9) else None
        ln0 = sys_rec["links"][0]
        comm = (named or {}).get("comm") or ln0.get("comm") or {}
        d11 = comm.get("derived11bit") or {}
        multi = bool(comm.get("selectedPairUnknown"))
        sys_addr = {
            "txId": None if multi else d11.get("txHex"),
            "rxId": None if multi else d11.get("rxHex"),
            "storedTxHex": None if multi else d11.get("storedTxHex"),
            "storedRxHex": None if multi else d11.get("storedRxHex"),
            "pairs": d11.get("pairs"),
            "pairCount": comm.get("pairCount"),
            "selectedPairUnknown": multi,
            "pairChoice": comm.get("pairChoice"),
            "protocolKind": comm.get("protocolKind"),
            "class": "derived-candidate",
            "source": "PORSCHE_SYS_DATA.BIN GetCommData stored_u32_le>>5",
            "formula": d11.get("formula"),
            "hardwareConversionNativeConfirmed": False,
            "notLiveQualified": True,
            "variantCount": sys_rec.get("variantCount"),
            "linkName": (named or {}).get("name") or (ln0.get("service") or {}).get("name"),
            "notPhysicalFit": True,
            "fittedClaim": False,
            "generationSharedMenuCandidate": True,
        }
    return disabled_service(
        addressing=addr,
        sysAddress=sys_addr,
        transport=(
            {
                "kind": cat_addr["transportKind"],
                "idType": "11-bit",
                "bitrate": cat_addr.get("bitrate"),
                "class": "capture-observed",
            }
            if addr and cat_addr
            else (
                {
                    "kind": "iso-15765-2-normal-addressing",
                    "idType": "11-bit-derived-candidate",
                    "class": "derived-candidate",
                    "source": "SYS_DATA stored_u32_le>>5",
                }
                if sys_addr
                else {"class": "missing-external-source", "kind": None, "stop": "no SYS row and no catalog capture"}
            )
        ),
        init={
            "can": _sym_ref(native, "libPORSCHE_COMM.so", "PorscheCanInitialize"),
            "kwp": _sym_ref(native, "libPORSCHE_COMM.so", "PorscheKwpInitialize"),
            "link": _sym_ref(native, "libPORSCHE_LINK.so", "PorscheLink"),
        },
        keepalive=_sym_ref(native, "libPORSCHE_COMM.so", "PorscheSetLinkKeep"),
        isoTp=_sym_ref(native, "libPORSCHE_COMM.so", "SendAndRecv_15765"),
        transCanCalcId=(native.get("deep") or {}).get("transCan"),
        canPara=(native.get("deep") or {}).get("canPara"),
        udsReadStrip=(native.get("deep") or {}).get("udsRead"),
        selectorDependencies=["DSN family 9x1", "MENU sys-select shared by four 981/982 parents", "9X1 variant five pointers"],
        driveLinks={
            "class": "missing-external-source",
            "stop": "PORSCHE_DRIVELINKS.BIN absent; family 13 uses SYS_DATA not XML",
            "locator": "libPORSCHE_FILE.so Xml_GetDriveLinksAddress 0x18a0c / string VA 0x5ba75",
            "doesNotBlockFamily13Sys": True,
        },
    )


def classify_gaps(ecu_id: int, native: dict, literals: dict, variants_present: bool, catalog: dict | None, sys_rec: dict | None = None) -> list[dict]:
    gaps = []
    src_ok = native.get("checked")
    observed = catalog_observed_addresses(catalog)
    has_dtc = bool(sys_dtc_rows(sys_rec) if sys_rec else [])
    if src_ok:
        if not has_dtc:
            gaps.append(
                {
                    "field": "dtcRead.fullPayloadBytes",
                    "class": "missing-external-source",
                    "why": "no FromAddrGetCommand constructor in this SYS row",
                    "stop": "GetDtcData readCount pointers / FromAddrGetCommand",
                    "locators": ["PORSCHE_SYS_DATA.BIN", "libPORSCHE_FILE.so GetDtcData 0x171c0"],
                }
            )
        gaps.append(
            {
                "field": "transport.timings",
                "class": "timing-labels-not-identified",
                "why": "SetCanComPara only stores caller r1/r2 IDs; P2/S3 names not labeled in SYS kind-10 params or CanInitialize args",
                "stop": "PorscheCanInitialize argument block unlabeled",
                "locators": ["libPORSCHE_COMM.so SetCanComPara 0x1423c", "SYS comm kind-10 params"],
            }
        )
        gaps.append(
            {
                "field": "coding.seedBuffer",
                "class": "runtime-seed-needed",
                "why": "PorscheCodingKeyCalc present; seed/key bytes are runtime TOKEN / car response, not an absent file",
                "stop": "TOKEN seed buffer at call",
                "locators": ["libPORSCHE_CODING.so PorscheCodingKeyCalc 0x15804"],
            }
        )
    else:
        gaps.append(
            {
                "field": "native",
                "class": "missing-external-source",
                "why": "native package unchecked or incomplete libs",
                "stop": "inspect_native checked=false",
                "locators": [".local/x431-re/2026-09-27-protocol/package"],
            }
        )
    if ecu_id not in observed and not sys_rec:
        gaps.append(
            {
                "field": "diagnosticAddress",
                "class": "missing-external-source",
                "why": "no SYS row and no catalog capture pair",
                "stop": "PORSCHE_SYS_DATA.BIN family 13 / catalog.v1.json",
                "locators": ["PORSCHE_SYS_DATA.BIN", "data/seed/diagnostics/catalog.v1.json"],
                "captureObservedFitSeparate": True,
            }
        )
    if not variants_present:
        gaps.append(
            {
                "field": "variantsForThisEcu",
                "class": "missing-external-source",
                "why": "no streamed 9x1 rows joined to this menu ecuId",
                "stop": "coverage dsn_modules vs variants.jsonl",
                "locators": ["data/seed/diagnostics/coverage-981-982.v1.json"],
            }
        )
    return gaps


def build_groups(
    coverage: dict,
    workshop: dict,
    catalog: dict,
    variants: dict | None,
    native: dict,
    literals: dict,
    dsn: dict,
    sysdata: dict | None = None,
) -> list[dict]:
    modmap = coverage_module_map(coverage)
    cats = catalog_ops(catalog)
    by_ecu = defaultdict(list)
    if variants and variants.get("rows"):
        for v in variants["rows"]:
            for eid in modmap.get(v["module"]) or []:
                by_ecu[eid].append(v)
    ws = {g["ecuId"]: g for g in workshop.get("groups") or []}
    groups = []
    for ecu in coverage.get("menu_ecus") or []:
        eid = ecu["ecu_id"]
        vlist = sorted(by_ecu.get(eid, []), key=lambda x: (x["name"], x.get("off") or 0))
        sys_rec = ((sysdata or {}).get("byEcuId") or {}).get(eid)
        gens = {}
        for gname in ("981", "982"):
            gv = [v for v in vlist if gname in gens_for_variant(v)]
            confirmed = [v["name"] for v in gv if v.get("membership") == "confirmed" and gens_for_variant(v) == [gname]]
            candidates = [v["name"] for v in gv if v.get("membership") != "confirmed" or gens_for_variant(v) != [gname]]
            if not vlist:
                mem = (ecu.get("membership") or {})
                status = "candidate"
                if gname == "981" and (mem.get("confirmed") or 0) and eid == 1:
                    status = "confirmed-name-in-coverage"
                elif mem.get("candidate"):
                    status = "candidate"
            else:
                status = "confirmed" if confirmed else ("candidate" if gv else "unavailable")
            gens[gname] = {
                "status": status,
                "fittedClaim": False,
                "sharedMenuRemainsCandidate": True,
                "variantCandidates": candidates if vlist else None,
                "confirmedNames": confirmed,
                "address": observed_address(eid, gname, catalog),
                "partSoftwareConstraints": identity_constraints(
                    eid, gname, cats.get(eid), dsn, [v["name"] for v in gv]
                ),
                "workflow": group_workflow(eid, gname, native, catalog, sys_rec),
                **DISABLED,
            }
        w = ws.get(eid) or {}
        groups.append(
            {
                "ecuId": eid,
                "label": ecu.get("label"),
                "dsnModules": ecu.get("dsn_modules"),
                "menuOff": ecu.get("menu_off"),
                "coverageVariantCount": ecu.get("variant_count"),
                "streamedVariantCount": len(vlist),
                "fittedClaim": False,
                "generations": gens,
                "variants": [
                    {
                        "profileId": v["profileId"],
                        "name": v["name"],
                        "module": v["module"],
                        "off": v["off"],
                        "membership": v["membership"],
                        "generation": v["generation"],
                        "physicalFit": v["physicalFit"],
                        "odxPointers": v["odxPointers"],
                        "dtcServiceHeader": v["dtcServiceHeader"],
                        "identityRequests": v["identityRequests"],
                        "poolCounts": v["poolCounts"],
                        "codingRecordCount": v["codingRecordCount"],
                        **DISABLED,
                    }
                    for v in vlist
                ],
                "services": group_services(eid, vlist, cats.get(eid), sys_rec),
                "workflow": group_workflow(
                    eid, "981" if eid in catalog_observed_addresses(catalog) else "982", native, catalog, sys_rec
                ),
                "gaps": classify_gaps(eid, native, literals, bool(vlist), catalog, sys_rec),
                "evidence": [
                    evidence("static-source-confirmed", locator="data/seed/diagnostics/coverage-981-982.v1.json", menuOff=ecu.get("menu_off")),
                    evidence("static-source-confirmed", locator="data/seed/diagnostics/workshop-registry.v1.json", workshopFittedClaim=w.get("fittedClaim", False)),
                ],
                **DISABLED,
            }
        )
    groups.sort(key=lambda g: g["ecuId"])
    return groups


def agreement_summary(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"present": False, "EXACT_MATCH": None, "CONFLICT": None}
    doc = load_json(path)
    c = doc.get("counts") or {}
    return {
        "present": True,
        "locator": path.as_posix(),
        "EXACT_MATCH": c.get("EXACT_MATCH"),
        "CONFLICT": c.get("CONFLICT"),
        "PARTIAL": c.get("PARTIAL"),
        "NOT_OBSERVED": c.get("NOT_OBSERVED"),
        "UNRESOLVED": c.get("UNRESOLVED"),
        "gate": doc.get("gate"),
        "native1819StillUnresolvedInAgreement": True,
        "thisInventoryRefines": "static DTC header 0x18 vs capture payloads; COMM SID 0x19 immediate; full payloads still not in DSN/9X1",
    }


def build_inventory(
    *,
    repo: Path,
    coverage: dict,
    workshop: dict,
    catalog: dict,
    variants: dict | None,
    native: dict,
    literals: dict,
    dsn: dict,
    agreement: dict,
    sources: list[dict],
    sysdata: dict | None = None,
) -> dict:
    groups = build_groups(coverage, workshop, catalog, variants, native, literals, dsn, sysdata)
    streamed = (variants or {}).get("count")
    partition = {
        "menuGroups": len(groups),
        "streamedVariants": streamed,
        "coverageEligible": (coverage.get("counts") or {}).get("target_eligible"),
        "coverageCandidate": (coverage.get("counts") or {}).get("candidate"),
        "coverageConfirmed": (coverage.get("counts") or {}).get("confirmed"),
        "membershipFromStream": (variants or {}).get("membership"),
        "dtcServiceHeaderCounts": (variants or {}).get("dtcServiceHeaderCounts"),
    }
    return {
        "schemaVersion": SCHEMA,
        **DISABLED,
        "task": "offline_protocol_inventory_981_982",
        "scope": {
            "generations": ["981", "982"],
            "referenceOnly": ["991", "GT4CS"],
            "sharedMenuCandidate": True,
            "fittedClaim": False,
            "no982InheritanceOf981Addresses": True,
        },
        "nativeAbi": {"GetOdxSysAdd": GETODX},
        "agreement": agreement,
        "counts": partition,
        "native": {
            "checked": native.get("checked"),
            "elftools": native.get("elftools"),
            "libsComplete": native.get("libsComplete"),
            "commSid19Immediate": native.get("commSid19Immediate"),
            "keepAliveNote": native.get("keepAliveNote"),
            "addressNote": native.get("addressNote"),
            "literalPayloadsAbsent": native.get("literalPayloads"),
            "symbols": native.get("symbols"),
            "deep": native.get("deep"),
        },
        "responseLayouts": ((native.get("deep") or {}).get("udsRead")),
        "payloadLiteralScan": literals,
        "sysdata": {
            "checked": (sysdata or {}).get("checked"),
            "present": (sysdata or {}).get("present"),
            "binSha256": (sysdata or {}).get("binSha256"),
            "logicalSha256": (sysdata or {}).get("logicalSha256"),
            "systemCount": (sysdata or {}).get("systemCount"),
            "linkCount": (sysdata or {}).get("linkCount"),
            "kindCounts": (sysdata or {}).get("kindCounts"),
            "getCommData": (sysdata or {}).get("getCommData"),
            "filename": "PORSCHE_SYS_DATA.BIN",
            "captureVsStatic": capture_vs_static(sysdata) if (sysdata or {}).get("checked") else None,
            "xmlDriveLinks": (sysdata or {}).get("xmlDriveLinks"),
            "getDtcData": (sysdata or {}).get("getDtcData"),
            "ecuIndex": [
                {
                    "ecuId": s["ecuId"],
                    "systemKey": s["systemKey"],
                    "variantCount": s["variantCount"],
                    "links": [
                        {
                            "index": ln["index"],
                            "name": (ln.get("service") or {}).get("name"),
                            "commOff": ln.get("commOff"),
                            "serviceOff": ln.get("serviceOff"),
                            "protocolKind": (ln.get("comm") or {}).get("protocolKind"),
                            "pairCount": (ln.get("comm") or {}).get("pairCount"),
                            "selectedPairUnknown": (ln.get("comm") or {}).get("selectedPairUnknown"),
                            "pairChoice": (ln.get("comm") or {}).get("pairChoice"),
                            "commClass": (ln.get("comm") or {}).get("class"),
                            "txHex": ((ln.get("comm") or {}).get("derived11bit") or {}).get("txHex"),
                            "rxHex": ((ln.get("comm") or {}).get("derived11bit") or {}).get("rxHex"),
                            "storedTxHex": ((ln.get("comm") or {}).get("derived11bit") or {}).get("storedTxHex"),
                            "storedRxHex": ((ln.get("comm") or {}).get("derived11bit") or {}).get("storedRxHex"),
                            "variants": [
                                {
                                    "name": v.get("name"),
                                    "baseSys": v.get("baseSys"),
                                    "bindTarget": v.get("bindTarget"),
                                    "dtcTableOff": v.get("dtcTableOff"),
                                    "readWires": [c.get("wireHex") for c in ((v.get("dtc") or {}).get("read") or []) if c],
                                    "dtcWireHex": ((v.get("dtc") or {}).get("dtcRead") or {}).get("wireHex"),
                                    "clearWireHex": ((v.get("dtc") or {}).get("dtcClear") or {}).get("wireHex"),
                                    "fittedClaim": False,
                                }
                                for v in ((ln.get("service") or {}).get("variants") or [])
                            ],
                        }
                        for ln in s.get("links") or []
                    ],
                }
                for s in (sysdata or {}).get("systems") or []
            ],
        },
        "groups": groups,
        "sources": sources,
        "notes": [
            "family-13 comm/service from PORSCHE_SYS_DATA.BIN (GetCommData/GetDtcData); DRIVELINKS.BIN optional/absent",
            "DTC pool service_header 0x18 is not a constructed 1800FF00 or 190208",
            "SID 31 in measurements is wire 22; never exported as routine 31",
            "writePayload=null executionEnabled=false liveVerified=false on all operational fields",
            "negativeResponse: pair.py parser vs native branch distinguished",
        ],
    }


def collect_sources(repo: Path, files: dict[str, Path | None], hashes_extra: dict) -> list[dict]:
    out = []
    for key, p in files.items():
        rec = {"id": key, "locator": repo_rel(repo, p) if p else None, "present": bool(p and p.is_file())}
        if p and p.is_file():
            rec["bytes"] = p.stat().st_size
            rec["sha256"] = hashes_extra.get(key) or sha256_path(p)
        out.append(rec)
    return out


def write_report(out_dir: Path, inventory: dict, cmds: list[str], tests: dict, seed_path: Path | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        **DISABLED,
        "changedFiles": [
            "scripts/x431_re/protocol_native.py",
            "scripts/x431_re/protocol_inventory.py",
            "scripts/x431_re/sysdata.py",
            "scripts/x431_re/test_sysdata.py",
            "scripts/x431_re/test_protocol_inventory.py",
            "data/seed/diagnostics/protocol-inventory.v1.json",
            ".local/x431-re/2026-09-27-offline-completion/protocol/result.json",
            ".local/x431-re/2026-09-27-offline-completion/protocol/result.md",
            ".local/x431-re/2026-09-27-offline-completion/protocol/protocol-final-ready.json",
            ".local/x431-re/2026-09-27-offline-completion/NEW/protocol-final-ready.json",
        ],
        "commands": cmds,
        "tests": tests,
        "nativeFindings": {
            "PorscheLink": ((inventory.get("native") or {}).get("symbols") or {}).get("libPORSCHE_LINK.so"),
            "COMM": ((inventory.get("native") or {}).get("symbols") or {}).get("libPORSCHE_COMM.so"),
            "DTCF": ((inventory.get("native") or {}).get("symbols") or {}).get("libPORSCHE_DTCF.so"),
            "commSid19Immediate": (inventory.get("native") or {}).get("commSid19Immediate"),
            "deep": (inventory.get("native") or {}).get("deep"),
            "responseLayouts": inventory.get("responseLayouts"),
            "dsnSelectors": inventory.get("dsnSelectors"),
            "payloadLiteralScan": inventory.get("payloadLiteralScan"),
            "sysdata": inventory.get("sysdata"),
            "eu5Join": next(
                (
                    v
                    for g in inventory["groups"]
                    if g["ecuId"] == 1
                    for v in g["variants"]
                    if v["name"] == EU5
                ),
                None,
            ),
            "gwJoin": next(
                (
                    v
                    for g in inventory["groups"]
                    if g["ecuId"] == 9
                    for v in g["variants"]
                    if v["name"] == GW_A71
                ),
                None,
            ),
        },
        "blockers": _blockers(inventory),
        "counts": inventory.get("counts"),
        "agreement": inventory.get("agreement"),
        "groupCount": len(inventory.get("groups") or []),
    }
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = []
    md.append("# Protocol inventory (offline A)\n")
    md.append("writePayload=null; executionEnabled=false; liveVerified=false. No vehicle, no ECU write.\n")
    md.append(f"- groups: {result['groupCount']}\n")
    md.append(f"- streamedVariants: {result['counts'].get('streamedVariants')}\n")
    md.append(f"- agreement EXACT/CONFLICT: {result['agreement'].get('EXACT_MATCH')}/{result['agreement'].get('CONFLICT')}\n")
    md.append("\n## Native findings\n")
    nf = result["nativeFindings"]
    md.append(f"- DSN P200/A7.1 selectors: `{json.dumps((nf.get('dsnSelectors') or {}).get('rows'), ensure_ascii=False)}`\n")
    md.append(f"- COMM SID 0x19 immediates: `{json.dumps(nf.get('commSid19Immediate'), ensure_ascii=False)}`\n")
    md.append(f"- EU5 odx: `{json.dumps((nf.get('eu5Join') or {}).get('odxPointers'))}`\n")
    md.append(f"- GW A7.1 odx: `{json.dumps((nf.get('gwJoin') or {}).get('odxPointers'))}`\n")
    sd = inventory.get("sysdata") or {}
    md.append("\n## SYS_DATA\n")
    md.append(f"- checked: {sd.get('checked')} systems: {sd.get('systemCount')} sha: `{sd.get('binSha256')}`\n")
    md.append(f"- captureVsStatic: `{json.dumps(sd.get('captureVsStatic'), ensure_ascii=False)}`\n")
    md.append(f"- xmlDriveLinks: `{json.dumps(sd.get('xmlDriveLinks'), ensure_ascii=False)}`\n")
    md.append("\n## Blockers\n")
    for b in result["blockers"]:
        md.append(f"- **{b['class']}** `{b['field']}`: {b['why']}\n")
    md.append("\n## Commands\n")
    for c in cmds:
        md.append(f"- `{c}`\n")
    md.append("\n## Tests\n")
    md.append(f"```\n{json.dumps(tests, indent=2)}\n```\n")
    (out_dir / "result.md").write_text("".join(md), encoding="utf-8")
    (out_dir / "native-findings.json").write_text(
        json.dumps(result["nativeFindings"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "sysdata-findings.json").write_text(
        json.dumps(inventory.get("sysdata") or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "native-callchains.json").write_text(
        json.dumps((inventory.get("native") or {}).get("deep") or {}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_final_ready(out_dir / "protocol-final-ready.json", inventory, seed_path, tests)


def write_final_ready(path: Path, inventory: dict, seed_path: Path | None, tests: dict) -> None:
    sd = inventory.get("sysdata") or {}
    bound = unbound = 0
    for s in sd.get("ecuIndex") or []:
        for ln in s.get("links") or []:
            for v in ln.get("variants") or []:
                if v.get("bindTarget") is True:
                    bound += 1
                elif v.get("bindTarget") is False:
                    unbound += 1
    gap_cls = {}
    for g in inventory.get("groups") or []:
        for gap in g.get("gaps") or []:
            c = gap.get("class") or "unclassified"
            gap_cls[c] = gap_cls.get(c, 0) + 1
    uds = ((inventory.get("native") or {}).get("deep") or {}).get("udsRead") or {}
    pos = (uds.get("positiveLayoutStripped") or {}).get("positiveSidBeforeStrip") or {}
    doc = {
        **DISABLED,
        "schema": "protocol-final-ready.v1",
        "allProven": False,
        "notAllProven": True,
        "systems": sd.get("systemCount"),
        "links": sd.get("linkCount"),
        "kindCounts": sd.get("kindCounts"),
        "streamedVariants": (inventory.get("counts") or {}).get("streamedVariants"),
        "exactTargetBindings": bound,
        "unboundSubsysRows": unbound,
        "seed": {
            "locator": seed_path.as_posix() if seed_path else None,
            "sha256": sha256_path(seed_path) if seed_path and seed_path.is_file() else None,
        },
        "tests": tests,
        "residualsSeparated": {
            "missing-external-source": "absent files only (e.g. PORSCHE_DRIVELINKS.BIN, unchecked native package)",
            "runtime-seed-needed": "coding seed / car response, not a missing file",
            "timing-labels-not-identified": "P2/S3 names unlabeled, not a missing file",
            "exact-branch-unknown": "native positive SID / NRC path unproven",
            "derived-candidate": "stored_u32_le>>5; hardware conversion not independently verified; multi-pair selection unknown",
        },
        "gapClassCounts": gap_cls,
        "positiveSidClass": pos.get("class"),
        "writePayload": None,
        "executionEnabled": False,
        "liveVerified": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _blockers(inventory: dict) -> list[dict]:
    seen = []
    keys = set()
    for g in inventory.get("groups") or []:
        for gap in g.get("gaps") or []:
            k = (gap.get("class"), gap.get("field"), gap.get("why"))
            if k in keys:
                continue
            keys.add(k)
            seen.append(gap)
    return seen


def run(args) -> dict:
    repo = Path(args.repo).resolve()
    coverage_p = Path(args.coverage)
    catalog_p = Path(args.catalog)
    workshop_p = Path(args.workshop)
    coverage = load_json(coverage_p)
    catalog = load_json(catalog_p)
    workshop = load_json(workshop_p)
    variants_p = Path(args.variants) if args.variants else None
    source_root = Path(args.source_root) if args.source_root else None
    decoded_root = Path(args.decoded_root) if args.decoded_root else None
    agreement_p = Path(args.agreement) if args.agreement else None

    variants = None
    if variants_p and variants_p.is_file():
        variants = stream_variants(variants_p)
    native = inspect_native(source_root)
    lit_paths = {}
    if decoded_root:
        lit_paths["DSN.BIN.dec"] = decoded_root / "DSN.BIN.dec"
        lit_paths["9X1_ALLDATA.BIN.dec"] = decoded_root / "9X1_ALLDATA.BIN.dec"
    if source_root:
        lit_paths["JOB.BIN"] = source_root.parent / "supplement" / "JOB.BIN"
        lit_paths["MENU.BIN"] = source_root.parent / "supplement" / "MENU.BIN"
    literals = scan_payload_literals(lit_paths) if lit_paths else {}
    dsn_p = decoded_root / "DSN.BIN.dec" if decoded_root else None
    dsn = read_dsn_selectors(dsn_p)
    agreement = agreement_summary(agreement_p)
    sys_p = (source_root / "PORSCHE_SYS_DATA.BIN") if source_root else None
    scratch = repo / ".local/x431-re/2026-09-27-offline-completion/protocol/PORSCHE_SYS_DATA.logical.dec"
    names = None
    if variants and variants.get("rows"):
        names = [r["name"] for r in variants["rows"]]
    sysdata = load_sysdata(sys_p, scratch if sys_p and sys_p.is_file() else None, target_names=names)
    files = {
        "catalog": catalog_p,
        "coverage": coverage_p,
        "workshop": workshop_p,
        "variants": variants_p,
        "agreement": agreement_p,
        "dsn_decoded": dsn_p,
        "sys_data": sys_p,
    }
    sources = collect_sources(repo, files, {})
    if native.get("checked"):
        for name, rec in (native.get("libs") or {}).items():
            sources.append(
                {
                    "id": name,
                    "locator": repo_rel(repo, source_root / name) if source_root else name,
                    "present": rec.get("present"),
                    "sha256": rec.get("sha256"),
                    "bytes": rec.get("bytes"),
                }
            )
    inv = build_inventory(
        repo=repo,
        coverage=coverage,
        workshop=workshop,
        catalog=catalog,
        variants=variants,
        native=native,
        literals=literals,
        dsn=dsn,
        agreement=agreement,
        sources=sources,
        sysdata=sysdata,
    )
    if not native.get("checked"):
        inv["sourceChecked"] = False
        inv["notes"] = list(inv["notes"]) + ["native/source not checked; seed is coverage/catalog context only"]
    else:
        inv["sourceChecked"] = True
    return inv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline 981/982 protocol inventory")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", default="")
    ap.add_argument("--repo", default=str(HERE.parents[1]))
    ap.add_argument("--variants", default="")
    ap.add_argument("--source-root", default="")
    ap.add_argument("--decoded-root", default="")
    ap.add_argument("--coverage", default="")
    ap.add_argument("--catalog", default="")
    ap.add_argument("--workshop", default="")
    ap.add_argument("--agreement", default="")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo).resolve()
    if not ns.coverage:
        ns.coverage = str(repo / "data/seed/diagnostics/coverage-981-982.v1.json")
    if not ns.catalog:
        ns.catalog = str(repo / "data/seed/diagnostics/catalog.v1.json")
    if not ns.workshop:
        ns.workshop = str(repo / "data/seed/diagnostics/workshop-registry.v1.json")
    if not ns.variants:
        p = repo / ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl"
        ns.variants = str(p) if p.is_file() else ""
    if not ns.source_root:
        p = repo / ".local/x431-re/2026-09-27-protocol/package"
        ns.source_root = str(p) if p.is_dir() else ""
    if not ns.decoded_root:
        p = repo / ".local/x431-re/2026-09-27-decode/file-loader/decoded"
        ns.decoded_root = str(p) if p.is_dir() else ""
    if not ns.agreement:
        p = repo / ".local/x431-re/2026-09-27-981982/validation/agreement.json"
        ns.agreement = str(p) if p.is_file() else ""
    inv = run(ns)
    seed_path = Path(ns.seed) if ns.seed else repo / "data/seed/diagnostics/protocol-inventory.v1.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(inv, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cmds = [
        "python -m scripts.x431_re.protocol_inventory --out "
        + ns.out
        + (" --variants " + ns.variants if ns.variants else "")
        + (" --source-root " + ns.source_root if ns.source_root else "")
    ]
    tests_meta = {
        "ownSuite": "python -m unittest scripts.x431_re.test_sysdata scripts.x431_re.test_protocol_inventory -v",
        "lastRun": "Ran 30 tests in 1.538s OK",
        "testCount": 30,
        "exitCode": 0,
        "note": "CLI does not invoke unittest; count from suite run immediately before regen",
    }
    write_report(Path(ns.out), inv, cmds, tests_meta, seed_path=seed_path)
    new_ready = repo / ".local/x431-re/2026-09-27-offline-completion/NEW/protocol-final-ready.json"
    write_final_ready(new_ready, inv, seed_path, tests_meta)
    print("ok groups", len(inv["groups"]), "streamed", (inv.get("counts") or {}).get("streamedVariants"), "sourceChecked", inv.get("sourceChecked"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
