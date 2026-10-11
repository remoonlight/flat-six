"""Full session-PDU forwarding; deterministic injected ports, no device IO."""
import tempfile
import unittest
from pathlib import Path

from scripts.diagnostics.elm import parse_ath1_response
from scripts.diagnostics.session_simulator import SessionSimPort, catalog_success_map
from scripts.diagnostics.sessions import run_session


class SessionPduCaptureTests(unittest.TestCase):
    def run_capture(self, profile, **kwargs):
        events = []
        with tempfile.TemporaryDirectory() as scratch:
            result = run_session(profile_id=profile, mode="simulation", artifact_root=Path(scratch),
                retain_artifacts=False, progress_sink=events.append, **kwargs)
            self.assertEqual(list(Path(scratch).iterdir()), [])
        pdus = [event for event in events if event["type"] == "diagnostic-pdu"]
        for event in pdus:
            self.assertEqual(event["runId"], result["runId"])
            self.assertEqual(event["profileId"], profile)
            self.assertIs(event["simulation"], True)
            pdu = event["pdu"]
            self.assertEqual(pdu["transportKind"], "elm-iso-tp")
            self.assertEqual(pdu["timestampSource"], "host-pdu-observed")
            self.assertLessEqual(pdu["startedTimestampUs"], pdu["timestampUs"])
        return result, pdus

    def test_dme_gateway_complete_bytes_and_existing_requests_only(self):
        for profile, rx, tx in [("porsche-981-2014-dme", 0x7e8, 0x7e0),
                                 ("porsche-981-2014-gateway", 0x77a, 0x710)]:
            with self.subTest(profile=profile):
                port = SessionSimPort(profile)
                result, events = self.run_capture(profile, port=port)
                self.assertTrue(result["ok"], result)
                self.assertEqual([e["pdu"]["requestHex"] for e in events], port.ecu_payloads)
                fixtures = catalog_success_map(profile)
                expected = [parse_ath1_response(fixtures[req].decode("ascii"), req_hex=req,
                    rx_id=rx, tx_id=tx, sent_sf="")["payload_hex"] for req in port.ecu_payloads]
                self.assertEqual([e["pdu"]["pduHex"] for e in events], expected)
                self.assertEqual(port.clear_tx_count, 0)
                self.assertTrue(port.closed)

    def test_identity_mismatch_keeps_full_identity_without_dtc(self):
        profile = "porsche-981-2014-dme"
        result, events = self.run_capture(profile, scenario="identity-mismatch")
        self.assertFalse(result["ok"])
        self.assertGreater(len(events), 2)
        self.assertNotIn("1800FF00", [e["pdu"]["requestHex"] for e in events])

    def test_negative_and_missing_prompt_keep_received_dtc(self):
        profile = "porsche-981-2014-dme"
        negative = SessionSimPort(profile)
        negative.ecu_map["1800FF00"] = b"7E8 03 7F 18 31\r>"
        result, events = self.run_capture(profile, port=negative)
        self.assertFalse(result["ok"])
        self.assertEqual(events[-1]["pdu"]["pduHex"], "7F1831")
        class NoPrompt(SessionSimPort):
            def __init__(self):
                super().__init__(profile)
                self.ecu_map["1800FF00"] = self.ecu_map["1800FF00"].replace(b">", b"")
        port = NoPrompt()
        result, events = self.run_capture(profile, port=port, budget_s=0.3)
        self.assertFalse(result["ok"])
        self.assertEqual(events[-1]["pdu"]["requestHex"], "1800FF00")
        self.assertTrue(events[-1]["pdu"]["pduHex"].startswith("58"))
        self.assertEqual(port.ecu_payloads.count("1800FF00"), 1)

    def test_standard_engine_full_responses_preserved(self):
        result, events = self.run_capture("porsche-981-2014-dme", session_task="engine",
            sample_cycles=1, selected_pids=["0C", "0D"])
        self.assertTrue(result["ok"], result)
        requests = [e["pdu"]["requestHex"] for e in events]
        self.assertEqual(requests[-3:], ["0100", "010C", "010D"])
        self.assertTrue(all(e["pdu"]["pduHex"].startswith("41") for e in events[-3:]))


if __name__ == "__main__":
    unittest.main()
