from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.diagnostics.pair import response_matches
from scripts.diagnostics.session_simulator import SessionSimPort, catalog_success_map
from scripts.diagnostics.sessions import prepare, run_session, stdio_loop


def _root():
    return Path(tempfile.mkdtemp(prefix="sess-"))


class TestSessionEcho(unittest.TestCase):
    def test_sid10_subfunction_required(self):
        self.assertEqual(response_matches(bytes.fromhex("1089"), bytes.fromhex("5089")), "positive-echo")
        self.assertEqual(response_matches(bytes.fromhex("1003"), bytes.fromhex("5003")), "positive-echo")
        self.assertEqual(response_matches(bytes.fromhex("14FF00"), bytes.fromhex("54FF00")), "positive-echo")
        self.assertEqual(response_matches(bytes.fromhex("14FFFFFF"), bytes.fromhex("54")), "positive-echo")
        self.assertIsNone(response_matches(bytes.fromhex("14FF00"), bytes.fromhex("54")))
        self.assertIsNone(response_matches(bytes.fromhex("14FF00"), bytes.fromhex("54FF01")))
        self.assertIsNone(response_matches(bytes.fromhex("14FF00"), bytes.fromhex("54FFFFFF")))
        self.assertIsNone(response_matches(bytes.fromhex("14FFFFFF"), bytes.fromhex("54FFFFFF")))
        self.assertIsNone(response_matches(bytes.fromhex("14FFFFFF"), bytes.fromhex("54FF00")))
        self.assertEqual(response_matches(bytes.fromhex("14FF00"), bytes.fromhex("7F1478")), "pending-7Fxx78")
        self.assertEqual(response_matches(bytes.fromhex("14FF00"), bytes.fromhex("7F1472")), "negative")
        self.assertIsNone(response_matches(bytes.fromhex("1089"), bytes.fromhex("5003")))
        self.assertIsNone(response_matches(bytes.fromhex("1003"), bytes.fromhex("5089")))


class TestPrepare(unittest.TestCase):
    def test_prepare_bounded_and_blocked(self):
        out = prepare("porsche-981-2014-dme")
        self.assertTrue(out["ok"], out)
        plan = out["plan"]
        self.assertEqual(plan["session"]["requestHex"], "1089")
        self.assertEqual(plan["session"]["expectedPositiveHex"], "5089")
        self.assertEqual([d["operationId"] for d in plan["dependentOperations"]], ["dme-dtc"])
        self.assertTrue(any(b.get("reason") == "offline-plan-not-executable-authority" for b in plan["blockedCandidates"]))
        self.assertEqual(plan["session"].get("requestEvidence"), "source-observed")
        self.assertNotEqual(plan["session"].get("synthetic"), True)
        self.assertFalse(plan["liveVerified"])
        self.assertEqual(plan["clearAuthorization"]["requestHex"], "14FF00")
        self.assertEqual(plan["clearAuthorization"]["expectedPositiveHex"], "54FF00")
        self.assertEqual(plan["clearAuthorization"]["protocol"], "kwp-cdi-16bit-group-echo")
        self.assertFalse(plan["clearAuthorization"]["sourceObservedResponse"])
        bad = prepare("porsche-981-2014-dme", ["dme-dsn"])
        self.assertFalse(bad["ok"])
        gw = prepare("porsche-981-2014-gateway", ["gw-dtc", "gw-vin"])
        self.assertTrue(gw["ok"])
        self.assertEqual(gw["plan"]["session"]["requestHex"], "1003")
        self.assertEqual(gw["plan"]["clearAuthorization"]["requestHex"], "14FFFFFF")
        self.assertEqual(gw["plan"]["clearAuthorization"]["expectedPositiveHex"], "54")


