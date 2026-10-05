import io
import json
import threading
import unittest
from unittest import mock

from scripts.diagnostics.internal_stream import MemoryReceiver, run_internal_monitor
from scripts.diagnostics.tests.test_can_monitor import Clock, Port
from scripts.diagnostics.tests.test_connection import MX
from scripts.diagnostics.can_monitor import ALLOWED


class TestInternalStream(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
