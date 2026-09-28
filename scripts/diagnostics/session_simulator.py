"""Deterministic BytePort ELM simulator. Never imports serial. Session traffic is synthetic."""
from __future__ import annotations

from .catalog import load_catalog, operation, profile_by_id
from .elm import ElmError

SCENARIOS = (
    "success",
    "identity-mismatch",
    "negative",
    "pending-timeout",
    "disconnect",
    "slow",
)

SYNTHETIC_VIN = "WP0ZZZ98ZES123456"
# 0100 bits: PID04 A.4, 05 A.3, 0C B.4, 0D B.3, 0F B.1, 11 C.7. Not a vehicle claim.
DEFAULT_ENGINE_MASK = bytes.fromhex("181A8000")
DEFAULT_ENGINE_PAYLOADS = {
    "0104": bytes.fromhex("410480"),
    "0105": bytes.fromhex("410564"),
    "010C": bytes.fromhex("410C1F40"),
    "010D": bytes.fromhex("410D50"),
    "010F": bytes.fromhex("410F32"),
    "0111": bytes.fromhex("411180"),
}


def isotp_ath1_lines(rx_id: int, payload: bytes) -> list[str]:
    def line(data: bytes) -> str:
        padded = (data + b"\xaa" * 8)[:8]
        return f"{rx_id:03X} " + " ".join(f"{b:02X}" for b in padded)

    if len(payload) <= 7:
        return [line(bytes([len(payload)]) + payload)]
    declared = len(payload)
    lines = [line(bytes([0x10 | ((declared >> 8) & 0x0F), declared & 0xFF]) + payload[:6])]
    rest = payload[6:]
    sn = 1
    while rest:
        chunk = rest[:7]
        rest = rest[7:]
        lines.append(line(bytes([0x20 | sn]) + chunk))
        sn = (sn + 1) & 0x0F
    return lines


def ath1_prompt(lines: list[str]) -> bytes:
    return ("\r".join(lines) + "\r\r>").encode("ascii")


def _ascii_pos(op: dict, text: str) -> bytes:
    prefix = bytes.fromhex(op["positivePrefixHex"])
    raw = text.encode("latin-1")
    length = (op.get("decode") or {}).get("length")
    if length is not None:
        raw = raw[: int(length)].ljust(int(length), b" ")
    return prefix + raw


def _sf_payload(spaced: str) -> str:
    toks = spaced.replace("\r", " ").split()
    raw = bytes(int(t, 16) for t in toks)
    n = raw[0] & 0x0F
    return raw[1 : 1 + n].hex().upper()


def catalog_success_map(
    profile_id: str,
    *,
    dtc_hex: str | None = None,
    identity_text: dict | None = None,
    engine_mask: bytes | None = None,
    engine_payloads: dict | None = None,
) -> dict[str, bytes]:
    cat = load_catalog()
    profile = profile_by_id(cat, profile_id)
    rx = int(profile["rxId"], 16)
    ident = identity_text or {}
    out: dict[str, bytes] = {}
    session_hex = "1089" if profile_id.endswith("-dme") else "1003"
    session_pos = bytes.fromhex("5089" if session_hex == "1089" else "5003")
    out[session_hex] = ath1_prompt(isotp_ath1_lines(rx, session_pos))
    for op in profile["operations"]:
        req = op["requestHex"].upper()
        kind = (op.get("decode") or {}).get("type")
        key = op["id"]
        if kind == "ascii":
            text = ident.get(key, op.get("expectedValue") or "")
            out[req] = ath1_prompt(isotp_ath1_lines(rx, _ascii_pos(op, str(text))))
        elif kind == "vin-ascii":
            out[req] = ath1_prompt(isotp_ath1_lines(rx, _ascii_pos(op, SYNTHETIC_VIN)))
        elif kind == "kwp-dtc-18":
            hx = dtc_hex or "5802C44728C41221"
            out[req] = ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex(hx)))
        elif kind == "uds-dtc-19-02":
            hx = dtc_hex or "590219C1300209"
            out[req] = ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex(hx)))
    if profile_id.endswith("-dme"):
        mask = engine_mask if engine_mask is not None else DEFAULT_ENGINE_MASK
        out["0100"] = ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex("4100") + mask))
        for req_hex, payload in (engine_payloads or DEFAULT_ENGINE_PAYLOADS).items():
            out[str(req_hex).upper()] = ath1_prompt(isotp_ath1_lines(rx, payload))
    out["_syntheticSession"] = b"1"
    return out


def scenario_map(profile_id: str, scenario: str, **kwargs) -> dict[str, bytes]:
    if scenario not in SCENARIOS:
        raise ElmError(f"unknown-scenario:{scenario}")
    cat = load_catalog()
    profile = profile_by_id(cat, profile_id)
    rx = int(profile["rxId"], 16)
    session_hex = "1089" if profile_id.endswith("-dme") else "1003"
    if scenario == "success":
        return catalog_success_map(profile_id, **kwargs)
    if scenario == "identity-mismatch":
        ident = {}
        for op in profile["operations"]:
            if (op.get("decode") or {}).get("type") == "ascii":
                ident[op["id"]] = "X" + str(op.get("expectedValue") or "")[1:]
        return catalog_success_map(profile_id, identity_text=ident, **kwargs)
    if scenario == "negative":
        nrc = bytes.fromhex("7F107F")
        return {session_hex: ath1_prompt(isotp_ath1_lines(rx, nrc))}
    if scenario == "pending-timeout":
        nrc = bytes.fromhex("7F1078")
        return {session_hex: ath1_prompt(isotp_ath1_lines(rx, nrc))}
    if scenario in ("disconnect", "slow"):
        return catalog_success_map(profile_id, **kwargs)
    return catalog_success_map(profile_id, **kwargs)


