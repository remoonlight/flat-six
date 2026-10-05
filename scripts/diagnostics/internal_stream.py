"""Continuous passive MX+ Drive CAN stream. No result files, ECU queries or replay.

Reuse the reviewed STN raw 500 kbit/s setup from can_monitor; ADAS/VNCI/vLinker
have no qualified raw receive profile here. Host arrival timestamps are explicit.
STCMM0 receive-only/no-ACK is specified by the official OBDLink manual:
https://www.scantool.net/scantool/downloads/678/obdlink_frpm_e.pdf
Software configuration does not substitute for independent physical silence tests.
"""
from __future__ import annotations

import json
import os
import threading
import time

from .can_monitor import Monitor, MAX_LINE_BYTES, parse_frame
from .connection import _watch_stdin_stop, open_selected_port, parse_voltage_volts, valid_device_id


class MemoryReceiver:
    def __init__(self, sink, device_id, network, clock=time.time_ns):
        self.sink, self.device_id, self.network, self.clock = sink, device_id, network, clock
        self.pending = bytearray()
        self.monitor_started = None
        self.count = 0
        self.invalid = 0
        self.enabled = False

    def event(self, *_args, **_kwargs):
        pass  # Adapter setup is not a physical CAN frame.

    def received(self, _data):
        pass  # No automatic serial log or file.

    def feed(self, data):
        frames = []
        stamp_us = self.clock() // 1000
        ended = False
        for byte in data:
            if byte in (10, 13, 62):
                line = bytes(self.pending).strip()
                self.pending.clear()
                if line:
                    parsed = parse_frame(line, allow_zero=self.enabled)
                    if parsed and self.enabled:
                        ident, extended, payload = parsed
                        frames.append({"canId": ident, "extended": extended, "dataHex": payload.hex().upper(),
                            "timestampUs": stamp_us, "timestampSource": "host-chunk-arrival"})
                        self.count += 1
                    elif line not in (b"STM", b"STOPPED"):
                        self.invalid += 1
                if byte == 62:
                    ended = True
                    break
            else:
                self.pending.append(byte)
                if len(self.pending) > MAX_LINE_BYTES:
                    raise RuntimeError("capture-line-limit")
        if frames:
            self.sink({"type": "frames", "ok": True, "deviceId": self.device_id, "canNetwork": self.network,
                "simulation": False, "frames": frames, "frameCount": self.count, "invalidLines": self.invalid})
        return ended


def run_internal_monitor(device_id, network, *, stdout, stdin=None, port=None, stop_event=None,
                         clock=time.monotonic, max_reads=None):
    flags = {"simulation": False, "liveVerified": False, "writePayload": None,
        "deviceId": device_id, "canNetwork": network, "physicalSilenceVerified": False,
        "ecuRequestsSent": 0, "silentMonitoringRequested": True}
    def emit(doc):
        stdout.write(json.dumps({**flags, **doc}, ensure_ascii=False) + "\n")
        stdout.flush()
    if any(os.environ.get(key) == "1" for key in ("PORSCHE981_SESSION_DENY_LIVE", "PORSCHE981_HEADLESS")):
        emit({"type": "error", "ok": False, "error": "live-probe-disabled"})
        return 2
    if not valid_device_id(device_id) or not device_id.startswith("bt:") or network != "drive":
        emit({"type": "error", "ok": False, "error": "internal-profile-not-qualified"})
        return 2
    stop = stop_event or threading.Event()
    if stdin is not None and stop_event is None:
        _watch_stdin_stop(stdin, stop)
    receiver = MemoryReceiver(emit, device_id, network)
    monitor = None
    error = None
    try:
        if stop.is_set():
            return 0
        if port is None:
            port, _ = open_selected_port(device_id)
        monitor = Monitor(port, receiver, cancel_event=stop, clock=clock, deadline=clock() + 25)
        identity = monitor.start("all")
        monitor.deadline = None
        receiver.enabled = True
        emit({"type": "handshake", "ok": True, "commOk": True, "at": time.time_ns() // 1_000_000,
            "volts": parse_voltage_volts(identity.get("ATRV")), "voltageSource": "atrv", "identity": identity})
        reads = 0
        next_heartbeat = clock() + 1
        while not stop.is_set() and (max_reads is None or reads < max_reads):
            monitor.guard()
            chunk = monitor.read()
            reads += 1
            if chunk and receiver.feed(chunk):
                monitor.monitoring = False
                monitor.prompt_ready = True
                raise RuntimeError("internal-monitor-ended")
            if clock() >= next_heartbeat:
                emit({"type": "reading", "ok": True, "commOk": True, "at": time.time_ns() // 1_000_000,
                    "volts": None, "voltageSource": "atrv", "frameCount": receiver.count,
                    "invalidLines": receiver.invalid})
                next_heartbeat = clock() + 1
    except InterruptedError:
        pass
    except Exception as exc:
        error = f"port-io:{exc}" if isinstance(exc, (OSError, ConnectionError)) else str(exc)
    finally:
        if monitor is not None:
            try:
                monitor.stop()
            except Exception as exc:
                error = error or f"internal-cleanup-failed:{exc}"
        if port is not None:
            try:
                port.close()
            except Exception as exc:
                error = error or f"internal-close-failed:{exc}"
        emit({"type": "error" if error else "stopped", "ok": error is None, "error": error,
            "frameCount": receiver.count, "invalidLines": receiver.invalid})
    return 1 if error else 0
