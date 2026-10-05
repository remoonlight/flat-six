"""Synthetic native events; no vendor DLL, USB, network or ECU access."""
import io
import hashlib
import json
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.connection import enumerate_devices, resolve_port, valid_device_id
from scripts.diagnostics.elm import ElmError, parse_ath1_response
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.vnci import DpuClient, NativeDpu, UNDEF, discover, module_device, require_matching_firmware, run_monitor, vendor_root, verify_vendor_files

ID = "vnci:10001"
MODULE = {"type": 14, "handle": 123, "status": 0x8063,
          "name": "ModuleName='VAS6154A' SerialNumber='10001' IP='192.168.13.69' ConnectionType='USB'"}


class FakeNative:
    def __init__(self, *, voltage=12.6, rows=None, response=b'\x41\x00\x18\x1A\x80\x00'):
        self.rows = [MODULE] if rows is None else rows
        self.volts = voltage
        self.response = response
        self.sent = []
        self.links = []
        self.events = deque()
        self.connected = []
        self.cancelled = []
        self.closed = 0
        self.close_errors = []
        self.make_events = None

    def modules(self): return self.rows
    def check_firmware(self, device): pass
    def connect_module(self, handle): self.connected.append(handle)
    def voltage(self): return self.volts
    def create_link(self, protocol, tx, rx):
        self.links.append((protocol, tx, rx))
        self.events.append({"type": 0x1301, "cop": UNDEF, "value": 0x8051})
    def event(self): return self.events.popleft() if self.events else None
    def cancel(self, cop): self.cancelled.append(cop)
    def close(self):
        self.closed += 1
        return {"errors": self.close_errors, "ecuRestorationProven": False}
    def start(self, payload):
        self.sent.append(payload.hex().upper())
        cop = len(self.sent)
        if self.make_events is not None:
            self.events.extend(self.make_events(payload, cop))
        elif self.response is not None:
            self.events.extend(self.reply(self.response, cop))
        return cop
    @staticmethod
    def reply(payload, cop):
        return [{"type": 0x1300, "cop": cop, "payload": payload, "timestamp": 1000,
                 "unique_id": 1, "flags": b'\0\0\0\0'},
                {"type": 0x1301, "cop": cop, "value": 0x8012}]


def configured(native=None):
    native = native or FakeNative()
    client = DpuClient(ID, native=native)
    client.validate_adapter()
    client.configure_standard_engine(time.monotonic() + 2)
    return client, native


