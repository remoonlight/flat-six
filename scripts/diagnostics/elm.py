from __future__ import annotations

import re
import threading
import time
from typing import Protocol
from .capture_clock import CAPTURE_CLOCK, capture_now_us, capture_timestamp

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
    return any(tok in u for tok in ("ELM327", "OBDLINK", "VLINKER", "V-LINKER", "OBDKEY", "STN11", "STN21"))


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

    def __init__(self, port: BytePort, timeout_s: float = 8.0, *, clock=None, sleeper=None, cancel_event=None, frame_sink=None, pdu_sink=None, expected_model=None):
        self.port = port
        self.timeout_s = timeout_s
        self.raw_log: list[dict] = []
        self._frame_sink = frame_sink
        self._pdu_sink = pdu_sink
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
        self._close_lock = threading.Lock()
        self._close_report = None
        self._port_close_lock = threading.Lock()
        self._port_close_errors = None
        self._expected_model = expected_model

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
        if self._poisoned:
            return
        self._poisoned = True
        self._poison_reason = reason
        self._configured = False
        self._close_port_once()

    def _close_port_once(self) -> list[str]:
        if not self._port_close_lock.acquire(blocking=False):
            return ["close-in-progress"]
        try:
            if self._port_close_errors is None:
                errors = []
                try:
                    self.port.close()
                except Exception as e:  # noqa: BLE001 — preserve release evidence
                    errors.append(f"close:{e}")
                self._port_close_errors = errors
            return list(self._port_close_errors)
        finally:
            self._port_close_lock.release()

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

    def _read_until_prompt(self, deadline: float, *, occupy: bool = False, observe_line=None) -> str:
        buf = bytearray()
        frame_line = bytearray()
        discard_line = False
        stamp_us = None
        line_started_us = None
        stamp_mono_us = None
        line_started_mono_us = None
        def observe_tail():
            # A complete ISO-TP payload may end in a line lacking CR/prompt.
            # The observer emits only lengths proven complete from actual bytes.
            if observe_line and (frame_line or discard_line) and stamp_us is not None:
                line = None if discard_line else bytes(frame_line).strip()
                frame_line.clear()
                observe_line(line, stamp_us, line_started_us or stamp_us, stamp_mono_us, line_started_mono_us or stamp_mono_us)
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
                observation = capture_timestamp()
                stamp_us, stamp_mono_us = observation['timestampUs'], observation['monotonicTimestampUs']
                self.raw_log.append({"dir": "rx", "hex": chunk.hex().upper(), **observation})
                if self._frame_sink or observe_line:
                    from .can_monitor import parse_frame
                    for byte in chunk:
                        if byte in (10, 13, 62):
                            line = None if discard_line else bytes(frame_line).strip()
                            started_us = line_started_us or stamp_us
                            started_mono_us = line_started_mono_us or stamp_mono_us
                            frame_line.clear(); discard_line = False; line_started_us = None
                            line_started_mono_us = None
                            if observe_line:
                                observe_line(line, stamp_us, started_us, stamp_mono_us, started_mono_us)
                            frame = None if line is None else parse_frame(line)
                            if frame and self._frame_sink:
                                can_id, extended, data = frame
                                self._frame_sink({"canId": can_id, "extended": extended, "dataHex": data.hex().upper(),
                                    **observation, "startedTimestampUs": started_us,
                                    "startedMonotonicTimestampUs": started_mono_us,
                                    "timestampSource": "host-chunk-arrival"})
                        elif not discard_line:
                            if len(frame_line) < 4096:
                                if not frame_line:
                                    line_started_us = stamp_us
                                    line_started_mono_us = stamp_mono_us
                                frame_line.append(byte)
                            else:
                                frame_line.clear(); discard_line = True
                if buf.endswith(b">"):
                    return buf.decode("latin-1", errors="replace")
            raise ElmError("prompt-timeout:" + buf.decode("latin-1", errors="replace"))
        except ElmError as e:
            if str(e).startswith("prompt-timeout"):
                self._poison("prompt-timeout")
            observe_tail()
            raise
        except (ConnectionError, OSError, TimeoutError) as e:
            self._poison(type(e).__name__)
            observe_tail()
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
            blob = ati.upper()
            if not adapter_identity_ok(blob):
                raise ElmError("adapter-identity-mismatch")
            if self._expected_model == 'OBDKey' and 'OBDKEY' not in blob:
                raise ElmError('adapter-model-mismatch')
            if self._expected_model == 'OBDLink' and not any(tok in blob for tok in ('OBDLINK', 'STN11', 'STN21')):
                raise ElmError('adapter-model-mismatch')
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

    def configure_standard_engine(self, deadline: float) -> None:
        """Fixed functional OBD route, filtered to known 7E8, SF-only responses.

        No ECU session-control request and no flow-control transmission. This
        configuration is used by the confirmed field runner and the bounded
        engine session after fresh named DME identity qualification.
        """
        self._enter_io()
        try:
            self._configured = False
            for cmd in (
                "ATE0", "ATL0", "ATS1", "ATH1", "ATD0", "ATCAF0",
                "ATCFC0", "ATTP6", "ATSH 7DF", "ATCRA 7E8", "ATCSM0",
            ):
                self._raise_if_blocked(deadline)
                self._send_at_unlocked(cmd, deadline, require_ok=True)
            self._tx_id = 0x7DF
            self._rx_id = 0x7E8
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
            observer = _ElmPduObserver(req, self._rx_id, self._tx_id, self._pdu_sink) if self._pdu_sink else None
            text = self._read_until_prompt(deadline, occupy=True, observe_line=observer.line if observer else None)
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
        if not self._close_lock.acquire(blocking=False):
            return {"errors": ["close-in-progress"]}
        try:
            if self._close_report is None:
                self._close_report = self._close_restore_once()
            return {"errors": list(self._close_report["errors"])}
        finally:
            self._close_lock.release()

    def _close_restore_once(self) -> dict:
        errors = []
        if not self._poisoned:
            cancel = self._cancel
            try:
                # Acquisition cancellation must not block bounded adapter-local
                # cleanup on a healthy link. No ECU request is replayed here.
                self._cancel = None
                self.send_at("ATPC", self._now() + 3, require_ok=True)
            except Exception as e:  # noqa: BLE001 — report restoration failure
                errors.append(f"ATPC:{e}")
            finally:
                self._cancel = cancel
        elif self._poison_reason:
            errors.append(f"poisoned:{self._poison_reason}")
        errors.extend(self._close_port_once())
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


