from __future__ import annotations

import io
import json
import threading
import unittest
from unittest import mock

from scripts.diagnostics.connection import (
    ATRV_INTERVAL_S,
    DEVICE_ID_RE,
    collect_snapshot,
    classify_brand,
    enumerate_devices,
    parse_voltage_volts,
    probe_adapter,
    resolve_port,
    run_voltage_monitor,
    stdio_loop,
    valid_device_id,
    _wait_interval,
)
from scripts.diagnostics.elm import ElmError, adapter_identity_ok
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.tests.test_diagnostics import FakePort, _ok_prompt

HOST_SERIAL = {
    "device": "COM9",
    "name": "蓝牙链接上的标准串行 (COM9)",
    "description": "蓝牙链接上的标准串行 (COM9)",
    "pnp": r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&10BB0FC2&0&0425E85BD4CB_C00000000",
}
AMT = {
    "device": "COM3",
    "name": "Intel(R) Active Management Technology - SOL (COM3)",
    "description": "Intel(R) AMT SOL",
    "pnp": r"PCI\VEN_8086&DEV_0000\AMT_SOL",
}
HOST_BT = {
    "status": "OK",
    "friendlyName": "vLinker FS 11436",
    "instanceId": r"BTHENUM\DEV_0425E85BD4CB\7&25D7CD33&0&BLUETOOTHDEVICE_0425E85BD4CB",
}
VLIKER = "bt:0425E85BD4CB"
MX = "bt:AABBCCDDEEFF"
MX_SERIAL = {**HOST_SERIAL, "device": "COM12", "pnp": HOST_SERIAL["pnp"].replace("0425E85BD4CB", "AABBCCDDEEFF")}
MX_BT = {**HOST_BT, "friendlyName": "OBDLink MX+", "instanceId": r"BTHENUM\DEV_AABBCCDDEEFF\x"}


def _snap(**kw):
    base = {
        "platform": "win32",
        "serial": kw.get("serial", [HOST_SERIAL, AMT]),
        "bluetooth": kw.get("bluetooth", [HOST_BT]),
        "pyserial": kw.get("pyserial", []),
        "errors": kw.get("errors", []),
    }
    return base


class TestParseVoltage(unittest.TestCase):
    def test_bounds_and_zero(self):
        self.assertEqual(parse_voltage_volts("ATRV\r12.6V\r>"), 12.6)
        self.assertIsNone(parse_voltage_volts("0V"))
        self.assertIsNone(parse_voltage_volts("0.0V"))
        self.assertIsNone(parse_voltage_volts("NO DATA"))
        self.assertIsNone(parse_voltage_volts("99V"))
        self.assertIsNone(parse_voltage_volts("-12.6V"))
        self.assertIsNone(parse_voltage_volts("12.6V 13.0V"))
        self.assertIsNone(parse_voltage_volts("bad12.6V"))
        self.assertIsNone(parse_voltage_volts("1.12.6V"))
        self.assertIsNone(parse_voltage_volts(""))
        self.assertTrue(valid_device_id(VLIKER))
        self.assertFalse(valid_device_id("COM9"))
        self.assertTrue(DEVICE_ID_RE.fullmatch(VLIKER))


