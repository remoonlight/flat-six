"""Production PT3G/D-PDU methods against independent storage and queues; no I/O."""
import ctypes as c
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.diagnostics import pt3g, pt3g_read_source as source, pt3g_read_transport as transport, vnci
from scripts.diagnostics.elm import ElmError, parse_ath1_response
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.tests.test_dpu_configuration import DriverStorage
from scripts.diagnostics.tests.test_dpu_terminal import Clock, QueueNative
from scripts.diagnostics.tests.test_pt3g import ID, MODULE
from scripts.diagnostics.tests.test_pt3g_read_source import fixture
from scripts.diagnostics.tests.test_vnci import FakeNative


def contract_fixture():
    doc = fixture()
    names = {value: name for name, value in transport.PARAMETER_CLASSES.items()}
    for p in doc.findall('COMPARAM'):
        p.find('CLASS').text = names[vnci.UINT_PARAMETER_CLASSES[p.findtext('SHORT_NAME')]]
    raw = ET.tostring(doc)
    with tempfile.TemporaryDirectory() as scratch:
        f = Path(scratch) / 'MDF_VCI.xml'; f.write_bytes(raw)
        with patch.dict(source.SUPPORT_SHA256, {'MDF_VCI.xml': hashlib.sha256(raw).hexdigest()}):
            return source.read_source_contract(f)


def preparation(contract=None):
    return dict(sourceContract=contract_fixture() if contract is None else contract, transportSource={},
                plannedApiNames=list(vnci.NativeDpu.export_signatures()), **transport.GUARDS)


class Function:
    def __init__(self, name, dll):
        self.name, self.dll = name, dll
    def __call__(self, *args):
        self.dll.calls.append(self.name)
        if self.name == 'PDUGetObjectId':
            value = self.dll.objects[(args[0], args[1])] + self.dll.id_drift
            c.cast(args[2], c.POINTER(vnci.U32)).contents.value = value
        return self.dll.errors.get(self.name, 0)


class Dll:
    def __init__(self, contract):
        self.calls, self.errors, self.id_drift = [], {}, 0
        self.objects = {(r['objectType'], r['name'].encode('ascii')): r['sourceId'] for r in contract['objectQueries']}
        self.functions = {n: Function(n, self) for n in vnci.NativeDpu.export_signatures()}
    def __getattr__(self, name):
        if name not in self.functions:
            raise AttributeError(name)
        return self.functions[name]


class Directory:
    def __init__(self):
        self.closed = 0
    def close(self):
        self.closed += 1


class SourceDriverStorage(DriverStorage):
    def __init__(self, contract):
        super().__init__()
        old_ids = self.ids
        old_addresses = self.addresses
        self.objects = {(r['objectType'], r['name'].encode('ascii')): r['sourceId']
                        for r in contract['objectQueries']}
        self.ids = {name: self.objects[(0x8024, name.encode('ascii'))] for name in old_ids}
        self.names = {value: name for name, value in self.ids.items()}
        self.addresses = {self.ids[name]: old_addresses[old_ids[name]] for name in self.address_names}
        self.addresses[999] = old_addresses[999]

    def call(self, name, *args):
        if name == 'PDUGetObjectId':
            self.calls.append(name)
            c.cast(args[2], c.POINTER(vnci.U32)).contents.value = self.objects[(args[0], args[1])]
        else:
            super().call(name, *args)


