from __future__ import annotations

import re
import threading
import time
from typing import Protocol

from .isotp import reconstruct_partition
from .pair import response_matches


class BytePort(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...


class ElmError(RuntimeError):
    pass


# ELM327DSJ: CAF0 owns PCI; H1 headers; S1 spaces; D0 no DLC column;
# CFC1 auto FC; FC SH/SD/SM1 user FC 30 00 00; CSM0 allows TX (default CSM1 silent).
SETTING_CMDS_BEFORE_IDS = (
    "ATE0",
    "ATL0",
    "ATS1",
    "ATH1",
    "ATD0",
    "ATCAF0",
    "ATCFC1",
    "ATTP6",
)


def sf_can_hex(req_hex: str) -> str:
    raw = bytes.fromhex(req_hex.replace(" ", ""))
    if not raw or len(raw) > 7:
        raise ElmError("sf-request-length")
    return (bytes([len(raw)]) + raw).hex().upper()


def spaced_hex(hx: str) -> str:
    h = hx.replace(" ", "").upper()
    return " ".join(h[i : i + 2] for i in range(0, len(h), 2))


def adapter_identity_ok(blob: str) -> bool:
    u = (blob or "").upper()
    return any(tok in u for tok in ("ELM327", "OBDLINK", "VLINKER", "STN11", "STN21"))


def _setting_ok(text: str) -> bool:
    lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip() and ln.strip() != ">"]
    if any(ln == "?" for ln in lines):
        return False
    blob = "\n".join(lines).upper()
    if "ERROR" in blob or "UNABLE" in blob:
        return False
    return any(ln.upper() == "OK" for ln in lines)