class TestEnumerate(unittest.TestCase):
    def test_mx_plus_and_vlinker_have_independent_bluetooth_identities(self):
        snap = _snap(serial=[HOST_SERIAL, MX_SERIAL, AMT], bluetooth=[HOST_BT, MX_BT], pyserial=[MX_SERIAL])
        out = enumerate_devices(snap)
        mx = next(d for d in out["devices"] if d["id"] == MX)
        self.assertEqual(mx["brand"], "OBDLink MX+")
        self.assertTrue(mx["available"])
        self.assertTrue(mx["paired"])
        self.assertFalse(out["openedPort"])
        self.assertEqual(resolve_port(MX, snap), "COM12")
        self.assertEqual(resolve_port(VLIKER, snap), "COM9")
        moved = _snap(serial=[{**MX_SERIAL, "device": "COM15"}], bluetooth=[MX_BT])
        self.assertEqual(resolve_port(MX, moved), "COM15")

    def test_mx_plus_without_spp_cannot_use_another_adapters_port(self):
        snap = _snap(bluetooth=[HOST_BT, MX_BT])
        mx = next(d for d in enumerate_devices(snap)["devices"] if d["id"] == MX)
        self.assertFalse(mx["available"])
        with self.assertRaisesRegex(ElmError, "device-port-unavailable"):
            resolve_port(MX, snap)

    def test_model_classification_does_not_label_other_obdlinks_mx_plus(self):
        self.assertEqual(classify_brand("OBDLink MX+", None), "OBDLink MX+")
        self.assertEqual(classify_brand("obdlink mx + (COM12)", None), "OBDLink MX+")
        for name in ("OBDLink MX", "OBDLink LX", "OBDLink CX", "OBDLink"):
            self.assertEqual(classify_brand(name, None), "unresolved")

    def test_ambiguity_stays_rejected_after_duplicate_source_rows(self):
        other_port = {**HOST_SERIAL, "device": "COM10"}
        for rows in ([HOST_SERIAL, other_port], [other_port, HOST_SERIAL]):
            snap = _snap(serial=rows, pyserial=[HOST_SERIAL, other_port, HOST_SERIAL])
            rec = enumerate_devices(snap)["devices"][0]
            self.assertFalse(rec["available"])
            with self.assertRaises(ElmError):
                resolve_port(VLIKER, snap)

    def test_host_vlinker_com9_not_amt(self):
        out = enumerate_devices(_snap())
        ids = [d["id"] for d in out["devices"]]
        self.assertEqual(ids, [VLIKER])
        rec = out["devices"][0]
        self.assertEqual(rec["comPort"], "COM9")
        self.assertEqual(rec["brand"], "vLinker")
        self.assertEqual(rec["name"], "vLinker FS 11436")
        self.assertTrue(rec["available"])
        self.assertTrue(rec["paired"])
        self.assertNotEqual(rec["osStatus"], "connected")
        self.assertFalse(out["openedPort"])
        self.assertEqual(resolve_port(VLIKER, _snap()), "COM9")

    def test_paired_no_com(self):
        out = enumerate_devices(_snap(serial=[], pyserial=[]))
        rec = out["devices"][0]
        self.assertTrue(rec["paired"])
        self.assertFalse(rec["available"])
        self.assertIn("串口", rec["guidance"])
        with self.assertRaises(ElmError) as ctx:
            resolve_port(VLIKER, _snap(serial=[], pyserial=[]))
        self.assertIn("device-port-unavailable", str(ctx.exception))

    def test_stale_identity_never_reroutes(self):
        other = dict(HOST_SERIAL)
        other["pnp"] = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&10BB0FC2&0&AABBCCDDEEFF_C00000000"
        snap = _snap(
            serial=[other, AMT],
            bluetooth=[{**HOST_BT, "friendlyName": "OBDLink MX+", "instanceId": r"BTHENUM\DEV_AABBCCDDEEFF\x"}],
        )
        with self.assertRaises(ElmError) as ctx:
            resolve_port(VLIKER, snap)
        self.assertIn("device-identity-missing", str(ctx.exception))
        self.assertEqual(resolve_port("bt:AABBCCDDEEFF", snap), "COM9")

    def test_empty_and_errors(self):
        empty = enumerate_devices({"platform": "win32", "serial": [], "bluetooth": [], "pyserial": [], "errors": []})
        self.assertEqual(empty["devices"], [])
        miss = enumerate_devices(
            {"platform": "win32", "serial": [], "bluetooth": [], "pyserial": [], "errors": ["pyserial-missing"]}
        )
        self.assertIn("pyserial-missing", miss["errors"])
        unsup = collect_snapshot(platform="darwin")
        self.assertIn("unsupported-platform", unsup["errors"])
        broken = enumerate_devices(collect_snapshot(platform="linux"))
        self.assertEqual(broken["devices"], [])

    def test_unresolved_needs_choice(self):
        bt = {
            "status": "OK",
            "friendlyName": "Bluetooth Device",
            "instanceId": r"BTHENUM\DEV_0425E85BD4CB\7&25D7CD33&0&BLUETOOTHDEVICE_0425E85BD4CB",
        }
        rec = enumerate_devices(_snap(bluetooth=[bt]))["devices"][0]
        self.assertEqual(rec["brand"], "vLinker")  # known MAC

    def test_duplicate_com_rejected(self):
        a = dict(HOST_SERIAL)
        b = dict(HOST_SERIAL)
        b["pnp"] = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&10BB0FC2&0&AABBCCDDEEFF_C00000000"
        snap = _snap(
            serial=[a, b],
            bluetooth=[HOST_BT, {**HOST_BT, "friendlyName": "OBDLink MX+", "instanceId": r"BTHENUM\DEV_AABBCCDDEEFF\x"}],
        )
        out = enumerate_devices(snap)
        mx = [d for d in out["devices"] if d["id"] == "bt:AABBCCDDEEFF"]
        self.assertTrue(mx)
        self.assertFalse(mx[0]["available"])
        vl = [d for d in out["devices"] if d["id"] == VLIKER]
        self.assertTrue(vl)
        self.assertFalse(vl[0]["available"])
        usb = {"device": "COM4", "name": "USB Serial Device", "description": "USB Serial", "pnp": r"USB\VID_1234"}
        out = enumerate_devices(_snap(serial=[usb, AMT], bluetooth=[]))
        self.assertEqual(out["devices"], [])


