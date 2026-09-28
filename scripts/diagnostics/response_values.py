"""Strip already-reassembled UDS/KWP 21/22/1A application PDUs. Never infer mode. No serial."""
from __future__ import annotations

from .x431_formula import formula_from_record
from .x431_values import _is_bytes_type, classify_decode, decode_record

READ_SIDS = frozenset({0x21, 0x22, 0x1A})
NRC_PENDING = 0x78


def _hex(s) -> bytes | None:
    if not isinstance(s, str) or not s.strip():
        return None
    h = s.replace(" ", "").upper()
    if len(h) % 2 or any(c not in "0123456789ABCDEF" for c in h):
        return None
    return bytes.fromhex(h)


def _payload_from_record(rec: dict) -> bytes | None:
    cand = rec.get("disabledreadrequestcandidate")
    if isinstance(cand, dict):
        p = _hex(cand.get("payload_hex"))
        if p is not None:
            return p
    return _hex(rec.get("read_request_candidate_hex"))


def _pid_from_request(sid: int, payload: bytes) -> int | None:
    if sid == 0x22 and len(payload) == 3:
        return (payload[1] << 8) | payload[2]
    if sid in (0x21, 0x1A) and len(payload) == 2:
        return payload[1]
    return None


def request_from_record(rec: dict) -> dict:
    if not isinstance(rec, dict):
        return {"ok": False, "reason": "malformed_record"}
    payload = _payload_from_record(rec)
    if payload is None:
        return {"ok": False, "reason": "no_read_candidate"}
    raw = rec.get("rawSID")
    wire = rec.get("wireSID")
    if wire is not None or raw is not None:
        if type(wire) is bool or type(raw) is bool:
            return {"ok": False, "reason": "malformed_read_sid"}
        if type(wire) is not int or wire not in READ_SIDS:
            return {"ok": False, "reason": "unsupported_read_sid"}
        if payload[0] != wire:
            return {"ok": False, "reason": "wire-payload-inconsistent"}
        if raw == 0x31:
            if wire != 0x22 or not payload.startswith(b"\x22"):
                return {"ok": False, "reason": "sid31-wire-or-payload-inconsistent"}
        if wire == 0x22:
            if len(payload) != 3:
                return {"ok": False, "reason": "request_len_not_single_did"}
        elif len(payload) != 2:
            return {"ok": False, "reason": "request_len_not_single_lid"}
        return {
            "ok": True,
            "sid": wire,
            "request": payload,
            "headerLen": 3 if wire == 0x22 else 2,
            "rawSID": raw,
            "alias31to22": raw == 0x31 and wire == 0x22,
        }
    read = rec.get("readSID")
    if type(read) is not int:
        return {"ok": False, "reason": "malformed_read_sid"}
    if read not in READ_SIDS:
        return {"ok": False, "reason": "unsupported_read_sid"}
    pid = rec.get("pid")
    if type(pid) is not int:
        return {"ok": False, "reason": "malformed_pid"}
    if payload[0] != read:
        return {"ok": False, "reason": "read_sid_payload_mismatch"}
    if read == 0x22:
        if len(payload) != 3:
            return {"ok": False, "reason": "request_len_not_single_did"}
    elif len(payload) != 2:
        return {"ok": False, "reason": "request_len_not_single_lid"}
    expect = _pid_from_request(read, payload)
    if expect != pid:
        return {"ok": False, "reason": "pid_mismatch", "pid": pid, "payloadPid": expect}
    return {
        "ok": True,
        "sid": read,
        "request": payload,
        "headerLen": 3 if read == 0x22 else 2,
        "rawSID": None,
        "alias31to22": False,
        "schema": "coding-readSID",
    }


def data_span(rec: dict) -> dict | None:
    _, parsed = formula_from_record(rec)
    clf = classify_decode(parsed, rec.get("byteOffset"), rec.get("bitOffset"))
    if not clf.get("ok"):
        return None
    bo, bi = rec.get("byteOffset"), rec.get("bitOffset")
    if type(bo) is not int or type(bi) is not int or bo < 0 or bi < 0 or bi > 7:
        return None
    if _is_bytes_type(parsed) and parsed.get("kind") == "IDENTICAL":
        bl = parsed.get("bitLength")
        if type(bl) is int and bl > 0 and bl % 8 == 0:
            return {"kind": "exact", "dataMin": bo + bl // 8}
        minlen = parsed.get("minLength")
        n = minlen if type(minlen) is int and minlen >= 0 else 0
        return {"kind": "lowerBound", "dataMin": bo + n, "unknownRemaining": True}
    bl = parsed.get("bitLength")
    if type(bl) is not int or bl <= 0:
        return None
    return {"kind": "exact", "dataMin": bo + (bi + bl + 7) // 8}


def normalize_response(data: bytes, rec: dict, mode: str, *, source: str | None = None) -> dict:
    if mode not in ("data", "pdu"):
        return {"ok": False, "reason": "invalid_response_mode", "inferred": False}
    if not isinstance(data, (bytes, bytearray)):
        return {"ok": False, "reason": "malformed_input"}
    data = bytes(data)
    req = request_from_record(rec)
    flags = {
        "responseMode": mode,
        "inferred": False,
        "isotpStripped": False,
        "executionEnabled": False,
        "liveVerified": False,
        "writePayload": None,
        "source": source or "caller",
        "contract": "reassembled-uds-kwp-21-22-1A",
    }
    if mode == "data":
        return {"ok": True, "reason": None, "payload": data, "headerLen": 0, **flags}
    if not req.get("ok"):
        return {**flags, **req}
    if not data:
        return {"ok": False, "reason": "empty_pdu", **flags}
    sid = req["sid"]
    request = req["request"]
    if data[0] == 0x7F:
        if len(data) < 3:
            return {"ok": False, "reason": "truncated_negative", **flags}
        if data[1] != sid:
            return {"ok": False, "reason": "negative_wrong_sid", "echoedSid": data[1], **flags}
        nrc = data[2]
        return {
            "ok": False,
            "reason": "pending" if nrc == NRC_PENDING else "negative",
            "nrc": nrc,
            "pending": nrc == NRC_PENDING,
            "decoded": False,
            "echoedSid": data[1],
            **flags,
        }
    pos = (sid + 0x40) & 0xFF
    if data[0] != pos:
        return {"ok": False, "reason": "wrong_sid", "got": data[0], "want": pos, **flags}
    if sid == 0x22:
        if len(data) < 3:
            return {"ok": False, "reason": "truncated", **flags}
        if data[1:3] != request[1:3]:
            return {"ok": False, "reason": "wrong_did", **flags}
        payload = data[3:]
        header_len = 3
    else:
        if len(data) < 2:
            return {"ok": False, "reason": "truncated", **flags}
        if data[1] != request[1]:
            return {"ok": False, "reason": "wrong_lid", **flags}
        payload = data[2:]
        header_len = 2
    return {"ok": True, "reason": None, "payload": payload, "pdu": data, "headerLen": header_len, **flags}


def decode_application_response(rec: dict, data: bytes, mode: str = "data", *, source: str | None = None) -> dict:
    norm = normalize_response(data, rec, mode, source=source)
    if not norm.get("ok"):
        return {**norm, "ok": False}
    dec = decode_record(rec, norm["payload"])
    return {
        **dec,
        "responseMode": mode,
        "headerLen": norm.get("headerLen"),
        "inferred": False,
        "isotpStripped": False,
        "source": norm.get("source"),
        "contract": norm.get("contract"),
    }