class _ElmPduObserver:
    """Observe actual complete RX before prompt/decode; never repair ISO-TP gaps.

    Keep only the current candidate. Reuse the authoritative reassembler once
    enough actual bytes have arrived, avoiding reparsing the whole prompt for
    every byte/frame. Validation and the original transport failure stay intact.
    """
    def __init__(self, request, rx_id, tx_id, sink):
        self.request, self.rx_id, self.tx_id, self.sink = request, rx_id, tx_id, sink
        self.frames, self.expected, self.received = [], 0, 0

    def line(self, line, stamp_us, started_us, stamp_mono_us=None, started_mono_us=None):
        if stamp_mono_us is None:
            stamp_mono_us = capture_now_us()
        if started_mono_us is None:
            started_mono_us = stamp_mono_us
        if line is None:
            self.frames = []; self.expected = self.received = 0
            return
        parsed = parse_can_line(line.decode('latin-1'))
        if parsed is None:
            return
        cid, data = parsed
        if cid != self.rx_id or cid == self.tx_id:
            return
        if not 1 <= len(data) <= 8:
            self.frames = []; self.expected = self.received = 0
            return
        kind = data[0] >> 4
        frame = {'frame_index': len(self.frames), 'utc': None, 'timestamp_us': stamp_us,
                 'can_id': cid, 'extended': False, 'bus': 0, 'dlc': len(data), 'data_hex': data.hex()}
        if kind in (0, 1):
            self.frames = [dict(frame, frame_index=0)]
            self.started_us = started_us
            self.started_mono_us = started_mono_us
            self.expected = ((data[0] & 15) << 8 | data[1]) if kind == 1 and len(data) >= 2 else 0
            self.received = len(data)-2 if kind == 1 else 0
        elif kind == 2:
            if not self.frames or not self.expected:
                return
            if len(data) < 2:
                self.frames = []; self.expected = self.received = 0
                return
            self.frames.append(frame); self.received += len(data)-1
        elif kind == 3:
            return  # FC carries no application bytes and does not interrupt.
        else:
            self.frames = []; self.expected = self.received = 0
            return
        if kind == 0 or not self.expected or self.received >= self.expected:
            pdus, _ = reconstruct_partition(self.frames)
            self.frames = []; self.expected = self.received = 0
            for pdu in pdus:
                if pdu.get('complete') and pdu.get('payload_hex'):
                    self.sink({'requestHex': self.request, 'pduHex': pdu['payload_hex'],
                        'timestampUs': stamp_us, 'timestampSource': 'host-pdu-observed',
                        'startedTimestampUs': self.started_us,
                        'monotonicClock': CAPTURE_CLOCK, 'monotonicTimestampUs': stamp_mono_us,
                        'startedMonotonicTimestampUs': self.started_mono_us,
                        'transportKind': 'elm-iso-tp', 'observation': 'reassembled-complete-pdu',
                        'duringCleanup': False})


def parse_ath1_response(
    text: str,
    *,
    req_hex: str,
    rx_id: int,
    tx_id: int | None,
    sent_sf: str,
    on_pdu=None,
) -> dict:
    pending: list[str] = []
    req = req_hex.replace(" ", "").upper()
    frames: list[dict] = []
    skipped = []
    idx = 0
    adapter_error = None
    for raw_line in text.replace("\r", "\n").split("\n"):
        s = raw_line.strip()
        if not s or s == ">":
            continue
        up = s.upper()
        if up in ("SEARCHING...", "OK") or up == req or s.replace(" ", "").upper() == sent_sf.upper():
            continue
        if up == "?":
            adapter_error = adapter_error or "elm-query-mark"
            continue
        if any(marker in up for marker in ("NO DATA", "UNABLE TO CONNECT", "STOPPED", "ERROR")):
            adapter_error = adapter_error or s
            continue
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
    if skipped and not frames and not adapter_error:
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
    # Preserve each complete observed PDU before SID/echo/decode checks. A later
    # malformed PDU or NRC does not erase earlier received bytes. Never pad a
    # partial ISO-TP assembly or invent a TX packet.
    if on_pdu:
        for p in data_pdus:
            if p.get('complete') and p.get('payload_hex'):
                on_pdu(bytes.fromhex(p['payload_hex']))
    if adapter_error:
        return {"ok": False, "error": adapter_error, "pending": pending, "payload_hex": None, "raw": text}
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
