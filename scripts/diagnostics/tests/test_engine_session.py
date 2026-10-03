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

from scripts.diagnostics.allowlist import engine_transaction_blocked
from scripts.diagnostics.engine_obd import decode_mode01, load_engine_spec, pid_bit_supported
from scripts.diagnostics.pair import response_matches
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.sessions import main, prepare, run_session, stdio_loop


def _root():
    return Path(tempfile.mkdtemp(prefix="eng-"))


def _engine(**kwargs):
    root = kwargs.pop("artifact_root", _root())
    port = kwargs.pop("port", None) or SessionSimPort("porsche-981-2014-dme", "success")
    out = run_session(
        profile_id="porsche-981-2014-dme",
        mode="simulation",
        session_task="engine",
        sample_cycles=kwargs.pop("sample_cycles", 1),
        interval_ms=kwargs.pop("interval_ms", 500),
        port=port,
        artifact_root=root,
        **kwargs,
    )
    return out, port, root


class TestSid01Pair(unittest.TestCase):
    def test_pid_echo_required(self):
        self.assertEqual(response_matches(bytes.fromhex("0100"), bytes.fromhex("4100181A8000")), "positive-echo")
        self.assertEqual(response_matches(bytes.fromhex("0104"), bytes.fromhex("410480")), "positive-echo")
        self.assertIsNone(response_matches(bytes.fromhex("0104"), bytes.fromhex("410564")))
        self.assertIsNone(response_matches(bytes.fromhex("0100"), bytes.fromhex("4104")))
        self.assertEqual(response_matches(bytes.fromhex("0100"), bytes.fromhex("7F0178")), "pending-7Fxx78")
        self.assertEqual(response_matches(bytes.fromhex("0104"), bytes.fromhex("7F0111")), "negative")


class TestBitmapAndFormulas(unittest.TestCase):
    def test_known_bit_positions_and_values(self):
        mask = bytes.fromhex("181A8000")
        for pid in ("04", "05", "0C", "0D", "0F", "11"):
            self.assertTrue(pid_bit_supported(mask, pid), pid)
        self.assertFalse(pid_bit_supported(mask, "01"))
        self.assertFalse(pid_bit_supported(mask, "06"))
        self.assertFalse(pid_bit_supported(mask, "20"))
        spec = load_engine_spec()
        by = {p["pid"]: p for p in spec["pids"]}
        self.assertAlmostEqual(decode_mode01(by["04"], "410480"), 128 * 100 / 255)
        self.assertEqual(decode_mode01(by["05"], "410564"), 60.0)
        self.assertEqual(decode_mode01(by["0C"], "410C1F40"), 2000.0)
        self.assertEqual(decode_mode01(by["0D"], "410D50"), 80.0)
        self.assertEqual(decode_mode01(by["0F"], "410F32"), 10.0)
        self.assertAlmostEqual(decode_mode01(by["11"], "411180"), 128 * 100 / 255)

    def test_0120_never_authorized(self):
        spec = load_engine_spec()
        self.assertIsNotNone(engine_transaction_blocked(bytes.fromhex("0120"), spec["authorizedHex"]))
        self.assertIsNotNone(engine_transaction_blocked(bytes.fromhex("0101"), spec["authorizedHex"]))
        self.assertIsNone(engine_transaction_blocked(bytes.fromhex("0100"), spec["authorizedHex"]))


