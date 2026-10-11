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
import queue
from collections import deque
import re
import threading
import time

from .can_monitor import Monitor, MAX_LINE_BYTES, parse_frame
from .connection import open_selected_port, parse_voltage_volts, valid_device_id


class MemoryReceiver:
    def __init__(self, sink, device_id, network, clock=time.time_ns):
        self.sink, self.device_id, self.network, self.clock = sink, device_id, network, clock
        self.pending = bytearray()
        self.monitor_started = None
        self.count = 0
        self.invalid = 0
        self.enabled = False
        self.batch_id = None

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
                "simulation": False, "receiveBatchId": self.batch_id, "frames": frames, "frameCount": self.count, "invalidLines": self.invalid})
        return ended


def _receive_control(doc):
    if not isinstance(doc, dict):
        raise ValueError("internal-control-invalid")
    if doc == {"action": "stop"}:
        return doc
    action = doc.get("action")
    expected = {"action", "receiveBatchId"} | ({"receiveMilliseconds"} if action == "receive-start" else set())
    if action not in ("receive-start", "receive-stop") or set(doc) != expected:
        raise ValueError("internal-control-invalid")
    if not isinstance(doc["receiveBatchId"], str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", doc["receiveBatchId"]):
        raise ValueError("internal-batch-invalid")
    if action == "receive-start" and (type(doc["receiveMilliseconds"]) is not int or not 1 <= doc["receiveMilliseconds"] <= 600000):
        raise ValueError("internal-duration-invalid")
    return doc


def run_internal_monitor(device_id, network, *, stdout, stdin=None, port=None, stop_event=None,
                         clock=time.monotonic, max_reads=None, receive_ms=600000,
                         batch_id="standalone", commands=None, wait_fn=None):
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
    if (type(receive_ms) is not int or not 0 <= receive_ms <= 600000
            or not isinstance(batch_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", batch_id)):
        emit({"type": "error", "ok": False, "error": "internal-duration-or-batch-invalid"})
        return 2
    stop = stop_event or threading.Event()
    controls = commands if commands is not None else queue.Queue(maxsize=8)
    control_errors = []
    if stdin is not None and stop_event is None:
        def watch():
            try:
                while not stop.is_set():
                    line = stdin.readline(4097)
                    if not line:
                        stop.set()
                        break
                    if len(line) > 4096:
                        raise ValueError("internal-control-limit")
                    doc = _receive_control(json.loads(line))
                    if doc["action"] == "stop":
                        stop.set()
                        break
                    controls.put_nowait(doc)
            except Exception as exc:
                control_errors.append(str(exc))
                stop.set()
        threading.Thread(target=watch, daemon=True).start()
    receiver = MemoryReceiver(emit, device_id, network)
    monitor = None
    error = None
    receive_deadline = clock() + receive_ms / 1000
    receive_reason = None
    seen_batches = deque(maxlen=32)
    wait = wait_fn or stop.wait
    def state(kind="reading", identity=None):
        emit({"type": kind, "ok": True, "commOk": True, "at": time.time_ns() // 1_000_000,
            "volts": parse_voltage_volts((identity or {}).get("ATRV")), "voltageSource": "atrv",
            "identity": identity, "frameCount": receiver.count, "invalidLines": receiver.invalid,
            "receiveBatchId": receiver.batch_id, "receiveState": "receiving" if receiver.enabled else "standby",
            "receiveReason": receive_reason})
    def standby(reason):
        nonlocal receive_reason
        receiver.enabled = False
        monitor.stop()
        monitor.cleaning = False
        receiver.pending.clear()
        receive_reason = reason
        state()
    def begin(identifier, milliseconds):
        nonlocal receive_deadline, receive_reason
        receiver.enabled = False
        if monitor.monitoring:
            monitor.stop()
        monitor.cleaning = False
        receiver.pending.clear()
        receiver.batch_id = identifier
        seen_batches.append(identifier)
        receive_deadline = clock() + milliseconds / 1000
        receive_reason = None
        # Configure and identify locally first. Never start STM after expiry,
        # including a reconnect whose remaining budget was already exhausted.
        monitor.deadline = clock() + 25
        identity = monitor.start("all", listen=False)
        monitor.deadline = None
        if clock() < receive_deadline:
            monitor.begin_monitoring()
            receiver.enabled = True
        else:
            standby("time-limit")
        return identity
    def collect():
        nonlocal monitor, port, error
        try:
            if stop.is_set():
                error = control_errors[0] if control_errors else None
                return 1 if error else 0
            if port is None:
                port, _ = open_selected_port(device_id)
            monitor = Monitor(port, receiver, cancel_event=stop, clock=clock)
            identity = begin(batch_id, receive_ms)
            state("handshake", identity)
            reads = 0
            next_heartbeat = clock() + 1
            while not stop.is_set() and (max_reads is None or reads < max_reads):
                monitor.guard()
                while not controls.empty():
                    command = _receive_control(controls.get_nowait())
                    if command["action"] == "stop":
                        stop.set()
                        break
                    if command["action"] == "receive-stop":
                        if command["receiveBatchId"] == receiver.batch_id and receiver.enabled:
                            standby("time-limit")
                    elif command["receiveBatchId"] not in seen_batches:
                        begin(command["receiveBatchId"], command["receiveMilliseconds"])
                        state()
                if stop.is_set():
                    break
                reads += 1
                if receiver.enabled and clock() >= receive_deadline:
                    standby("time-limit")
                if receiver.enabled:
                    chunk = monitor.read()
                    if clock() >= receive_deadline:
                        standby("time-limit")
                    elif chunk and receiver.feed(chunk):
                        monitor.monitoring = False
                        monitor.prompt_ready = True
                        raise RuntimeError("internal-monitor-ended")
                else:
                    wait(0.05)
                if clock() >= next_heartbeat:
                    # ATRV is a head-local query, never a vehicle CAN request.
                    local = {"ATRV": monitor.command("ATRV")} if not receiver.enabled else None
                    state(identity=local)
                    next_heartbeat = clock() + 1
            error = control_errors[0] if control_errors else None
        except InterruptedError:
            pass
        except Exception as exc:
            error = f"port-io:{exc}" if isinstance(exc, (OSError, ConnectionError)) else str(exc)

    try:
        collect()
    finally:
        cleanup_errors = []
        receiver.enabled = False
        if monitor is not None:
            try:
                monitor.stop()
            except Exception as exc:
                cleanup_errors.append(f"internal-cleanup-failed:{exc}")
        if port is not None:
            try:
                port.close()
            except Exception as exc:
                cleanup_errors.append(f"internal-close-failed:{exc}")
        if error:
            emit({"type": "error", "ok": False, "error": error})
        emit({"type": "error" if cleanup_errors else "stopped", "ok": not cleanup_errors,
            "error": "internal-cleanup-failed" if cleanup_errors else None,
            "restoration": {"errors": cleanup_errors},
            "frameCount": receiver.count, "invalidLines": receiver.invalid})
    return 1 if error or cleanup_errors else 0
