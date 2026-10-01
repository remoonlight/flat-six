"""Bounded MX+ raw Drive CAN reception; never issues an OBD/CAN request.

OBDLink FRPM revision F: https://www.obdlink.com/frpm (sections 8.6–8.10).
STP 31 selects raw ISO 11898 / 500 kbit/s; STM preserves raw frame payloads.
STCMM 0 disables CAN ACKs. These are adapter runtime settings, not ECU writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

from .connection import collect_snapshot, enumerate_devices, open_selected_port, valid_device_id

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".local" / "mxplus-drive-can"
SETUP = (
    "STPC", "ATE0", "ATL0", "ATH1", "ATS1", "ATD0",
    "STP 31", "STCMM 0", "ATCAF0", "ATCFC0", "STCSEGR 0",
    "STFAC", "ATCF 00000000", "ATCM 00000000", "STFPA 0000,0000",
)
QUERIES = ("ATI", "STI", "STDI", "STPR", "STPBRR", "ATRV")
CANDIDATE_IDS = (0x081, 0x086, 0x102, 0x103, 0x104, 0x105, 0x11F)
CANDIDATE_FILTERS = tuple(f"STFPA {ident:03X},7FF" for ident in CANDIDATE_IDS)
ALLOWED = frozenset((*SETUP, *QUERIES, *CANDIDATE_FILTERS, "STM"))
MAX_RX_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 256


def utc():
    return datetime.now(timezone.utc).isoformat()


def archive_references(directory):
    """Freeze the inspected project candidates alongside captures; no live decode."""
    index = ROOT / "docs/research/can-data/internal/signals.json"
    reference = {"verified_signals": 0, "runtime_decode_enabled": False,
                 "command_manual": "https://www.obdlink.com/frpm", "manual_revision": "F (2025-08-29)",
                 "candidate_id_counting": "embedded 981 REF candidate IDs; standard IDE and DLC 8 only",
                 "index_sha256": None, "signals": [], "missing": []}
    if index.is_file():
        raw = index.read_bytes()
        reference["index_sha256"] = hashlib.sha256(raw).hexdigest()
        reference["signals"] = [row for row in json.loads(raw)["signals"] if "981" in row.get("models", [])]
    else:
        reference["missing"].append(str(index.relative_to(ROOT)))
    names = ["reference-candidates.json"]
    for model in ("Boxster", "Cayman"):
        name = f"Porsche-{model}_(981)_2012-2016.REF"
        path = ROOT / ".local/can-data-library/internal/racelogic" / name
        if path.is_file():
            (directory / name).write_bytes(path.read_bytes())
            names.append(name)
        else:
            reference["missing"].append(str(path.relative_to(ROOT)))
    (directory / names[0]).write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
    return names


def parse_frame(line: bytes):
    """H1/S1/D0 raw classical CAN only; preserve IDE and DLC, reject other text."""
    tokens = line.split()
    if not tokens or not re.fullmatch(rb"(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{8})", tokens[0]):
        return None
    extended = len(tokens[0]) == 8
    ident = int(tokens[0], 16)
    if ident > (0x1FFFFFFF if extended else 0x7FF) or not 1 <= len(tokens) - 1 <= 8:
        return None
    if any(not re.fullmatch(rb"[0-9A-Fa-f]{2}", token) for token in tokens[1:]):
        return None
    return ident, extended, bytes(int(token, 16) for token in tokens[1:])


class Recorder:
    def __init__(self, directory: Path, *, clock=time.monotonic):
        self.directory = directory
        self.clock = clock
        self.start = clock()
        self.files = []
        try:
            for name, mode in (("serial.jsonl", "x"), ("serial-rx.bin", "xb"), ("frames.jsonl", "x")):
                self.files.append((directory / name).open(mode, **({"encoding": "utf-8"} if mode == "x" else {})))
        except BaseException:
            for file in self.files:
                file.close()
            raise
        self.serial_log, self.raw, self.frames = self.files
        self.rx_bytes = 0
        self.counts = Counter()
        self.pending = bytearray()
        self.invalid = 0
        self.notices = Counter()
        self.monitor_started = None

    def event(self, kind, **fields):
        self.serial_log.write(json.dumps({"utc": utc(), "event": kind,
                                          "host_elapsed_us": int((self.clock() - self.start) * 1e6), **fields}) + "\n")

    def received(self, data):
        self.raw.write(data)
        self.rx_bytes += len(data)
        self.event("serial_rx", hex=data.hex())
        if self.rx_bytes > MAX_RX_BYTES:
            raise RuntimeError("capture-byte-limit")

    def feed(self, data):
        # Timestamp is the host arrival of the chunk containing the final line byte.
        # It is NOT a CAN hardware timestamp; several frames may share this value.
        stamp = utc()
        ticks = int((self.clock() - self.start) * 1e6)
        for byte in data:
            if byte in (10, 13, 62):
                line = bytes(self.pending).strip()
                self.pending.clear()
                if line:
                    frame = parse_frame(line)
                    if frame is None:
                        if line not in (b"STM", b"STOPPED"):
                            self.invalid += 1
                        notice = line.decode("ascii", errors="replace")[:128]
                        if notice not in self.notices and len(self.notices) >= 256:
                            notice = "[additional distinct notices]"
                        self.notices[notice] += 1
                    else:
                        ident, extended, payload = frame
                        self.counts[(ident, extended, len(payload))] += 1
                        self.frames.write(json.dumps({"utc": stamp, "event": "frame", "timestamp_us": ticks,
                            "source": "OBDLink MX+", "bus": 0, "can_id": ident, "extended": extended,
                            "dlc": len(payload), "data_hex": payload.hex()}) + "\n")
                if byte == 62:
                    return True
            else:
                self.pending.append(byte)
                if len(self.pending) > MAX_LINE_BYTES:
                    raise RuntimeError("capture-line-limit")
        return False

    def summary(self):
        return {
            "frame_count": sum(self.counts.values()), "rx_bytes": self.rx_bytes,
            "partitions": [{"source": "OBDLink MX+", "bus": 0, "can_id": ident,
                "id_hex": f"0x{ident:03X}", "extended": ide, "dlc": dlc, "count": count}
                for (ident, ide, dlc), count in sorted(self.counts.items())],
            "candidate_id_hits": {f"0x{ident:03X}": sum(count for (cid, ide, dlc), count in self.counts.items()
                if cid == ident and not ide and dlc == 8) for ident in CANDIDATE_IDS},
            "unparsed_lines": self.invalid, "adapter_notices": dict(self.notices),
            "trailing_partial_bytes": len(self.pending),
        }

    def close(self):
        for file in self.files:
            file.flush()
            os.fsync(file.fileno())
            file.close()


class Monitor:
    def __init__(self, port, recorder, *, clock=time.monotonic, timeout_s=4.0, cancel_event=None, deadline=None):
        self.port, self.recorder, self.clock = port, recorder, clock
        self.timeout_s = timeout_s
        self.monitoring = False
        self.prompt_ready = False
        self.mx_verified = False
        self.cancel_event = cancel_event
        self.deadline = deadline
        self.cleaning = False

    def guard(self):
        if self.cleaning:
            return
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("cancelled")
        if self.deadline is not None and self.clock() >= self.deadline:
            raise TimeoutError("capture-deadline")

    def write(self, payload):
        self.recorder.event("serial_tx", hex=payload.hex())
        if self.port.write(payload) != len(payload):
            raise RuntimeError("serial-short-write")

    def read(self, amount=4096):
        data = self.port.read(amount)
        if data:
            self.recorder.received(data)
        return data

    def prompt(self, *, record_frames=False):
        end = self.clock() + self.timeout_s
        data = bytearray()
        while self.clock() < end:
            self.guard()
            chunk = self.read()
            data.extend(chunk)
            if record_frames and chunk:
                self.recorder.feed(chunk)
            if len(data) > 65536:
                raise RuntimeError("adapter-response-limit")
            if b">" in data:
                self.prompt_ready = True
                return bytes(data)
        raise TimeoutError("adapter-prompt-timeout")

    def command(self, command, *, require_ok=False):
        self.guard()
        if command not in ALLOWED or command == "STM":
            raise ValueError("command-not-allowed")
        self.prompt_ready = False
        self.write((command + "\r").encode("ascii"))
        response = self.prompt()
        # Require a single clean response; OK followed by ?/ERROR must fail closed.
        lines = [s.strip() for s in response.replace(b">", b"\r").replace(b"\n", b"\r").split(b"\r") if s.strip()]
        lines = [s for s in lines if s.replace(b" ", b"").upper() != command.replace(" ", "").encode("ascii")]
        if require_ok and lines != [b"OK"]:
            raise RuntimeError(f"adapter-command-rejected:{command}:{response!r}")
        return b"\n".join(lines).decode("ascii", errors="strict")

    def start(self, filter_profile="all"):
        identity = {command: self.command(command) for command in ("ATI", "STI", "STDI")}
        if not re.fullmatch(r"STN225[56] v\d+\.\d+\.\d+", identity["STI"], re.I) or not re.fullmatch(
            r"OBDLink MX\+ r\d+\.\d+(?:\.\d+)?", identity["STDI"], re.I
        ):
            raise RuntimeError("mxplus-hardware-identity-mismatch")
        self.mx_verified = True
        identity["original_protocol"] = self.command("STPR")
        identity["ATRV"] = self.command("ATRV")
        for command in SETUP:
            if filter_profile == "981-candidates" and command == "STFPA 0000,0000":
                continue
            self.command(command, require_ok=True)
        if filter_profile == "981-candidates":
            for command in CANDIDATE_FILTERS:
                self.command(command, require_ok=True)
        identity["protocol"] = self.command("STPR")
        identity["bitrate"] = self.command("STPBRR")
        if identity["protocol"] != "31" or identity["bitrate"] != "500000":
            raise RuntimeError("monitor-protocol-mismatch")
        self.prompt_ready = False
        self.guard()
        self.monitoring = True  # cleanup also handles a partially transmitted start
        self.recorder.monitor_started = self.clock()
        self.write(b"STM\r")
        return identity

    def stop(self):
        self.cleaning = True
        if self.monitoring:
            # One character stops a running monitor. Never send a second CR to a
            # prompt: an empty command would replay the previous command.
            self.write(b"\r")
            self.prompt(record_frames=True)
            self.monitoring = False
        if self.mx_verified and self.prompt_ready:
            self.command("STPC", require_ok=True)


def capture(device_id: str, directory: Path, *, duration_s=20.0, filter_profile="all", snapshot=None, port=None, clock=time.monotonic, cancel_event=None, progress_sink=None):
    if not valid_device_id(device_id):
        raise ValueError("device-id-invalid: supply bt:<12 hex MAC digits>, not COM")
    if not math.isfinite(duration_s) or not 0 < duration_s <= 60:
        raise ValueError("duration must be > 0 and <= 60 seconds")
    if filter_profile not in ("all", "981-candidates"):
        raise ValueError("invalid-filter-profile")
    injected = port is not None
    if not injected and any(os.environ.get(key) == "1" for key in ("PORSCHE981_SESSION_DENY_LIVE", "PORSCHE981_HEADLESS")):
        raise RuntimeError("live-capture-disabled")
    directory.mkdir(parents=True, exist_ok=False)
    state = {"device_id": device_id, "started_utc": utc(), "duration_requested_s": duration_s,
        "filter_profile": filter_profile, "receive_filter_commands": list(CANDIDATE_FILTERS) if filter_profile == "981-candidates" else ["STFPA 0000,0000"],
        "simulation": injected, "opened_port": False, "state": "starting", "error": None, "cleanup_error": None,
        "network": "Drive CAN (user-declared physical wiring)", "bus_index_meaning": "single adapter input, not a verified physical bus number",
        "timestamp_source": "host chunk reception; no hardware clock / independent reference",
        "raw_protocol": "ISO 11898, preset 31, configured 500000 bit/s", "ecu_requests_sent": 0,
        "silent_monitoring_requested": True, "physical_silence_verified": False,
        "runtime_settings_restored": False, "reference_verified_signals": 0}
    def save():
        temporary = directory / "status.json.tmp"
        with temporary.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        temporary.replace(directory / "status.json")
        if progress_sink:
            progress_sink({"type": "progress", "state": state["state"], "frame_count": sum(recorder.counts.values())})
    recorder = Recorder(directory, clock=clock)
    monitor = None
    reference_files = []
    save()
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("cancelled")
        reference_files = archive_references(directory)
        snap = snapshot if snapshot is not None else collect_snapshot()
        listed = enumerate_devices(snap)
        state["discovery"] = listed
        devices = [d for d in listed["devices"] if d["id"] == device_id]
        if len(devices) != 1 or devices[0]["brand"] == "vLinker":
            raise RuntimeError("mxplus-device-identity-missing-or-wrong")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("cancelled")
        if port is None:
            port, state["com_port"] = open_selected_port(device_id, snapshot=snap)
        else:
            state["com_port"] = "injected"
        state["opened_port"] = True
        monitor = Monitor(port, recorder, clock=clock, cancel_event=cancel_event, deadline=clock() + duration_s + 30)
        state["identity"] = monitor.start(filter_profile)
        state["state"] = "receiving"
        save()
        end = clock() + duration_s
        next_progress = clock() + 1
        while clock() < end:
            monitor.guard()
            chunk = monitor.read()
            if chunk and recorder.feed(chunk):
                monitor.monitoring = False
                monitor.prompt_ready = True
                raise RuntimeError("monitor-ended-before-duration")
            if progress_sink and clock() >= next_progress:
                progress_sink({"type": "progress", "state": "receiving", "frame_count": sum(recorder.counts.values())})
                next_progress = clock() + 1
        state["state"] = "received"
    except InterruptedError:
        state["state"] = "cancelled"
        state["error"] = "cancelled"
    except KeyboardInterrupt:
        state["state"] = "cancelled"
        state["error"] = "user-interrupt"
    except Exception as exc:
        state["state"] = "failed"
        state["error"] = str(exc)
    finally:
        if monitor is not None:
            try:
                monitor.stop()
            except Exception as exc:
                state["cleanup_error"] = str(exc)
        if port is not None:
            try:
                port.close()
            except Exception as exc:
                state["cleanup_error"] = state["cleanup_error"] or str(exc)
        if recorder.monitor_started is not None:
            state["monitor_elapsed_s"] = clock() - recorder.monitor_started
        state.update(recorder.summary())
        recorder.close()
        state["artifacts"] = []
        for name in ("serial.jsonl", "serial-rx.bin", "frames.jsonl", *reference_files):
            path = directory / name
            state["artifacts"].append({"file": name, "bytes": path.stat().st_size,
                                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        state["finished_utc"] = utc()
        state["recorder_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        state["ok"] = state["state"] == "received" and not state["cleanup_error"] and state["frame_count"] > 0 and state["unparsed_lines"] == 0
        if state["state"] == "received" and not state["frame_count"]:
            state["state"] = "no-frames"
        save()
    return state


def replay(directory: Path):
    """Verify local capture hashes and recompute partitions without any device I/O."""
    state = json.loads((directory / "status.json").read_text(encoding="utf-8"))
    checked = []
    for artifact in state["artifacts"]:
        name = artifact["file"]
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("invalid-artifact-path")
        path = directory / name
        if path.stat().st_size > 64 * 1024 * 1024 or path.resolve().parent != directory.resolve():
            raise ValueError("artifact-limit-or-path")
        raw = path.read_bytes()
        if len(raw) != artifact["bytes"] or hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise ValueError("artifact-hash-mismatch:" + name)
        checked.append(name)
    if not {"serial.jsonl", "serial-rx.bin", "frames.jsonl"} <= set(checked):
        raise ValueError("required-artifact-missing")
    counts = Counter()
    last_stamp = -1
    for line in (directory / "frames.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        payload = bytes.fromhex(row["data_hex"])
        if (row["source"] != "OBDLink MX+" or row["bus"] != 0 or type(row["extended"]) is not bool
                or type(row["can_id"]) is not int or not 0 <= row["can_id"] <= (0x1FFFFFFF if row["extended"] else 0x7FF)
                or row["dlc"] != len(payload) or not 1 <= row["dlc"] <= 8
                or type(row["timestamp_us"]) is not int or row["timestamp_us"] < last_stamp):
            raise ValueError("invalid-recorded-frame")
        last_stamp = row["timestamp_us"]
        counts[(row["can_id"], row["extended"], row["dlc"])] += 1
    recorded = {(r["can_id"], r["extended"], r["dlc"]): r["count"] for r in state["partitions"]}
    if counts != recorded or sum(counts.values()) != state["frame_count"]:
        raise ValueError("partition-summary-mismatch")
    rx = hashlib.sha256()
    rx_bytes = 0
    monitored = False
    pending = bytearray()
    rebuilt = Counter()
    notices = Counter()
    for line in (directory / "serial.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["event"] == "serial_tx":
            tx = bytes.fromhex(row["hex"])
            if tx == b"STM\r":
                monitored = True
            elif tx not in (b"\r",):
                monitored = False
        if row["event"] == "serial_rx":
            chunk = bytes.fromhex(row["hex"])
            rx.update(chunk)
            rx_bytes += len(chunk)
            if monitored:
                for byte in chunk:
                    if byte in (10, 13, 62):
                        raw_line = bytes(pending).strip()
                        pending.clear()
                        parsed = parse_frame(raw_line)
                        if parsed:
                            ident, extended, payload = parsed
                            rebuilt[(ident, extended, len(payload))] += 1
                        elif raw_line:
                            notices[raw_line.decode("ascii", errors="replace")[:128]] += 1
                    else:
                        pending.append(byte)
                        if len(pending) > MAX_LINE_BYTES:
                            raise ValueError("replay-line-limit")
    if rx.hexdigest() != hashlib.sha256((directory / "serial-rx.bin").read_bytes()).hexdigest() or rx_bytes != state["rx_bytes"]:
        raise ValueError("serial-chunks-mismatch")
    if rebuilt != counts or dict(notices) != state["adapter_notices"]:
        raise ValueError("raw-frame-replay-mismatch")
    return {"type": "result", "ok": True, "replay": True, "integrityVerified": True,
            "captureQualityOk": state["ok"], "capture": state, "directory": str(directory),
            "verifiedArtifacts": checked, "referenceVerifiedSignals": 0}


def stdio_loop():
    request = json.loads(sys.stdin.readline())
    allowed = {"action", "deviceId", "seconds", "confirmedReadOnly", "x431Inactive"}
    if (not isinstance(request, dict) or set(request) - allowed or request.get("action") != "start"
            or request.get("confirmedReadOnly") is not True or request.get("x431Inactive") is not True
            or type(request.get("seconds", 20)) not in (int, float)):
        raise ValueError("capture-request-invalid")
    cancelled = threading.Event()
    def watch():
        for line in sys.stdin:
            try:
                if json.loads(line) == {"action": "cancel"}:
                    cancelled.set()
            except (ValueError, TypeError):
                cancelled.set()
        cancelled.set()  # owner disappeared
    threading.Thread(target=watch, daemon=True).start()
    def emit(row):
        print(json.dumps(row, ensure_ascii=True), flush=True)
    directory = DEFAULT_OUTPUT / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    state = capture(request["deviceId"], directory, duration_s=request.get("seconds", 20),
                    filter_profile="981-candidates", cancel_event=cancelled, progress_sink=emit)
    emit({"type": "result", "ok": state["ok"], "capture": state, "directory": str(directory)})
    return 0 if state["ok"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="enumerate Bluetooth metadata without opening a port")
    parser.add_argument("--stdio", action="store_true", help="bounded desktop NDJSON capture with cancellation")
    parser.add_argument("--replay-directory", type=Path, help="verify and replay a local capture, no hardware")
    parser.add_argument("--device-id", help="explicit Bluetooth identity bt:<12 hex MAC digits>")
    parser.add_argument("--seconds", type=float, default=20, help="capture duration, >0 and <=60 (default 20)")
    parser.add_argument("--filter-profile", choices=("all", "981-candidates"), default="all",
                        help="all frames or only existing 981 REF candidate IDs (reduces Bluetooth output load)")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.stdio:
        return stdio_loop()
    if args.replay_directory:
        print(json.dumps(replay(args.replay_directory), ensure_ascii=True))
        return 0
    if args.list:
        print(json.dumps(enumerate_devices(), ensure_ascii=False, indent=2))
        return 0
    if not args.device_id:
        parser.error("--device-id is required for capture")
    directory = args.output_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    state = capture(args.device_id, directory, duration_s=args.seconds, filter_profile=args.filter_profile)
    print(json.dumps({"directory": str(directory), **state}, ensure_ascii=False, indent=2))
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
