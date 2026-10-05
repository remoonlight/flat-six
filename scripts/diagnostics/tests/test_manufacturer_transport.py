from copy import deepcopy
import hashlib
import threading
import unittest

from scripts.diagnostics.manufacturer_transport import transport_rehearsal, prepared_read_contract, CATALOG_PROFILE
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_acquisition import profile


def fixtures():
    p = profile()
    raw = bytes.fromhex('62F1002A07') + bytes(range(40))
    for param in p['parameters']:
        param.update(decodedSampleCount=1, examples=[{'groupId': 'g', 'responseFrameId': 'f',
            'pduSha256': hashlib.sha256(raw).hexdigest()}])
    groups = [{'groupId': 'g', 'requestHex': '22F100', 'txId': 0x7E0, 'rxId': 0x7E8,
        'adapterStream': [2, 2], 'capturePhase': 'before-coding',
        'samples': [{'responseFrameId': 'f', 'pduHex': raw.hex()}]}]
    return p, groups, raw


class TransportTests(unittest.TestCase):
    def test_contract_contains_explicit_session_identity_and_selected_groups(self):
        p, _, _ = fixtures()
        contract = prepared_read_contract(p, ['a' * 64, 'b' * 64])
        self.assertEqual((contract['txId'], contract['rxId']), (0x7E0, 0x7E8))
        self.assertEqual(contract['session']['requestHex'], '1089')
        self.assertEqual(len(contract['groups']), 1)
        self.assertEqual(len(contract['identityRequests']), 5)
        self.assertFalse(contract['executionEnabled']); self.assertFalse(contract['liveVerified'])
        self.assertEqual(contract, prepared_read_contract(p, ['a' * 64, 'b' * 64]))

    def test_all_requests_and_spans_checked_before_open(self):
        p, groups, _ = fixtures()
        def forbidden(): raise AssertionError('port opened')
        for hx in ('22F10000', '21A400', '2EF100', ''):
            invalid = deepcopy(p); invalid['parameters'][1]['requestHex'] = hx
            out = transport_rehearsal(invalid, ['a' * 64, 'b' * 64], groups, port_factory=forbidden)
            self.assertFalse(out['ok']); self.assertEqual(out['samples'], [])
        invalid = deepcopy(p); invalid['parameters'][1]['dataSpan']['dataMin'] = 100
        out = transport_rehearsal(invalid, ['a' * 64, 'b' * 64], groups, port_factory=forbidden)
        self.assertEqual(out['error'], 'manufacturer-field-span-mismatch')

    def test_invalid_budget_never_opens_and_returns_structured_failure(self):
        p, groups, _ = fixtures()
        def forbidden(): raise AssertionError('port opened')
        for budget in (float('nan'), float('inf'), True, 0, -1, 61, 'bad'):
            out = transport_rehearsal(p, ['a' * 64], groups, budget_s=budget, port_factory=forbidden)
            self.assertFalse(out['ok']); self.assertEqual(out['samples'], [])

    def run_case(self, modify=lambda _: None, **kwargs):
        p, groups, raw = fixtures()
        ports = []
        def factory():
            port = SessionSimPort(CATALOG_PROFILE)
            port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, raw))
            modify(port)
            ports.append(port)
            return port
        out = transport_rehearsal(p, ['a' * 64, 'b' * 64], groups, port_factory=factory, **kwargs)
        return out, ports

    def test_byte_transport_multiframe_identity_once_per_group_and_close(self):
        out, ports = self.run_case()
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['value'] for s in out['samples']], [42, 7])
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)
        self.assertEqual(ports[0].ecu_payloads[0], '1089')
        self.assertTrue(out['transport']['qualification']['observedProfileMatch'])
        self.assertTrue(ports[0].closed)
        self.assertFalse(out['vehicleDataCollected'])

    def test_live_denied_before_factory(self):
        p, groups, _ = fixtures()
        def forbidden(): raise AssertionError('port opened')
        self.assertEqual(transport_rehearsal(p, ['a' * 64], groups, mode='live', port_factory=forbidden)['error'],
                         'manufacturer-transport-unqualified')

    def test_identity_mismatch_prevents_measurement(self):
        def change(port):
            wrong = SessionSimPort(CATALOG_PROFILE, 'identity-mismatch')
            port.ecu_map.update(wrong.ecu_map)
        out, ports = self.run_case(change)
        self.assertEqual(out['error'], 'identity-mismatch')
        self.assertNotIn('22F100', ports[0].ecu_payloads)
        self.assertTrue(ports[0].closed)

    def test_pending_same_prompt_not_resent(self):
        def pending(port):
            pdu = bytes.fromhex('7F2278')
            port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, pdu) +
                isotp_ath1_lines(0x7E8, bytes.fromhex('62F1002A07')))
        out, ports = self.run_case(pending)
        self.assertTrue(out['ok'], out)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)

    def test_negative_pending_no_data_wrong_echo_and_truncated_are_terminal(self):
        values = [bytes.fromhex('7F2231'), bytes.fromhex('7F2278'), bytes.fromhex('62F1012A07'),
                  bytes.fromhex('62F1002A')]
        for pdu in values:
            with self.subTest(pdu=pdu):
                out, ports = self.run_case(lambda port: port.ecu_map.update(
                    {'22F100': ath1_prompt(isotp_ath1_lines(0x7E8, pdu))}))
                self.assertFalse(out['ok']); self.assertEqual(out['samples'], [])
                self.assertEqual(len(ports), 1); self.assertTrue(ports[0].closed)
        out, _ = self.run_case(lambda p: p.ecu_map.pop('22F100'))
        self.assertFalse(out['ok'])

    def test_interrupted_read_recovers_after_fresh_identity(self):
        number = 0
        def modify(port):
            nonlocal number
            number += 1
            if number == 1:
                write = port.write
                def disconnect(data):
                    if data.startswith(b'03 22 F1 00'): raise ConnectionError('disconnect')
                    return write(data)
                port.write = disconnect
        out, ports = self.run_case(modify)
        self.assertTrue(out['ok'], out)
        self.assertEqual(len(ports), 2)
        self.assertTrue(all(p.closed for p in ports))
        self.assertEqual(ports[1].ecu_payloads[0], '1089')
        self.assertTrue(out['transport']['recovery'][0]['recovered'])

    def test_three_attempts_exhausted_no_more_ports(self):
        def modify(port):
            write = port.write
            def disconnect(data):
                if data.startswith(b'03 22 F1 00'): raise ConnectionError('disconnect')
                return write(data)
            port.write = disconnect
        out, ports = self.run_case(modify)
        self.assertEqual(out['error'], 'recovery-exhausted')
        self.assertEqual(len(ports), 4)
        self.assertEqual(len(out['transport']['recovery'][0]['attempts']), 3)
        self.assertTrue(all(p.closed for p in ports))

    def test_cancel_before_open_and_close_failure(self):
        event = threading.Event(); event.set()
        out, ports = self.run_case(cancel_event=event)
        self.assertEqual(out['error'], 'cancelled'); self.assertEqual(ports, [])
        def modify(port):
            def bad_close(): raise OSError('cannot-close')
            port.close = bad_close
        out, ports = self.run_case(modify)
        self.assertFalse(out['ok']); self.assertFalse(out['transport']['closed'])


if __name__ == '__main__': unittest.main()