class TestEngineSession(unittest.TestCase):
    def test_selection_limits_actual_requests_and_saved_samples(self):
        out, port, root = _engine(selected_pids=["11", "0C"], sample_cycles=2)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["engine"]["selectedPids"], ["0C", "11"])
        self.assertEqual(out["engine"]["supportedPids"], ["0C", "11"])
        self.assertEqual([p for p in port.ecu_payloads if p.startswith("01")], ["0100", "010C", "0111", "010C", "0111"])
        saved = json.loads((root / out["runId"] / "engine.json").read_text())
        self.assertEqual({s["pid"] for s in saved["samples"]}, {"0C", "11"})
        plan = prepare("porsche-981-2014-dme", session_task="engine", selected_pids=["0C"])
        self.assertEqual([r["pid"] for r in plan["plan"]["engine"]["definitions"]], ["0C"])

    def test_bad_selection_rejected_before_port_open(self):
        for selection in ([], ["0C", "0C"], ["20"], ["0c"], "0C", [False], ["0C"] * 13):
            with self.subTest(selection=selection):
                port = SessionSimPort("porsche-981-2014-dme")
                out, port, _ = _engine(selected_pids=selection, port=port)
                self.assertEqual(out["error"], "invalid-selected-pids")
                self.assertEqual(port.writes, [])
        self.assertEqual(prepare("porsche-981-2014-dme", selected_pids=["0C"])["error"], "engine-options-without-task")
        self.assertEqual(prepare("porsche-981-2014-gateway", session_task="engine", selected_pids=["0C"])["error"], "engine-profile-unsupported")

    def test_selected_unsupported_items_not_sent(self):
        port = SessionSimPort("porsche-981-2014-dme", engine_mask=bytes.fromhex("10000000"))
        out, port, _ = _engine(port=port, selected_pids=["04", "0C"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["engine"]["unsupportedPids"], ["0C"])
        self.assertEqual([p for p in port.ecu_payloads if p.startswith("01")], ["0100", "0104"])

    def test_unvalidated_vnci_route_refused_before_native_constructor(self):
        with mock.patch("scripts.diagnostics.vnci.DpuClient") as native:
            out = run_session(profile_id="porsche-981-2014-dme", mode="live", device_id="vnci:6055836",
                session_task="engine", confirmed_read_only=True, x431_inactive=True, artifact_root=_root())
            self.assertEqual(out["error"], "vnci-engine-route-not-validated")
            native.assert_not_called()

    def test_mode01_switches_to_qualified_standard_route(self):
        class RoutePort(SessionSimPort):
            route = None

            def write(self, data):
                command = data.decode("ascii").strip().upper()
                if command.startswith("ATSH "):
                    self.route = command.split()[1]
                if command.startswith("02 01") and self.route != "7DF":
                    raise AssertionError("Mode01 sent on manufacturer route")
                return super().write(data)

        port = RoutePort("porsche-981-2014-dme")
        out, port, root = _engine(port=port)
        self.assertTrue(out["ok"], out)
        commands = [b.decode("ascii").strip() for b in port.writes]
        standard = commands.index("ATSH 7DF")
        self.assertIn("ATPC", commands[:standard])
        self.assertIn("ATCFC0", commands[:standard])
        self.assertIn("ATCRA 7E8", commands[standard:])
        self.assertEqual(port.ecu_payloads.count("1089"), 1)
        self.assertEqual(out["engine"]["acquisitionRoute"]["txId"], "7DF")
        self.assertFalse(out["engine"]["acquisitionRoute"]["automaticFallback"])
        saved = json.loads((root / out["runId"] / "engine.json").read_text())
        self.assertEqual(saved["acquisitionRoute"], out["engine"]["acquisitionRoute"])

    def test_identity_failure_does_not_switch_or_query_standard(self):
        port = SessionSimPort("porsche-981-2014-dme", "identity-mismatch")
        out, port, _ = _engine(port=port)
        self.assertEqual(out["error"], "identity-mismatch")
        self.assertNotIn(b"ATSH 7DF\r", port.writes)
        self.assertNotIn("0100", port.ecu_payloads)

    def test_known_values_on_fake_port(self):
        out, port, root = _engine()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["sessionTask"], "engine")
        self.assertFalse(out["liveVerified"])
        self.assertIsNone(out["writePayload"])
        self.assertEqual(out["engine"]["supportedPids"], ["04", "05", "0C", "0D", "0F", "11"])
        self.assertEqual(out["engine"]["unsupportedPids"], [])
        self.assertEqual(out["engine"]["completedCycles"], 1)
        by = {s["pid"]: s for s in out["engine"]["samples"]}
        self.assertAlmostEqual(by["04"]["value"], 128 * 100 / 255)
        self.assertEqual(by["05"]["value"], 60.0)
        self.assertEqual(by["0C"]["value"], 2000.0)
        self.assertEqual(by["0D"]["value"], 80.0)
        self.assertEqual(by["0F"]["value"], 10.0)
        self.assertAlmostEqual(by["11"]["value"], 128 * 100 / 255)
        self.assertTrue(all(s["cycle"] == 1 and s["synthetic"] for s in out["engine"]["samples"]))
        self.assertIn("0100", port.ecu_payloads)
        self.assertNotIn("0120", port.ecu_payloads)
        self.assertNotIn("1800FF00", port.ecu_payloads)
        self.assertTrue((root / out["runId"] / "engine.json").is_file())
        self.assertTrue(port.closed)

    def test_unsupported_never_sent(self):
        port = SessionSimPort("porsche-981-2014-dme", engine_mask=bytes.fromhex("10000000"))
        out, port, _ = _engine(port=port)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["engine"]["supportedPids"], ["04"])
        self.assertEqual(out["engine"]["unsupportedPids"], ["05", "0C", "0D", "0F", "11"])
        self.assertEqual([p for p in port.ecu_payloads if p.startswith("01")], ["0100", "0104"])
        self.assertNotIn("0105", port.ecu_payloads)
        self.assertNotIn("0120", port.ecu_payloads)

    def test_full_mask_still_skips_next_support_group(self):
        port = SessionSimPort("porsche-981-2014-dme", engine_mask=bytes.fromhex("FFFFFFFF"))
        out, port, _ = _engine(port=port)
        self.assertTrue(out["ok"], out)
        self.assertNotIn("0120", port.ecu_payloads)
        self.assertEqual(port.ecu_payloads.count("0100"), 1)

    def test_identity_mismatch_blocks_0100(self):
        port = SessionSimPort("porsche-981-2014-dme", "identity-mismatch")
        out, port, _ = _engine(port=port)
        self.assertEqual(out["error"], "identity-mismatch")
        self.assertNotIn("0100", port.ecu_payloads)
        self.assertNotIn("0104", port.ecu_payloads)

    def test_malformed_and_wrong_pid_length(self):
        port = SessionSimPort("porsche-981-2014-dme")
        port.ecu_map["0100"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("4100181A80")))
        out, port, _ = _engine(port=port)
        self.assertEqual(out["error"], "engine-pid-length-mismatch")
        self.assertNotIn("0104", port.ecu_payloads)

        port2 = SessionSimPort("porsche-981-2014-dme")
        port2.ecu_map["0104"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("410564")))
        out2, port2, _ = _engine(port=port2)
        self.assertEqual(out2["error"], "wrong-sid-or-did-echo")
        self.assertEqual(port2.ecu_payloads.count("0104"), 1)
        self.assertNotIn("0105", port2.ecu_payloads)

        port3 = SessionSimPort("porsche-981-2014-dme")
        port3.ecu_map["010C"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("410C1F")))
        out3, port3, _ = _engine(port=port3)
        self.assertEqual(out3["error"], "engine-pid-length-mismatch")
        self.assertEqual(port3.ecu_payloads.count("010C"), 1)
        self.assertNotIn("010D", port3.ecu_payloads)

    def test_nrc_and_timeout_no_retry(self):
        port = SessionSimPort("porsche-981-2014-dme")
        port.ecu_map["0100"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("7F0111")))
        out, port, _ = _engine(port=port)
        self.assertEqual(out["error"], "negative-response")
        self.assertEqual(port.ecu_payloads.count("0100"), 1)
        self.assertNotIn("0104", port.ecu_payloads)

        port2 = SessionSimPort("porsche-981-2014-dme")
        port2.ecu_map["0100"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("7F0178")))
        out2, port2, _ = _engine(port=port2)
        self.assertIn(out2["error"], ("nrc78-prompt-without-final", "nrc78-timeout"))
        self.assertEqual(port2.ecu_payloads.count("0100"), 1)

    def test_cancel_during_interval_no_further_tx(self):
        cancel = threading.Event()

        def sink(ev):
            eng = ev.get("engine") or {}
            if ev.get("stage") == "engine" and eng.get("completedCycles") == 0 and len(eng.get("samples") or []) == 6:
                cancel.set()

        port = SessionSimPort("porsche-981-2014-dme")
        out, port, _ = _engine(
            port=port,
            sample_cycles=2,
            interval_ms=5000,
            cancel_event=cancel,
            progress_sink=sink,
        )
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(port.ecu_payloads.count("0104"), 1)
        self.assertEqual(port.ecu_payloads.count("0100"), 1)

    def test_interval_deadline(self):
        port = SessionSimPort("porsche-981-2014-dme")
        out, port, _ = _engine(port=port, sample_cycles=2, interval_ms=5000, budget_s=3.0)
        self.assertEqual(out["error"], "deadline-expired")
        self.assertEqual(out["engine"]["completedCycles"], 1)
        self.assertEqual(port.ecu_payloads.count("0104"), 1)

    def test_lock_collision_with_read_and_clear(self):
        root = _root()
        started = threading.Event()
        slow = SessionSimPort("porsche-981-2014-dme", "slow")

        def one():
            started.set()
            run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                scenario="slow",
                port=slow,
                artifact_root=root,
                budget_s=2.0,
            )

        t = threading.Thread(target=one)
        t.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.08)
        eng = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="engine",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=root,
        )
        self.assertEqual(eng["error"], "session-lock-busy")
        t.join(5)

    def test_mode_flags_limits_options(self):
        boom = []

        class Boom:
            def write(self, data):
                boom.append(data)
                raise AssertionError("port opened")

            def read(self, size=1):
                return b""

            def close(self):
                pass

        live = run_session(profile_id="porsche-981-2014-dme", mode="live", session_task="engine", artifact_root=_root())
        self.assertEqual(live["error"], "live-confirmations-required")
        flag = run_session(
            profile_id="porsche-981-2014-dme",
            mode="live",
            session_task="engine",
            confirmed_read_only=True,
            x431_inactive=True,
            confirmed_clear_dtc=True,
            artifact_root=_root(),
        )
        self.assertEqual(flag["error"], "clear-flag-without-task")
        gw = run_session(
            profile_id="porsche-981-2014-gateway",
            mode="simulation",
            session_task="engine",
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(gw["error"], "engine-profile-unsupported")
        self.assertFalse(boom)
        ids = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="engine",
            operation_ids=["dme-dtc"],
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(ids["error"], "engine-operationIds-forbidden")
        first = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            port=SessionSimPort("porsche-981-2014-dme"),
            artifact_root=_root(),
        )
        resumed = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="engine",
            resume_run_id=first["runId"],
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(resumed["error"], "engine-resume-forbidden")
        bad_c = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="engine",
            sample_cycles=True,
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(bad_c["error"], "sampleCycles-type")
        rng = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="engine",
            sample_cycles=11,
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(rng["error"], "sampleCycles-range")
        iv = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            session_task="engine",
            interval_ms=499,
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(iv["error"], "intervalMs-range")
        extra = run_session(
            profile_id="porsche-981-2014-dme",
            mode="simulation",
            sample_cycles=2,
            port=Boom(),
            artifact_root=_root(),
        )
        self.assertEqual(extra["error"], "engine-options-without-task")
        self.assertFalse(boom)
        prep = prepare("porsche-981-2014-dme", session_task="engine")
        self.assertTrue(prep["ok"], prep)
        self.assertEqual(prep["plan"]["engine"]["evidenceStatus"], "standard-obd-unverified-on-vehicle")
        self.assertEqual(prep["plan"]["engine"]["sampleCycles"], 5)
        self.assertEqual(prep["plan"]["dependentOperations"], [])
        self.assertFalse(any(r.get("operationId") == "dme-dtc" for r in prep["plan"]["dependentOperations"]))

    def test_persist_partial_and_no_serial(self):
        port = SessionSimPort("porsche-981-2014-dme")
        port.ecu_map["0105"] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex("7F0111")))
        out, port, root = _engine(port=port)
        self.assertFalse(out["ok"])
        eng = json.loads((root / out["runId"] / "engine.json").read_text(encoding="utf-8"))
        self.assertEqual([s["pid"] for s in eng["samples"]], ["04"])
        self.assertEqual(eng["completedCycles"], 0)
        self.assertTrue((root / out["runId"] / "raw.json").is_file())
        sys.modules.pop("serial", None)
        with mock.patch.dict(sys.modules, {"serial": None}):
            with mock.patch("scripts.diagnostics.sessions._open_live_port", side_effect=AssertionError("serial")):
                ok, _, _ = _engine()
        self.assertTrue(ok["ok"], ok)
        self.assertNotIn("serial", sys.modules)

    def test_zero_supported_fails(self):
        port = SessionSimPort("porsche-981-2014-dme", engine_mask=bytes.fromhex("00000000"))
        out, port, _ = _engine(port=port)
        self.assertEqual(out["error"], "no-supported-engine-pids")
        self.assertEqual([p for p in port.ecu_payloads if p.startswith("01")], ["0100"])