class TestProbeAndSession(unittest.TestCase):
    def test_atrv_probe_no_ecu(self):
        port = FakePort([_ok_prompt("ATRV\r13.1V\r\r")])
        out = probe_adapter(VLIKER, kind="voltage", port=port)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["volts"], 13.1)
        self.assertEqual(out["voltageSource"], "atrv")
        self.assertFalse(out["simulation"])
        self.assertTrue(any(w == b"ATRV\r" for w in port.writes))
        self.assertFalse(any(b"14FF" in w or b"10 89" in w for w in port.writes))

    def test_simulation_does_not_open(self):
        with mock.patch("scripts.diagnostics.sessions._open_live_port", side_effect=AssertionError("hw")):
            out = run_session(
                profile_id="porsche-981-2014-dme",
                mode="simulation",
                device_id=VLIKER,
                artifact_root=__import__("pathlib").Path(__import__("tempfile").mkdtemp()),
            )
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["simulation"])

    def test_live_stale_does_not_open_other(self):
        class Boom:
            def Serial(self, *a, **k):
                raise AssertionError("serial")

        snap = _snap(serial=[AMT], bluetooth=[HOST_BT], pyserial=[])
        with self.assertRaises(ElmError):
            resolve_port(VLIKER, snap)
        out = run_session(
            profile_id="porsche-981-2014-dme",
            mode="live",
            confirmed_read_only=True,
            x431_inactive=True,
            device_id=VLIKER,
            discovery=snap,
            serial_module=Boom(),
            artifact_root=__import__("pathlib").Path(__import__("tempfile").mkdtemp()),
            budget_s=5,
        )
        self.assertFalse(out["ok"])
        self.assertIn("device-port-unavailable", out["error"] or "")

    def test_stdio_list_fixture_not_used(self):
        stdin = io.StringIO(json.dumps({"action": "list", "port": "COM9"}) + "\n")
        stdout = io.StringIO()
        rc = stdio_loop(stdin, stdout)
        self.assertEqual(rc, 2)
        self.assertIn("unexpected-keys", stdout.getvalue())

    def test_concurrency_gate(self):
        lock = threading.Lock()
        order = []

        def occupy():
            with lock:
                order.append("session")
                threading.Event().wait(0.05)

        t = threading.Thread(target=occupy)
        t.start()
        got = lock.acquire(blocking=False)
        self.assertFalse(got)
        t.join()
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_adapter_identity_mx(self):
        self.assertTrue(adapter_identity_ok("OBDLink MX+"))
        self.assertTrue(adapter_identity_ok("ELM327 v2.3"))
        self.assertFalse(adapter_identity_ok("UNKNOWN"))