class ElmClient:
    """ELM327 ISO-TP with explicit CAF0 PCI ownership. Not ATMA. Not live-proven."""

    def __init__(self, port: BytePort, timeout_s: float = 8.0, *, clock=None, sleeper=None, cancel_event=None):
        self.port = port
        self.timeout_s = timeout_s
        self.raw_log: list[dict] = []
        self._closed = False
        self._configured = False
        self._rx_id: int | None = None
        self._tx_id: int | None = None
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._cancel = cancel_event
        self._io_lock = threading.Lock()
        self._poisoned = False
        self._poison_reason: str | None = None

    def _now(self) -> float:
        return self._clock()

    def _cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    def _raise_if_blocked(self, deadline: float | None = None) -> None:
        if self._poisoned or self._closed:
            raise ElmError("client-poisoned:" + (self._poison_reason or ""))
        if self._cancelled():
            raise ElmError("cancelled")
        if deadline is not None and self._now() >= deadline:
            raise ElmError("deadline-expired")

    def _poison(self, reason: str) -> None:
        self._poisoned = True
        self._poison_reason = reason
        self._configured = False
        try:
            self.port.close()
        except Exception:  # noqa: BLE001
            pass

    def _enter_io(self) -> None:
        if self._poisoned or self._closed:
            raise ElmError("client-poisoned:" + (self._poison_reason or ""))
        if not self._io_lock.acquire(blocking=False):
            raise ElmError("io-in-flight")

    def _drain_rx(self) -> bytes:
        buf = bytearray()
        try:
            while True:
                chunk = self.port.read(1)
                if not chunk:
                    break
                buf.extend(chunk)
                self.raw_log.append({"dir": "rx", "hex": chunk.hex().upper(), "note": "drain"})
        except (ConnectionError, OSError, TimeoutError) as e:
            self._poison(type(e).__name__)
            raise ElmError(f"port-io:{e}") from e
        return bytes(buf)

    def _write_all(self, payload: bytes, deadline: float | None = None) -> None:
        self._raise_if_blocked(deadline)
        try:
            n = self.port.write(payload)
        except (ConnectionError, OSError) as e:
            self._poison(type(e).__name__)
            raise ElmError(f"port-io:{e}") from e
        if n != len(payload):
            self._poison("short-write")
            raise ElmError("short-write")

    def _read_until_prompt(self, deadline: float, *, occupy: bool = False) -> str:
        buf = bytearray()
        try:
            while self._now() < deadline:
                if self._cancelled():
                    if occupy:
                        self._poison("cancelled")
                    raise ElmError("cancelled")
                chunk = self.port.read(1)
                if not chunk:
                    self._sleeper(0.01)
                    continue
                buf.extend(chunk)
                self.raw_log.append({"dir": "rx", "hex": chunk.hex().upper()})
                if buf.endswith(b">"):
                    return buf.decode("latin-1", errors="replace")
            raise ElmError("prompt-timeout:" + buf.decode("latin-1", errors="replace"))
        except ElmError as e:
            if str(e).startswith("prompt-timeout"):
                self._poison("prompt-timeout")
            raise
        except (ConnectionError, OSError, TimeoutError) as e:
            self._poison(type(e).__name__)
            raise ElmError(f"port-io:{e}") from e

    def _send_at_unlocked(self, cmd: str, deadline: float | None = None, *, require_ok: bool = False) -> str:
        dl = deadline if deadline is not None else (self._now() + self.timeout_s)
        self._raise_if_blocked(dl)
        payload = (cmd.strip() + "\r").encode("ascii")
        self.raw_log.append({"dir": "tx", "hex": payload.hex().upper(), "cmd": cmd})
        self._write_all(payload, dl)
        text = self._read_until_prompt(dl, occupy=True)
        if require_ok and not _setting_ok(text):
            raise ElmError(f"at-failed:{cmd}:{text}")
        return text

    def send_at(self, cmd: str, deadline: float | None = None, *, require_ok: bool = False) -> str:
        self._enter_io()
        try:
            return self._send_at_unlocked(cmd, deadline, require_ok=require_ok)
        finally:
            self._io_lock.release()

    def validate_adapter(self, budget_s: float = 10.0) -> dict:
        self._enter_io()
        try:
            deadline = self._now() + budget_s
            self._raise_if_blocked(deadline)
            ati = self._send_at_unlocked("ATI", deadline)
            atdpn = self._send_at_unlocked("ATDPN", deadline)
            atrv = self._send_at_unlocked("ATRV", deadline)
            blob = (ati + atdpn + atrv).upper()
            if not adapter_identity_ok(blob):
                raise ElmError("adapter-identity-mismatch")
            return {"ati": ati, "atdpn": atdpn, "atrv": atrv}
        finally:
            self._io_lock.release()

    def configure_pair(self, tx_hex: str, rx_hex: str, deadline: float) -> None:
        self._enter_io()
        try:
            self._configured = False
            tx = tx_hex.replace(" ", "").upper()
            rx = rx_hex.replace(" ", "").upper()
            cmds = list(SETTING_CMDS_BEFORE_IDS) + [
                f"ATSH {tx}",
                f"ATCRA {rx}",
                f"ATFCSH {tx}",
                "ATFCSD 300000",
                "ATFCSM1",
                "ATCSM0",
            ]
            for cmd in cmds:
                self._raise_if_blocked(deadline)
                self._send_at_unlocked(cmd, deadline, require_ok=True)
            self._tx_id = int(tx, 16)
            self._rx_id = int(rx, 16)
            self._configured = True
        finally:
            self._io_lock.release()

    def request(self, req_hex: str, deadline: float) -> dict:
        """One catalog SF. Wait NRC78 in the same prompt. Never retransmit. No user TX."""
        if self._poisoned or self._closed:
            raise ElmError("client-poisoned:" + (self._poison_reason or ""))
        if not self._configured or self._rx_id is None:
            raise ElmError("not-configured")
        try:
            self._enter_io()
        except ElmError as e:
            if str(e) == "io-in-flight":
                raise ElmError("request-in-flight") from e
            raise
        try:
            leftover = self._drain_rx()
            if leftover:
                self._poison("desynchronized-rx")
                raise ElmError("desynchronized-rx")
            self._raise_if_blocked(deadline)
            req = req_hex.replace(" ", "").upper()
            frame = spaced_hex(sf_can_hex(req))
            payload = (frame + "\r").encode("ascii")
            self.raw_log.append({"dir": "tx", "hex": payload.hex().upper(), "req": req, "sf": frame})
            self._write_all(payload, deadline)
            text = self._read_until_prompt(deadline, occupy=True)
            return parse_ath1_response(
                text,
                req_hex=req,
                rx_id=self._rx_id,
                tx_id=self._tx_id,
                sent_sf=frame.replace(" ", ""),
            )
        finally:
            self._io_lock.release()

    def close_restore(self) -> dict:
        errors = []
        if self._closed:
            return {"errors": errors}
        if not self._poisoned:
            try:
                self.send_at("ATPC", self._now() + 3, require_ok=True)
            except Exception as e:  # noqa: BLE001 — report restoration failure
                errors.append(f"ATPC:{e}")
        elif self._poison_reason:
            errors.append(f"poisoned:{self._poison_reason}")
        try:
            self.port.close()
        except Exception as e:  # noqa: BLE001
            errors.append(f"close:{e}")
        self._closed = True
        self._configured = False
        return {"errors": errors}


