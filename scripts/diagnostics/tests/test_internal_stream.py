import io
import json
import queue
import threading
import unittest
from unittest import mock

from scripts.diagnostics.internal_stream import MemoryReceiver, run_internal_monitor
from scripts.diagnostics.tests.test_can_monitor import Clock, Port
from scripts.diagnostics.tests.test_connection import MX
from scripts.diagnostics.can_monitor import ALLOWED


class TestInternalStream(unittest.TestCase):
    def test_release_report_preserves_operation_and_all_cleanup_failures(self):
        for stage in ('close', 'identity-and-close', 'stop-and-close', 'cancel-before-start'):
            with self.subTest(stage=stage):
                clock, output = Clock(), io.StringIO()
                stopped = threading.Event()
                if stage == 'cancel-before-start': stopped.set()
                class FailedClose(Port):
                    def close(inner):
                        self.assertNotIn('"type": "stopped"', output.getvalue())
                        raise OSError('SYNTHETIC serial close failed')
                options = {'firmware': 'STN2100 v5.6.5'} if stage == 'identity-and-close' else {}
                if stage == 'stop-and-close': options['stop_prompt'] = False
                status = run_internal_monitor(MX, 'drive', stdout=output, port=FailedClose(clock, **options),
                    clock=clock, stop_event=stopped, max_reads=1)
                rows = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(status, 1)
                self.assertFalse(any(row['type'] == 'stopped' for row in rows))
                self.assertEqual(rows[-1]['error'], 'internal-cleanup-failed')
                self.assertEqual(len(rows[-1]['restoration']['errors']), 2 if stage == 'stop-and-close' else 1)
                if stage == 'identity-and-close':
                    self.assertTrue(any(row.get('error') == 'mxplus-hardware-identity-mismatch' for row in rows))

    def test_continuous_receive_uses_only_local_silent_setup_without_files(self):
        clock, output = Clock(), io.StringIO()
        port = Port(clock)
        with mock.patch("pathlib.Path.open", side_effect=AssertionError("unexpected file")):
            status = run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock, max_reads=100)
        self.assertEqual(status, 0, output.getvalue())
        docs = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(sum(len(doc.get("frames", [])) for doc in docs), 2)
        self.assertEqual(docs[-1]["frameCount"], 2)
        self.assertTrue(port.closed)
        self.assertIn(b"STCMM 0\r", port.writes)
        self.assertEqual(port.writes.count(b"STM\r"), 1)
        self.assertEqual(port.writes.count(b"\r"), 1)
        self.assertTrue(all(write == b"\r" or write.decode().strip() in ALLOWED for write in port.writes))
        self.assertTrue(all(doc.get("physicalSilenceVerified") is False and doc.get("ecuRequestsSent") == 0 for doc in docs))
        self.assertTrue(all(type(doc["at"]) is int for doc in docs if doc.get("type") in ("handshake", "reading")),
                        "millisecond stamps cannot lie fractionally ahead of JS Date.now in the same millisecond")

    def test_missing_adas_definition_never_opens_or_sends(self):
        output = io.StringIO()
        with mock.patch("scripts.diagnostics.internal_stream.open_selected_port") as opened:
            self.assertEqual(run_internal_monitor(MX, "adas", stdout=output), 2)
        opened.assert_not_called()
        self.assertIn("internal-profile-not-qualified", output.getvalue())

    def test_headless_guard_prevents_real_transport(self):
        with mock.patch.dict("os.environ", {"PORSCHE981_HEADLESS": "1"}), mock.patch("scripts.diagnostics.internal_stream.open_selected_port") as opened:
            self.assertEqual(run_internal_monitor(MX, "drive", stdout=io.StringIO()), 2)
        opened.assert_not_called()

    def test_wrong_hardware_never_starts_monitor(self):
        clock = Clock()
        port = Port(clock, firmware="STN2100 v5.6.5")
        output = io.StringIO()
        self.assertEqual(run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock), 1)
        self.assertNotIn(b"STM\r", port.writes)
        self.assertTrue(port.closed)

    def test_fragments_and_extended_ids_are_preserved_not_guessed(self):
        rows = []
        receiver = MemoryReceiver(rows.append, MX, "drive", clock=lambda: 1700000000123456789)
        receiver.enabled = True
        self.assertFalse(receiver.feed(b"123 01 02\r18DAF1"))
        self.assertFalse(receiver.feed(b"01 AB CD EF\rgarbage\r"))
        self.assertEqual(rows[0]["frames"][0]["dataHex"], "0102")
        self.assertEqual(rows[1]["frames"][0]["canId"], 0x18DAF101)
        self.assertTrue(rows[1]["frames"][0]["extended"])
        self.assertEqual(receiver.invalid, 1)

    def test_cancel_closes_port_and_returns_no_invented_frames(self):
        clock, stopped = Clock(), threading.Event()
        class CancelledPort(Port):
            def read(inner, amount):
                value = super().read(amount)
                if value.startswith(b"105 "):
                    stopped.set()
                return value
        port = CancelledPort(clock, stream=[b"105 01 02\r"])
        output = io.StringIO()
        self.assertEqual(run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock, stop_event=stopped), 0)
        self.assertTrue(port.closed)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["frameCount"], 1)


    def test_deadline_stops_monitor_but_keeps_port_for_local_standby(self):
        clock, output = Clock(), io.StringIO()
        idle = []
        class DeadlinePort(Port):
            def read(inner, amount):
                data = super().read(amount)
                if data.startswith(b"106 "):
                    clock.value = 600
                return data
        port = DeadlinePort(clock, stream=[b"105 01 02\r", b"106 03 04\r"])
        def wait(_seconds):
            self.assertFalse(port.closed)
            self.assertEqual(port.writes.count(b"STM\r"), 1)
            idle.append(True)
            clock.value += 1.1
        status = run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock,
                                      batch_id="first", max_reads=5, wait_fn=wait)
        self.assertEqual(status, 0, output.getvalue())
        docs = [json.loads(x) for x in output.getvalue().splitlines()]
        frames = [f for d in docs for f in d.get("frames", [])]
        self.assertEqual([f["canId"] for f in frames], [0x105])
        self.assertTrue(idle)
        self.assertTrue(any(d.get("receiveState") == "standby" and d.get("receiveReason") == "time-limit" for d in docs))
        self.assertGreaterEqual(port.writes.count(b"ATRV\r"), 2)
        self.assertEqual(port.writes.count(b"STM\r"), 1)
        self.assertTrue(all(w == b"\r" or w.decode().strip() in ALLOWED for w in port.writes))
        self.assertTrue(port.closed)

    def test_zero_budget_reconnect_never_starts_receiving(self):
        clock, output = Clock(), io.StringIO()
        port = Port(clock)
        status = run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock,
                                      receive_ms=0, batch_id="expired", max_reads=2, wait_fn=lambda _: None)
        self.assertEqual(status, 0)
        self.assertNotIn(b"STM\r", port.writes)
        self.assertIn('"receiveState": "standby"', output.getvalue())
        self.assertNotIn('"type": "frames"', output.getvalue())

    def test_setup_time_counts_and_manual_next_batch_reuses_port(self):
        clock, output, commands = Clock(), io.StringIO(), queue.Queue()
        port = Port(clock, stream=[b"105 01 02\r"])
        issued = []
        def wait(_seconds):
            self.assertFalse(port.closed)
            if not issued:
                commands.put({"action": "receive-start", "receiveBatchId": "second", "receiveMilliseconds": 600000})
                commands.put({"action": "receive-start", "receiveBatchId": "second", "receiveMilliseconds": 600000})
                commands.put({"action": "receive-stop", "receiveBatchId": "first"})
                issued.append(True)
        status = run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock, receive_ms=1,
                                      batch_id="first", max_reads=5, commands=commands, wait_fn=wait)
        self.assertEqual(status, 0, output.getvalue())
        self.assertEqual(port.writes.count(b"STM\r"), 1, "expired setup and duplicate control cannot start new monitors")
        docs = [json.loads(x) for x in output.getvalue().splitlines()]
        received = [d for d in docs if d.get("frames")]
        self.assertEqual([d["receiveBatchId"] for d in received], ["second"])
        self.assertTrue(port.closed)

    def test_expiry_cleanup_failure_closes_and_reports_error(self):
        clock, output = Clock(), io.StringIO()
        class DeadlinePort(Port):
            def read(inner, amount):
                data = super().read(amount)
                if data.startswith(b"105 "):
                    clock.value = 600
                return data
        port = DeadlinePort(clock, stream=[b"105 01 02\r"], stop_prompt=False)
        status = run_internal_monitor(MX, "drive", stdout=output, port=port, clock=clock, max_reads=3)
        self.assertEqual(status, 1)
        self.assertIn("adapter-prompt-timeout", output.getvalue())
        self.assertTrue(port.closed)
        self.assertNotIn('"receiveState": "standby"', output.getvalue())

    def test_invalid_duration_never_opens_a_port(self):
        for value in (-1, 600001, True, 1.5):
            with self.subTest(value=value), mock.patch("scripts.diagnostics.internal_stream.open_selected_port") as opened:
                self.assertEqual(run_internal_monitor(MX, "drive", stdout=io.StringIO(), receive_ms=value), 2)
                opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
