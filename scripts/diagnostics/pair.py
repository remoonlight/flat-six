from __future__ import annotations

from datetime import datetime

NRC_PENDING = 0x78
DEFAULT_DEADLINE_S = 15.0


def _hex_bytes(pdu: dict) -> bytes:
    hx = pdu.get("payload_hex") or ""
    return bytes.fromhex(hx) if hx else b""


def _is_pending_nrc(payload: bytes, req_sid: int) -> bool:
    return (
        len(payload) >= 3
        and payload[0] == 0x7F
        and payload[1] == req_sid
        and payload[2] == NRC_PENDING
    )


def _is_terminal_nrc(payload: bytes, req_sid: int) -> bool:
    return (
        len(payload) >= 3
        and payload[0] == 0x7F
        and payload[1] == req_sid
        and payload[2] != NRC_PENDING
    )


def _positive_echo(req: bytes, resp: bytes) -> bool:
    if not req or not resp:
        return False
    rsid, ssid = req[0], resp[0]
    if ssid != ((rsid + 0x40) & 0xFF):
        return False
    if rsid == 0x1A:
        return len(req) >= 2 and len(resp) >= 2 and req[1] == resp[1]
    if rsid == 0x22:
        return len(req) >= 3 and len(resp) >= 3 and req[1:3] == resp[1:3]
    if rsid == 0x19:
        return len(req) >= 2 and len(resp) >= 2 and req[1] == resp[1]
    if rsid == 0x10:
        return len(req) >= 2 and len(resp) >= 2 and req[1] == resp[1]
    if rsid == 0x01:
        return len(req) == 2 and len(resp) >= 2 and req[1] == resp[1]
    if rsid == 0x14:
        return clear_sid14_positive(req, resp)
    if rsid == 0x17:
        return len(req) >= 2 and req[1:].hex().upper() in resp.hex().upper()
    return True


def clear_sid14_positive(req: bytes, resp: bytes) -> bool:
    """KWP 14+16bit group echoes in PR; UDS 14+24bit group has empty PR. Not a live-vehicle claim."""
    if not req or req[0] != 0x14 or not resp or resp[0] != 0x54:
        return False
    if len(req) == 3:
        return resp == bytes([0x54]) + req[1:3]
    if len(req) == 4:
        return resp == bytes([0x54])
    return False


def response_matches(req: bytes, resp: bytes) -> str | None:
    if not req or not resp:
        return None
    rsid = req[0]
    if _is_pending_nrc(resp, rsid):
        return "pending-7Fxx78"
    if _is_terminal_nrc(resp, rsid):
        return "negative"
    if resp[0] == 0x7F:
        return None
    if _positive_echo(req, resp):
        return "positive-echo"
    return None


def _pdu_t(pdu: dict) -> float | None:
    ts = pdu.get("timestamp_us")
    if ts is not None:
        return float(ts) / 1_000_000.0
    utc = pdu.get("utc")
    if not utc or utc == "t":
        return None
    try:
        return datetime.fromisoformat(str(utc).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pair_transactions(
    pdus: list[dict],
    req_id: int,
    resp_id: int,
    *,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> list[dict]:
    """Pair by address + SID/NRC SID + DID/local-id echo. 7Fxx78 stays open.

    Responses later than deadline_s after the request are not associated.
    Repeated identical requests stay flagged; they are not treated as a unique proof.
    """
    txs: list[dict] = []
    open_tx = None

    def close_open(status: str) -> None:
        nonlocal open_tx
        if open_tx is None:
            return
        open_tx["status"] = status
        txs.append(open_tx)
        open_tx = None

    def start_req(pdu: dict) -> None:
        nonlocal open_tx
        prev_hex = (open_tx.get("req_payload_hex") or "").upper() if open_tx is not None else ""
        same = prev_hex != "" and prev_hex == (pdu.get("payload_hex") or "").upper()
        if open_tx is not None:
            if same:
                open_tx["ambiguousRepeatedRequest"] = True
            close_open("incomplete-bounded-by-next-request")
        req = _hex_bytes(pdu)
        open_tx = {
            "status": "open",
            "req_id_hex": f"{req_id:03X}",
            "resp_id_hex": f"{resp_id:03X}",
            "req_payload_hex": pdu.get("payload_hex"),
            "req_sid": f"{req[0]:02X}" if req else None,
            "req_utc": pdu.get("utc"),
            "req_t": _pdu_t(pdu),
            "req_frames": list(pdu.get("frame_indices") or []),
            "req_complete": bool(pdu.get("complete")),
            "responses": [],
            "pending_nrc78": [],
            "final_payload_hex": None,
            "relation": None,
            "ambiguousRepeatedRequest": bool(same),
            "deadline_s": deadline_s,
        }

    for pdu in pdus:
        if pdu.get("kind") == "FC":
            continue
        cid = pdu["can_id"]
        if cid == req_id:
            if not pdu.get("complete"):
                start_req(pdu)
                close_open("incomplete-request")
                continue
            start_req(pdu)
            continue
        if cid != resp_id:
            continue
        payload = _hex_bytes(pdu)
        if open_tx is None:
            txs.append(
                {
                    "status": "unpaired_response",
                    "resp_payload_hex": pdu.get("payload_hex"),
                    "resp_utc": pdu.get("utc"),
                    "resp_frames": list(pdu.get("frame_indices") or []),
                    "resp_complete": bool(pdu.get("complete")),
                }
            )
            continue
        t_req = open_tx.get("req_t")
        t_resp = _pdu_t(pdu)
        if (
            t_req is not None
            and t_resp is not None
            and (t_resp - t_req) > float(open_tx.get("deadline_s") or DEFAULT_DEADLINE_S)
        ):
            txs.append(
                {
                    "status": "unpaired_response",
                    "reason": "late-after-deadline",
                    "resp_payload_hex": pdu.get("payload_hex"),
                    "resp_utc": pdu.get("utc"),
                    "resp_frames": list(pdu.get("frame_indices") or []),
                    "resp_complete": bool(pdu.get("complete")),
                }
            )
            continue
        if not pdu.get("complete"):
            open_tx["responses"].append(
                {
                    "kind": "partial-pdu",
                    "payload_hex": pdu.get("payload_hex"),
                    "frames": list(pdu.get("frame_indices") or []),
                    "issues": pdu.get("issues"),
                }
            )
            continue
        req = bytes.fromhex(open_tx["req_payload_hex"] or "")
        rel = response_matches(req, payload)
        if rel is None:
            txs.append(
                {
                    "status": "unmatched_response",
                    "reason": "wrong-sid-or-did-echo",
                    "req_payload_hex": open_tx["req_payload_hex"],
                    "resp_payload_hex": pdu.get("payload_hex"),
                    "resp_frames": list(pdu.get("frame_indices") or []),
                }
            )
            continue
        rec = {
            "relation": rel,
            "payload_hex": pdu.get("payload_hex"),
            "utc": pdu.get("utc"),
            "frames": list(pdu.get("frame_indices") or []),
        }
        open_tx["responses"].append(rec)
        if rel == "pending-7Fxx78":
            open_tx["pending_nrc78"].append(rec)
            continue
        open_tx["relation"] = rel
        open_tx["final_payload_hex"] = pdu.get("payload_hex")
        close_open("paired")

    if open_tx is not None:
        close_open("incomplete-eof")
    return txs
