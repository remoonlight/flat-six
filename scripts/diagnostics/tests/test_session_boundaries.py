"""Regressions discovered by coordinator acceptance; no real serial access."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from scripts.diagnostics.session_simulator import SessionSimPort
from scripts.diagnostics.sessions import run_session


class TestSessionBoundaries(unittest.TestCase):
    def test_cancel_in_same_pipe_write_is_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = {"action": "run", "profileId": "porsche-981-2014-dme",
                       "mode": "simulation", "scenario": "slow"}
            result = subprocess.run(
                [sys.executable, "-m", "scripts.diagnostics.sessions", "--stdio",
                 "--artifact-root", tmp],
                input=json.dumps(request) + '\n{"action":"cancel"}\n',
                capture_output=True, text=True, encoding="utf-8", timeout=5,
                cwd=Path(__file__).resolve().parents[3],
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            final = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(final["status"], "cancelled")
            self.assertFalse(final["ok"])
            self.assertFalse(final["liveVerified"])

    def test_cancel_before_identity_can_resume_without_reusing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cancel = threading.Event()
            first_port = SessionSimPort("porsche-981-2014-dme")

            def stop_after_session(event):
                if event.get("stage") == "session":
                    cancel.set()

            first = run_session(profile_id="porsche-981-2014-dme", port=first_port,
                                cancel_event=cancel, progress_sink=stop_after_session,
                                artifact_root=root)
            self.assertEqual(first["status"], "cancelled")
            self.assertEqual(first_port.ecu_payloads, ["1089"])
            parent = root / first["runId"]
            hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in parent.iterdir() if p.is_file()}
            next_port = SessionSimPort("porsche-981-2014-dme")
            resumed = run_session(profile_id="porsche-981-2014-dme", port=next_port,
                                  resume_run_id=first["runId"], artifact_root=root)
            self.assertTrue(resumed["ok"], resumed)
            self.assertNotEqual(resumed["runId"], first["runId"])
            self.assertEqual(resumed["resumeFromRunId"], first["runId"])
            self.assertIn("1A9F", next_port.ecu_payloads)
            self.assertIn("1800FF00", next_port.ecu_payloads)
            self.assertFalse(any(r.get("skippedResume") for r in resumed["results"]))
            self.assertEqual(hashes, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in parent.iterdir() if p.is_file()})