class VnciTests(unittest.TestCase):
    def test_incomplete_or_drifted_support_stops_before_native_library_load(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            dll = root / 'PDUAPI_VW.dll'
            dll.write_bytes(b'qualified-library-fixture')
            expected = hashlib.sha256(dll.read_bytes()).hexdigest()
            support = root / 'VAS6154' / 'config.para'
            support.parent.mkdir()
            original = b'qualified-support-fixture'
            manifest = {'VAS6154/config.para': hashlib.sha256(original).hexdigest()}
            with patch('scripts.diagnostics.vnci.LIBRARY_SHA256', expected), \
                    patch('scripts.diagnostics.vnci.SUPPORT_SHA256', manifest), \
                    patch('scripts.diagnostics.vnci.sys.platform', 'win32'), \
                    patch('scripts.diagnostics.vnci.c.WinDLL', create=True) as loader:
                with self.assertRaisesRegex(ElmError, 'driver-support-missing'):
                    NativeDpu(root)
                support.write_bytes(b'wrong-version')
                with self.assertRaisesRegex(ElmError, 'driver-support-not-qualified'):
                    NativeDpu(root)
                loader.assert_not_called()
                support.write_bytes(original)
                verify_vendor_files(root)
                dll.write_bytes(b'wrong-library')
                with self.assertRaisesRegex(ElmError, 'driver-version-not-qualified'):
                    verify_vendor_files(root)

    def test_vendor_root_follows_installed_library_or_explicit_host_configuration(self):
        relative = Path('.local/vnci-support/vendor/VW_PDUAPI_OS')
        self.assertEqual(vendor_root({}), Path(__file__).resolve().parents[3] / relative)
        with tempfile.TemporaryDirectory() as scratch:
            library = Path(scratch) / 'user data' / 'diagnostic-library'
            explicit = Path(scratch) / 'private driver'
            self.assertEqual(vendor_root({'PORSCHE981_DEFINITION_ROOT': str(library)}), library / relative)
            self.assertEqual(vendor_root({'PORSCHE981_DEFINITION_ROOT': str(library),
                                         'PORSCHE981_VNCI_ROOT': str(explicit)}), explicit.resolve())

    def test_discovery_reports_cleanup_failure(self):
        native = FakeNative()
        native.close_errors = ['destruct failed']
        with patch('scripts.diagnostics.vnci.sys.platform', 'win32'), \
                patch('scripts.diagnostics.vnci.Path.is_file', return_value=True), \
                patch('scripts.diagnostics.vnci.NativeDpu', return_value=native):
            with self.assertRaisesRegex(ElmError, 'discovery-cleanup-failed'):
                discover()
        self.assertEqual(native.closed, 1)
        self.assertEqual(native.connected, [])
        self.assertEqual(native.sent, [])

    def test_zero_voltage_retains_native_reading_without_claiming_vehicle_power(self):
        native = FakeNative(voltage=0)
        client = DpuClient(ID, native=native)
        self.assertIsNone(client.validate_adapter()['volts'])
        self.assertEqual(client.raw_log[-1]['reportedVolts'], 0)
        self.assertIsNone(client.raw_log[-1]['volts'])
        client.close_restore()
        self.assertEqual(native.connected, [123])
        self.assertEqual(native.sent, [])

    def test_firmware_check_rejects_version_drift_and_external_addresses(self):
        xml = b'<formular><formdata key="VersionDIAGvers">29.0.0</formdata><formdata key="VersionSystem">29.0r00-14769-2964</formdata></formular>'
        seen = []
        def fetch(url):
            seen.append(url)
            return xml
        require_matching_firmware('192.168.13.69', fetch=fetch)
        declaration = b'<!DOCTYPE formular [ <!ENTITY nbsp "&#160;"> <!ENTITY copy "&#xA9;"> ]>'
        device_xml = declaration + xml.replace(b'<formular>', b'<formular><label>&nbsp;&copy;</label>')
        require_matching_firmware('192.168.13.69', fetch=lambda url: device_xml)
        for ip in ('https://evil.test', '10.10.10.205', '192.168.13.69/evil', None):
            with self.assertRaises(ElmError): require_matching_firmware(ip, fetch=fetch)
        self.assertEqual(len(seen), 1)
        for bad in (xml.replace(b'29.0.0', b'30.0.0'), b'not-xml', b'x' * 65537,
                    xml.replace(b'</formular>', b'<formdata key="VersionDIAGvers">29.0.0</formdata></formular>'),
                    b'<!DOCTYPE formular SYSTEM "file:///secret">' + xml,
                    declaration + declaration + xml,
                    declaration.replace(b'&#160;', b'29.0.0') + xml):
            with self.assertRaises(ElmError): require_matching_firmware('192.168.13.69', fetch=lambda url: bad)

    def test_firmware_mismatch_stops_before_module_connect(self):
        native = FakeNative()
        def mismatch(device): raise ElmError('vnci-firmware-mismatch-no-auto-update')
        native.check_firmware = mismatch
        client = DpuClient(ID, native=native)
        with self.assertRaisesRegex(ElmError, 'firmware-mismatch'): client.validate_adapter()
        self.assertEqual(native.connected, [])
        self.assertEqual(native.sent, [])
        client.close_restore()

    def test_identity_uses_unique_usb_serial_and_never_resolves_com(self):
        row = module_device(MODULE)
        self.assertEqual(row["id"], ID)
        self.assertTrue(valid_device_id(ID))
        snap = {"platform": "win32", "vnci": [row], "errors": []}
        self.assertEqual(enumerate_devices(snap)["devices"], [row])
        with self.assertRaises(ElmError): resolve_port(ID, snap)
        for changed in ({**MODULE, "type": 3}, {**MODULE, "name": MODULE['name'].replace('USB', 'WLAN')},
                        {**MODULE, "name": MODULE['name'].replace('10001', '../bad')}):
            self.assertIsNone(module_device(changed))

    def test_missing_duplicate_busy_identity_stops_before_connect(self):
        for rows in ([], [MODULE, MODULE], [{**MODULE, "status": 0x8062}]):
            native = FakeNative(rows=rows)
            client = DpuClient(ID, native=native)
            with self.assertRaises(ElmError): client.validate_adapter()
            self.assertEqual(native.connected, [])
            self.assertEqual(native.sent, [])
            client.close_restore()
            self.assertEqual(native.closed, 1)

    def test_zero_voltage_has_no_can_link_or_ecu_send(self):
        native = FakeNative(voltage=0)
        out = io.StringIO()
        rc = run_monitor(ID, stdout=out, max_samples=1,
                         client_factory=lambda did, **kw: DpuClient(did, native=native, **kw))
        self.assertEqual(rc, 0)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(events[0]['type'], 'handshake')
        self.assertIsNone(events[0]['volts'])
        self.assertTrue(events[0]['commOk'])
        self.assertEqual(native.links, [])
        self.assertEqual(native.sent, [])
        self.assertEqual(native.closed, 1)

    def test_voltage_loss_and_return_do_not_disconnect_adapter(self):
        native = FakeNative()
        samples = iter((12.6, 0, 12.7))
        native.voltage = lambda: next(samples)
        stop = threading.Event()
        out = io.StringIO()
        with patch.object(stop, 'wait', return_value=False):
            rc = run_monitor(ID, stdout=out, max_samples=3, stop_event=stop,
                             client_factory=lambda did, **kw: DpuClient(did, native=native, **kw))
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(rc, 0)
        self.assertEqual([event['volts'] for event in events[:-1]], [12.6, None, 12.7])
        self.assertTrue(all(event['commOk'] for event in events[:-1]))
        self.assertEqual(native.connected, [123])
        self.assertEqual(native.sent, [])
        self.assertEqual(native.links, [])
        self.assertEqual(native.closed, 1)

    def test_invalid_voltage_never_becomes_a_numeric_sample(self):
        for volts in (None, -1, 5.9, 20.1, float('nan'), float('inf'), True):
            with self.subTest(volts=volts):
                native = FakeNative(voltage=volts)
                client = DpuClient(ID, native=native)
                adapter = client.validate_adapter()
                self.assertIsNone(adapter['volts'])
                self.assertTrue(adapter['commOk'])
                json.dumps(client.raw_log, allow_nan=False)
                client.close_restore()

    def test_native_voltage_io_failure_still_stops_and_releases(self):
        native = FakeNative()
        readings = iter((12.6, ElmError('d-pdu:PDUIoCtl:disconnected')))
        def voltage():
            value = next(readings)
            if isinstance(value, Exception): raise value
            return value
        native.voltage = voltage
        stop = threading.Event()
        out = io.StringIO()
        with patch.object(stop, 'wait', return_value=False):
            rc = run_monitor(ID, stdout=out, max_samples=3, stop_event=stop,
                             client_factory=lambda did, **kw: DpuClient(did, native=native, **kw))
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(rc, 1)
        self.assertEqual(events[1]['error'], 'd-pdu:PDUIoCtl:disconnected')
        self.assertEqual(native.closed, 1)
        self.assertEqual(native.sent, [])

    def test_no_coding_security_clear_or_unknown_pids_are_sent(self):
        client, native = configured()
        for request in ('2EF19000', '2701', '14000000', '04', '3101', '3400', '0120', '0106'):
            with self.assertRaises(ElmError): client.request(request, time.monotonic() + 1)
        self.assertEqual(native.sent, [])
        self.assertEqual(client.request('0100', time.monotonic() + 1)['payload_hex'], '4100181A8000')
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(native.links[0][1:], (0x7DF, 0x7E8))
        self.assertEqual([row['layer'] for row in client.raw_log if 'dir' in row], ['diagnostic-pdu'] * 2)

    def test_negative_response_is_returned_once_without_retry(self):
        client, native = configured(FakeNative(response=bytes.fromhex('7F0111')))
        result = client.request('0100', time.monotonic() + 1)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'negative-response')
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(native.cancelled, [])

    def test_foreign_cop_wrong_identity_flags_and_wrong_echo_poison(self):
        mutations = ({'cop': 999}, {'unique_id': 99}, {'flags': b'\x80'}, {'payload': b'\x41\x0C\0\0'})
        for change in mutations:
            client, native = configured()
            def events(payload, cop):
                result = FakeNative.reply(b'\x41\0\x18\x1A\x80\0', cop)
                result[0].update(change)
                return result
            native.make_events = events
            with self.assertRaises(ElmError): client.request('0100', time.monotonic() + 1)
            with self.assertRaisesRegex(ElmError, 'client-poisoned'): client.request('0100', time.monotonic() + 1)
            self.assertEqual(native.sent, ['0100'])
            self.assertEqual(native.cancelled, [1])

    def test_stale_response_is_not_reused(self):
        client, native = configured()
        native.events.append(FakeNative.reply(b'\x41\0\0\0\0\0', 777)[0])
        with self.assertRaisesRegex(ElmError, 'desynchronized'): client.request('0100', time.monotonic() + 1)
        self.assertEqual(native.sent, [])

    def test_uncertain_native_start_preserves_attempt_and_does_not_retry(self):
        client, native = configured()
        def uncertain(payload):
            native.sent.append(payload.hex().upper())
            raise ElmError('d-pdu:uncertain-start')
        native.start = uncertain
        with self.assertRaisesRegex(ElmError, 'uncertain-start'): client.request('0100', time.monotonic() + 1)
        with self.assertRaisesRegex(ElmError, 'client-poisoned'): client.request('0100', time.monotonic() + 1)
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(client.raw_log[-1]['sendState'], 'attempted')

    def test_timeout_and_cancel_stop_without_retry_and_close_once(self):
        for cancelled in (False, True):
            client, native = configured(FakeNative(response=None))
            stop = threading.Event()
            client.cancel_event = stop
            if cancelled:
                native.make_events = lambda payload, cop: (stop.set() or [])
            with self.assertRaisesRegex(ElmError, 'cancelled' if cancelled else 'deadline-expired'):
                client.request('0100', time.monotonic() + 0.025)
            self.assertEqual(native.sent, ['0100'])
            self.assertEqual(native.cancelled, [1])
            client.close_restore()
            client.close_restore()
            self.assertEqual(native.closed, 1)

    def test_cleanup_failure_is_reported_as_failed_monitor(self):
        native = FakeNative()
        native.close_errors = ['disconnect failed']
        out = io.StringIO()
        rc = run_monitor(ID, stdout=out, max_samples=1,
                         client_factory=lambda did, **kw: DpuClient(did, native=native, **kw))
        self.assertEqual(rc, 1)
        event = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(event['error'], 'vnci-cleanup-failed')

    def test_real_session_runner_uses_native_pdu_and_qualifies_identity_before_dtc(self):
        profile = 'porsche-981-2014-dme'
        fixtures = catalog_success_map(profile)
        native = FakeNative()
        def events(payload, cop):
            req = payload.hex().upper()
            parsed = parse_ath1_response(fixtures[req].decode('ascii'), req_hex=req,
                                        rx_id=0x7E8, tx_id=0x7E0, sent_sf='')
            return FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
        native.make_events = events
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: DpuClient(did, native=native, **kw)):
            result = run_session(profile_id=profile, mode='live', device_id=ID,
                                 confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch))
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['identityQualification']['observedProfileMatch'])
            self.assertEqual(native.sent[-1], '1800FF00')
            self.assertEqual(result['restoration']['adapterClose'], 'D-PDU disconnect/destruct')
            raw = json.loads((Path(result['artifactDir']) / 'raw.json').read_text())
            self.assertTrue(all(r.get('layer') == 'diagnostic-pdu' for r in raw if 'dir' in r))
        self.assertEqual(native.closed, 1)

    def test_vnci_clear_rejected_before_native_constructor(self):
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient') as factory:
            result = run_session(profile_id='porsche-981-2014-dme', mode='live', device_id=ID,
                                 session_task='clear', confirmed_clear_dtc=True, x431_inactive=True,
                                 artifact_root=Path(scratch))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'vnci-clear-not-validated')
            factory.assert_not_called()

    def test_live_identity_mismatch_does_not_issue_dtc(self):
        profile = 'porsche-981-2014-dme'
        fixtures = catalog_success_map(profile, identity_text={'dme-dsn': 'X200'})
        native = FakeNative()
        def events(payload, cop):
            req = payload.hex().upper()
            parsed = parse_ath1_response(fixtures[req].decode('ascii'), req_hex=req,
                                        rx_id=0x7E8, tx_id=0x7E0, sent_sf='')
            return FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
        native.make_events = events
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: DpuClient(did, native=native, **kw)):
            result = run_session(profile_id=profile, mode='live', device_id=ID,
                                 confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch))
            self.assertFalse(result['ok'])
            self.assertNotIn('1800FF00', native.sent)
            self.assertEqual(native.closed, 1)


if __name__ == '__main__':
    unittest.main()