_CAN_ID = re.compile(r"^[0-9A-Fa-f]{3}$")
_HEX2 = re.compile(r"^[0-9A-Fa-f]{2}$")


def parse_can_line(line: str) -> tuple[int, bytes] | None:
    tokens = line.strip().split()
    if len(tokens) < 2 or not _CAN_ID.fullmatch(tokens[0]):
        return None
    if any(not _HEX2.fullmatch(t) for t in tokens[1:]):
        return None
    return int(tokens[0], 16), bytes(int(t, 16) for t in tokens[1:])


def parse_ath1_response(
    text: str,
    *,
    req_hex: str,
    rx_id: int,
    tx_id: int | None,
    sent_sf: str,
) -> dict:
    pending: list[str] = []
    req = req_hex.replace(" ", "").upper()
    frames: list[dict] = []
    skipped = []
    idx = 0
    for raw_line in text.replace("\r", "\n").split("\n"):
        s = raw_line.strip()
        if not s or s == ">":
            continue
        up = s.upper()
        if up in ("SEARCHING...", "OK") or up == req or s.replace(" ", "").upper() == sent_sf.upper():
            continue
        if up == "?":
            return {
                "ok": False,
                "error": "elm-query-mark",
                "pending": pending,
                "payload_hex": None,
                "raw": text,
            }
        if "NO DATA" in up or "UNABLE TO CONNECT" in up:
            return {"ok": False, "error": s, "pending": pending, "payload_hex": None, "raw": text}
        if "STOPPED" in up or "ERROR" in up:
            return {"ok": False, "error": s, "pending": pending, "payload_hex": None, "raw": text}
        parsed = parse_can_line(s)
        if parsed is None:
            skipped.append(s)
            continue
        cid, data = parsed
        if tx_id is not None and cid == tx_id:
            continue
        if cid != rx_id:
            skipped.append(s)
            continue
        frames.append(
            {
                "frame_index": idx,
                "utc": None,
                "timestamp_us": idx,
                "can_id": cid,
                "extended": False,
                "bus": 0,
                "data_hex": data.hex().upper(),
                "dlc": len(data),
            }
        )
        idx += 1
    if skipped and not frames:
        return {
            "ok": False,
            "error": "malformed-or-wrong-id",
            "pending": pending,
            "payload_hex": None,
            "raw": text,
            "skipped": skipped,
        }
    pdus, issues = reconstruct_partition(frames)
    data_pdus = [p for p in pdus if p.get("kind") != "FC"]
    if any(not p.get("complete") for p in data_pdus) or issues:
        return {
            "ok": False,
            "error": "isotp-incomplete-or-malformed",
            "pending": pending,
            "payload_hex": None,
            "raw": text,
            "issues": issues,
        }
    finals = []
    req_b = bytes.fromhex(req)
    for p in data_pdus:
        payload = bytes.fromhex(p.get("payload_hex") or "")
        rel = response_matches(req_b, payload)
        if rel == "pending-7Fxx78":
            pending.append(payload.hex().upper())
            continue
        if rel == "negative":
            return {
                "ok": False,
                "error": "negative-response",
                "pending": pending,
                "payload_hex": payload.hex().upper(),
                "raw": text,
            }
        if rel is None:
            return {
                "ok": False,
                "error": "wrong-sid-or-did-echo",
                "pending": pending,
                "payload_hex": payload.hex().upper() if payload else None,
                "raw": text,
            }
        finals.append(payload)
    if not finals:
        return {
            "ok": False,
            "error": "nrc78-prompt-without-final" if pending else "empty-payload",
            "pending": pending,
            "payload_hex": pending[-1] if pending else None,
            "raw": text,
        }
    if len(finals) != 1:
        return {
            "ok": False,
            "error": "mixed-final-frames",
            "pending": pending,
            "payload_hex": None,
            "raw": text,
        }
    return {
        "ok": True,
        "error": None,
        "pending": pending,
        "payload_hex": finals[0].hex().upper(),
        "raw": text,
    }
