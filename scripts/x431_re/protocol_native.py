"""ELF call-chain facts for protocol inventory. Research-only; no send."""
from __future__ import annotations

import struct
from pathlib import Path

REQUIRED_LIBS = (
    "libPORSCHE_LINK.so",
    "libPORSCHE_COMM.so",
    "libPORSCHE_READWRITE.so",
    "libPORSCHE_DTCF.so",
    "libPORSCHE_CODING.so",
    "libPORSCHE_CDSF.so",
    "libPORSCHE_VERF.so",
    "libPORSCHE_FILE.so",
    "libPORSCHE_CALC.so",
)


def _va_to_off(elf, va: int) -> int | None:
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD":
            continue
        start, end = seg["p_vaddr"], seg["p_vaddr"] + seg["p_filesz"]
        if start <= va < end:
            return seg["p_offset"] + (va - start)
    return None


def _plt(elf) -> dict[int, str]:
    rel = elf.get_section_by_name(".rel.plt") or elf.get_section_by_name(".rela.plt")
    dynsym = elf.get_section_by_name(".dynsym")
    plt = elf.get_section_by_name(".plt")
    if not rel or not dynsym or not plt:
        return {}
    base = plt["sh_addr"]
    out = {}
    i = 0
    for r in rel.iter_relocations():
        s = dynsym.get_symbol(r["r_info_sym"])
        stub = base + 20 + i * 12
        if s and s.name:
            out[stub] = s.name
            out[stub | 1] = s.name
        i += 1
    return out


def _trace_fn(data: bytes, elf, plt: dict, va: int, size: int, cap: int = 80) -> dict:
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    off = _va_to_off(elf, va & ~1)
    if off is None:
        return {"error": "unmap", "va": hex(va)}
    blob = data[off : off + size]
    direct = []
    indirect = []
    for insn in md.disasm(blob, va & ~1):
        if insn.mnemonic not in ("bl", "blx"):
            continue
        if insn.op_str.startswith("#"):
            tgt = int(insn.op_str[1:], 0) & ~1
            direct.append({"at": hex(insn.address), "target": plt.get(tgt) or plt.get(tgt | 1) or hex(tgt)})
        else:
            indirect.append({"at": hex(insn.address), "slot": insn.op_str})
        if len(direct) + len(indirect) >= cap:
            break
    return {
        "va": hex(va & ~1),
        "size": size,
        "fileOff": off,
        "directCalls": direct,
        "indirectSlots": indirect,
        "disasmRange": {"start": hex(va & ~1), "end": hex((va & ~1) + size)},
    }


def transcan_tables(data: bytes, elf) -> dict:
    """PorscheTransCanCalcID 0x13fc0: two PC-relative 8-byte tables."""
    t1_va = 0x2A211
    t2_va = 0x2E428
    o1, o2 = _va_to_off(elf, t1_va), _va_to_off(elf, t2_va)
    t1 = list(data[o1 : o1 + 8]) if o1 is not None else []
    t2 = list(data[o2 : o2 + 8]) if o2 is not None else []
    return {
        "function": "PorscheTransCanCalcID",
        "va": "0x13fc0",
        "size": 64,
        "algorithm": "hi=(id>>8)&0xff; i=0..7 if tableHi[i]==hi then ((id&0xff)|(i<<8))^tableXor[i]; else unchanged",
        "tableHi": {
            "va": hex(t1_va),
            "fileOff": o1,
            "bytes": t1,
            "class": "static-source-confirmed",
        },
        "tableXor": {
            "va": hex(t2_va),
            "fileOff": o2,
            "bytes": t2,
            "ascii": bytes(t2).decode("latin1") if t2 else None,
            "class": "static-source-confirmed",
            "note": "ADR of second ldr [pc,#0x10]+add pc; 8-byte XOR table (ASCII coincidence not a payload)",
        },
        "callsiteInputs": "no direct bl in COMM dynsym span; callers use pointer/GOT — inputs remain TOKEN CAN id",
        "class": "static-source-confirmed",
    }


