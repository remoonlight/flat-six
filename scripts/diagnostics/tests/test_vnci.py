"""Synthetic native events; no vendor DLL, USB, network or ECU access."""
import io
import ctypes as c
import hashlib
import json
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.diagnostics.connection import enumerate_devices, resolve_port, valid_device_id
from scripts.diagnostics.elm import ElmError, parse_ath1_response
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.vnci import DpuClient, NativeDpu, UNDEF, discover, module_device, require_matching_firmware, run_monitor, vendor_root, verify_vendor_files
from scripts.diagnostics.vnci import Event, U32

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
    def release_link(self):
        self.events.clear()
        return {"errors": [], "moduleRetained": True, "ecuRestorationProven": False}
    def event(self): return self.events.popleft() if self.events else None
    def cancel(self, cop):
        self.cancelled.append(cop)
        self.events.append({"type": 0x1301, "cop": cop, "value": 0x8013})
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
    def test_native_event_empty_queue_before_close_and_rejection_after_close(self):
        native = NativeDpu.__new__(NativeDpu)
        native.module, native.link = 0, 0
        native.online, native.constructed = False, True
        native._directory = Mock()
        native.dll = Mock()
        native.dll.PDUGetEventItem.return_value = 0x71
        native.dll.PDUModuleDisconnect.return_value = 0
        native.dll.PDUDestroyComLogicalLink.return_value = 0
        native.dll.PDUDestruct.return_value = 0
        self.assertIsNone(native.event())
        native.dll.PDUGetEventItem.assert_called_once()
        self.assertEqual(native.dll.PDUGetEventItem.call_args.args[:2], (0, 0))
        report = native.close()
        self.assertEqual(report['errors'], [])
        calls = list(native.dll.mock_calls)
        with self.assertRaisesRegex(ElmError, '^d-pdu:adapter-closed$'): native.event()
        with self.assertRaisesRegex(ElmError, '^d-pdu:adapter-closed$'): native.call('PDUConstruct', None, None)
        self.assertEqual(native.dll.mock_calls, calls)
        self.assertEqual(native.close(), report)
        self.assertEqual(native.dll.mock_calls, calls)
        native._directory.close.assert_called_once()

    def test_failed_native_close_rejects_events_and_preserves_first_error(self):
        for failure in ('PDUModuleDisconnect', 'PDUDestruct', 'directory-close'):
            with self.subTest(failure=failure):
                native = NativeDpu.__new__(NativeDpu)
                native.module, native.link = 123, None
                native.online, native.constructed = False, True
                native._directory = Mock()
                native.dll = Mock()
                native.dll.PDUModuleDisconnect.return_value = 0x60 if failure == 'PDUModuleDisconnect' else 0
                native.dll.PDUDestruct.return_value = 0x60 if failure == 'PDUDestruct' else 0
                if failure == 'directory-close': native._directory.close.side_effect = OSError('SYNTHETIC close failed')
                report = native.close()
                self.assertTrue(report['errors'])
                calls = list(native.dll.mock_calls)
                with self.assertRaisesRegex(ElmError, '^d-pdu:adapter-closed$'): native.event()
                self.assertEqual(native.dll.mock_calls, calls)
                copy = native.close(); copy['errors'].clear()
                self.assertEqual(native.close(), report)
                native._directory.close.assert_called_once()

    def test_closed_native_event_does_not_access_driver_or_handles(self):
        native = NativeDpu.__new__(NativeDpu)
        native._close_report = {'errors': []}
        # A completed-close guard precedes all ctypes setup and DLL/handle access.
        with self.assertRaisesRegex(ElmError, '^d-pdu:adapter-closed$'): native.event()

    def test_open_native_status_event_still_destroys_its_owned_item(self):
        native = NativeDpu.__new__(NativeDpu)
        native.module, native.link = 123, 456
        value = U32(0x8013)
        event = Event()
        event.type, event.cop, event.timestamp = 0x1301, 99, 1000
        event.data = c.cast(c.pointer(value), c.c_void_p)
        def receive(module, link, pointer):
            self.assertEqual((module, link), (123, 456))
            c.cast(pointer, c.POINTER(c.POINTER(Event)))[0] = c.pointer(event)
            return 0
        native.dll = Mock()
        native.dll.PDUGetEventItem.side_effect = receive
        native.dll.PDUDestroyItem.return_value = 0
        self.assertEqual(native.event(), {'type': 0x1301, 'cop': 99, 'timestamp': 1000, 'value': 0x8013})
        native.dll.PDUGetEventItem.assert_called_once()
        native.dll.PDUDestroyItem.assert_called_once()

    def test_logical_link_release_retains_module_and_preserves_cleanup_failure(self):
        for failure in (None, 'PDUDisconnect', 'PDUDestroyComLogicalLink'):
            with self.subTest(failure=failure):
                native = NativeDpu.__new__(NativeDpu)
                native.module, native.link = 123, 456
                native.online = native.constructed = True
                native._directory = Mock()
                calls = []
                def call(name, *args):
                    calls.append((name, args))
                    if name == failure:
                        raise ElmError('synthetic-' + name + '-failed')
                native.call = call
                report = native.release_link()
                self.assertEqual([name for name, _ in calls], ['PDUDisconnect', 'PDUDestroyComLogicalLink'])
                self.assertEqual(native.module, 123)
                self.assertTrue(native.constructed)
                self.assertTrue(report['moduleRetained'])
                self.assertFalse(report['ecuRestorationProven'])
                native._directory.close.assert_not_called()
                if failure:
                    self.assertEqual(report['errors'], ['synthetic-' + failure + '-failed'])
                    with self.assertRaisesRegex(ElmError, 'link-transition-failed'):
                        native.create_link('ISO_OBD_on_ISO_15765_4', 0x7DF, 0x7E8)
                    with self.assertRaisesRegex(ElmError, 'link-transition-failed'):
                        native.start(b'\x01\x00')
                    self.assertEqual(native.release_link()['errors'], report['errors'])
                    self.assertEqual(len(calls), 2, 'failed handoff is not automatically retried')
                else:
                    self.assertEqual(report['errors'], [])
                    self.assertIsNone(native.link)
                    self.assertFalse(native.online)
                closed = native.close()
                if failure:
                    self.assertIn('synthetic-' + failure + '-failed', closed['errors'])
                else:
                    self.assertEqual(closed['errors'], [])
                native._directory.close.assert_called_once()
                self.assertIn(('PDUModuleDisconnect', (123,)), calls)
                count = len(calls)
                self.assertEqual(native.close(), closed)
                self.assertEqual(len(calls), count)

    def test_standard_engine_handoff_releases_link_before_replacing_allowlist(self):
        native = FakeNative()
        client = DpuClient(ID, native=native)
        client.validate_adapter()
        client.configure_pair('7E0', '7E8', time.monotonic() + 2)
        actions = []
        release, create = native.release_link, native.create_link
        native.release_link = lambda: (actions.append('release'), release())[1]
        native.create_link = lambda *args: (actions.append('create'), create(*args))[1]
        client.configure_standard_engine(time.monotonic() + 2)
        self.assertEqual(actions, ['release', 'create'])
        self.assertEqual(native.connected, [123], 'module and head identity are retained')
        self.assertEqual(native.sent, [], 'handoff itself sends no ECU read')
        self.assertEqual(native.links[-1], ('ISO_OBD_on_ISO_15765_4', 0x7DF, 0x7E8))
        with self.assertRaisesRegex(ElmError, 'unknown-request'):
            client.request('1089', time.monotonic() + 2)
        self.assertEqual(native.sent, [])
        self.assertTrue(client.request('0100', time.monotonic() + 2)['ok'])
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(client.close_restore()['errors'], [])

    def test_failed_or_expired_handoff_never_creates_a_new_link(self):
        for case in ('release-failed', 'deadline', 'cancelled', 'invalid-report', 'module-lost'):
            with self.subTest(case=case):
                native, stop, clock = FakeNative(), threading.Event(), [0]
                client = DpuClient(ID, native=native, cancel_event=stop, clock=lambda: clock[0])
                client.validate_adapter()
                client.configure_pair('7E0', '7E8', 10)
                def release():
                    if case == 'deadline': clock[0] = 10
                    if case == 'cancelled': stop.set()
                    if case == 'invalid-report': return {'errors': 'invalid'}
                    return {'errors': ['synthetic-release-failed'] if case == 'release-failed' else [],
                            'moduleRetained': case != 'module-lost'}
                native.release_link = release
                with self.assertRaises(ElmError):
                    client.configure_standard_engine(10)
                self.assertEqual(len(native.links), 1)
                self.assertEqual(native.sent, [])
                self.assertEqual(client.allowed, set())
                with self.assertRaises(ElmError): client.request('0100', 20)
                closed = client.close_restore()
                if case == 'release-failed': self.assertIn('synthetic-release-failed', closed['errors'])
                if case in ('invalid-report', 'module-lost'): self.assertTrue(closed['errors'])
                self.assertEqual(native.closed, 1)

    def test_handoff_cannot_reconfigure_an_inflight_request(self):
        client, native = configured()
        with client._lock:
            with self.assertRaisesRegex(ElmError, 'configure-during-request'):
                client.configure_standard_engine(time.monotonic() + 2)
        self.assertEqual(len(native.links), 1)
        self.assertEqual(native.sent, [])
        self.assertIn('d-pdu:configure-during-request', client.close_restore()['errors'])

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
        source_root = Path(NativeDpu.__init__.__code__.co_filename).resolve().parents[2]
        self.assertEqual(vendor_root({}), source_root / relative)
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
        head = b'<formular formid="GSINF" state="load:OK">'
        xml = head + b'<formdata key="VersionDIAGvers">29.0.0</formdata><formdata key="VersionSystem">29.0r00-14769-2964</formdata></formular>'
        seen = []
        def fetch(url):
            seen.append(url)
            return xml
        require_matching_firmware('192.168.13.69', fetch=fetch)
        declaration = b'<!DOCTYPE formular [ <!ENTITY nbsp "&#160;"> <!ENTITY copy "&#xA9;"> ]>'
        device_xml = declaration + xml.replace(head, head + b'<label>&nbsp;&copy;</label>')
        require_matching_firmware('192.168.13.69', fetch=lambda url: device_xml)
        for ip in ('https://evil.test', '10.10.10.205', '192.168.13.69/evil', None):
            with self.assertRaises(ElmError): require_matching_firmware(ip, fetch=fetch)
        self.assertEqual(len(seen), 1)
        for bad in (xml.replace(b'29.0.0', b'30.0.0'), b'not-xml', b'x' * 65537,
                    xml.replace(b'</formular>', b'<formdata key="VersionDIAGvers">29.0.0</formdata></formular>'),
                    b'<!DOCTYPE formular SYSTEM "file:///secret">' + xml,
                    declaration + declaration + xml,
                    declaration.replace(b'&#160;', b'29.0.0') + xml,
                    xml.replace(b'GSINF', b'CSID'), xml.replace(b'load:OK', b'load:ERROR'),
                    xml.replace(b'formid="GSINF"', b''), xml.decode(), b''):
            with self.assertRaises(ElmError): require_matching_firmware('192.168.13.69', fetch=lambda url: bad)
        for bad in ((b'<!DOCTYPE formular [<!ENTITY v "29.0.0">]>' + xml.replace(b'29.0.0', b'&v;')).decode().encode('utf-16'),
                    xml.replace(b'29.0.0</formdata>', b'29.0.0<extra/></formdata>')):
            with self.assertRaises(ElmError): require_matching_firmware('192.168.13.69', fetch=lambda url: bad)

    def test_management_serial_is_checked_before_firmware_and_connect(self):
        identity = b'<formular formid="CSID" state="load:OK"><formdata key="Serial">10001</formdata></formular>'
        versions = b'<formular formid="GSINF" state="load:OK"><formdata key="VersionDIAGvers">29.0.0</formdata><formdata key="VersionSystem">29.0r00-14769-2964</formdata></formular>'
        seen = []
        def fetch(url):
            seen.append(url)
            return identity if url.endswith('/CSID') else versions
        native = FakeNative()
        native.check_firmware = lambda device: require_matching_firmware(device['address'], expected_serial=device['serial'], fetch=fetch)
        client = DpuClient(ID, native=native)
        adapter = client.validate_adapter()
        info = adapter['adapterInfo']
        self.assertEqual(info['serial'], '10001')
        self.assertTrue(info['managementSerialMatched']); self.assertTrue(info['driverFirmwareMatched'])
        self.assertEqual(info['identityPageSha256'], hashlib.sha256(identity).hexdigest())
        self.assertEqual(info['versionPageSha256'], hashlib.sha256(versions).hexdigest())
        self.assertFalse(info['vehicleQualified'])
        self.assertEqual(seen, ['http://192.168.13.69/cgi-bin/formular.fcgi/CSID', 'http://192.168.13.69/cgi-bin/formular.fcgi/GSINF'])
        self.assertEqual(native.connected, [123]); self.assertEqual(native.sent, [])
        client.close_restore()
        for bad in (identity.replace(b'10001', b'10002'), identity.replace(b'10001', b'010001'),
                    identity.replace(b'</formular>', b'<formdata key="Serial">10001</formdata></formular>'),
                    identity.replace(b'key="Serial"', b'key="serial"'), identity.replace(b'10001', b''),
                    identity.replace(b'10001</formdata>', b'10001<extra/></formdata>')):
            native = FakeNative(); seen.clear()
            native.check_firmware = lambda device: require_matching_firmware(device['address'], expected_serial=device['serial'], fetch=lambda url: (seen.append(url), bad)[1])
            client = DpuClient(ID, native=native)
            with self.assertRaisesRegex(ElmError, 'management-serial-mismatch'): client.validate_adapter()
            self.assertEqual(len(seen), 1); self.assertEqual(native.connected, []); self.assertEqual(native.sent, [])
            client.close_restore()
        firmware_only = require_matching_firmware('192.168.13.69', fetch=lambda url: versions)
        self.assertFalse(firmware_only['managementSerialMatched']); self.assertIsNone(firmware_only['serial'])
        for serial in (True, 10001, '', '../bad'):
            with self.assertRaisesRegex(ElmError, 'device-id-invalid'):
                require_matching_firmware('192.168.13.69', expected_serial=serial, fetch=lambda _: self.fail('must not fetch'))

    def test_native_firmware_check_supplies_the_enumerated_serial(self):
        native = NativeDpu.__new__(NativeDpu)
        with patch('scripts.diagnostics.vnci.require_matching_firmware', return_value={'sentinel': True}) as check:
            self.assertEqual(native.check_firmware(module_device(MODULE)), {'sentinel': True})
            check.assert_called_once_with('192.168.13.69', expected_serial='10001')

    def test_cancel_or_deadline_during_management_read_prevents_connect(self):
        for reason in ('cancelled', 'deadline-expired'):
            native = FakeNative(); stop = threading.Event(); clock = [0]
            def check(device):
                if reason == 'cancelled': stop.set()
                else: clock[0] = 11
            native.check_firmware = check
            client = DpuClient(ID, native=native, cancel_event=stop, clock=lambda: clock[0])
            with self.assertRaisesRegex(ElmError, reason): client.validate_adapter(budget_s=10)
            self.assertEqual(native.connected, []); self.assertEqual(native.sent, [])
            client.close_restore(); self.assertEqual(native.closed, 1)

    def test_management_fetch_is_bounded_proxy_free_and_cannot_redirect(self):
        from scripts.diagnostics.vnci import _NoManagementRedirect
        from urllib.request import ProxyHandler
        for target in ('http://192.168.13.70/', 'http://10.0.0.1/', 'file:///secret', '/other'):
            with self.assertRaisesRegex(ElmError, 'redirect-not-qualified'):
                _NoManagementRedirect().redirect_request(None, None, 302, '', {}, target)
        class Response:
            closed = False
            def __enter__(self): return self
            def __exit__(self, *args): self.closed = True
            def geturl(self): return self.url
            def read(self, size):
                self.size = size
                return b'<formular formid="GSINF" state="load:OK"><formdata key="VersionDIAGvers">29.0.0</formdata><formdata key="VersionSystem">29.0r00-14769-2964</formdata></formular>'
        response = Response(); response.url = 'http://192.168.13.69/cgi-bin/formular.fcgi/GSINF'
        with patch('scripts.diagnostics.vnci.urllib.request.build_opener') as build:
            build.return_value.open.return_value = response
            require_matching_firmware('192.168.13.69')
            handlers = build.call_args.args
            self.assertIsInstance(handlers[0], ProxyHandler); self.assertEqual(handlers[0].proxies, {})
            self.assertIsInstance(handlers[1], _NoManagementRedirect)
            build.return_value.open.assert_called_once_with(response.url, timeout=3)
            self.assertEqual(response.size, 65537); self.assertTrue(response.closed)
            response.url = 'http://10.0.0.1/other'
            with self.assertRaisesRegex(ElmError, 'redirect-not-qualified'): require_matching_firmware('192.168.13.69')

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
                        {**MODULE, "name": MODULE['name'].replace('10001', '../bad')},
                        {**MODULE, "name": None}, None,
                        *({**MODULE, 'name': MODULE['name'] + ' ' + field} for field in (
                            "SerialNumber='10001'", "SerialNumber='99999'", "IP='192.168.13.70'",
                            "ConnectionType='WLAN'", "ModuleName='other'"))):
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
        adapter_info = {'serial': '10001', 'driverVersion': '29.0.0', 'firmwareImage': '29.0r00-14769-2964',
                        'managementSerialMatched': True, 'driverFirmwareMatched': True, 'vehicleQualified': False,
                        'identityPageSha256': 'a' * 64, 'versionPageSha256': 'b' * 64}
        native.check_firmware = lambda device: adapter_info
        out = io.StringIO()
        rc = run_monitor(ID, stdout=out, max_samples=1,
                         client_factory=lambda did, **kw: DpuClient(did, native=native, **kw))
        self.assertEqual(rc, 0)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(events[0]['type'], 'handshake')
        self.assertIsNone(events[0]['volts'])
        self.assertTrue(events[0]['commOk'])
        self.assertEqual(events[0]['adapterInfo'], adapter_info, 'metadata survives Python monitor serialization')
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
        self.assertEqual([row['dir'] for row in client.raw_log if 'dir' in row], ['tx', 'rx', 'status'])
        self.assertTrue(all(row['layer'] == 'diagnostic-pdu' for row in client.raw_log if 'dir' in row))

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
        captured = []
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: DpuClient(did, native=native, **kw)):
            result = run_session(profile_id=profile, mode='live', device_id=ID,
                                 confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch), progress_sink=captured.append)
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['identityQualification']['observedProfileMatch'])
            self.assertEqual(native.sent[-1], '1800FF00')
            pdus = [e for e in captured if e['type'] == 'diagnostic-pdu']
            self.assertEqual([e['pdu']['requestHex'] for e in pdus], native.sent)
            self.assertTrue(all(e['profileId'] == profile and e['runId'] == result['runId'] and e['simulation'] is False for e in pdus))
            self.assertTrue(all(e['pdu']['expectedPrimitive'] == e['pdu']['primitive'] for e in pdus))
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
        captured = []
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: DpuClient(did, native=native, **kw)):
            result = run_session(profile_id=profile, mode='live', device_id=ID,
                                 confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch), progress_sink=captured.append)
            self.assertFalse(result['ok'])
            self.assertNotIn('1800FF00', native.sent)
            self.assertEqual(len([e for e in captured if e['type'] == 'diagnostic-pdu']), len(native.sent))
            self.assertEqual(native.closed, 1)


if __name__ == '__main__':
    unittest.main()
