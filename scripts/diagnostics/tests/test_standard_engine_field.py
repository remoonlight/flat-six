from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics import VLIKER_MAC
from scripts.diagnostics.engine_obd import ENGINE_PROFILE
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.standard_engine_field import TASK, baseline, confirmation, plan, run


class TestStandardEngineField(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        result = run_session(profile_id=ENGINE_PROFILE, artifact_root=self.root, mode="simulation")
        self.assertTrue(result["ok"])
        self.manifest = Path(result["artifactDir"]) / "manifest.json"

    def execute(self, port=None, **kwargs):
        port = port or SessionSimPort(ENGINE_PROFILE)
        result = run(identity_manifest=self.manifest, artifact_root=self.root, port=port,
                     cycles=1, interval_ms=500, **kwargs)
        self.assertTrue(port.closed)
        self.assertFalse(list(self.root.glob(".exclusive-*")))
        return result, port

    def test_standard_route_never_starts_vendor_session(self):
        result, port = self.execute()
        self.assertTrue(result["ok"], result)
        self.assertEqual(port.ecu_payloads, ["0100", "0104", "0105", "010C", "010D", "010F", "0111"])
        commands = [data.decode("ascii").strip() for data in port.writes]
        self.assertIn("ATSH 7DF", commands)
        self.assertIn("ATCRA 7E8", commands)
        self.assertIn("ATCFC0", commands)
        self.assertNotIn("ATCFC1", commands)
        self.assertEqual(len(result["samples"]), 6)
        self.assertTrue(all(s["synthetic"] and s["responderId"] == "7E8" for s in result["samples"]))
        self.assertFalse(result["standardEngineReadObserved"])
        self.assertFalse(result["identityEvidence"]["vinVerified"])
        saved = json.loads((Path(result["artifactDir"]) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["samples"], result["samples"])

    def test_real_rejection_shape_saved_once_without_retry(self):
        port = SessionSimPort(ENGINE_PROFILE)
        port.ecu_map["0100"] = b"7E8 03 7F 01 11 AA AA AA AA\r\r>"
        result, port = self.execute(port)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "negative-response")
        self.assertEqual(result["results"][0]["payload_hex"], "7F0111")
        self.assertEqual(port.ecu_payloads, ["0100"])
        self.assertEqual(result["samples"], [])
        self.assertIsNone(result["supportedPids"])

    def test_support_bitmap_skips_other_pids(self):
        result, port = self.execute(SessionSimPort(ENGINE_PROFILE, engine_mask=bytes.fromhex("10000000")))
        self.assertTrue(result["ok"], result)
        self.assertEqual(port.ecu_payloads, ["0100", "0104"])
        self.assertEqual(result["unsupportedPids"], ["05", "0C", "0D", "0F", "11"])

    def test_wrong_responder_cannot_supply_support(self):
        port = SessionSimPort(ENGINE_PROFILE)
        port.ecu_map["0100"] = ath1_prompt(isotp_ath1_lines(0x7E9, bytes.fromhex("4100181A8000")))
        result, port = self.execute(port)
        self.assertFalse(result["ok"])
        self.assertEqual(port.ecu_payloads, ["0100"])
        self.assertEqual(result["samples"], [])

    def test_malformed_support_and_wrong_pid_stop(self):
        for request, payload, expected in (
            ("0100", "4100181A80", ["0100"]),
            ("0104", "410564", ["0100", "0104"]),
        ):
            with self.subTest(request=request):
                port = SessionSimPort(ENGINE_PROFILE)
                port.ecu_map[request] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex(payload)))
                result, port = self.execute(port)
                self.assertFalse(result["ok"])
                self.assertEqual(port.ecu_payloads, expected)

    def test_live_rejects_synthetic_identity_before_open(self):
        with patch("scripts.diagnostics.standard_engine_field._open_live_port") as opened:
            result = run(identity_manifest=self.manifest, artifact_root=self.root, mode="live",
                         yes_standard_engine_live=True)
        opened.assert_not_called()
        self.assertEqual(result["error"], "synthetic-identity-not-live-evidence")

    def test_missing_confirmation_never_opens_live_port(self):
        evidence = baseline(self.manifest, False)
        with patch("scripts.diagnostics.standard_engine_field.baseline", return_value=evidence), \
                patch("scripts.diagnostics.standard_engine_field._open_live_port") as opened:
            result = run(identity_manifest=self.manifest, artifact_root=self.root, mode="live")
        opened.assert_not_called()
        self.assertEqual(result["error"], "standard-engine-confirmation-required")

    def test_confirmation_binds_exact_evidence_and_standard_scope(self):
        evidence = baseline(self.manifest, False)
        conf = dict(sessionTask=TASK, adapterMac=VLIKER_MAC,
                    baselineManifestSha256=evidence["manifestSha256"], userStatement="test only",
                    confirmedUtc=datetime.now(timezone.utc).isoformat(), x431InactiveConfirmed=True,
                    sameVehicleConfirmed=True, stationaryConfirmed=True, standardAddressingConfirmed=True)
        path = self.root / "confirmation.json"
        path.write_text(json.dumps(conf), encoding="utf-8")
        self.assertEqual(confirmation(path, evidence), conf)
        for field, value in (("standardAddressingConfirmed", False), ("baselineManifestSha256", "different"),
                             ("sessionTask", "engine"), ("confirmedUtc", "2000-01-01T00:00:00+00:00")):
            with self.subTest(field=field):
                changed = dict(conf, **{field: value})
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    confirmation(path, evidence)

    def test_invalid_baseline_and_options_never_transmit(self):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["results"][1]["decoded"]["text"] = "other"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        result, port = self.execute()
        self.assertEqual(result["error"], "baseline-identity-or-cleanup-invalid")
        self.assertEqual(port.writes, [])
        for cycles, interval in ((0, 1000), (11, 1000), (1, 499), (1, 5001), (True, 1000)):
            with self.assertRaises(ValueError):
                plan(cycles, interval)


if __name__ == "__main__":
    unittest.main()