def send_uds_layout() -> dict:
    return {
        "function": "_Z20SendUdsReadSidPidCmdhiPh",
        "va": "0xfddc0",
        "size": 276,
        "requestPack": {
            "sid22": {"internalLength": 4, "hdr": [4, 3, 0x22], "pid": "u16be after SID", "at": "0xfde08..0xfde24", "notIsoTpPci": True},
            "sid1Aor21": {"internalLength": 3, "hdr": [3, 2, "SID"], "pid": "u8 after SID", "at": "0xfde2a..0xfde54", "notIsoTpPci": True},
        },
        "commCall": {
            "at": "0xfde6a",
            "got": 0x248390,
            "resolves": "PorscheSendCommandByIDEX",
            "wrapperVa": "0x13240",
            "commProBuffer": "TOKEN+0x1961",
            "nrc7f": "TOKEN+0x2961",
            "class": "static-source-confirmed",
        },
        "afterCommReturn": {
            "note": "r6=returned length; copy from sp buffer then strip",
            "sid22": {"skipBytes": 2, "at": "0xfde72", "ifLenGt2": "orr r1,r4,#2; subs r6,#2; memcpy", "ifLenLe2": "subs r6,#2 without copy at 0xfde9e"},
            "sid1Aor21": {"skipBytes": 1, "at": "0xfde82", "ifLenGe2": "orr r1,r4,#1; subs r6,#1; memcpy", "ifLenLt2": "r6=0 at 0xfdea2"},
        },
        "positiveLayoutStripped": {
            "sid22": "helper strips 2 bytes from COMM return buffer; not a proven wire APDU layout",
            "sid1Aor21": "helper strips 1 byte from COMM return buffer; not a proven wire APDU layout",
            "positiveSidBeforeStrip": {
                "class": "exact-branch-unknown",
                "note": "native positive-SID handling before this strip is unproven; not a native-reproduction claim",
            },
            "notOfflineApduContract": True,
        },
        "negativeTruncated": {
            "commLenLt1": "branch 0xfde70 copies nothing",
            "sid22_len_le_2": "no memcpy; r6-=2 may underflow semantics unresolved",
            "sid1A21_len_lt_2": "r6 forced 0",
            "unsupportedSid": "bne 0xfde30 skips pack; still calls comm with leftover stack header",
        },
        "class": "static-source-confirmed",
        "writePayload": None,
        "executionEnabled": False,
    }


def set_can_com_para() -> dict:
    return {
        "function": "SetCanComPara",
        "va": "0x1423c",
        "size": 42,
        "fileOff": 82492,
        "layout": {
            "b0": "stack arg ip (filter/flag)",
            "b1": "r3",
            "b2": "r1>>8",
            "b3": "r1 lo (11-bit id packing)",
            "b4": "r2>>8",
            "b5": "r2 lo",
            "zeroU32": ["+8", "+0x10", "+0x2c"],
        },
        "timingsInThisFn": False,
        "class": "static-source-confirmed",
        "remaining": {
            "class": "timing-labels-not-identified",
            "stop": "PorscheCanInitialize / PorscheCommunicationPro pass r1/r2 from TOKEN; P2/S3 names not labeled in this 42B setter",
            "locator": "libPORSCHE_COMM.so PorscheCanInitialize 0x11c30",
        },
    }


def coding_flow() -> dict:
    return {
        "read": {
            "record": "7-byte prefix + u16 len + id cstr + 8-byte suffix (byteOffset u16le, bitOffset u8, formula u32le)",
            "class": "static-source-confirmed",
            "writePayload": None,
        },
        "writeDispatch": {
            "CodingInterface": {"va": "0xcf7c", "TOKEN+0x28": ["0xb20", "0x1400", "0x1920"]},
            "Coding_Config": {"va": "0x1562c", "size": 472},
            "Coding_SendWriteCMD": {"va": "0x162e4", "size": 1460, "writePayload": None},
            "class": "static-source-confirmed",
        },
        "integrity": {
            "checksumInSource": None,
            "class": "unidentified-in-source",
            "stop": "no CRC/checksum immediate isolated in Coding_SendWriteCMD head; length uses 0xff fills then caller buffer",
        },
        "security": {
            "PorscheCodingKeyCalc": {"va": "0x15804", "size": 452},
            "class": "runtime-seed-needed",
            "stop": "algorithm symbol present; key/seed bytes are runtime TOKEN buffers / car response, not a missing file",
            "not": "missing-external-source",
        },
        "readback": {
            "PorscheReadSysCodeData": {"va": "0xf6a50", "lib": "libPORSCHE_READWRITE.so"},
            "class": "static-source-confirmed",
            "writePayload": None,
        },
        "executionEnabled": False,
        "liveVerified": False,
        "writePayload": None,
    }


