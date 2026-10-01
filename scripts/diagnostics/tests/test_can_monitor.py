from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from scripts.diagnostics.can_monitor import ALLOWED, CANDIDATE_FILTERS, SETUP, capture, parse_frame, replay
from scripts.diagnostics.tests.test_connection import MX, MX_BT, MX_SERIAL, VLIKER, _snap


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


class Port:
    def __init__(self, clock, *, stream=None, firmware="STN2256 v5.11.4", reject=None, stop_prompt=True):
        self.clock = clock
        self.stream = stream if stream is not None else [b"105 00 00 10", b" 27 00 00 00 00\r\n103 00 00 00 00 00 00 00 00\r"]
        self.firmware, self.reject, self.stop_prompt = firmware, reject, stop_prompt
        self.pending = []
        self.writes = []
        self.closed = False
        self.protocol = "6"

    def write(self, data):
        self.writes.append(data)
        cmd = data.decode("ascii").strip()
        if data == b"\r":
            self.pending.extend([b"STOPPED\r>"] if self.stop_prompt else [])
        elif cmd == "STM":
            self.pending.extend(self.stream)
        else:
            if cmd == "STP 31":
                self.protocol = "31"
            response = {"ATI": "ELM327 v1.4b", "STI": self.firmware, "STDI": "OBDLink MX+ r3.2.1",
                "STPR": self.protocol, "STPBRR": "500000", "ATRV": "12.6V"}.get(cmd, "OK")
            if self.reject == cmd:
                response = "OK\r?"
            self.pending.append((cmd + "\r" + response + "\r>").encode("ascii"))
        return len(data)

    def read(self, amount):
        self.clock.value += 0.025
        return self.pending.pop(0) if self.pending else b""

    def close(self):
        self.closed = True