class TestBytePortSessions(unittest.TestCase):
    def _run(self, profile, scenario="success", **kwargs):
        root = kwargs.pop("artifact_root", _root())
        return run_session(
            profile_id=profile,
            mode="simulation",
            scenario=scenario,
            artifact_root=root,
            **kwargs,
        ), root

    def test_success_dme_and_gateway(self):
        for pid, sess in (
            ("porsche-981-2014-dme", "1089"),
            ("porsche-981-2014-gateway", "1003"),
        ):
            with self.subTest(pid=pid):
                port = SessionSimPort(pid, "success")
                out, _ = self._run(pid, port=port)
                self.assertTrue(out["ok"], out)
                self.assertEqual(out["status"], "completed")
                self.assertTrue(out["simulation"])
                self.assertFalse(out["liveVerified"])
                self.assertIsNone(out["writePayload"])
                self.assertFalse(out["restoration"]["ecuRestorationProven"])
                self.assertIn(sess, port.ecu_payloads)
                self.assertIn("1800FF00" if pid.endswith("-dme") else "190208", port.ecu_payloads)
                self.assertTrue(port.closed)
                q = out["identityQualification"]
                self.assertTrue(q["observedProfileMatch"])
                self.assertFalse(q["liveVerified"])
                sess_rec = next(r for r in out["results"] if r["role"] == "session")
                self.assertTrue(sess_rec["synthetic"])
                self.assertEqual(sess_rec.get("requestEvidence"), "source-observed")

    def test_dtc_zero_and_changed_counts_not_identity(self):
        dme = SessionSimPort("porsche-981-2014-dme", "success")
        dme.ecu_map.update({k: v for k, v in catalog_success_map("porsche-981-2014-dme", dtc_hex="5800").items() if not k.startswith("_")})
        out, _ = self._run("porsche-981-2014-dme", port=dme)
        self.assertTrue(out["ok"], out)
        dtc = next(r for r in out["results"] if r.get("operationId") == "dme-dtc")
        self.assertEqual(len(dtc["decoded"]["records"]), 0)
        dme2 = SessionSimPort("porsche-981-2014-dme", "success")
        dme2.ecu_map.update({k: v for k, v in catalog_success_map("porsche-981-2014-dme", dtc_hex="5801C44728").items() if not k.startswith("_")})
        out2, _ = self._run("porsche-981-2014-dme", port=dme2)
        self.assertTrue(out2["ok"], out2)
        dtc2 = next(r for r in out2["results"] if r.get("operationId") == "dme-dtc")
        self.assertEqual(len(dtc2["decoded"]["records"]), 1)
        self.assertNotEqual(out["identityQualification"]["status"], "mismatch")

    def test_identity_mismatch_stops_dependents(self):
        port = SessionSimPort("porsche-981-2014-dme", "identity-mismatch")
        out, _ = self._run("porsche-981-2014-dme", port=port)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "identity-mismatch")
        self.assertNotIn("1800FF00", port.ecu_payloads)
        self.assertIn("1089", port.ecu_payloads)
        self.assertIn("1A9F", port.ecu_payloads)

    def test_truncated_identity_stops_dependents(self):
        port = SessionSimPort("porsche-981-2014-dme", "success")
        from scripts.diagnostics.session_simulator import ath1_prompt, isotp_ath1_lines

        port.ecu_map["1A9F"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("5A9F50")))
        out, _ = self._run("porsche-981-2014-dme", port=port)
        self.assertFalse(out["ok"])
        self.assertNotIn("1800FF00", port.ecu_payloads)

    def test_negative_and_pending_timeout(self):
        for sc in ("negative", "pending-timeout"):
            port = SessionSimPort("porsche-981-2014-gateway", sc)
            out, _ = self._run("porsche-981-2014-gateway", scenario=sc, port=port)
            self.assertFalse(out["ok"], out)
            self.assertNotIn("190208", port.ecu_payloads)
            self.assertNotIn("22F187", port.ecu_payloads)

    def test_disconnect(self):
        port = SessionSimPort("porsche-981-2014-dme", "disconnect")
        out, _ = self._run("porsche-981-2014-dme", scenario="disconnect", port=port)
        self.assertFalse(out["ok"])
        self.assertTrue(port.closed)

    def test_cancel_during_prompt(self):
        cancel = threading.Event()
        port = SessionSimPort("porsche-981-2014-dme", "slow")
        root = _root()

        def later():
            time.sleep(0.05)
            cancel.set()

        threading.Thread(target=later, daemon=True).start()
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            scenario="slow",
            port=port,
            artifact_root=root,
            cancel_event=cancel,
            budget_s=5.0,
        )
        self.assertEqual(out["status"], "cancelled")
        self.assertFalse(out["ok"])
        self.assertTrue(port.closed)
        self.assertTrue((root / out["runId"] / "raw.json").is_file())

    def test_resume_skips_completed_dtc_redoes_identity(self):
        root = _root()
        port1 = SessionSimPort("porsche-981-2014-dme", "success")
        first = run_session(profile_id="porsche-981-2014-dme", mode="simulation", port=port1, artifact_root=root)
        self.assertTrue(first["ok"], first)
        rid = first["runId"]
        port2 = SessionSimPort("porsche-981-2014-dme", "success")
        second = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=rid,
            port=port2,
            artifact_root=root,
        )
        self.assertTrue(second["ok"], second)
        self.assertIn("1089", port2.ecu_payloads)
        self.assertIn("1A9F", port2.ecu_payloads)
        self.assertNotIn("1800FF00", port2.ecu_payloads)
        dtc = next(r for r in second["results"] if r.get("operationId") == "dme-dtc")
        self.assertTrue(dtc.get("skippedResume"))
        self.assertNotEqual(second["runId"], first["runId"])
        self.assertEqual(second["resumeFromRunId"], first["runId"])
        self.assertTrue(dtc.get("historical"))
        self.assertFalse(dtc.get("freshlyRead"))
        self.assertEqual(dtc.get("sourceRunId"), first["runId"])

    def test_resume_identity_mismatch_does_not_skip_send_anyway(self):
        root = _root()
        port1 = SessionSimPort("porsche-981-2014-dme", "success")
        first = run_session(profile_id="porsche-981-2014-dme", mode="simulation", port=port1, artifact_root=root)
        port2 = SessionSimPort("porsche-981-2014-dme", "identity-mismatch")
        second = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=port2,
            artifact_root=root,
        )
        self.assertFalse(second["ok"])
        self.assertNotIn("1800FF00", port2.ecu_payloads)

    def test_resume_mode_and_plan_tamper_no_port(self):
        root = _root()
        first = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        ck = json.loads((root / first["runId"] / "checkpoint.json").read_text(encoding="utf-8"))
        exploded = []

        class Boom:
            def write(self, data):
                exploded.append("write")
                raise AssertionError("port opened")

            def read(self, size=1):
                exploded.append("read")
                return b""

            def close(self):
                pass

        live = run_session(
            profile_id="porsche-981-2014-dme",
            mode="live",
            resume_run_id=first["runId"],
            confirmed_read_only=True,
            x431_inactive=True,
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(live["error"], "simulation-checkpoint-not-live")
        self.assertFalse(exploded)

        tampered = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            operation_ids=["dme-dtc", "dme-vin"],
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(tampered["error"], "plan-mismatch")
        self.assertFalse(exploded)

        ck["catalogSha256"] = "00" * 32
        (root / first["runId"] / "checkpoint.json").write_text(json.dumps(ck), encoding="utf-8")
        fp = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(fp["error"], "catalog-fingerprint-mismatch")
        self.assertFalse(exploded)

    def test_double_run_lock(self):
        root = _root()
        started = threading.Event()
        port = SessionSimPort("porsche-981-2014-dme", "slow")

        def one():
            started.set()
            run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                scenario="slow",
                port=port,
                artifact_root=root,
                budget_s=2.0,
            )

        t = threading.Thread(target=one)
        t.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.08)
        second = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        self.assertEqual(second["error"], "session-lock-busy")
        t.join(5)

    def test_simulation_does_not_import_serial(self):
        sys.modules.pop("serial", None)
        with mock.patch.dict(sys.modules, {"serial": None}):
            out, _ = self._run("porsche-981-2014-dme", port=SessionSimPort("porsche-981-2014-dme"))
        self.assertTrue(out["ok"], out)
        self.assertNotIn("serial", sys.modules)

    def test_live_without_flags_no_serial(self):
        sys.modules.pop("serial", None)
        with mock.patch.dict(sys.modules, {"serial": None}):
            out = run_session(
                profile_id="porsche-981-2014-dme",
                mode="live",
                artifact_root=_root(),
            )
        self.assertEqual(out["error"], "live-confirmations-required")
        self.assertNotIn("serial", sys.modules)

    def test_cli_dtc_goes_through_session(self):
        from scripts.diagnostics.live import run_read

        port = SessionSimPort("porsche-981-2014-dme", "identity-mismatch")
        out = run_read("porsche-981-2014-dme", "dme-dtc", port=port, budget_s=20, out_dir=_root())
        self.assertFalse(out["ok"])
        self.assertNotIn("1800FF00", port.ecu_payloads)

    def test_cancel_at_identity_boundary_no_dtc(self):
        cancel = threading.Event()

        def sink(ev):
            if ev.get("stage") == "identity" and ev.get("operationId") == "dme-hardware-part":
                cancel.set()

        port = SessionSimPort("porsche-981-2014-dme", "success")
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=port,
            artifact_root=_root(),
            cancel_event=cancel,
            progress_sink=sink,
        )
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(out["error"], "cancelled")
        self.assertNotIn("1800FF00", port.ecu_payloads)
        self.assertFalse(out["ok"])

    def test_expired_deadline_no_ecu_payload(self):
        port = SessionSimPort("porsche-981-2014-dme", "success")
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=port,
            artifact_root=_root(),
            budget_s=0,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "deadline-expired")
        self.assertEqual(port.ecu_payloads, [])

    def test_resume_parent_hashes_immutable(self):
        from scripts.diagnostics.hashutil import sha256_file

        root = _root()
        first = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        parent = root / first["runId"]
        names = ("checkpoint.json", "manifest.json", "raw.json")
        before = {n: sha256_file(parent / n) for n in names}
        second = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        self.assertTrue(second["ok"], second)
        after = {n: sha256_file(parent / n) for n in names}
        self.assertEqual(before, after)
        failed = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=SessionSimPort("porsche-981-2014-dme", "identity-mismatch"),
            artifact_root=root,
        )
        self.assertFalse(failed["ok"])
        self.assertEqual(before, {n: sha256_file(parent / n) for n in names})
        cancel = threading.Event()
        cancel.set()
        cancelled = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
            cancel_event=cancel,
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(before, {n: sha256_file(parent / n) for n in names})

    def test_resume_bad_dependent_rejected_before_open(self):
        root = _root()
        first = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        ck_path = root / first["runId"] / "checkpoint.json"
        ck = json.loads(ck_path.read_text(encoding="utf-8"))
        exploded = []

        class Boom:
            def write(self, data):
                exploded.append("write")
                raise AssertionError("port opened")

            def read(self, size=1):
                exploded.append("read")
                return b""

            def close(self):
                pass

        missing = dict(ck)
        missing["dependentResults"] = []
        ck_path.write_text(json.dumps(missing), encoding="utf-8")
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(out["error"], "resume-evidence-invalid")
        self.assertFalse(exploded)
        ck_path.write_text(json.dumps(ck), encoding="utf-8")
        bad = dict(ck)
        bad["dependentResults"] = [{"operationId": "dme-dtc", "ok": True, "payload_hex": "00", "decoded": {"ok": True}}]
        ck_path.write_text(json.dumps(bad), encoding="utf-8")
        out2 = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(out2["error"], "resume-evidence-invalid")
        self.assertFalse(exploded)
        none_q = dict(ck)
        none_q["identityQualification"] = None
        ck_path.write_text(json.dumps(none_q), encoding="utf-8")
        out3 = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(out3["error"], "resume-evidence-invalid")
        self.assertIsNone(out3["runId"])
        self.assertFalse(exploded)

    def test_persist_failure_releases_lock(self):
        root = _root()
        port = SessionSimPort("porsche-981-2014-dme", "success")
        with mock.patch("scripts.diagnostics.sessions._atomic_write", side_effect=OSError("disk")):
            out = run_session(profile_id="porsche-981-2014-dme", mode="simulation", port=port, artifact_root=root)
        self.assertFalse(out["ok"])
        self.assertTrue(port.closed)
        nxt = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        self.assertTrue(nxt["ok"], nxt)

    def test_keyboard_interrupt_cancelled_manifest(self):
        root = _root()

        def sink(ev):
            if ev.get("stage") == "session":
                raise KeyboardInterrupt()

        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
            progress_sink=sink,
        )
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(out["error"], "cancelled")
        self.assertFalse(out["ok"])
        man = json.loads((root / out["runId"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["status"], "cancelled")
        self.assertEqual(man["error"], "cancelled")
        self.assertFalse(man["ok"])

    def test_direct_api_rejects_truthy_strings_and_bad_scenario_type(self):
        root = _root()
        out = run_session(profile_id="porsche-981-2014-dme", mode="simulation", scenario={"x": 1}, artifact_root=root)
        self.assertEqual(out["error"], "invalid-scenario")
        live = run_session(
            profile_id="porsche-981-2014-dme",
            mode="live",
            confirmed_read_only="true",
            x431_inactive=True,
            artifact_root=root,
        )
        self.assertEqual(live["error"], "confirmedReadOnly-type")


class TestClearDtc(unittest.TestCase):
    def _clear(self, profile, *, clear_behavior="positive", **kwargs):
        port = kwargs.pop("port", None) or SessionSimPort(profile, "success", clear_behavior=clear_behavior)
        root = kwargs.pop("artifact_root", _root())
        out = run_session(
            profile_id=profile,
            mode="simulation",
            session_task="clear",
            port=port,
            artifact_root=root,
            **kwargs,
        )
        return out, port, root

    def test_read_backup_clear_read_both_identity_sets(self):
        from scripts.diagnostics.allowlist import blocked_reason, clear_transaction_blocked
        from scripts.diagnostics.catalog import live_allowed_hex, load_catalog, profile_by_id

        cat = load_catalog()
        for pid, clear_hex, dtc_hex, n_ident in (
            ("porsche-981-2014-dme", "14FF00", "1800FF00", 5),
            ("porsche-981-2014-gateway", "14FFFFFF", "190208", 7),
        ):
            with self.subTest(pid=pid):
                allowed = live_allowed_hex(profile_by_id(cat, pid))
                self.assertEqual(blocked_reason(bytes.fromhex(clear_hex), allowed), "blocked-sid-14")
                self.assertIsNone(clear_transaction_blocked(bytes.fromhex(clear_hex), clear_hex))
                self.assertIsNotNone(clear_transaction_blocked(bytes.fromhex("14"), clear_hex))
                self.assertIsNotNone(clear_transaction_blocked(bytes.fromhex("04"), clear_hex))
                out, port, root = self._clear(pid)
                self.assertTrue(out["ok"], out)
                self.assertEqual(out["status"], "completed")
                self.assertFalse(out["liveVerified"])
                self.assertIsNone(out["writePayload"])
                self.assertEqual(out["clear"]["requestHex"], clear_hex)
                self.assertEqual(
                    out["clear"]["expectedPositiveHex"],
                    "54FF00" if pid.endswith("-dme") else "54",
                )
                self.assertTrue(out["clear"]["clearSucceeded"])
                self.assertEqual(out["clear"]["outcome"], "cleared-zero")
                self.assertEqual(out["clear"]["dtcCountAfter"], 0)
                self.assertEqual(out["clear"]["attemptCount"], 1)
                self.assertGreater(len((out["preClear"]["results"][0]["decoded"]["records"])), 0)
                dtc = next(r for r in out["results"] if r.get("operationId") and "dtc" in r["operationId"])
                self.assertTrue(dtc["freshlyRead"])
                self.assertTrue(dtc.get("postClear"))
                self.assertEqual(len(dtc["decoded"]["records"]), 0)
                self.assertTrue((root / out["runId"] / "pre-clear.json").is_file())
                self.assertEqual(port.clear_tx_count, 1)
                i = port.ecu_payloads.index(clear_hex)
                self.assertIn(dtc_hex, port.ecu_payloads[:i])
                self.assertIn(dtc_hex, port.ecu_payloads[i + 1 :])
                ident = [r for r in out["results"] if r.get("role") == "identity"]
                self.assertEqual(len(ident), n_ident)
                self.assertTrue(all(r["ok"] for r in ident))
                self.assertTrue(out["identityQualification"]["observedProfileMatch"])
                self.assertTrue((root / out["runId"] / "checkpoint.json").is_file())
                self.assertTrue(port.closed)

    def test_no_clear_without_confirmation_identity_backup_or_resume(self):
        boom_writes = []

        class Boom:
            def write(self, data):
                boom_writes.append(data)
                raise AssertionError("port opened")

            def read(self, size=1):
                return b""

            def close(self):
                pass

        live = run_session(
            profile_id="porsche-981-2014-dme",
            mode="live",
            session_task="clear",
            artifact_root=_root(),
        )
        self.assertEqual(live["error"], "live-clear-confirmation-required")
        conflict = run_session(
            profile_id="porsche-981-2014-dme",
            mode="live",
            session_task="clear",
            confirmed_clear_dtc=True,
            x431_inactive=True,
            confirmed_read_only=True,
            artifact_root=_root(),
        )
        self.assertEqual(conflict["error"], "clear-conflicts-read-only")
        ids = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="clear",
            operation_ids=["dme-dtc"],
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(ids["error"], "clear-operationIds-forbidden")
        self.assertFalse(boom_writes)
        first = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=_root(),
        )
        resumed = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="clear",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(resumed["error"], "clear-resume-forbidden")
        port_mm = SessionSimPort("porsche-981-2014-dme", "identity-mismatch", clear_behavior="positive")
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="clear",
            port=port_mm,
            artifact_root=_root(),
        )
        self.assertEqual(out["error"], "identity-mismatch")
        self.assertNotIn("14FF00", port_mm.ecu_payloads)
        self.assertFalse(out["ok"])
        unknown = run_session(profile_id="nope", mode="simulation", session_task="clear", artifact_root=_root())
        self.assertEqual(unknown["error"], "unknown-profile")

    def test_persist_fail_before_clear_tx(self):
        import scripts.diagnostics.sessions as sess

        port = SessionSimPort("porsche-981-2014-dme", "success", clear_behavior="positive")
        orig = sess._atomic_write

        def wrapped(path, obj):
            if path.name == "pre-clear.json":
                raise OSError("disk")
            return orig(path, obj)

        with mock.patch("scripts.diagnostics.sessions._atomic_write", side_effect=wrapped):
            out = run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                session_task="clear",
                port=port,
                artifact_root=_root(),
            )
        self.assertFalse(out["ok"])
        self.assertNotIn("14FF00", port.ecu_payloads)
        self.assertTrue(port.closed)

    def test_preclear_snapshot_visible_inside_clear_write(self):
        root = _root()
        port = SessionSimPort("porsche-981-2014-dme", "success", clear_behavior="positive")
        seen = {}

        def probe():
            snaps = list(root.glob("*/pre-clear.json"))
            self.assertTrue(snaps, "pre-clear.json must exist before clear TX")
            data = json.loads(snaps[0].read_text(encoding="utf-8"))
            seen.update(data)
            self.assertEqual(data["kind"], "pre-clear")
            self.assertTrue(data["identityQualification"]["observedProfileMatch"])
            self.assertTrue(data["identityResults"])
            self.assertGreater(len(data["dependentResults"][0]["decoded"]["records"]), 0)
            self.assertTrue(data["raw"])
            ck = json.loads(snaps[0].with_name("checkpoint.json").read_text(encoding="utf-8"))
            self.assertTrue(ck.get("clearCheckpoint"))

        port.preclear_probe = probe
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="clear",
            port=port,
            artifact_root=root,
        )
        self.assertTrue(out["ok"], out)
        self.assertTrue(seen.get("raw"))

    def test_clear_checkpoint_not_resumable_as_read(self):
        root = _root()
        first, _, _ = self._clear("porsche-981-2014-dme", artifact_root=root)
        self.assertTrue(first["ok"], first)
        boom = []

        class Boom:
            def write(self, data):
                boom.append("w")
                raise AssertionError("port opened")

            def read(self, size=1):
                return b""

            def close(self):
                pass

        nxt = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=root,
        )
        self.assertEqual(nxt["error"], "clear-checkpoint-not-resumable")
        self.assertFalse(boom)

    def test_missing_clear_seed_does_not_block_read(self):
        with mock.patch("scripts.diagnostics.sessions.CLEAR_SEED", Path("no-such-dtc-clear.json")):
            prep = prepare("porsche-981-2014-dme")
            self.assertTrue(prep["ok"], prep)
            self.assertFalse((prep["plan"].get("clearCapability") or {}).get("available"))
            port = SessionSimPort("porsche-981-2014-dme")
            out = run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                port=port,
                artifact_root=_root(),
            )
            self.assertTrue(out["ok"], out)
            self.assertNotIn("14FF00", port.ecu_payloads)
            blocked = run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                session_task="clear",
                port=SessionSimPort("porsche-981-2014-dme"),
                artifact_root=_root(),
            )
            self.assertFalse(blocked["ok"])
            self.assertIn(blocked["error"], ("clear-spec-unavailable", "clear-spec-invalid"))

    def test_residual_nrc78_disconnect_cancel_mismatch_no_retry(self):
        residual, port_r, _ = self._clear("porsche-981-2014-dme", clear_behavior="residual")
        self.assertEqual(residual["error"], "residual-dtc-after-clear")
        self.assertTrue(residual["clear"]["clearSucceeded"])
        self.assertEqual(residual["clear"]["outcome"], "cleared-residual")
        self.assertGreater(residual["clear"]["dtcCountAfter"], 0)
        self.assertNotEqual(residual["clear"]["dtcCountAfter"], 0)
        self.assertEqual(port_r.clear_tx_count, 1)
        dtc = next(r for r in residual["results"] if r.get("operationId") == "dme-dtc" and r.get("postClear"))
        self.assertTrue(dtc["freshlyRead"])
        self.assertGreater(len(dtc["decoded"]["records"]), 0)

        nrc78, port78, _ = self._clear("porsche-981-2014-gateway", clear_behavior="nrc78")
        self.assertEqual(nrc78["error"], "nrc78-timeout")
        self.assertEqual(port78.clear_tx_count, 1)
        self.assertFalse(nrc78.get("clear", {}).get("clearSucceeded"))

        pending_ok, port_p, _ = self._clear("porsche-981-2014-dme", clear_behavior="nrc78-then-positive")
        self.assertTrue(pending_ok["ok"], pending_ok)
        self.assertTrue(pending_ok["clear"]["pending"])
        self.assertEqual(port_p.clear_tx_count, 1)
        self.assertEqual(pending_ok["clear"]["attemptCount"], 1)

        post_fail, port_f, _ = self._clear("porsche-981-2014-dme", clear_behavior="post-fail")
        self.assertFalse(post_fail["ok"])
        self.assertTrue(post_fail["clear"]["clearSucceeded"])
        self.assertEqual(post_fail["clear"]["outcome"], "unknown")
        hist = [r for r in post_fail["results"] if r.get("operationId") == "dme-dtc" and r.get("historical")]
        self.assertTrue(hist)
        self.assertFalse(hist[0]["freshlyRead"])
        self.assertGreater(len(hist[0]["decoded"]["records"]), 0)
        self.assertEqual(port_f.clear_tx_count, 1)

        nrc, port_n, _ = self._clear("porsche-981-2014-dme", clear_behavior="nrc")
        self.assertEqual(nrc["error"], "negative-response")
        self.assertEqual(port_n.clear_tx_count, 1)

        extra, port_e, _ = self._clear("porsche-981-2014-dme", clear_behavior="extra-positive")
        self.assertIn(extra["error"], ("wrong-sid-or-did-echo", "clear-positive-mismatch"))
        self.assertEqual(port_e.clear_tx_count, 1)

        bare, port_b, _ = self._clear("porsche-981-2014-dme", clear_behavior="bare54")
        self.assertFalse(bare["ok"])
        self.assertNotEqual(bare.get("clear", {}).get("payload_hex"), "54FF00")
        self.assertEqual(port_b.clear_tx_count, 1)

        wrong, port_w, _ = self._clear("porsche-981-2014-dme", clear_behavior="wrong-group")
        self.assertFalse(wrong["ok"])
        self.assertEqual(port_w.clear_tx_count, 1)

        disc, port_d, _ = self._clear("porsche-981-2014-dme", clear_behavior="disconnect")
        self.assertFalse(disc["ok"])
        self.assertEqual(port_d.clear_tx_count, 1)
        self.assertEqual(port_d.ecu_payloads.count("14FF00"), 1)
        self.assertTrue(port_d.closed)

        cancel = threading.Event()

        def sink(ev):
            if ev.get("stage") == "clear":
                cancel.set()

        port_c = SessionSimPort("porsche-981-2014-dme", "success", clear_behavior="positive")
        cancelled = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="clear",
            port=port_c,
            artifact_root=_root(),
            cancel_event=cancel,
            progress_sink=sink,
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertNotIn("14FF00", port_c.ecu_payloads)
        self.assertTrue(port_c.closed)

    def test_clear_shares_lock_with_read(self):
        root = _root()
        started = threading.Event()
        port = SessionSimPort("porsche-981-2014-dme", "slow")

        def one():
            started.set()
            run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                scenario="slow",
                port=port,
                artifact_root=root,
                budget_s=2.0,
            )

        t = threading.Thread(target=one)
        t.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.08)
        second = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="clear",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        self.assertEqual(second["error"], "session-lock-busy")
        t.join(5)