class Pt3gReadTransportTests(unittest.TestCase):
    def make_native(self, dll=None):
        prep = preparation(); dll = dll or Dll(prep['sourceContract']); directory = Directory()
        with patch.object(transport, 'prepare_read_transport', return_value=prep), \
                patch.object(pt3g, 'verify_vendor_files'), patch.object(pt3g, 'verify_windows_config', return_value={}), \
                patch.object(pt3g.sys, 'platform', 'win32'), \
                patch.object(pt3g.os, 'add_dll_directory', return_value=directory, create=True), \
                patch.object(pt3g.c, 'WinDLL', return_value=dll, create=True):
            native = transport.NativeReadTransport(Path('private-driver-fixture'))
        return native, dll, directory

    def stored_driver(self):
        contract = contract_fixture()
        driver = SourceDriverStorage(contract)
        native = transport.NativeReadTransport.__new__(transport.NativeReadTransport)
        native.__dict__.update(driver.native.__dict__)
        native.contract = contract
        native._source_objects = driver.objects
        native.object_id = transport.NativeReadTransport.object_id.__get__(native)
        driver.native = native
        return driver

    def client(self, native=None):
        native = native or FakeNative(rows=[MODULE])
        clock = Clock()
        client = transport.ReadClient(ID, native=native, clock=clock, sleeper=clock.sleep)
        result = client.validate_adapter()
        self.assertEqual(result['ati'], 'PT3G / E70 10001')
        self.assertFalse(result['vehicleTransportQualified'])
        return client, native, clock

    def test_source_classes_must_match_shared_parameter_ABI_before_loading(self):
        contract = contract_fixture(); contract['routes'][0]['parameters'][0]['parameterClass'] = 'COM'
        with patch.object(transport, 'read_source_contract', return_value=contract), \
                patch.object(transport, 'audit_transport_source', return_value={}), \
                patch.object(pt3g.c, 'WinDLL', create=True) as loader:
            with self.assertRaisesRegex(ElmError, 'parameter-class-not-compatible'):
                transport.NativeReadTransport(Path('private-driver-fixture'))
        loader.assert_not_called()

    def test_source_drift_stops_before_adapter_configuration_or_loading(self):
        with patch.object(transport, 'prepare_read_transport', side_effect=ValueError('unreviewed-source')), \
                patch.object(pt3g, 'verify_windows_config') as config, \
                patch.object(pt3g.c, 'WinDLL', create=True) as loader:
            with self.assertRaisesRegex(ValueError, 'unreviewed-source'):
                transport.NativeReadTransport(Path('private-driver-fixture'))
        config.assert_not_called(); loader.assert_not_called()

    def test_bindings_use_shared_x64_signatures_and_only_construct_is_called(self):
        native, dll, directory = self.make_native()
        self.assertEqual(dll.calls, ['PDUConstruct'])
        for name, args in vnci.NativeDpu.export_signatures().items():
            self.assertEqual(dll.functions[name].argtypes, args)
            self.assertIs(dll.functions[name].restype, vnci.U32)
        self.assertEqual(native.bound_api_names, list(vnci.NativeDpu.export_signatures()))
        self.assertEqual(native.close()['errors'], [])
        self.assertEqual(dll.calls, ['PDUConstruct', 'PDUDestruct'])
        self.assertEqual(directory.closed, 1)

    def test_every_vehicle_api_and_direct_event_entry_is_blocked_before_native_call(self):
        native, dll, _ = self.make_native()
        for name in set(vnci.NativeDpu.export_signatures()) - transport.METADATA_APIS:
            with self.subTest(name=name), self.assertRaisesRegex(ElmError, transport.BLOCK_REASON):
                native.call(name)
        with self.assertRaisesRegex(ElmError, transport.BLOCK_REASON):
            native.event()
        with self.assertRaisesRegex(ElmError, 'firmware-not-qualified'):
            native.check_firmware(pt3g.module_device(MODULE))
        self.assertEqual(dll.calls, ['PDUConstruct'])
        native.close()

    def test_unknown_object_cannot_enter_Dll_and_declared_zero_id_is_valid(self):
        native, dll, _ = self.make_native()
        with self.assertRaisesRegex(ElmError, 'not-in-source-contract'):
            native.object_id(0x8023, 'PDU_IOCTL_UNKNOWN')
        self.assertEqual(dll.calls, ['PDUConstruct'])
        self.assertEqual(native.object_id(0x8025, 'HI'), 0)
        self.assertEqual(native.object_id(0x8022, 'ISO_11898_2_DWCAN'), 0)
        native.close()

    def test_runtime_object_drift_is_not_used_as_a_resource_handle(self):
        native, dll, _ = self.make_native(); dll.id_drift = 1
        with self.assertRaisesRegex(ElmError, 'native-object-id-mismatch'):
            native.object_id(0x8025, 'HI')
        self.assertNotIn('PDUCreateComLogicalLink', dll.calls)
        native.close()

    def test_binding_failure_destructs_owned_context_and_retains_cleanup_failure(self):
        for error in (0, 0x60):
            with self.subTest(error=error):
                dll = Dll(contract_fixture()); del dll.functions['PDUGetEventItem']; dll.errors['PDUDestruct'] = error
                with self.assertRaises(ElmError if error else AttributeError):
                    self.make_native(dll)
                self.assertEqual(dll.calls, ['PDUConstruct', 'PDUDestruct'])

    def test_metadata_probe_keeps_primary_query_and_destruct_errors(self):
        native, dll, _ = self.make_native(); dll.id_drift = 1; dll.errors['PDUDestruct'] = 0x60
        with patch.object(transport, 'NativeReadTransport', return_value=native):
            result = transport.probe_read_transport_metadata(Path('fixture'))
        self.assertFalse(result['ok'])
        self.assertIn('native-object-id-mismatch', result['error'])
        self.assertTrue(result['cleanup']['errors'])
        self.assertEqual({k: result[k] for k in transport.GUARDS}, transport.GUARDS)

    def test_three_routes_use_full_configuration_readback_and_keep_unknown_fields(self):
        for route in contract_fixture()['routes']:
            with self.subTest(route=route['purpose']):
                driver = self.stored_driver()
                driver.native.create_link(route['protocol'], int(route['txId'], 16), int(route['rxId'], 16))
                self.assertEqual(driver.calls[-1], 'PDUConnect')
                self.assertEqual(driver.param_gets, 33)
                self.assertEqual(driver.table_gets, 2)
                self.assertEqual(driver.addresses[999], 12345)
                for p in route['parameters']:
                    actual = driver.addresses[driver.ids[p['name']]] if p['parameterClass'] == 'UNIQUE_ID' else driver.parameters[p['name']]
                    self.assertEqual(actual, p['candidateValue'])
                driver.native.close()

    def test_wrong_protocol_address_route_or_type_stops_before_native_setup(self):
        for protocol, tx, rx in (('unknown', 0x7e0, 0x7e8),
                ('ISO_14230_3_on_ISO_15765_2', 0x710, 0x77a),
                ('ISO_15765_3_on_ISO_15765_2', 0x710, 0x7e8),
                ('ISO_OBD_on_ISO_15765_4', True, 0x7e8)):
            driver = self.stored_driver()
            with self.assertRaisesRegex(ElmError, 'route-not-in-source-contract'):
                driver.native.create_link(protocol, tx, rx)
            self.assertEqual(driver.calls, [])

    def test_silent_parameter_and_response_setter_failures_do_not_connect(self):
        for field in ('omit_param_set', 'omit_table_set', 'reset_after_table_set', 'corrupt_unmodified_address'):
            with self.subTest(field=field):
                driver = self.stored_driver(); setattr(driver, field, True)
                with self.assertRaises(ElmError):
                    driver.native.create_link('ISO_14230_3_on_ISO_15765_2', 0x7e0, 0x7e8)
                self.assertNotIn('PDUConnect', driver.calls)
                self.assertNotIn('PDUStartComPrimitive', driver.calls)
                self.assertEqual(driver.alive, set())

    def test_pt3g_client_cannot_select_vnci_or_unmatched_module(self):
        for rows in ([], [MODULE, MODULE], [{**MODULE, 'status': 0x8062}],
                     [{**MODULE, 'type': 14}]):
            native = FakeNative(rows=rows); client = transport.ReadClient(ID, native=native)
            with self.assertRaises(ElmError):
                client.validate_adapter()
            self.assertEqual(native.connected, [])
            self.assertEqual(native.sent, [])
            client.close_restore()
        with self.assertRaisesRegex(ElmError, 'pt3g-device-id-invalid'):
            transport.ReadClient('vnci:10001', native=FakeNative())

    def test_unqualified_firmware_stops_before_module_connection(self):
        native = FakeNative(rows=[MODULE]); native.check_firmware = transport.NativeReadTransport.check_firmware.__get__(native)
        client = transport.ReadClient(ID, native=native)
        with self.assertRaisesRegex(ElmError, 'firmware-not-qualified'):
            client.validate_adapter()
        self.assertEqual(native.connected, []); self.assertEqual(native.sent, [])
        client.close_restore()

    def test_named_identity_requests_and_negative_responses_use_shared_validation(self):
        for ecu, pair, session in (('dme', ('7E0', '7E8'), '1089'), ('gateway', ('710', '77A'), '1003')):
            with self.subTest(ecu=ecu):
                client, native, clock = self.client()
                client.configure_pair(*pair, 10)
                profile = 'porsche-981-2014-' + ecu; fixtures = catalog_success_map(profile)
                parsed = parse_ath1_response(fixtures[session].decode('ascii'), req_hex=session,
                                             tx_id=int(pair[0], 16), rx_id=int(pair[1], 16), sent_sf='')
                native.response = bytes.fromhex(parsed['payload_hex'])
                self.assertTrue(client.request(session, 10)['ok'])
                native.response = bytes.fromhex('7F' + session[:2] + '11')
                self.assertFalse(client.request(session, 10)['ok'])
                self.assertEqual(native.sent, [session, session]); self.assertEqual(native.cancelled, [])
                client.close_restore()

    def test_standard_engine_and_write_blocking_are_not_bypassed_by_brand(self):
        client, native, _ = self.client(); client.configure_standard_engine(10)
        for req in ('04', '14FFFFFF', '2EF19000', '2701', '3101', '3400', '0120'):
            with self.assertRaises(ElmError):
                client.request(req, 10)
        self.assertEqual(native.sent, [])
        self.assertTrue(client.request('0100', 10)['ok'])
        self.assertEqual(native.links[-1][0], 'ISO_OBD_on_ISO_15765_4')
        client.close_restore()

    def test_cancelled_pt3g_request_preserves_missing_terminal_across_closes(self):
        native = QueueNative(); native.rows = [MODULE]
        client, _, clock = self.client(native)
        client.configure_standard_engine(10)
        with self.assertRaisesRegex(ElmError, 'deadline-expired'):
            client.request('0100', 0.05)
        self.assertEqual(native.sent, ['0100']); self.assertEqual(native.cancelled, [1])
        self.assertAlmostEqual(clock.now, 1.05)
        first = client.close_restore()
        self.assertEqual(first['errors'], ['d-pdu:primitive-terminal-timeout'])
        first['errors'].clear()
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:primitive-terminal-timeout'])
        self.assertEqual(native.closed, 1)

    def test_desktop_live_pt3g_still_refuses_before_constructing_new_transport(self):
        with tempfile.TemporaryDirectory() as scratch, patch.object(transport, 'NativeReadTransport') as loader:
            result = run_session(profile_id='porsche-981-2014-dme', mode='live', device_id=ID,
                confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch))
        self.assertFalse(result['ok']); self.assertEqual(result['error'], transport.BLOCK_REASON)
        loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
