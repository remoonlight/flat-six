"""PT3G adapter boundaries with synthetic native responses; no device I/O."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.diagnostics import pt3g
from scripts.diagnostics.connection import collect_snapshot, enumerate_devices, resolve_port, valid_device_id
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.sessions import run_session

ID = 'pt3g:10001'
MODULE = {'type': 11, 'handle': 1, 'status': 0x8063,
          'name': "ModuleName='PT3G-VCI' SerialNumber='10001' IP='127.0.0.1' ConnectionType='USB'"}


class FakeNative:
    def __init__(self, rows=None, volts=0, errors=None):
        self.rows = [MODULE] if rows is None else rows
        self.volts = volts
        self.errors = errors or []
        self.connected = []
        self.closed = 0

    def modules(self): return self.rows
    def connect_module(self, handle): self.connected.append(handle)
    def voltage(self): return self.volts
    def close(self):
        self.closed += 1
        return {'errors': self.errors, 'ecuRestorationProven': False}


class Pt3gTests(unittest.TestCase):
    def test_effective_profile_read_cannot_fall_back_or_override_pinned_bytes(self):
        class Read:
            def __call__(self, section, key, default, filename):
                self.args = (section, default, filename)
                return self.values[key]
        class Kernel: pass
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / pt3g.ACTIVE_CONFIG).write_text('[T6CONFIG]\n15765StartDetect=0\nSN=10001\n')
            read = Read(); kernel = Kernel(); kernel.GetPrivateProfileIntA = read
            with patch.object(pt3g, '_windows_ini_path', return_value=b'absolute-config-path'), \
                    patch.object(pt3g.c, 'WinDLL', return_value=kernel, create=True) as loader:
                read.values = {b'15765StartDetect': 0, b'SN': 10001}
                result = pt3g.verify_windows_config(root)
                self.assertEqual(result['verifiedKeys'], 2)
                self.assertTrue(result['windowsProfileApiVerified'])
                loader.assert_called_once_with('kernel32', use_last_error=True)
                self.assertEqual(read.args, (b'T6CONFIG', -1, b'absolute-config-path'))
                for key, value in ((b'15765StartDetect', 1), (b'15765StartDetect', 0xffffffff), (b'SN', 99999)):
                    read.values = {b'15765StartDetect': 0, b'SN': 10001}; read.values[key] = value
                    with self.assertRaisesRegex(ElmError, 'effective-config-mismatch'):
                        pt3g.verify_windows_config(root)

    def test_injected_discovery_never_loads_either_native_driver(self):
        with patch('scripts.diagnostics.connection.windows_serial_rows', return_value=([], None)), \
                patch('scripts.diagnostics.connection.windows_bluetooth_rows', return_value=([], None)), \
                patch('scripts.diagnostics.connection.pyserial_rows', return_value=([], None)), \
                patch('scripts.diagnostics.vnci.discover') as vnci_discovery, \
                patch.object(pt3g, 'discover') as pt3g_discovery:
            snapshot = collect_snapshot(platform='win32', pt3g_rows=[pt3g.module_device(MODULE)])
        self.assertEqual(len(snapshot['pt3g']), 1)
        vnci_discovery.assert_not_called()
        pt3g_discovery.assert_not_called()

    def test_exact_native_identity_required_and_com_routing_prohibited(self):
        device = pt3g.module_device(MODULE)
        self.assertEqual(device['id'], ID)
        self.assertTrue(valid_device_id(ID))
        self.assertFalse(device['vehicleTransportQualified'])
        snapshot = {'pt3g': [device]}
        self.assertEqual(enumerate_devices(snapshot)['devices'], [device])
        with self.assertRaises(ElmError): resolve_port(ID, snapshot)
        for name in (MODULE['name'].replace('USB', 'WLAN'), MODULE['name'].replace('127.0.0.1', '10.0.0.1'),
                     MODULE['name'].replace('10001', '../bad'), MODULE['name'] + " SerialNumber='9999'"):
            self.assertIsNone(pt3g.module_device({**MODULE, 'name': name}))
        self.assertIsNone(pt3g.module_device({**MODULE, 'type': 14}))

    def test_missing_duplicate_busy_module_cannot_connect(self):
        for rows in ([], [MODULE, MODULE], [{**MODULE, 'status': 0x8062}]):
            native = FakeNative(rows=rows)
            client = pt3g.AdapterClient(ID, native=native)
            with self.assertRaises(ElmError): client.validate_adapter()
            self.assertEqual(native.connected, [])
            client.close_restore()
            self.assertEqual(native.closed, 1)

    def test_zero_voltage_and_cancellation_do_not_claim_vehicle_connection(self):
        native = FakeNative()
        stop = threading.Event()
        client = pt3g.AdapterClient(ID, native=native, cancel_event=stop)
        doc = client.validate_adapter()
        self.assertTrue(doc['commOk'])
        self.assertIsNone(doc['volts'])
        self.assertFalse(doc['vehicleTransportQualified'])
        self.assertEqual(native.connected, [1])
        stop.set()
        with self.assertRaisesRegex(ElmError, 'cancelled'): client.validate_adapter()
        self.assertEqual(native.connected, [1])
        client.close_restore()

    def test_hash_and_detection_checks_run_before_dll_loading(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            manifest = {}
            for name, content in [('PDU_VCI.dll', b'fixture'), ('PDU_VCI.ini', b'15765StartDetect=0\n'),
                                  (pt3g.ACTIVE_CONFIG, b'15765StartDetect=0\n')]:
                (root / name).write_bytes(content)
                manifest[name] = hashlib.sha256(content).hexdigest()
            with patch.object(pt3g, 'SUPPORT_SHA256', manifest):
                pt3g.verify_vendor_files(root)
                (root / 'PDU_VCI.ini').write_bytes(b'15765StartDetect=1\n')
                manifest['PDU_VCI.ini'] = hashlib.sha256((root / 'PDU_VCI.ini').read_bytes()).hexdigest()
                with self.assertRaisesRegex(ElmError, 'detection-not-disabled'): pt3g.verify_vendor_files(root)
                with patch.object(pt3g.c, 'WinDLL', create=True) as loader:
                    with self.assertRaises(ElmError): pt3g.NativeAdapter(root)
                    loader.assert_not_called()
                (root / 'PDU_VCI.ini').unlink()
                with self.assertRaisesRegex(ElmError, 'support-missing'): pt3g.verify_vendor_files(root)
                (root / 'PDU_VCI.ini').write_bytes(b'changed')
                with self.assertRaisesRegex(ElmError, 'support-not-qualified'): pt3g.verify_vendor_files(root)

    def test_active_config_is_required_and_cannot_hide_behind_legacy_zero(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / 'PDU_VCI.ini').write_bytes(b'15765StartDetect=0\n')
            manifest = {name: hashlib.sha256(b'15765StartDetect=0\n').hexdigest()
                        for name in ('PDU_VCI.ini', pt3g.ACTIVE_CONFIG)}
            with patch.object(pt3g, 'SUPPORT_SHA256', manifest):
                with self.assertRaisesRegex(ElmError, 'support-missing:' + pt3g.ACTIVE_CONFIG):
                    pt3g.verify_vendor_files(root)
                (root / pt3g.ACTIVE_CONFIG).write_bytes(b'15765StartDetect=1\n')
                with self.assertRaisesRegex(ElmError, 'support-not-qualified:' + pt3g.ACTIVE_CONFIG):
                    pt3g.verify_vendor_files(root)
                # Even a source manifest accepting this fixture cannot allow detection.
                manifest[pt3g.ACTIVE_CONFIG] = hashlib.sha256((root / pt3g.ACTIVE_CONFIG).read_bytes()).hexdigest()
                with self.assertRaisesRegex(ElmError, 'detection-not-disabled:' + pt3g.ACTIVE_CONFIG):
                    pt3g.verify_vendor_files(root)

    def test_native_guard_rejects_links_and_non_voltage_ioctls(self):
        native = pt3g.NativeAdapter.__new__(pt3g.NativeAdapter)
        native._allowed = {'PDUGetObjectId', 'PDUIoCtl'}
        native._voltage_oid = 123
        for name, args in [('PDUConnect', ()), ('PDUCreateComLogicalLink', ()),
                           ('PDUStartComPrimitive', ()), ('PDUGetObjectId', (0x8023, b'UPDATE', None)),
                           ('PDUIoCtl', (1, pt3g.UNDEF, 124, None, None)),
                           ('PDUIoCtl', (1, 2, 123, None, None)),
                           ('PDUIoCtl', (1, pt3g.UNDEF, 123, b'write', None))]:
            with self.assertRaises(ElmError): native.call(name, *args)

    def test_cleanup_failure_is_reported_and_discovery_does_not_connect(self):
        native = FakeNative(errors=['native release failed'])
        with patch.object(pt3g, 'NativeAdapter', return_value=native), \
                patch.object(pt3g.sys, 'platform', 'win32'), \
                patch.object(pt3g.Path, 'is_file', return_value=True):
            with self.assertRaisesRegex(ElmError, 'discovery-cleanup-failed'): pt3g.discover()
        self.assertEqual(native.connected, [])
        self.assertEqual(native.closed, 1)
        native = FakeNative(errors=['native release failed'])
        output = io.StringIO()
        with patch.object(pt3g, 'NativeAdapter', return_value=native):
            code = pt3g.run_monitor(ID, stdout=output, max_samples=1)
        self.assertEqual(code, 1)
        docs = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(docs[-1]['error'], 'pt3g-cleanup-failed')

    def test_adapter_client_preserves_first_close_failure_and_blocks_validation(self):
        native = FakeNative(errors=['SYNTHETIC native release failed'])
        client = pt3g.AdapterClient(ID, native=native)
        first = client.close_restore(); first['errors'].clear()
        native.errors.clear()
        self.assertEqual(client.close_restore()['errors'], ['SYNTHETIC native release failed'])
        self.assertEqual(native.closed, 1)
        with self.assertRaisesRegex(ElmError, 'adapter-closed'):
            client.validate_adapter()
        self.assertEqual(native.connected, [])

    def test_adapter_client_close_exception_or_invalid_report_stays_failed_on_retry(self):
        for result in (None, {}, {'errors': None}, {'errors': [None]}, {'errors': 'failed'}, OSError('SYNTHETIC close failed')):
            with self.subTest(result=result):
                native = FakeNative()
                def close():
                    native.closed += 1
                    if isinstance(result, Exception): raise result
                    return result
                native.close = close
                client = pt3g.AdapterClient(ID, native=native)
                first = client.close_restore()
                self.assertTrue(first['errors']); self.assertEqual(client.close_restore(), first)
                self.assertEqual(native.closed, 1)

    def test_vehicle_read_clear_and_engine_rejected_without_opening_a_port(self):
        with tempfile.TemporaryDirectory() as scratch, patch.dict('os.environ', {}, clear=True), \
                patch('scripts.diagnostics.sessions._open_live_port') as opener:
            for task in ('read', 'engine', 'clear'):
                result = run_session(profile_id='porsche-981-2014-dme', operation_ids=['dme-dtc'] if task == 'read' else None,
                    mode='live', session_task=task, device_id=ID, confirmed_read_only=task != 'clear',
                    confirmed_clear_dtc=task == 'clear', x431_inactive=True, artifact_root=Path(scratch))
                self.assertEqual(result['error'], 'pt3g-vehicle-transport-not-qualified')
            opener.assert_not_called()


if __name__ == '__main__': unittest.main()