class TestCanMonitor(unittest.TestCase):
    def test_global_setup_deadline_stops_before_monitor_and_closes_port(self):
        clock = Clock()
        class SlowSetupPort(Port):
            def read(self, amount):
                clock.value += 2
                return super().read(amount)
        port = SlowSetupPort(clock)
        with tempfile.TemporaryDirectory() as tmp:
            state = capture(MX, Path(tmp) / "run", port=port, snapshot=_snap(serial=[MX_SERIAL], bluetooth=[MX_BT]),
                            duration_s=1, clock=clock)
            self.assertEqual(state["error"], "capture-deadline")
            self.assertNotIn(b"STM\r", port.writes)
            self.assertTrue(port.closed)

    def test_cancel_during_discovery_prevents_port_open(self):
        cancelled = threading.Event()
        def discover():
            cancelled.set()
            return _snap(serial=[MX_SERIAL], bluetooth=[MX_BT])
        with tempfile.TemporaryDirectory() as tmp, mock.patch("scripts.diagnostics.can_monitor.collect_snapshot", side_effect=discover), mock.patch("scripts.diagnostics.can_monitor.open_selected_port") as opened:
            state = capture(MX, Path(tmp) / "run", cancel_event=cancelled)
            self.assertEqual(state["state"], "cancelled")
            self.assertFalse(state["opened_port"])
            opened.assert_not_called()

    def test_replay_rebuilds_raw_and_detects_mutation_without_opening_hardware(self):
        state, _, directory = self.run_capture()
        with mock.patch("scripts.diagnostics.can_monitor.open_selected_port") as opened:
            result = replay(directory)
            self.assertTrue(result["integrityVerified"])
            self.assertEqual(result["capture"]["frame_count"], 2)
            opened.assert_not_called()
            with (directory / "frames.jsonl").open("a") as fh:
                fh.write("{}\n")
            with self.assertRaisesRegex(ValueError, "hash-mismatch"):
                replay(directory)

    def test_cancel_stops_once_and_preserves_partial_frames(self):
        clock, cancelled = Clock(), threading.Event()
        class CancellingPort(Port):
            def read(self, amount):
                data = super().read(amount)
                if b"103 00" in data:
                    cancelled.set()
                return data
        port = CancellingPort(clock)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "run"
            out = capture(MX, directory, port=port, snapshot=_snap(serial=[MX_SERIAL], bluetooth=[MX_BT]),
                          clock=clock, cancel_event=cancelled)
            self.assertEqual(out["state"], "cancelled")
            self.assertEqual(out["frame_count"], 2)
            self.assertEqual(port.writes.count(b"\r"), 1)
            self.assertEqual(port.writes.count(b"STM\r"), 1)
            self.assertTrue(port.closed)
            self.assertTrue(replay(directory)["integrityVerified"])

    def run_capture(self, *, stream=None, firmware="STN2256 v5.11.4", reject=None, stop_prompt=True, duration=.12, filter_profile="all"):
        clock = Clock()
        port = Port(clock, stream=stream, firmware=firmware, reject=reject, stop_prompt=stop_prompt)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "session"
        state = capture(MX, directory, port=port, snapshot=_snap(serial=[MX_SERIAL], bluetooth=[MX_BT]),
                        duration_s=duration, clock=clock, filter_profile=filter_profile)
        return state, port, directory

    def test_strict_frame_parser_preserves_ide_and_rejects_truncated_and_isotp_text(self):
        self.assertEqual(parse_frame(b"105 00 10"), (0x105, False, bytes([0, 16])))
        self.assertEqual(parse_frame(b"00000105 00"), (0x105, True, b"\x00"))
        for line in (b"105", b"800 00", b"20000000 00", b"105 0", b"105 00 <DATA ERROR",
                     b"105 8 00 00", b"0: 00 00", b"105 " + b"00 " * 9, b"10500", b"STOPPED"):
            self.assertIsNone(parse_frame(line), line)

    def test_fragmented_receive_exact_raw_payload_hash_and_whitelisted_serial_writes(self):
        state, port, directory = self.run_capture()
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["frame_count"], 2)
        self.assertTrue(state["simulation"])
        self.assertFalse(state["physical_silence_verified"])
        self.assertFalse(state["runtime_settings_restored"])
        self.assertEqual(state["candidate_id_hits"]["0x105"], 1)
        frames = [json.loads(line) for line in (directory / "frames.jsonl").read_text().splitlines()]
        self.assertEqual(frames[0]["data_hex"], "0000102700000000")
        self.assertEqual(frames[0]["dlc"], 8)
        for artifact in state["artifacts"]:
            raw = (directory / artifact["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
        log = [json.loads(line) for line in (directory / "serial.jsonl").read_text().splitlines()]
        rx = b"".join(bytes.fromhex(row["hex"]) for row in log if row["event"] == "serial_rx")
        self.assertEqual(rx, (directory / "serial-rx.bin").read_bytes())
        commands = [data.decode().strip() for data in port.writes if data != b"\r"]
        self.assertTrue(set(commands) <= ALLOWED)
        self.assertLess(commands.index("STCMM 0"), commands.index("STM"))
        self.assertEqual(port.writes.count(b"\r"), 1)
        self.assertEqual(port.writes[-1], b"STPC\r")
        self.assertTrue(port.closed)

    def test_other_stn_hardware_never_enters_setup_or_monitor(self):
        state, port, _ = self.run_capture(firmware="STN2100 v5.11.4")
        self.assertIn("hardware-identity-mismatch", state["error"])
        self.assertEqual(port.writes, [b"ATI\r", b"STI\r", b"STDI\r"])
        self.assertTrue(port.closed)

    def test_ok_plus_error_cannot_bypass_silent_mode_requirement(self):
        state, port, _ = self.run_capture(reject="STCMM 0")
        self.assertIn("command-rejected", state["error"])
        self.assertNotIn(b"STM\r", port.writes)
        self.assertTrue(port.closed)

    def test_no_frames_does_not_claim_reception_succeeded(self):
        state, _, _ = self.run_capture(stream=[])
        self.assertFalse(state["ok"])
        self.assertEqual(state["state"], "no-frames")
        self.assertEqual(state["frame_count"], 0)

    def test_buffer_full_is_recorded_and_not_restarted(self):
        state, port, _ = self.run_capture(stream=[b"105 00\rBUFFER FULL\r>"])
        self.assertFalse(state["ok"])
        self.assertIn("before-duration", state["error"])
        self.assertEqual(state["adapter_notices"]["BUFFER FULL"], 1)
        self.assertEqual(state["frame_count"], 1)
        self.assertNotIn(b"\r", port.writes)
        self.assertEqual(port.writes.count(b"STM\r"), 1)

    def test_missing_stop_prompt_never_replays_monitor_or_sends_further_commands(self):
        state, port, _ = self.run_capture(stop_prompt=False)
        self.assertFalse(state["ok"])
        self.assertIn("prompt-timeout", state["cleanup_error"])
        self.assertEqual(port.writes[-1], b"\r")
        self.assertEqual(port.writes.count(b"\r"), 1)
        self.assertTrue(port.closed)

    def test_byte_and_line_limits_abort_and_close(self):
        for stream, limit, expected in (([b"x" * 300], "MAX_LINE_BYTES", "line-limit"),
                                         ([b"x" * 300], "MAX_RX_BYTES", "byte-limit")):
            with self.subTest(limit=limit), mock.patch("scripts.diagnostics.can_monitor." + limit, 260):
                state, port, _ = self.run_capture(stream=stream)
                self.assertIn(expected, state["error"])
                self.assertTrue(port.closed)

    def test_candidate_hits_require_standard_ide_and_dlc8(self):
        state, _, _ = self.run_capture(stream=[b"00000105 00 00 00 00 00 00 00 00\r105 00\r"])
        self.assertEqual(state["frame_count"], 2)
        self.assertEqual(state["candidate_id_hits"]["0x105"], 0)
        self.assertEqual(len(state["partitions"]), 2)

    def test_candidate_profile_removes_pass_all_and_requires_every_fixed_filter(self):
        state, port, _ = self.run_capture(filter_profile="981-candidates")
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["filter_profile"], "981-candidates")
        self.assertNotIn(b"STFPA 0000,0000\r", port.writes)
        for command in CANDIDATE_FILTERS:
            self.assertIn((command + "\r").encode(), port.writes)
        self.assertLess(port.writes.index(b"STFAC\r"), port.writes.index((CANDIDATE_FILTERS[0] + "\r").encode()))
        state, port, _ = self.run_capture(filter_profile="981-candidates", reject=CANDIDATE_FILTERS[0])
        self.assertFalse(state["ok"])
        self.assertNotIn(b"STM\r", port.writes)

    def test_explicit_vlinker_identity_not_opened(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("scripts.diagnostics.can_monitor.open_selected_port") as opened:
            state = capture(VLIKER, Path(tmp) / "session", snapshot=_snap())
            self.assertFalse(state["ok"])
            self.assertFalse(state["opened_port"])
            opened.assert_not_called()

    def test_validation_and_live_deny_happen_before_opening_or_creating_session(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("scripts.diagnostics.can_monitor.open_selected_port") as opened:
            directory = Path(tmp) / "session"
            for device, duration in (("COM12", 1), (MX, 61), (MX, float("nan")), (MX, 0)):
                with self.assertRaises(ValueError):
                    capture(device, directory, duration_s=duration)
            with mock.patch.dict(os.environ, {"PORSCHE981_SESSION_DENY_LIVE": "1"}):
                with self.assertRaisesRegex(RuntimeError, "live-capture-disabled"):
                    capture(MX, directory)
            self.assertFalse(directory.exists())
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