class TestEngineCli(unittest.TestCase):
    def test_cli_parse_engine_and_named_clear(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main(["prepare", "--profile", "porsche-981-2014-dme", "--session-task", "engine"])
        self.assertEqual(rc, 0)
        plan = json.loads(buf.getvalue().splitlines()[0])
        self.assertEqual(plan["plan"]["engine"]["evidenceStatus"], "standard-obd-unverified-on-vehicle")
        root = _root()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main(
                [
                    "run",
                    "--profile",
                    "porsche-981-2014-dme",
                    "--session-task",
                    "engine",
                    "--sample-cycles",
                    "1",
                    "--interval-ms",
                    "500",
                    "--artifact-root",
                    str(root),
                ]
            )
        self.assertEqual(rc, 0, buf.getvalue())
        result = json.loads(buf.getvalue().splitlines()[-1])
        self.assertTrue(result["ok"], result)
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main(
                [
                    "run",
                    "--profile",
                    "porsche-981-2014-dme",
                    "--session-task",
                    "clear",
                    "--yes-clear-dtc",
                    "--x431-inactive",
                    "--artifact-root",
                    str(root),
                ]
            )
        self.assertEqual(rc, 0, buf.getvalue())
        cleared = json.loads(buf.getvalue().splitlines()[-1])
        self.assertTrue(cleared["ok"], cleared)
        self.assertEqual(cleared["sessionTask"], "clear")
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main(
                [
                    "run",
                    "--profile",
                    "porsche-981-2014-dme",
                    "--session-task",
                    "read",
                    "--sample-cycles",
                    "2",
                    "--artifact-root",
                    str(root),
                ]
            )
        self.assertEqual(rc, 1)
        bad = json.loads(buf.getvalue().splitlines()[-1])
        self.assertEqual(bad["error"], "engine-options-without-task")

    def test_cli_artifact_root_global_and_subcommand(self):
        seen: list[tuple[str, Path | None]] = []

        def _capture(**kwargs):
            seen.append((kwargs["session_task"], kwargs.get("artifact_root")))
            return {"ok": True, "status": "completed"}

        root = _root()
        after = root / "after"
        with mock.patch("scripts.diagnostics.sessions.run_session", side_effect=_capture):
            for task, extra in (
                ("read", []),
                ("engine", []),
                ("clear", ["--yes-clear-dtc", "--x431-inactive"]),
            ):
                seen.clear()
                rc = main(
                    [
                        "--artifact-root",
                        str(root),
                        "run",
                        "--profile",
                        "porsche-981-2014-dme",
                        "--session-task",
                        task,
                        *extra,
                    ]
                )
                self.assertEqual(rc, 0, task)
                self.assertEqual(seen, [(task, root)])
                seen.clear()
                rc = main(
                    [
                        "run",
                        "--profile",
                        "porsche-981-2014-dme",
                        "--session-task",
                        task,
                        *extra,
                        "--artifact-root",
                        str(after),
                    ]
                )
                self.assertEqual(rc, 0, task)
                self.assertEqual(seen, [(task, after)])
        with mock.patch("scripts.diagnostics.sessions.stdio_loop", return_value=0) as stdio:
            rc = main(["--stdio", "--artifact-root", str(root)])
        self.assertEqual(rc, 0)
        self.assertEqual(stdio.call_args.kwargs["artifact_root"], root)
        with self.assertRaises(SystemExit) as raised:
            main(
                [
                    "--artifact-root",
                    str(root),
                    "run",
                    "--profile",
                    "porsche-981-2014-dme",
                    "--artifact-root",
                    str(after),
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_stdio_engine_types(self):
        root = _root()
        stdout = io.StringIO()
        rc = stdio_loop(
            io.StringIO(json.dumps({"action": "run", "profileId": "porsche-981-2014-dme", "sampleCycles": 2}) + "\n"),
            stdout,
            artifact_root=root,
        )
        self.assertEqual(rc, 2)
        stdout = io.StringIO()
        rc = stdio_loop(
            io.StringIO(
                json.dumps(
                    {
                        "action": "prepare",
                        "profileId": "porsche-981-2014-dme",
                        "sessionTask": "engine",
                        "sampleCycles": True,
                    }
                )
                + "\n"
            ),
            stdout,
            artifact_root=root,
        )
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