class RepeatAtrvPort(FakePort):
    def __init__(self, identity="ELM327 v2.3"):
        super().__init__([])
        self.identity = identity
        self.serial_opens = 1

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ElmError("disconnect")
        self.writes.append(bytes(data))
        cmd = data.strip().upper()
        if cmd == b"ATI":
            self._rx.extend(_ok_prompt(self.identity + "\r\r"))
        elif cmd == b"ATDPN":
            self._rx.extend(_ok_prompt("A6\r\r"))
        elif cmd == b"ATRV":
            self._rx.extend(_ok_prompt("12.6V\r\r"))
        elif cmd == b"ATPC":
            self._rx.extend(_ok_prompt("OK\r\r"))
        return len(data)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class TestVoltageMonitor(unittest.TestCase):
    def test_mx_plus_spp_monitor_and_session_use_selected_com_only(self):
        from scripts.diagnostics.sessions import _open_live_port

        port = RepeatAtrvPort("STN2100 v5.6.5")
        serial = mock.Mock()
        serial.Serial.return_value = port
        snap = _snap(serial=[HOST_SERIAL, MX_SERIAL], bluetooth=[HOST_BT, MX_BT])
        self.assertIs(_open_live_port(serial_module=serial, device_id=MX, discovery=snap), port)
        serial.Serial.assert_called_once_with("COM12", 115200, timeout=0.05, exclusive=True)
        serial.reset_mock()
        stdout = io.StringIO()
        clock = FakeClock()
        rc = run_voltage_monitor(MX, serial_module=serial, snapshot=snap, stdout=stdout,
                                 clock=clock.now, sleep_fn=clock.sleep, max_samples=2)
        self.assertEqual(rc, 0, stdout.getvalue())
        serial.Serial.assert_called_once_with("COM12", 115200, timeout=0.05, exclusive=True)
        rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
        readings = [r for r in rows if r.get("type") in ("handshake", "reading")]
        self.assertEqual(len(readings), 2)
        self.assertTrue(all(r["deviceId"] == MX and r["volts"] == 12.6 for r in readings))
        self.assertTrue(port.closed)
        self.assertTrue(all(w.strip() in (b"ATI", b"ATDPN", b"ATRV", b"ATPC") for w in port.writes))

    def test_one_open_identity_once_then_atrv(self):
        port = RepeatAtrvPort()

        class SerialMod:
            def __init__(self):
                self.opens = 0
                self.port = port

            def Serial(self, *a, **k):
                self.opens += 1
                return self.port

        serial = SerialMod()
        clock = FakeClock()
        stdout = io.StringIO()
        rc = run_voltage_monitor(
            VLIKER,
            serial_module=serial,
            snapshot=_snap(),
            stdout=stdout,
            clock=clock.now,
            sleep_fn=clock.sleep,
            interval_s=ATRV_INTERVAL_S,
            max_samples=3,
        )
        self.assertEqual(rc, 0, stdout.getvalue())
        self.assertEqual(serial.opens, 1)
        cmds = [w.strip() for w in port.writes]
        self.assertEqual(cmds.count(b"ATI"), 1)
        self.assertEqual(cmds.count(b"ATDPN"), 1)
        self.assertEqual(cmds.count(b"ATRV"), 3)
        self.assertTrue(port.closed)
        self.assertEqual(clock.sleeps, [2.0, 2.0])
        lines = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
        kinds = [x.get("type") for x in lines]
        self.assertEqual(kinds[0], "handshake")
        self.assertEqual(sum(1 for k in kinds if k == "reading"), 2)
        self.assertTrue(all(x.get("at") for x in lines if x.get("type") in ("handshake", "reading")))
        self.assertTrue(all(x.get("serialOpens") == 1 for x in lines if "serialOpens" in x))
        self.assertFalse(any(b"14FF" in w or b"10 89" in w for w in port.writes))

    def test_wait_interval_interruptible(self):
        import time

        stop = threading.Event()
        t0 = time.monotonic()
        _wait_interval(0.05, stop, time.sleep, time.sleep)
        self.assertGreaterEqual(time.monotonic() - t0, 0.04)
        stop.set()
        t1 = time.monotonic()
        _wait_interval(2.0, stop, time.sleep, time.sleep)
        self.assertLess(time.monotonic() - t1, 0.3)

    def test_stop_before_open(self):
        stop = threading.Event()
        stop.set()

        class Boom:
            def Serial(self, *a, **k):
                raise AssertionError("opened")

        stdout = io.StringIO()
        rc = run_voltage_monitor(VLIKER, serial_module=Boom(), snapshot=_snap(), stdout=stdout, stop_event=stop)
        self.assertEqual(rc, 0)
        self.assertIn("stopped", stdout.getvalue())

    def test_cancel_cleanup(self):
        port = RepeatAtrvPort()
        stop = threading.Event()
        clock = FakeClock()

        def sleep(s):
            clock.sleep(s)
            stop.set()

        stdout = io.StringIO()
        rc = run_voltage_monitor(VLIKER, port=port, stdout=stdout, clock=clock.now, sleep_fn=sleep, stop_event=stop, max_samples=9)
        self.assertIn(rc, (0, 1))
        self.assertTrue(port.closed)
        self.assertEqual([w.strip() for w in port.writes].count(b"ATRV"), 1)

    def test_deny_live_stdio(self):
        stdin = io.StringIO(json.dumps({"action": "monitor", "deviceId": VLIKER}) + "\n")
        stdout = io.StringIO()
        with mock.patch.dict("os.environ", {"PORSCHE981_HEADLESS": "1"}):
            rc = stdio_loop(stdin, stdout)
        self.assertEqual(rc, 2)
        self.assertIn("live-probe-disabled", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
