"""Acceptance-only fake BytePort through the production passive stream parser.

No serial/native device is opened. Output uses the adapter event contract; the
acceptance report and capture description explicitly label the injected source.
"""
import json
import os
from pathlib import Path
import sys
import time
from unittest import mock

from scripts.diagnostics.can_monitor import ALLOWED
from scripts.diagnostics.internal_stream import run_internal_monitor


class FixturePort:
    def __init__(self, directory):
        self.directory = directory
        self.pending = []
        self.monitoring = False
        self.protocol = "6"
        self.frames = 0
        self.writes = []
        self.closed = False
        self.next_control = 0

    def write(self, data):
        command = data.decode("ascii").strip()
        if data != b"\r" and command not in ALLOWED:
            raise AssertionError("fixture forbids vehicle request")
        self.writes.append(command)
        if data == b"\r":
            self.monitoring = False
            self.pending.append(b"STOPPED\r>")
        elif command == "STM":
            self.monitoring = True
        else:
            if command == "STP 31":
                self.protocol = "31"
            response = {"ATI": "ELM327 v1.4b", "STI": "STN2256 v5.11.4",
                        "STDI": "OBDLink MX+ r3.2.1", "STPR": self.protocol,
                        "STPBRR": "500000", "ATRV": "12.6V"}.get(command, "OK")
            self.pending.append((command + "\r" + response + "\r>").encode("ascii"))
        return len(data)

    def read(self, amount):
        if self.pending:
            return self.pending.pop(0)
        time.sleep(0.025)
        if not self.monitoring:
            return b""
        if time.monotonic() >= self.next_control:
            self.next_control = time.monotonic() + 0.25
            control = self.directory / "fixture-control.json"
            if control.exists():
                mode = json.loads(control.read_text(encoding="utf-8"))["mode"]
                control.unlink()
                if mode == "disconnect":
                    raise OSError("synthetic cable disconnect")
                if mode == "stall":
                    time.sleep(8)  # Delayed worker, not actual OS sleep/resume.
        result = []
        for _ in range(50):
            index = self.frames
            payload = " ".join(f"{byte:02X}" for byte in index.to_bytes(8, "big"))
            result.append(f"{index % 512:03X} {payload}\r")
            self.frames += 1
        return "".join(result).encode("ascii")

    def close(self):
        self.closed = True
        (self.directory / f"worker-{os.getpid()}.json").write_text(json.dumps({
            "synthetic": True, "serialOpens": 0, "ecuRequestsSent": 0,
            "closed": self.closed, "framesGenerated": self.frames,
            "adapterLocalCommands": self.writes,
        }), encoding="utf-8")


def main():
    directory = Path(sys.argv[1]).resolve()
    if ".local" not in directory.parts or not directory.is_dir():
        raise SystemExit("private scratch required")
    request = json.loads(sys.stdin.readline())
    if (request.get("action") != "monitor" or request.get("purpose") != "internal"
            or request.get("deviceId") != "bt:AABBCCDDEEFF" or request.get("canNetwork") != "drive"):
        raise SystemExit("fixture-request-not-permitted")
    port = FixturePort(directory)
    # Guards are relaxed only inside this child with an explicit fake port and
    # independent assertions preventing fallback to any physical transport.
    with mock.patch.dict(os.environ, {"PORSCHE981_HEADLESS": "0", "PORSCHE981_SESSION_DENY_LIVE": "0"}), \
            mock.patch("serial.Serial", side_effect=AssertionError("physical serial forbidden")), \
            mock.patch("scripts.diagnostics.internal_stream.open_selected_port", side_effect=AssertionError("physical port forbidden")):
        return run_internal_monitor(request["deviceId"], "drive", stdout=sys.stdout, stdin=sys.stdin, port=port)


if __name__ == "__main__":
    raise SystemExit(main())
