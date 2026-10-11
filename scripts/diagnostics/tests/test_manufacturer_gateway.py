"""Qualified Gateway software route with synthetic ports/events, never hardware."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.elm import ElmError, parse_ath1_response
from scripts.diagnostics.manufacturer_acquisition import GATEWAY_PROFILE, historical_fixtures
from scripts.diagnostics.manufacturer_transport import prepared_read_contract, transport_rehearsal
from scripts.diagnostics.realtime_preparation import handle_ready
from scripts.diagnostics.session_simulator import SessionSimPort, catalog_success_map, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.sessions import IDENTITY_FIELDS
from scripts.diagnostics.tests.test_manufacturer_read import bundle, request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_vnci import FakeNative, ID
from scripts.diagnostics.vnci import DpuClient

CATALOG = 'porsche-981-2014-gateway'
MEASUREMENT = bytes.fromhex('62F1022A07')


def profile():
    return {'profileId': GATEWAY_PROFILE, 'generation': None, 'ecuIds': [9],
        'status': 'identity-matched', 'parameters': [field('a', '22F102'), field('b', '22F102', 1)],
        'identity': [], 'checklist': []}


def gateway_request(**kwargs):
    return {**request(), 'profileId': GATEWAY_PROFILE, 'ecuId': 9, **kwargs}


def fixture_profile():
    p = profile()
    for parameter in p['parameters']:
        parameter.update(decodedSampleCount=1, examples=[{'groupId': 'gateway', 'responseFrameId': 'f',
            'pduSha256': hashlib.sha256(MEASUREMENT).hexdigest()}])
    g = {'groupId': 'gateway', 'requestHex': '22F102', 'txId': 0x710, 'rxId': 0x77A,
        'adapterStream': [2, 2], 'capturePhase': 'before-coding',
        'samples': [{'responseFrameId': 'f', 'pduHex': MEASUREMENT.hex()}]}
    return p, g


def native_fixture(identity=None, measurement=MEASUREMENT):
    native = FakeNative(); frames = catalog_success_map(CATALOG, identity_text=identity)
    def events(payload, cop):
        hx = payload.hex().upper()
        if hx == '22F102': return FakeNative.reply(measurement, cop)
        parsed = parse_ath1_response(frames[hx].decode('ascii'), req_hex=hx,
            rx_id=0x77A, tx_id=0x710, sent_sf='')
        return FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
    native.make_events = events
    return native


class GatewayTests(unittest.TestCase):
    def run_case(self, *, device='elm', identity=None, measurement=MEASUREMENT,
                 modify=lambda client: None, req_changes=None, **kwargs):
        native = native_fixture(identity, measurement); ports = []
        def opened(_device, *, adapter_model=None):
            self.assertIsNone(adapter_model)
            port = SessionSimPort(CATALOG, identity_text=identity)
            port.ecu_map['22F102'] = ath1_prompt(isotp_ath1_lines(0x77A, measurement))
            modify(port); ports.append(port); return port, 'mocked-port'
        modify(native) if device == 'vnci' else None
        req = gateway_request(**(req_changes or {}))
        if device == 'vnci': req['deviceId'] = ID
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root, profile())
            with patch('scripts.diagnostics.connection.open_selected_port', side_effect=opened), \
                    patch('scripts.diagnostics.vnci.NativeDpu', return_value=native):
                result = read.read_manufacturer(req, root=root, **kwargs)
        return result, native if device == 'vnci' else ports

    def test_contract_and_preflight_bind_gateway_pair_session_and_seven_fields(self):
        for device in ['elm', 'vnci']:
            result, clients = self.run_case(device=device, check_only=True)
            self.assertTrue(result['ok'], result); c = result['readContract']
            self.assertEqual((c['txId'], c['rxId'], c['ecuId']), (0x710, 0x77A, 9))
            self.assertEqual(c['generation'], '981'); self.assertEqual(c['catalogProfileId'], CATALOG)
            self.assertEqual(c['session'], {'requestHex': '1003', 'positivePrefixHex': '5003'})
            self.assertEqual(len(c['identityRequests']), 7); self.assertFalse(result['executionEnabled'])
            self.assertEqual(clients.connected if device == 'vnci' else clients, [])

    def test_ready_plan_exposes_same_typed_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root, profile())
            req = {'action': 'ready-plan', 'generation': '981', 'ecuId': 9,
                'profileId': GATEWAY_PROFILE, 'parameterIds': ['a' * 64, 'b' * 64]}
            result = handle_ready(req, root)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['plan']['preparedTransport'], prepared_read_contract(profile(), req['parameterIds']))

    def test_shared_9x1_name_does_not_qualify_982_or_other_gateway_version(self):
        for changes in [{'generation': '982'}, {'ecuId': 1}, {'ecuId': True},
                        {'profileId': GATEWAY_PROFILE.replace('A7_1', 'A7_2')}]:
            with patch.object(read, 'verified_profile', side_effect=AssertionError('loaded')):
                result = read.read_manufacturer(gateway_request(**changes))
                self.assertEqual(result['error'], 'manufacturer-profile-unqualified')
        for changes in [{'status': 'definition-only'}, {'generation': '982'},
                        {'generation': '981'}, {'ecuIds': [1, 9]}]:
            with self.assertRaisesRegex(ValueError, 'manufacturer-profile-unqualified'):
                prepared_read_contract({**profile(), **changes}, ['a' * 64])

    def test_both_transports_identify_gateway_before_sampling_once_per_group(self):
        for device in ['elm', 'vnci']:
            frames = []; result, clients = self.run_case(device=device, on_frame=frames.append)
            self.assertTrue(result['ok'], result)
            sent = clients.sent if device == 'vnci' else clients[0].ecu_payloads
            self.assertEqual(sent, ['1003'] + [row['requestHex'] for row in result['readContract']['identityRequests']] + ['22F102'])
            self.assertEqual([s['value'] for s in result['samples']], [42, 7])
            self.assertEqual(result['transport']['qualification']['variantLink']['profile_id'], GATEWAY_PROFILE)
            self.assertTrue(result['transport']['closed']); self.assertFalse(result['liveVerified'])
            if device == 'vnci':
                self.assertEqual(clients.links, [('ISO_15765_3_on_ISO_15765_2', 0x710, 0x77A)])
                self.assertEqual(clients.closed, 1); self.assertEqual(frames, [])
            else:
                self.assertTrue(clients[0].closed); self.assertTrue(frames)
                self.assertTrue(all(frame['canId'] == 0x77A for frame in frames))

    def test_every_required_identity_mismatch_stops_before_measurement(self):
        for _field, operation_id in IDENTITY_FIELDS[CATALOG]:
            for device in ['elm', 'vnci']:
                with self.subTest(field=_field, device=device):
                    result, clients = self.run_case(device=device, identity={operation_id: 'WRONG'})
                    self.assertEqual(result['error'], 'identity-mismatch'); self.assertEqual(result['samples'], [])
                    self.assertNotIn('22F102', clients.sent if device == 'vnci' else clients[0].ecu_payloads)
                    self.assertTrue(result['transport']['closed'])

    def test_whitelist_excludes_other_head_routes_dtc_writes_and_unselected_groups(self):
        native = native_fixture(); client = DpuClient(ID, native=native)
        client.validate_adapter(); client.configure_manufacturer(profile(), ['a' * 64], time.monotonic() + 2)
        for hx in ['1089', '1A9C', '010C', '190208', '14FFFFFF', '22F103', '2EF10200', '2701', '3400']:
            with self.subTest(hx=hx), self.assertRaises(ElmError): client.request(hx, time.monotonic() + 2)
        self.assertEqual(native.sent, []); client.close_restore()

    def test_negative_wrong_echo_short_and_session_mismatch_make_no_values(self):
        for device in ['elm', 'vnci']:
            for payload in [b'\x7F\x22\x31', b'\x62\xF1\x03\x2A\x07', b'\x62\xF1\x02\x2A']:
                result, _clients = self.run_case(device=device, measurement=payload)
                self.assertFalse(result['ok']); self.assertEqual(result['samples'], [])
                self.assertTrue(result['transport']['closed'])
        def wrong_session(port): port.ecu_map['1003'] = ath1_prompt(isotp_ath1_lines(0x77A, b'\x50\x89'))
        result, clients = self.run_case(modify=wrong_session)
        self.assertEqual(result['error'], 'wrong-sid-or-did-echo'); self.assertNotIn('22F102', clients[0].ecu_payloads)

    def test_cancel_keeps_complete_cycle_and_closes_each_transport(self):
        for device in ['elm', 'vnci']:
            event = threading.Event(); cycles = []
            def stop(value): cycles.append(value); event.set()
            result, _ = self.run_case(device=device, cancel_event=event, on_cycle=stop, req_changes={'cycleCount': 3})
            self.assertEqual(result['error'], 'cancelled'); self.assertEqual(result['completedCycles'], 1)
            self.assertEqual(result['samples'], cycles[0]['samples']); self.assertTrue(result['transport']['closed'])

    def test_disconnect_requalifies_gateway_and_never_reuses_dme_route(self):
        count = 0
        def fail_first(port):
            nonlocal count
            count += 1
            if count == 1:
                write = port.write
                def interrupted(data):
                    if data.startswith(b'03 22 F1 02'): raise ConnectionError('disconnect')
                    return write(data)
                port.write = interrupted
        result, ports = self.run_case(modify=fail_first)
        self.assertTrue(result['ok'], result); self.assertEqual(len(ports), 2)
        self.assertTrue(all(p.closed and p.ecu_payloads[0] == '1003' for p in ports))
        self.assertTrue(result['transport']['recovery'][0]['recovered'])

    def test_historical_fixture_keeps_exact_gateway_route_phase_and_reference(self):
        p, group = fixture_profile()
        result = transport_rehearsal(p, ['a' * 64, 'b' * 64], [group])
        self.assertTrue(result['ok'], result); self.assertTrue(result['simulation']); self.assertFalse(result['vehicleDataCollected'])
        for changes in [{'txId': 0x7E0, 'rxId': 0x7E8}, {'capturePhase': 'coding-workflow'}, {'adapterStream': [1, 2]}]:
            with self.assertRaisesRegex(ValueError, 'reference-mismatch'):
                historical_fixtures(p, ['a' * 64], [{**group, **changes}])


if __name__ == '__main__': unittest.main()
