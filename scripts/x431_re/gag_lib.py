"""GAG lookup by id. Field-semantic namespaces only; no all-gag fanout."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from .gag_codec import decode_record, encrypt_number_strict, parse_text_gag
    from .ggp_index import parse_header
except ImportError:
    from gag_codec import decode_record, encrypt_number_strict, parse_text_gag
    from ggp_index import parse_header

KIND = {
    "TEXT_CN.GAG": "text_string",
    "TCODE_CN.GAG": "dtc",
    "TCODES_CN.GAG": "dtc_status",
    "DSTREAM_CN.GAG": "ds_name",
    "DSTREAMH_CN.GAG": "ds_help",
    "DSTREAMU_CN.GAG": "ds_unit",
    "EXPRESS.GAG": "express_formula",
}

RE_UNIT = re.compile(r"Unit=0x([0-9A-Fa-f]+)(?![0-9A-Fa-f])", re.I)
RE_MAP = re.compile(r"->0x([0-9A-Fa-f]{8})(?![0-9A-Fa-f])", re.I)
RE_KIND = re.compile(r"^([A-Z][A-Z0-9_]+):")
RE_ID = re.compile(r"0x([0-9A-Fa-f]{8})(?![0-9A-Fa-f])")
RE_HEX_LIT = re.compile(r"0x([0-9A-Fa-f]+)(?![0-9A-Fa-f])", re.I)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest().upper()


def sha256_path(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest().upper()


def split_nul(body: bytes) -> list[bytes]:
    if not body:
        return []
    parts = body.split(b"\x00")
    if body.endswith(b"\x00"):
        parts = parts[:-1]
    return parts


def decode_field(p: bytes) -> dict:
    row = {"hex": p.hex(), "len": len(p)}
    for enc in ("gb18030", "utf-8", "latin1"):
        try:
            s = p.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc != "latin1" and "\ufffd" in s:
            continue
        row["text"] = s
        row["encoding"] = enc
        break
    else:
        row["encoding"] = "binary"
    return row


def parse_express(text: str) -> dict:
    kind_m = RE_KIND.match(text)
    out = {
        "formula_kind": kind_m.group(1) if kind_m else None,
        "unit_id": None,
        "text_ids": [],
        "execution_enabled": False,
    }
    um = RE_UNIT.search(text)
    if um:
        out["unit_id"] = int(um.group(1), 16)
    out["text_ids"] = [int(x, 16) for x in RE_MAP.findall(text)]
    return out


def chain_ids(text: str) -> list[int]:
    return [int(x, 16) for x in RE_ID.findall(text)]


@dataclass
class GagFile:
    name: str
    offset: int
    size: int
    sha256: str
    st: dict
    kind: str


class GgpLanguage:
    def __init__(self, ggp: Path):
        self.path = Path(ggp)
        self.raw = self.path.read_bytes()
        self.ggp_sha256 = sha256_path(self.path)
        self.info = parse_header(self.raw)
        self.files: dict[str, GagFile] = {}
        for e in self.info["entries"]:
            blob = self.raw[e["offset"] : e["offset"] + e["size"]]
            st = parse_text_gag(blob)
            self.files[e["name_ascii"]] = GagFile(
                name=e["name_ascii"],
                offset=e["offset"],
                size=e["size"],
                sha256=sha256_bytes(blob),
                st=st,
                kind=KIND.get(e["name_ascii"], "unknown"),
            )

    def lookup_index(self, gag_name: str, i: int) -> dict | None:
        gf = self.files[gag_name]
        rec = decode_record(gf.st, i)
        if rec is None:
            return None
        key, body = rec
        pid = bytearray(key)
        encrypt_number_strict(pid, gf.st["ctx"])
        id_u32 = int.from_bytes(bytes(pid), "big")
        parts = [decode_field(p) for p in split_nul(body)]
        structured = {"fields": parts}
        if gf.kind == "express_formula":
            txt = next((p["text"] for p in parts if p.get("text")), "")
            structured["express"] = parse_express(txt)
        elif gf.kind == "dtc" and len(parts) >= 2:
            structured["code"] = parts[0].get("text")
            structured["description"] = parts[1].get("text")
        return {
            "gag": gf.name,
            "kind": gf.kind,
            "index": i,
            "id": id_u32,
            "id_hex": f"{id_u32:08X}",
            "structured": structured,
        }

    def lookup_id(self, gag_name: str, id_u32: int) -> dict | None:
        return self._lookup_id_cached(gag_name, id_u32)

    @lru_cache(maxsize=200000)
    def _lookup_id_cached(self, gag_name: str, id_u32: int) -> dict | None:
        gf = self.files[gag_name]
        needle = bytearray(id_u32.to_bytes(4, "big"))
        encrypt_number_strict(needle, gf.st["ctx"])
        lo, hi = 0, gf.st["count"] - 1
        keylen = gf.st["keylen"]
        rec = keylen + 4
        base0 = gf.st["index_off"] + 5
        gag = gf.st["gag"]
        while lo <= hi:
            mid = (lo + hi) // 2
            k = gag[base0 + mid * rec : base0 + mid * rec + keylen]
            if needle < k:
                hi = mid - 1
            elif needle > k:
                lo = mid + 1
            else:
                return self.lookup_index(gag_name, mid)
        return None


def compact_text(row: dict | None) -> str | None:
    """None = no GAG entry. '' = source entry with empty string. Do not collapse."""
    if not row:
        return None
    fields = (row.get("structured") or {}).get("fields") or []
    for f in fields:
        if "text" in f:
            return f["text"]
    return None


def compact_express(row: dict | None) -> dict | None:
    if not row:
        return None
    st = row.get("structured") or {}
    txt = compact_text(row)
    out = {"id_hex": row["id_hex"], "execution_enabled": False}
    if txt is not None:
        out["text"] = txt
    if st.get("express"):
        out["express"] = st["express"]
    return out