class TestStdio(unittest.TestCase):
    def test_prepare_and_run_success(self):
        root = _root()
        stdin = io.StringIO(json.dumps({"action": "prepare", "profileId": "porsche-981-2014-dme"}) + "\n")
        stdout = io.StringIO()
        rc = stdio_loop(stdin, stdout, artifact_root=root)
        self.assertEqual(rc, 0)
        plan = json.loads(stdout.getvalue().splitlines()[0])
        self.assertEqual(plan["type"], "plan")
        self.assertTrue(plan["ok"])

        req = {
            "action": "run",
            "profileId": "porsche-981-2014-dme",
            "mode": "simulation",
            "scenario": "success",
        }
        stdin = io.StringIO(json.dumps(req) + "\n")
        stdout = io.StringIO()
        rc = stdio_loop(stdin, stdout, artifact_root=root)
        self.assertEqual(rc, 0)
        events = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln]
        self.assertTrue(any(e["type"] == "progress" for e in events))
        result = events[-1]
        self.assertEqual(result["type"], "result")
        self.assertTrue(result["ok"])
        self.assertFalse(result["liveVerified"])

    def test_bad_types_extra_keys_oversize(self):
        root = _root()
        for obj in (
            {"action": "run", "profileId": 1},
            {"action": "run", "profileId": "porsche-981-2014-dme", "port": "COM9"},
            {"action": "run", "profileId": "porsche-981-2014-dme", "operationIds": "dme-dtc"},
            {"action": "run", "profileId": "porsche-981-2014-dme", "scenario": {"x": 1}},
            {"action": "run", "profileId": "porsche-981-2014-dme", "mode": ["simulation"]},
            {"action": "run", "profileId": "porsche-981-2014-dme", "confirmedReadOnly": "true"},
            {"action": "run", "profileId": "porsche-981-2014-dme", "x431Inactive": "true"},
            {"action": ["run"], "profileId": "porsche-981-2014-dme"},
            {"action": "run", "profileId": "porsche-981-2014-dme", "requestHex": "14FF00"},
            {"action": "run", "profileId": "porsche-981-2014-dme", "sessionTask": "write"},
            {"action": "run", "profileId": "porsche-981-2014-dme", "confirmedClearDtc": "true"},
        ):
            stdout = io.StringIO()
            rc = stdio_loop(io.StringIO(json.dumps(obj) + "\n"), stdout, artifact_root=root)
            self.assertEqual(rc, 2)
            ev = json.loads(stdout.getvalue().splitlines()[0])
            self.assertFalse(ev["ok"])
        huge = "{" + ("a" * 70000) + "}"
        stdout = io.StringIO()
        rc = stdio_loop(io.StringIO(huge + "\n"), stdout, artifact_root=root)
        self.assertEqual(rc, 2)

    def test_stdio_subprocess_keeps_stdin_open(self):
        import subprocess

        root = _root()
        req = json.dumps({"action": "run", "profileId": "porsche-981-2014-dme", "mode": "simulation", "scenario": "success"}) + "\n"
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "scripts.diagnostics.sessions", "--stdio", "--artifact-root", str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        chunks: list[bytes] = []
        err_chunks: list[bytes] = []

        def _read_out():
            assert proc.stdout is not None
            chunks.append(proc.stdout.read())

        def _read_err():
            assert proc.stderr is not None
            err_chunks.append(proc.stderr.read())

        threading.Thread(target=_read_out, daemon=True).start()
        threading.Thread(target=_read_err, daemon=True).start()
        try:
            assert proc.stdin is not None
            proc.stdin.write(req.encode("utf-8"))
            proc.stdin.flush()
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            self.fail((b"".join(chunks) + b"".join(err_chunks)).decode("utf-8", errors="replace"))
        time.sleep(0.05)
        stdout = b"".join(chunks).decode("utf-8", errors="replace")
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
        self.assertEqual(rc, 0, stdout + stderr)
        self.assertNotIn("Fatal Python error", stderr)
        lines = [json.loads(ln) for ln in stdout.splitlines() if ln]
        self.assertEqual(lines[-1]["type"], "result")
        self.assertTrue(lines[-1]["ok"])


if __name__ == "__main__":
    unittest.main()