def _clear_hex(profile_id: str) -> str:
    return "14FF00" if profile_id.endswith("-dme") else "14FFFFFF"


def _dtc_req_hex(profile_id: str) -> str:
    return "1800FF00" if profile_id.endswith("-dme") else "190208"


def _empty_dtc_payload(profile_id: str) -> bytes:
    return bytes.fromhex("5800" if profile_id.endswith("-dme") else "590200")


class SessionSimPort:
    """BytePort: AT + one SF -> one ATH1 prompt. No serial."""

    trafficKind = "synthetic-session"

    def __init__(self, profile_id: str, scenario: str = "success", **kwargs):
        self.profile_id = profile_id
        self.scenario = scenario
        self.clear_behavior = kwargs.pop("clear_behavior", "positive")
        self.ecu_map = {k: v for k, v in scenario_map(profile_id, scenario, **kwargs).items() if not k.startswith("_")}
        self.writes: list[bytes] = []
        self.ecu_payloads: list[str] = []
        self.closed = False
        self._rx = bytearray()
        self.disconnect = scenario == "disconnect"
        self.hang = scenario == "slow"
        self._ecu_seen = 0
        self._cleared = False
        self.clear_tx_count = 0
        cat = load_catalog()
        rx = int(profile_by_id(cat, profile_id)["rxId"], 16)
        self._clear_hex = _clear_hex(profile_id)
        self._dtc_hex = _dtc_req_hex(profile_id)
        self._empty_dtc = ath1_prompt(isotp_ath1_lines(rx, _empty_dtc_payload(profile_id)))
        pos = bytes.fromhex("54FF00" if profile_id.endswith("-dme") else "54")
        nrc78 = bytes.fromhex("7F1478")
        self.preclear_probe = None
        self._clear_blobs = {
            "positive": ath1_prompt(isotp_ath1_lines(rx, pos)),
            "residual": ath1_prompt(isotp_ath1_lines(rx, pos)),
            "nrc": ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex("7F1472"))),
            "nrc78": ath1_prompt(isotp_ath1_lines(rx, nrc78)),
            "nrc78-then-positive": ath1_prompt(isotp_ath1_lines(rx, nrc78) + isotp_ath1_lines(rx, pos)),
            "mismatch": ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex("62F187"))),
            "extra-positive": ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex("54FFFFFF"))),
            "bare54": ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex("54"))),
            "wrong-group": ath1_prompt(isotp_ath1_lines(rx, bytes.fromhex("54FF01"))),
            "post-fail": ath1_prompt(isotp_ath1_lines(rx, pos)),
        }

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ConnectionError("disconnect")
        self.writes.append(bytes(data))
        text = data.decode("ascii", errors="replace").strip()
        up = text.upper()
        if up.startswith("AT"):
            if self.disconnect and self._ecu_seen:
                raise ConnectionError("disconnect")
            if up == "ATI":
                self._rx.extend(b"ELM327 v2.3\r\r>")
            elif up == "ATDPN":
                self._rx.extend(b"A6\r\r>")
            elif up == "ATRV":
                self._rx.extend(b"12.0V\r\r>")
            elif up == "ATPC":
                self._rx.extend(b"OK\r\r>")
            else:
                self._rx.extend(b"OK\r\r>")
            return len(data)
        if self.disconnect:
            raise ConnectionError("disconnect")
        if self.hang:
            return len(data)
        req = _sf_payload(up)
        self.ecu_payloads.append(req)
        self._ecu_seen += 1
        if req == self._clear_hex:
            self.clear_tx_count += 1
            if callable(self.preclear_probe):
                self.preclear_probe()
            if self.clear_behavior == "disconnect":
                raise ConnectionError("disconnect")
            blob = self._clear_blobs.get(self.clear_behavior)
            if blob is None:
                self._rx.extend(b"NO DATA\r\r>")
            else:
                if self.clear_behavior in ("positive", "residual", "nrc78-then-positive", "post-fail"):
                    self._cleared = True
                self._rx.extend(blob)
            return len(data)
        if req == self._dtc_hex and self._cleared and self.clear_behavior == "post-fail":
            self._rx.extend(b"NO DATA\r\r>")
            return len(data)
        if req == self._dtc_hex and self._cleared and self.clear_behavior in ("positive", "nrc78-then-positive"):
            self._rx.extend(self._empty_dtc)
            return len(data)
        blob = self.ecu_map.get(req)
        if blob is None:
            self._rx.extend(b"NO DATA\r\r>")
        else:
            self._rx.extend(blob)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if self.closed:
            return b""
        if not self._rx:
            return b""
        n = min(size, len(self._rx))
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def close(self) -> None:
        self.closed = True
