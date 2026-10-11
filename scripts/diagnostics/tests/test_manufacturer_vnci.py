"""Production read engine with injected native D-PDU events; no DLL/head/car IO."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.elm import ElmError, parse_ath1_response
from scripts.diagnostics.manufacturer_transport import CATALOG_PROFILE, prepared_read_contract
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.tests.test_manufacturer_read import bundle, request
from scripts.diagnostics.tests.test_manufacturer_acquisition import profile
from scripts.diagnostics.tests.test_vnci import FakeNative, ID
from scripts.diagnostics.vnci import DpuClient


def vnci_request(**kwargs):
    return {**request(), 'deviceId': ID, **kwargs}


def native_fixture(*, identity=None, measurement=b'\x62\xF1\x00\x2A\x07'):
    native = FakeNative()
    frames = catalog_success_map(CATALOG_PROFILE, identity_text=identity)
    def events(payload, cop):
        hx = payload.hex().upper()
        if hx == '22F100':
            return FakeNative.reply(measurement, cop)
        parsed = parse_ath1_response(frames[hx].decode('ascii'), req_hex=hx,
                                    rx_id=0x7E8, tx_id=0x7E0, sent_sf='')
        return FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
    native.make_events = events
    return native


class ManufacturerVnciTests(unittest.TestCase):
    def run_case(self, native=None, **kwargs):
        native = native or native_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            with patch('scripts.diagnostics.vnci.NativeDpu', return_value=native), \
                    patch('scripts.diagnostics.connection.open_selected_port', side_effect=AssertionError('serial opened')):
                result = read.read_manufacturer(vnci_request(**kwargs.pop('request_changes', {})), root=root, **kwargs)
        return result, native

    def test_contract_preflight_is_typed_dpu_and_never_loads_native_or_fixtures(self):
        with patch('scripts.diagnostics.vnci.DpuClient', side_effect=AssertionError('native constructed')):
            result, native = self.run_case(check_only=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['readContract']['transportKind'], 'd-pdu-iso-tp')
        self.assertEqual(result['transport']['kind'], 'd-pdu-iso-tp')
        self.assertFalse(result['readContract']['rawCanFramesAvailable'])
        self.assertFalse(result['executionEnabled']); self.assertEqual(native.connected, [])

    def test_default_named_client_does_not_gain_manufacturer_requests(self):
        native = native_fixture(); client = DpuClient(ID, native=native)
        client.validate_adapter(); client.configure_pair('7E0', '7E8', time.monotonic() + 2)
        with self.assertRaisesRegex(ElmError, 'unknown-request'):
            client.request('22F100', time.monotonic() + 2)
        self.assertEqual(native.sent, []); client.close_restore()

    def test_selected_read_contract_replaces_broader_named_allowlist(self):
        native = native_fixture(); client = DpuClient(ID, native=native)
        client.validate_adapter(); client.configure_manufacturer(profile(), ['a' * 64], time.monotonic() + 2)
        contract = prepared_read_contract(profile(), ['a' * 64], transport_kind='d-pdu-iso-tp')
        expected = {r['requestHex'] for r in contract['identityRequests']} | {'1089', '22F100'}
        self.assertEqual(client.allowed, expected)
        for hx in ['22F101', '21FF', '010C', '1800FF00', '14FF00', '2EF10001', '2701', '3101', '3400']:
            with self.subTest(hx=hx), self.assertRaises(ElmError):
                client.request(hx, time.monotonic() + 2)
        self.assertEqual(native.sent, [])
        self.assertEqual(native.links, [('ISO_14230_3_on_ISO_15765_2', 0x7E0, 0x7E8)])
        client.close_restore()

    def test_invalid_profile_switch_fails_closed_before_new_link(self):
        native = native_fixture(); client = DpuClient(ID, native=native)
        client.validate_adapter(); client.configure_standard_engine(time.monotonic() + 2)
        invalid = deepcopy(profile()); invalid['generation'] = '982'
        with self.assertRaisesRegex(ValueError, 'profile-unqualified'):
            client.configure_manufacturer(invalid, ['a' * 64], time.monotonic() + 2)
        self.assertEqual(len(native.links), 1); self.assertFalse(client._configured); self.assertEqual(client.allowed, set())
        client.close_restore()

    def test_production_native_pdu_branch_qualifies_complete_identity_then_groups(self):
        frames = []; result, native = self.run_case(on_frame=frames.append)
        self.assertTrue(result['ok'], result)
        self.assertEqual([s['value'] for s in result['samples']], [42, 7])
        self.assertEqual(native.sent[0], '1089'); self.assertEqual(native.sent[-1], '22F100')
        self.assertEqual(len(native.sent), 7); self.assertEqual(native.sent.count('22F100'), 1)
        self.assertEqual(result['transport']['kind'], 'd-pdu-iso-tp')
        self.assertTrue(result['transport']['qualification']['observedProfileMatch'])
        self.assertTrue(result['vehicleDataCollected']); self.assertFalse(result['simulation']); self.assertFalse(result['liveVerified'])
        self.assertFalse(result['independentLiveVerified']); self.assertTrue(result['transport']['closed'])
        self.assertEqual(native.closed, 1); self.assertEqual(frames, [], 'D-PDU is never invented CAN frames')
        self.assertTrue(all(r['layer'] == 'diagnostic-pdu' for r in result['transport']['rawLog'] if 'dir' in r))

    def test_wrong_fresh_identity_or_selected_head_never_reads_measurements(self):
        mismatch = native_fixture(identity={'dme-dsn': 'X200'})
        result, native = self.run_case(mismatch)
        self.assertEqual(result['error'], 'identity-mismatch'); self.assertNotIn('22F100', native.sent)
        self.assertTrue(result['transport']['closed']); self.assertEqual(native.closed, 1)
        missing = native_fixture(); missing.rows = []
        result, native = self.run_case(missing)
        self.assertIn('device-identity-missing-or-ambiguous', result['error']); self.assertEqual(native.sent, [])
        self.assertEqual(native.closed, 1)

    def test_negative_wrong_echo_or_short_group_never_makes_values(self):
        for payload in [b'\x7F\x22\x31', b'\x62\xF1\x01\x2A\x07', b'\x62\xF1\x00\x2A']:
            with self.subTest(payload=payload):
                result, native = self.run_case(native_fixture(measurement=payload))
                self.assertFalse(result['ok']); self.assertEqual(result['samples'], [])
                self.assertEqual(native.closed, 1)
                # A matched physical PDU was received for the short group;
                # it still yields no decoded values and no successful cycle.
                self.assertEqual(result['vehicleDataCollected'], payload.startswith(b'\x62\xF1\x00'))

    def test_cancel_after_cycle_preserves_batch_and_native_terminal_close(self):
        stop = threading.Event(); cycles = []
        def completed(doc): cycles.append(doc); stop.set()
        result, native = self.run_case(cancel_event=stop, on_cycle=completed, request_changes={'cycleCount': 3})
        self.assertEqual(result['error'], 'cancelled'); self.assertEqual(result['completedCycles'], 1)
        self.assertEqual(result['samples'], cycles[0]['samples']); self.assertEqual(native.sent.count('1089'), 1)
        self.assertEqual(native.sent.count('22F100'), 1); self.assertTrue(result['transport']['closed']); self.assertEqual(native.closed, 1)

    def test_native_cleanup_failure_is_not_closed_or_success_and_never_replayed(self):
        native = native_fixture(); native.close_errors = ['PDUModuleDisconnect:failure']
        result, native = self.run_case(native)
        self.assertFalse(result['ok']); self.assertFalse(result['transport']['closed'])
        self.assertIn('recovery-close-failed', result['error']); self.assertEqual(len(result['samples']), 2)
        self.assertEqual(native.sent.count('22F100'), 1); self.assertEqual(native.closed, 1)

    def test_firmware_mismatch_stops_before_module_connect_link_or_ecu_requests(self):
        native = native_fixture()
        def mismatch(device): raise ElmError('vnci-firmware-mismatch')
        native.check_firmware = mismatch
        result, native = self.run_case(native)
        self.assertEqual(result['error'], 'vnci-firmware-mismatch')
        self.assertEqual(native.connected, []); self.assertEqual(native.links, []); self.assertEqual(native.sent, [])
        self.assertEqual(native.closed, 1)


if __name__ == '__main__': unittest.main()
