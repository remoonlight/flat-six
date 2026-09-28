from __future__ import annotations

# SID 04/14 clear, 27 security, 2E write DID, 2F io, 31 routine, 34-37 download/transfer.
BLOCKED_SIDS = frozenset({0x04, 0x14, 0x27, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37})


def request_hex(data: bytes) -> str:
    return data.hex().upper()


def blocked_reason(payload: bytes, allowed_hex: set[str]) -> str | None:
    if not payload:
        return "empty-request"
    sid = payload[0]
    if sid in BLOCKED_SIDS:
        return f"blocked-sid-{sid:02X}"
    hx = request_hex(payload)
    if hx not in allowed_hex:
        return "unknown-request"
    return None


def engine_transaction_blocked(payload: bytes, authorized_hex: set[str] | frozenset[str]) -> str | None:
    """SID 01 only for the frozen Mode 01 seeds. Never 0120 or other groups."""
    if not payload:
        return "empty-request"
    if payload[0] != 0x01:
        return "not-authorized-engine"
    hx = request_hex(payload)
    if hx == "0120" or hx not in authorized_hex:
        return "engine-not-authorized"
    return None


def clear_transaction_blocked(payload: bytes, authorized_hex: str) -> str | None:
    """SID 14 only for one exact profile wire. SID 04/coding/security stay on blocked_reason."""
    if not payload:
        return "empty-request"
    if payload[0] != 0x14:
        return "not-authorized-clear"
    if not isinstance(authorized_hex, str) or not authorized_hex:
        return "clear-not-authorized"
    if request_hex(payload) != authorized_hex.replace(" ", "").upper():
        return "clear-not-authorized"
    return None
