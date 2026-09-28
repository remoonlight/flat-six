"""Fake-port only. Never enumerate or open real serial."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.diagnostics import adapter_check as ac
from scripts.diagnostics.adapter_check import (
    ALLOWED_AT,
    AtOnlyGuard,
    main,
    run_adapter_check,
)
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.session_simulator import SessionSimPort
from scripts.diagnostics.sessions import _FileLock


class _Sink:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


class _BenchSimPort(SessionSimPort):
    def write(self, data: bytes) -> int:
        if data == b"ATWS\r":
            self._rx.extend(b"\r\rELM327 v2.3\r\r>")
            return len(data)
        return super().write(data)


class _Wrap:
    def __init__(self, fail: bytes | None = None, fail_rx: bytes = b"?\r\r>"):
        self.inner = _BenchSimPort("porsche-981-2014-dme")
        self.fail = fail
        self.fail_rx = fail_rx
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.fail is not None and data == self.fail:
            self.inner._rx.extend(self.fail_rx)
            return len(data)
        return self.inner.write(data)

    def read(self, size: int = 1) -> bytes:
        return self.inner.read(size)

    def close(self) -> None:
        self.closed = True
        self.inner.close()


class _HangAfterQueries:
    def __init__(self):
        self.inner = SessionSimPort("porsche-981-2014-dme")
        self.closed = False

    def write(self, data: bytes) -> int:
        if data in (b"ATI\r", b"ATDPN\r", b"ATRV\r"):
            return self.inner.write(data)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return self.inner.read(size)

    def close(self) -> None:
        self.closed = True
        self.inner.close()


class _HangAll:
    def __init__(self):
        self.closed = False

    def write(self, data: bytes) -> int:
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def _run(factory, tmp):
    return run_adapter_check(
        confirmed_powered_bench=True,
        out_dir=Path(tmp),
        port_factory=factory,
        serial_module=object(),
        list_ports=object(),
    )


class _LockIsolated(unittest.TestCase):
    def setUp(self):
        self._lock_tmp = tempfile.TemporaryDirectory()
        self.lock_root = Path(self._lock_tmp.name)
        patch = mock.patch.object(ac, "DEFAULT_ARTIFACT_ROOT", self.lock_root)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._lock_tmp.cleanup)

    def assertLockFree(self):
        self.assertFalse((self.lock_root / ".exclusive-live").exists())


class TestAdapterCheck(_LockIsolated):
    def test_guard_blocks_before_write(self):
        sink = _Sink()
        g = AtOnlyGuard(sink, [])
        for payload in (
            b"ATSP6\r",
            b"ATMA\r",
            b"ATWR\r",
            b"ATPP 0C SV\r",
            b"1003\r",
            b"22 F1 87\r",
            b"03\r",
            b"ATST FF\r",
        ):
            with self.assertRaises(ElmError) as ctx:
                g.write(payload)
            self.assertIn("at-only-denied", str(ctx.exception))
        self.assertEqual(sink.writes, [])
        self.assertIn(b"ATI\r", ALLOWED_AT)
        self.assertEqual(g.write(b"ATI\r"), 4)
        self.assertEqual(sink.writes, [b"ATI\r"])

    def test_success_session_sim(self):
        n = {"opens": 0}

        def factory():
            n["opens"] += 1
            return _BenchSimPort("porsche-981-2014-dme")

        with tempfile.TemporaryDirectory() as tmp:
            out = _run(factory, tmp)
            self.assertTrue(out["ok"], out)
            self.assertEqual(n["opens"], 3)
            self.assertEqual(out["kind"], "powered-adapter-only")
            self.assertEqual(out["ecuRequestsSent"], 0)
            self.assertIs(out["canWiresConnected"], False)
            self.assertIs(out["canCommunicationVerified"], False)
            self.assertIs(out["isotpVerified"], False)
            self.assertIs(out["independentLiveVerified"], False)
            self.assertIsNone(out["writePayload"])
            self.assertEqual(out["reopenCount"], 2)
            self.assertEqual(out["voltageString"], "12.0V")
            self.assertFalse(out["restoration"]["fullStateRestoreClaimed"])
            self.assertTrue(out["adapterCommandsVerified"]["ATCSM0"])
            self.assertTrue(out["lockReleased"])
            self.assertLockFree()
            man = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            raw = json.loads((Path(tmp) / "raw-traffic.json").read_text(encoding="utf-8"))
            self.assertEqual(man["rawSha256"], out["rawSha256"])
            self.assertEqual(man["lockReleased"], out["lockReleased"])
            self.assertTrue(raw)
            self.assertTrue(all("utc" in e for e in raw))
            self.assertTrue(out["commandRecords"])
            self.assertTrue(all(r.get("success") for r in out["commandRecords"]))

    def test_unsupported_config_retained(self):
        ports = []

        def factory():
            p = _Wrap(fail=b"ATCSM0\r")
            ports.append(p)
            return p

        with tempfile.TemporaryDirectory() as tmp:
            out = _run(factory, tmp)
            self.assertFalse(out["ok"])
            self.assertIn("at-failed:ATCSM0", out["error"] or "")
            self.assertIs(out["adapterCommandsVerified"].get("ATCSM0"), False)
            self.assertTrue((Path(tmp) / "raw-traffic.json").is_file())
            self.assertTrue(ports[0].closed)
            self.assertTrue(out["lockReleased"])
            self.assertLockFree()

    def test_cleanup_failure_closed_lock_released(self):
        ports = []

        def factory():
            p = _Wrap(fail=b"ATPC\r", fail_rx=b"ERROR\r\r>")
            ports.append(p)
            return p

        with tempfile.TemporaryDirectory() as tmp:
            out = _run(factory, tmp)
            self.assertFalse(out["ok"])
            self.assertTrue(any(p.closed for p in ports))
            self.assertTrue(out["lockReleased"])
            self.assertLockFree()
            self.assertTrue((Path(tmp) / "manifest.json").is_file())
            self.assertIs(out["adapterCommandsVerified"].get("ATPC"), False)

    def test_invalid_query_not_accepted(self):
        ports = []

        def factory():
            p = _Wrap(fail=b"ATI\r", fail_rx=b"?\r\r>")
            ports.append(p)
            return p

        with tempfile.TemporaryDirectory() as tmp:
            out = _run(factory, tmp)
            self.assertFalse(out["ok"])
            self.assertIs(out["adapterCommandsVerified"].get("ATI"), False)
            ati_recs = [r for r in out["commandRecords"] if r.get("command") == "ATI"]
            self.assertTrue(ati_recs)
            self.assertFalse(ati_recs[0]["success"])
            self.assertTrue(ports[0].closed)
            self.assertTrue(out["lockReleased"])

    def test_initial_validation_failure_closes(self):
        ports = []

        def factory():
            p = _Wrap(fail=b"ATDPN\r", fail_rx=b"?\r\r>")
            ports.append(p)
            return p

        with tempfile.TemporaryDirectory() as tmp:
            out = _run(factory, tmp)
            self.assertFalse(out["ok"])
            self.assertTrue(ports[0].closed)
            self.assertTrue(out["lockReleased"])
            self.assertIs(out["adapterCommandsVerified"].get("ATDPN"), False)
            self.assertIsNotNone(out.get("restoration"))

    def test_reopen_failure_partial(self):
        n = {"i": 0}

        def factory():
            n["i"] += 1
            if n["i"] == 2:
                raise OSError("fake-reopen-fail")
            return _BenchSimPort("porsche-981-2014-dme")

        with tempfile.TemporaryDirectory() as tmp:
            out = _run(factory, tmp)
            self.assertFalse(out["ok"])
            self.assertEqual(out["reopenCount"], 1)
            self.assertIn("fake-reopen-fail", out["error"] or "")
            self.assertTrue((Path(tmp) / "raw-traffic.json").is_file())
            self.assertTrue(out["lockReleased"])
            self.assertLockFree()

    def test_missing_confirmation_never_enumerates_or_opens(self):
        listed = []
        opened = []

        class LP:
            def comports(self):
                listed.append(1)
                return []

        def factory():
            opened.append(1)
            raise AssertionError("factory-must-not-run")

        with tempfile.TemporaryDirectory() as tmp:
            out = run_adapter_check(
                confirmed_powered_bench=False,
                out_dir=Path(tmp),
                port_factory=factory,
                list_ports=LP(),
                serial_module=mock.Mock(side_effect=AssertionError("serial")),
            )
            self.assertEqual(out["error"], "powered-bench-confirmation-required")
            self.assertFalse(out["ok"])
            self.assertEqual(listed, [])
            self.assertEqual(opened, [])
            self.assertFalse(out["lockReleased"])
            self.assertLockFree()

    def test_cli_missing_flag_no_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.diagnostics.adapter_check.run_adapter_check") as run:
                rc = main(["--out", tmp])
            self.assertEqual(rc, 2)
            run.assert_not_called()

    def test_existing_artifacts_preserved_no_open(self):
        opened = []

        def factory():
            opened.append(1)
            raise AssertionError("factory-must-not-run")

        with tempfile.TemporaryDirectory() as tmp:
            kept = Path(tmp) / "manifest.json"
            kept.write_text("KEEP-ME\n", encoding="utf-8")
            out = _run(factory, tmp)
            self.assertEqual(out["error"], "output-artifacts-exist")
            self.assertFalse(out["ok"])
            self.assertEqual(opened, [])
            self.assertFalse(out["lockReleased"])
            self.assertEqual(kept.read_text(encoding="utf-8"), "KEEP-ME\n")
            self.assertLockFree()

    def test_existing_artifacts_preserved_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            kept = Path(tmp) / "raw-traffic.json"
            kept.write_bytes(b"KEEP")
            factory = mock.Mock()
            out = run_adapter_check(confirmed_powered_bench=False, out_dir=Path(tmp), port_factory=factory)
            self.assertEqual(out["error"], "output-artifacts-exist")
            self.assertEqual(kept.read_bytes(), b"KEEP")
            factory.assert_not_called()

    def test_bad_protocol_or_reset_response_fails(self):
        for cmd, reply in ((b"ATDPN\r", b"OK\r>"), (b"ATWS\r", b"OK\r>")):
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as tmp:
                ports = []
                def factory():
                    p = _Wrap(fail=cmd, fail_rx=reply)
                    ports.append(p)
                    return p
                out = _run(factory, tmp)
                self.assertFalse(out["ok"])
                self.assertIs(out["adapterCommandsVerified"][cmd.decode().strip()], False)
                self.assertTrue(all(p.closed for p in ports))
                if cmd == b"ATDPN\r":
                    self.assertIn("response-invalid", out["error"] or "")
                    self.assertNotIn("ATE0", out["adapterCommandsVerified"])
                else:
                    self.assertTrue(any("response-invalid:ATWS" in e for e in out["restoration"]["errors"]))

    def test_close_failure_not_reported_success(self):
        class CloseErrorPort(_BenchSimPort):
            def close(self):
                super().close()
                raise OSError("fake-close-error")
        with tempfile.TemporaryDirectory() as tmp:
            out = _run(lambda: CloseErrorPort("porsche-981-2014-dme"), tmp)
            self.assertFalse(out["ok"])
            self.assertIn("fake-close-error", out["error"])
            self.assertTrue(out["lockReleased"])
            self.assertTrue(all(not c["portClosed"] for c in out["connections"]))

    def test_command_deadline_clamped(self):
        clock = _Clock()
        ports = []

        def factory():
            p = _HangAfterQueries()
            ports.append(p)
            return p

        with (
            mock.patch.object(ac.time, "monotonic", clock.monotonic),
            mock.patch("scripts.diagnostics.elm.time.monotonic", clock.monotonic),
            mock.patch("scripts.diagnostics.elm.time.sleep", clock.sleep),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                out = _run(factory, tmp)
        self.assertFalse(out["ok"])
        self.assertLess(clock.t - 1000.0, 40.0)
        ate0 = [r for r in out["commandRecords"] if r.get("command") == "ATE0"]
        self.assertTrue(ate0)
        self.assertFalse(ate0[0]["success"])
        self.assertIn("prompt-timeout", ate0[0].get("error") or "")
        self.assertTrue(ports[0].closed)
        self.assertTrue(out["lockReleased"])

    def test_deadline_stops_new_opens(self):
        clock = _Clock()
        n = {"opens": 0}
        ports = []

        def factory():
            n["opens"] += 1
            p = _HangAll()
            ports.append(p)
            return p

        with (
            mock.patch.object(ac, "TOTAL_BUDGET_S", 2.0),
            mock.patch.object(ac.time, "monotonic", clock.monotonic),
            mock.patch("scripts.diagnostics.elm.time.monotonic", clock.monotonic),
            mock.patch("scripts.diagnostics.elm.time.sleep", clock.sleep),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                out = _run(factory, tmp)
        self.assertEqual(n["opens"], 1)
        self.assertTrue(out["error"])
        self.assertTrue(ports[0].closed)
        self.assertTrue(out["lockReleased"])
        self.assertLess(len(out["connections"]), 3)


class TestLiveLockNotStolen(_LockIsolated):
    def test_stale_live_lock_not_stolen(self):
        held = _FileLock("live", ac.DEFAULT_ARTIFACT_ROOT)
        held.acquire()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = _run(lambda: SessionSimPort("porsche-981-2014-dme"), tmp)
                man = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(out["error"], "session-lock-busy")
            self.assertFalse(out["ok"])
            self.assertFalse(out["lockReleased"])
            self.assertTrue((self.lock_root / ".exclusive-live").exists())
            self.assertFalse(man["lockReleased"])
        finally:
            held.release()
        self.assertLockFree()


if __name__ == "__main__":
    unittest.main()