def dtc_chains() -> dict:
    return {
        "PorscheReadDtc": {
            "va": "0x15ac8",
            "size": 3888,
            "fileOff": 88776,
            "opens": ["OpenDiagCommDll", "OpenDiagReadFileDll", "OpenShowInterfaceDLL", "OpenSearchIDDLL", "OpenAbstractCommDll", "OpenDiagFuncDll"],
            "parsers": ["PorscheSingleByteDtc", "PorscheDoubleByteDtc"],
            "buffer": "stack 0x400-byte clr; TOKEN+0xbe3c region clr 0x2000",
            "commandSource": {
                "class": "static-source-confirmed",
                "file": "PORSCHE_SYS_DATA.BIN",
                "GetDtcData": "opens same filename as GetCommData; TOKEN+0x60c file offset; TOKEN+0x6860 is TOKEN dest",
                "FromAddrGetCommand": "SID,b1,n,payload[n]; terminator 0/0xff after header; wire omits n",
                "wireExamples": {"kwp18": "1800FF00 from 180002ff00", "uds19": "190208 from 19020108"},
            },
        },
        "PorscheEraseDtc": {
            "va": "0x19f1c",
            "size": 2204,
            "fileOff": 106268,
            "TOKEN+0x28_window": "cmp 0x1603 then movw r6,#0x2101 (local-id path) vs other modes",
            "clearPayload": {
                "class": "static-source-confirmed",
                "note": "SID 0x14 constructors exist in SYS command table; not an executable write here",
                "writePayload": None,
            },
            "writePayload": None,
        },
        "statusKind": "DTC pool u8 is table field, not live status",
        "poolHeaderU32": {"value": 24, "hex": "0x18", "class": "static-source-confirmed", "notFullRequest": True},
    }


def deep_inspect(source_root: Path | None) -> dict:
    empty = {
        "ok": False,
        "dtc": None,
        "transCan": None,
        "udsRead": send_uds_layout(),
        "canPara": set_can_com_para(),
        "coding": coding_flow(),
        "fileReaders": None,
    }
    if source_root is None or not source_root.is_dir():
        return empty
    try:
        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs  # noqa: F401
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return empty
    comm = source_root / "libPORSCHE_COMM.so"
    dtcf = source_root / "libPORSCHE_DTCF.so"
    fil = source_root / "libPORSCHE_FILE.so"
    if not comm.is_file():
        return empty
    out = {
        "ok": True,
        "udsRead": send_uds_layout(),
        "canPara": set_can_com_para(),
        "coding": coding_flow(),
        "dtc": dtc_chains(),
    }
    with comm.open("rb") as f:
        elf = ELFFile(f)
        data = comm.read_bytes()
        out["transCan"] = transcan_tables(data, elf)
        plt = _plt(elf)
        out["commTraces"] = {
            "PorscheCommunicationPro": _trace_fn(data, elf, plt, 0xF6F0, 5812, 40),
            "PorscheCanInitialize": _trace_fn(data, elf, plt, 0x11C30, 1324, 25),
            "PorscheSetLinkKeep": _trace_fn(data, elf, plt, 0x12324, 1200, 25),
        }
    if dtcf.is_file():
        with dtcf.open("rb") as f:
            elf = ELFFile(f)
            data = dtcf.read_bytes()
            plt = _plt(elf)
            out["dtcTraces"] = {
                "PorscheReadDtc": _trace_fn(data, elf, plt, 0x15AC8, 3888, 50),
                "PorscheEraseDtc": _trace_fn(data, elf, plt, 0x19F1C, 2204, 40),
            }
    if fil.is_file():
        with fil.open("rb") as f:
            elf = ELFFile(f)
            data = fil.read_bytes()
            plt = _plt(elf)
            out["fileReaders"] = {
                "GetDtcData": _trace_fn(data, elf, plt, 0x171C0, 728, 20),
                "FromAddrGetCommand": _trace_fn(data, elf, plt, 0x17498, 140, 15),
                "GetCommData": _trace_fn(data, elf, plt, 0x15580, 1688, 20),
                "Xml_GetDriveLinksAddress": _trace_fn(data, elf, plt, 0x18A0C, 360, 20),
            }
    return out
