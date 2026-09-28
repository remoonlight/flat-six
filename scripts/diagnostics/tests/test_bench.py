"""Offline bench: authentic replay vs synthetic faults. Captures optional."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts.diagnostics.bench import (
    AUTHENTIC_NRC78_REQ,
    DEFAULT_CAPTURE_ROOT,
    VirtualElmPort,
    ath1_prompt,
    captures_present,
    main,
    run_bench,
)
from scripts.diagnostics.catalog import live_allowed_hex, load_catalog, operation, profile_by_id
from scripts.diagnostics.decode import redact_vin
from scripts.diagnostics.elm import ElmClient, ElmError, parse_ath1_response, sf_can_hex

REPO = Path(__file__).resolve().parents[3]
BENCH_PY = REPO / "scripts" / "diagnostics" / "bench.py"


def _skip_captures():
    return not captures_present(DEFAULT_CAPTURE_ROOT)


class TestBenchImports(unittest.TestCase):
    def test_source_and_runtime_never_import_serial(self):
        tree = ast.parse(BENCH_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(any(a.name.split(".")[0] == "serial" for a in node.names))
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotEqual(node.module.split(".")[0], "serial")
        sys.modules.pop("serial", None)
        sys.modules.pop("serial.tools", None)
        sys.modules.pop("serial.tools.list_ports", None)
        import importlib

        import scripts.diagnostics.bench as bench_mod

        importlib.reload(bench_mod)
        self.assertNotIn("serial", sys.modules)


class TestSyntheticBench(unittest.TestCase):
    def test_run_bench_synthetic_expectations_from_catalog(self):
        cat = load_catalog()
        dsn = operation(profile_by_id(cat, "porsche-981-2014-dme"), "dme-dsn")
        part = operation(profile_by_id(cat, "porsche-981-2014-gateway"), "gw-part-f187")
        self.assertEqual(dsn["expectedValue"], "P200")
        self.assertEqual(part["expectedValue"], "95B907530N")
        # Force synthetic-only path
        result = run_bench(Path(tempfile.mkdtemp()) / "missing-runs")
        by_id = {c["id"]: c for c in result["cases"]}
        self.assertFalse(result["executionEnabled"])
        self.assertFalse(result["independentLiveVerified"])
        self.assertTrue(result["summary"]["authenticSkipped"])
        skipped = [c for c in result["cases"] if c["evidenceKind"] == "capture_replay"]
        self.assertTrue(skipped)
        self.assertTrue(all(c["detail"].get("skipped") for c in skipped))
        self.assertTrue(by_id["synthetic:sf-dme-dsn"]["ok"], by_id["synthetic:sf-dme-dsn"])
        self.assertEqual(by_id["synthetic:sf-dme-dsn"]["detail"]["expected"], "P200")
        self.assertEqual(by_id["synthetic:sf-dme-dsn"]["detail"]["got"], "P200")
        self.assertTrue(by_id["synthetic:mf-gw-f187"]["ok"], by_id["synthetic:mf-gw-f187"])
        self.assertEqual(by_id["synthetic:mf-gw-f187"]["detail"]["got"], "95B907530N")
        for cid in (
            "synthetic:nrc78-then-final",
            "synthetic:wrong-ecu-header",
            "synthetic:mismatched-did",
            "synthetic:truncated-mf",
            "synthetic:out-of-order-cf",
            "synthetic:malformed-line",
            "synthetic:negative-response",
            "synthetic:timeout",
            "synthetic:disconnect",
            "synthetic:teardown-failure",
            "synthetic:late-response-other-request",
            "synthetic:clear-and-coding-rejected",
            "synthetic:missing-frames",
            "synthetic:adapter-identity-mismatch",
            "synthetic:allowlist-unchanged",
        ):
            self.assertTrue(by_id[cid]["ok"], (cid, by_id[cid]))
            self.assertEqual(by_id[cid]["evidenceKind"], "synthetic")
        self.assertEqual(result["summary"]["failCount"], 0)
        blob = json.dumps(result)
        self.assertNotIn("WVWZZZ", blob)
        self.assertEqual(by_id["synthetic:nrc78-then-final"]["detail"]["ecuWriteCount"], 1)
        self.assertEqual(by_id["synthetic:negative-response"]["detail"]["error"], "negative-response")
        self.assertEqual(by_id["synthetic:wrong-ecu-header"]["detail"]["error"], "malformed-or-wrong-id")

    def test_negative_parse_is_not_success(self):
        parsed = parse_ath1_response(
            "77A 03 7F 22 31 AA AA AA AA\r>",
            req_hex="22F187",
            rx_id=0x77A,
            tx_id=0x710,
            sent_sf=sf_can_hex("22F187"),
        )
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"], "negative-response")

    def test_clear_write_not_in_allowlist(self):
        cat = load_catalog()
        gw = profile_by_id(cat, "porsche-981-2014-gateway")
        allowed = live_allowed_hex(gw)
        self.assertEqual(len(allowed), 9)
        self.assertNotIn("14FFFFFF", allowed)
        self.assertFalse(any(h.startswith("2E") for h in allowed))
        self.assertFalse(gw["coding"]["enabled"])

    def test_virtual_port_restore_on_fault(self):
        port = VirtualElmPort(ecu_map={"1A9F": ath1_prompt(["7E8 ZZ"])})
        from scripts.diagnostics.live import run_read

        out = run_read("porsche-981-2014-dme", "dme-dsn", port=port, budget_s=10, out_dir=Path(tempfile.mkdtemp()))
        self.assertFalse(out["ok"])
        self.assertTrue(port.closed)

    def test_cli_missing_captures_exit_zero(self):
        td = tempfile.TemporaryDirectory()
        try:
            out = Path(td.name) / "result.json"
            rc = main(["--out", str(out), "--capture-root", str(Path(td.name) / "none")])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(data["executionEnabled"])
            self.assertTrue(data["summary"]["authenticSkipped"])
        finally:
            td.cleanup()


@unittest.skipUnless(not _skip_captures(), f"private captures absent under {DEFAULT_CAPTURE_ROOT}")
class TestAuthenticReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_bench(DEFAULT_CAPTURE_ROOT)
        cls.by_id = {c["id"]: c for c in cls.result["cases"]}
        cls.cat = load_catalog()

    def test_all_catalog_ops_replay(self):
        ops = [op["id"] for p in self.cat["profiles"] for op in p["operations"]]
        self.assertEqual(len(ops), 16)
        for oid in ops:
            row = self.by_id[f"capture:{oid}"]
            self.assertTrue(row["ok"], row)
            self.assertEqual(row["evidenceKind"], "capture_replay")
            self.assertTrue(row["detail"]["hashMatchesProvenance"])
            self.assertEqual(row["detail"]["ecuWriteCount"], 1)
            self.assertFalse(row["detail"].get("skipped"))
        nrc = self.by_id["capture:nrc78-2206F4"]
        self.assertTrue(nrc["ok"], nrc)
        self.assertTrue(nrc["detail"]["notLiveAllowed"])
        self.assertNotIn(AUTHENTIC_NRC78_REQ, live_allowed_hex(profile_by_id(self.cat, "porsche-981-2014-gateway")))

    def test_known_catalog_values(self):
        dsn = operation(profile_by_id(self.cat, "porsche-981-2014-dme"), "dme-dsn")
        self.assertEqual(self.by_id["capture:dme-dsn"]["detail"]["decoded"]["text"], dsn["expectedValue"])
        recs = self.by_id["capture:dme-dtc"]["detail"]["decoded"]["records"]
        self.assertEqual(recs[0]["dtcHex"], "C447")
        self.assertEqual(recs[1]["dtcHex"], "C412")
        vin = self.by_id["capture:dme-vin"]["detail"]["decoded"]["text"]
        self.assertEqual(vin, redact_vin(vin))
        self.assertIn("*", vin)
        self.assertEqual(len(vin), 17)
        self.assertIn("*", self.by_id["capture:gw-vin"]["detail"]["decoded"]["text"])

    def test_cli_with_captures(self):
        td = tempfile.TemporaryDirectory()
        try:
            out = Path(td.name) / "result.json"
            rc = main(["--out", str(out), "--capture-root", str(DEFAULT_CAPTURE_ROOT)])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["failCount"], 0)
            self.assertFalse(data["summary"]["authenticSkipped"])
        finally:
            td.cleanup()


class TestClockInjection(unittest.TestCase):
    def test_timeout_does_not_wait_wall_clock(self):
        from scripts.diagnostics.bench import FakeClock

        clock = FakeClock()
        port = VirtualElmPort(ecu_map={}, hang_unknown=True)
        client = ElmClient(port, timeout_s=1.0, clock=clock.time, sleeper=clock.sleep)
        client.validate_adapter(2.0)
        client.configure_pair("710", "77A", clock.time() + 5)
        with self.assertRaises(Exception):
            client.request("22F187", clock.time() + 1.0)
        self.assertGreaterEqual(clock.t, 1.0)
        client.close_restore()
        self.assertTrue(port.closed)


class TestInFlightGuard(unittest.TestCase):
    def test_concurrent_request_fails_before_second_write(self):
        entered = threading.Event()
        release = threading.Event()
        mf = [
            "77A 10 0E 62 F1 87 39 35 42",
            "77A 21 39 30 37 35 33 30 4E",
            "77A 22 20 AA AA AA AA AA AA",
        ]

        class GatePort(VirtualElmPort):
            def write(self, data: bytes) -> int:
                if not data.upper().startswith(b"AT"):
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("gate")
                return super().write(data)

        port = GatePort(ecu_map={"22F187": ath1_prompt(mf)})
        client = ElmClient(port, timeout_s=2.0)
        client.validate_adapter(2.0)
        client.configure_pair("710", "77A", client._now() + 5)
        err = []

        def first():
            try:
                client.request("22F187", client._now() + 5)
            except Exception as e:  # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(entered.wait(2))
        writes_mid = list(port.writes)
        with self.assertRaises(ElmError) as cm:
            client.request("22F187", client._now() + 5)
        self.assertEqual(str(cm.exception), "request-in-flight")
        self.assertEqual(port.writes, writes_mid)
        release.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertFalse(err)
        self.assertEqual(port.ecu_payloads, ["22F187"])
        client.close_restore()

    def test_request_blocks_configure_and_at_without_writes(self):
        entered = threading.Event()
        release = threading.Event()
        mf = [
            "77A 10 0E 62 F1 87 39 35 42",
            "77A 21 39 30 37 35 33 30 4E",
            "77A 22 20 AA AA AA AA AA AA",
        ]

        class GatePort(VirtualElmPort):
            def write(self, data: bytes) -> int:
                if not data.upper().startswith(b"AT"):
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("gate")
                return super().write(data)

        port = GatePort(ecu_map={"22F187": ath1_prompt(mf)})
        client = ElmClient(port, timeout_s=2.0)
        client.validate_adapter(2.0)
        client.configure_pair("710", "77A", client._now() + 5)
        err = []

        def first():
            try:
                client.request("22F187", client._now() + 5)
            except Exception as e:  # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(entered.wait(2))
        writes_mid = list(port.writes)
        with self.assertRaises(ElmError) as cm:
            client.configure_pair("7E0", "7E8", client._now() + 5)
        self.assertEqual(str(cm.exception), "io-in-flight")
        with self.assertRaises(ElmError) as cm2:
            client.send_at("ATI", client._now() + 2)
        self.assertEqual(str(cm2.exception), "io-in-flight")
        self.assertEqual(port.writes, writes_mid)
        release.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertFalse(err)
        client.close_restore()

    def test_drain_oserror_poisons(self):
        inner = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["77A 03 62 F1 87 AA AA AA AA"])})
        client = ElmClient(inner, timeout_s=2.0)
        client.validate_adapter(2.0)
        client.configure_pair("710", "77A", client._now() + 5)
        n = len(inner.writes)

        class Boom:
            def write(self, data: bytes) -> int:
                return inner.write(data)

            def read(self, size: int = 1) -> bytes:
                raise OSError("drain-fail")

            def close(self) -> None:
                inner.close()

        client.port = Boom()
        with self.assertRaises(ElmError) as cm:
            client.request("22F187", client._now() + 2)
        self.assertIn("port-io", str(cm.exception))
        self.assertTrue(client._poisoned)
        self.assertEqual(len(inner.writes), n)
        with self.assertRaises(ElmError) as cm2:
            client.request("22F187", client._now() + 2)
        self.assertIn("client-poisoned", str(cm2.exception))
        self.assertEqual(len(inner.writes), n)

    def test_short_write_poisons(self):
        class ShortWrite:
            def __init__(self):
                self.writes = []
                self.closed = False

            def write(self, data: bytes) -> int:
                self.writes.append(bytes(data))
                return 0

            def read(self, size: int = 1) -> bytes:
                return b""

            def close(self) -> None:
                self.closed = True

        port = ShortWrite()
        client = ElmClient(port, timeout_s=2.0)
        with self.assertRaises(ElmError) as cm:
            client.send_at("ATI")
        self.assertEqual(str(cm.exception), "short-write")
        self.assertTrue(client._poisoned)
        n = len(port.writes)
        with self.assertRaises(ElmError):
            client.send_at("ATI")
        self.assertEqual(len(port.writes), n)

    def test_timeout_poisons_reuse_same_did_leftover_and_fresh_port_reconnects(self):
        from scripts.diagnostics.bench import FakeClock

        clock = FakeClock()
        hang = VirtualElmPort(ecu_map={}, hang_unknown=True)
        client = ElmClient(hang, timeout_s=1.0, clock=clock.time, sleeper=clock.sleep)
        client.validate_adapter(2.0)
        client.configure_pair("710", "77A", clock.time() + 5)
        with self.assertRaises(ElmError):
            client.request("22F187", clock.time() + 1.0)
        self.assertTrue(client._poisoned)
        writes = list(hang.writes)
        with self.assertRaises(ElmError) as cm:
            client.request("22F187", clock.time() + 1.0)
        self.assertIn("client-poisoned", str(cm.exception))
        self.assertEqual(hang.writes, writes)

        leftover = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["77A 03 7F 22 31 AA AA AA AA"])})
        c2 = ElmClient(leftover, timeout_s=2.0)
        c2.validate_adapter(2.0)
        c2.configure_pair("710", "77A", c2._now() + 5)
        leftover._rx.extend(ath1_prompt(["77A 10 0E 62 F1 87 39 35 42", "77A 21 39 30 37 35 33 30 4E", "77A 22 20 AA AA AA AA AA AA"]))
        n_ecu = len(leftover.ecu_payloads)
        with self.assertRaises(ElmError) as cm2:
            c2.request("22F187", c2._now() + 2)
        self.assertEqual(str(cm2.exception), "desynchronized-rx")
        self.assertEqual(len(leftover.ecu_payloads), n_ecu)
        self.assertTrue(c2._poisoned)

        mf = [
            "77A 10 0E 62 F1 87 39 35 42",
            "77A 21 39 30 37 35 33 30 4E",
            "77A 22 20 AA AA AA AA AA AA",
        ]
        fresh = VirtualElmPort(ecu_map={"22F187": ath1_prompt(mf)})
        c3 = ElmClient(fresh, timeout_s=2.0)
        c3.validate_adapter(2.0)
        c3.configure_pair("710", "77A", c3._now() + 5)
        out = c3.request("22F187", c3._now() + 2)
        self.assertTrue(out["ok"], out)
        self.assertTrue((out.get("payload_hex") or "").upper().startswith("62F187"))
        c3.close_restore()
        self.assertTrue(fresh.closed)


class TestNoSerialOnRunReadInjected(unittest.TestCase):
    def test_injected_port_does_not_load_serial(self):
        sys.modules.pop("serial", None)
        from scripts.diagnostics.live import run_read

        port = VirtualElmPort(ecu_map={"1A9F": ath1_prompt(["7E8 06 5A 9F 50 32 30 30 AA"])})
        with mock.patch.dict(sys.modules, {"serial": None}):
            out = run_read("porsche-981-2014-dme", "dme-dsn", port=port, budget_s=10, out_dir=Path(tempfile.mkdtemp()))
        self.assertTrue(out["ok"])
        self.assertNotIn("serial", sys.modules)


if __name__ == "__main__":
    unittest.main()
