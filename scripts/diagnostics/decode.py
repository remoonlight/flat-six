from __future__ import annotations

import re

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def redact_vin(text: str) -> str:
    if _VIN_RE.match(text):
        return text[:3] + "*" * 11 + text[-3:]
    return text


def sae_from_two_byte(dtc_hex: str) -> str:
    raw = bytes.fromhex(dtc_hex)
    if len(raw) != 2:
        raise ValueError("need two bytes")
    kind = "PCBU"[(raw[0] >> 6) & 3]
    return f"{kind}{(raw[0] >> 4) & 3}{raw[0] & 0xF:X}{raw[1]:02X}"


def _slice(payload: bytes, offset: int, length: int | None) -> bytes:
    if length is None:
        return payload[offset:]
    return payload[offset : offset + length]


def _ascii(raw: bytes) -> str:
    return raw.decode("latin-1", errors="replace").rstrip("\x00 ").strip()


def _fail(kind: str, reason: str, **extra) -> dict:
    out = {"type": kind, "ok": False, "reason": reason}
    out.update(extra)
    return out


def _observed_row(spec: dict, dtc_hex: str, status_hex: str) -> dict:
    cat = (spec or {}).get("observedCatalog") or {}
    row = cat.get(dtc_hex.upper()) or {}
    if not row:
        return {}
    extra = {}
    if row.get("displayCode"):
        extra["displayCode"] = row["displayCode"]
    if row.get("text"):
        extra["observedText"] = row["text"]
    if row.get("statusHex", "").upper() == status_hex.upper() and row.get("statusText"):
        extra["observedStatusText"] = row["statusText"]
    if row.get("manufacturerCode"):
        extra["manufacturerCode"] = True
    return extra


def decode_payload(payload_hex: str, spec: dict) -> dict:
    payload = bytes.fromhex(payload_hex or "")
    kind = spec.get("type")
    offset = int(spec.get("offset") or 0)
    length = spec.get("length")
    chunk = _slice(payload, offset, length)
    if kind in ("ascii", "vin-ascii"):
        if length is not None and len(chunk) != int(length):
            return _fail(kind, "truncated-ascii", hex=chunk.hex().upper())
        text = _ascii(chunk)
        if not text:
            return _fail(kind, "empty-ascii", hex=chunk.hex().upper())
        if kind == "vin-ascii":
            # A VIN is exactly 17 ASCII characters; trimming can hide bad wire
            # bytes and invalid text must not be saved as a vehicle identity.
            try:
                vin = chunk.decode("ascii")
            except UnicodeDecodeError:
                return _fail(kind, "invalid-vin")
            if _VIN_RE.fullmatch(vin) is None:
                return _fail(kind, "invalid-vin")
            return {"type": kind, "ok": True, "text": redact_vin(vin), "hex": None}
        return {"type": kind, "ok": True, "text": text, "hex": chunk.hex().upper()}
    if kind == "kwp-dtc-18":
        if len(payload) < 2:
            return _fail(kind, "truncated-dtc", rawHex=payload.hex().upper(), records=[])
        count = payload[1]
        need = 2 + count * 3
        if len(payload) != need:
            return _fail(
                kind,
                "count-inconsistent-dtc",
                rawHex=payload.hex().upper(),
                records=[],
                declaredCount=count,
            )
        records = []
        body = payload[2:]
        for i in range(count):
            rec = body[i * 3 : i * 3 + 3]
            dtc = rec[:2].hex().upper()
            status = f"{rec[2]:02X}"
            row = {
                "dtcHex": dtc,
                "statusHex": status,
                "displayCode": sae_from_two_byte(dtc),
            }
            row.update(_observed_row(spec, dtc, status))
            records.append(row)
        return {"type": kind, "ok": True, "records": records, "rawHex": payload.hex().upper()}
    if kind == "uds-dtc-19-02":
        if len(payload) < 3:
            return _fail(kind, "truncated-dtc", rawHex=payload.hex().upper(), records=[])
        avail = f"{payload[2]:02X}"
        body = payload[3:]
        if len(body) % 4 != 0:
            return _fail(
                kind,
                "count-inconsistent-dtc",
                rawHex=payload.hex().upper(),
                records=[],
                statusAvailabilityMaskHex=avail,
            )
        records = []
        for i in range(0, len(body), 4):
            rec = body[i : i + 4]
            dtc = rec[:3].hex().upper()
            status = f"{rec[3]:02X}"
            row = {"dtcHex": dtc, "statusHex": status, "displayCode": dtc, "manufacturerCode": True}
            row.update(_observed_row(spec, dtc, status))
            records.append(row)
        return {
            "type": kind,
            "ok": True,
            "statusAvailabilityMaskHex": avail,
            "records": records,
            "rawHex": payload.hex().upper(),
        }
    return {"type": kind or "unknown", "ok": True, "hex": chunk.hex().upper()}


def decode_transaction(tx: dict, operations: list[dict]) -> dict | None:
    req = (tx.get("req_payload_hex") or "").upper()
    final = tx.get("final_payload_hex")
    if tx.get("status") != "paired" or not final:
        return None
    for op in operations:
        if op["requestHex"].upper() != req:
            continue
        prefix = op["positivePrefixHex"].upper()
        if not final.upper().startswith(prefix):
            return {
                "operationId": op["id"],
                "ok": False,
                "reason": "positive-prefix-mismatch",
                "label": op.get("label"),
            }
        decoded = decode_payload(final, op["decode"])
        return {
            "operationId": op["id"],
            "ok": bool(decoded.get("ok")),
            "reason": decoded.get("reason"),
            "label": op.get("label"),
            "unit": (op.get("decode") or {}).get("unit"),
            "evidenceStatus": op.get("evidenceStatus"),
            "decoded": decoded,
        }
    return None
