from copy import deepcopy
import hashlib
import threading
import time
import unittest
from unittest import mock

from scripts.diagnostics.manufacturer_transport import transport_rehearsal, prepared_read_contract, CATALOG_PROFILE
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_acquisition import profile
from scripts.diagnostics.tests.test_realtime_preparation import field


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
    def test_identity_details_distinguish_grouped_source_fields(self):
        from scripts.diagnostics.manufacturer_transport import identity_details
        first = field('a'); second = field('b', offset=1)
        first.update(name=None, labels=[{'text':'编程状态'}, {'text':'数据记录不可靠'}])
        second.update(name=None, labels=[{'text':'编程状态'}, {'text':'可编程'}, {'text':''}])
        records = [first, second]; before = deepcopy(records)
        result = identity_details({'identityRequests':[{'requestHex':'22F100','records':records}]}, {'22F100':'62F1000100'})
        self.assertEqual([row['label'] for row in result], ['编程状态 / 数据记录不可靠', '编程状态 / 可编程'])
        self.assertEqual([row['value'] for row in result], ['1','0'])
        self.assertEqual([row['recordIndex'] for row in result], [0,1])
        self.assertEqual({row['pduSha256'] for row in result}, {hashlib.sha256(bytes.fromhex('62F1000100')).hexdigest()})
        self.assertEqual(records, before)

    def test_identity_details_keep_explicit_names_and_skip_unresolved_or_invalid_fields(self):
        from scripts.diagnostics.manufacturer_transport import identity_details
        named = field('a'); named.update(name='来源字段名称', labels=[{'text':'上级标题'}])
        unknown = field('b'); unknown.update(name=None, labels=[{'text':''}, {'text':None}])
        contract = {'identityRequests':[{'requestHex':'22F100','records':[named,unknown]}]}
        result = identity_details(contract, {'22F100':'62F10001'})
        self.assertEqual([row['label'] for row in result], ['来源字段名称'])
        self.assertEqual(identity_details(contract, {'22F100':'62F100'}), [])

    def slow_grouped_stream(self, *, response_delay=3, adapter_delay=0, cleanup_delay=0, cleanup_error=None, **kwargs):
        from scripts.diagnostics.elm import ElmClient
        from scripts.diagnostics.manufacturer_transport import _client_cycles
        p = profile()
        p['parameters'] = [field(key, f'22F1{i:02X}') for i, key in enumerate('0123456789ab')]
        clock, ports, request_budgets = [100.0], [], []
        def factory(cancel):
            port = SessionSimPort(CATALOG_PROFILE); ports.append(port)
            original = port.write
            for row in p['parameters']:
                hx = row['requestHex']
                port.ecu_map[hx] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('62' + hx[2:] + '2A')))
            def write(data):
                if data.startswith(b'03 22 F1 '): clock[0] += response_delay
                if data.startswith(b'ATI\r'): clock[0] += adapter_delay
                return original(data)
            port.write = write
            client = ElmClient(port, cancel_event=cancel)
            request = client.request
            def timed_request(hx, deadline):
                if hx.startswith('22F1'): request_budgets.append(deadline - clock[0])
                return request(hx, deadline)
            client.request = timed_request
            close = client.close_restore
            def delayed_close():
                result = close()
                clock[0] += cleanup_delay
                if cleanup_error: result['errors'].append(cleanup_error)
                return result
            client.close_restore = delayed_close
            return client
        with mock.patch('scripts.diagnostics.manufacturer_transport.time.monotonic', side_effect=lambda: clock[0]):
            out = _client_cycles(p, [row['id'] for row in p['parameters']], factory, streaming=True, **kwargs)
        return out, ports, clock[0] - 100, request_budgets

    def test_twelve_healthy_requests_continue_when_cycle_exceeds_qualification_budget(self):
        for budget in (5, 30):
            with self.subTest(budget=budget):
                out, ports, elapsed, requests = self.slow_grouped_stream(cycles=2, budget_s=budget)
                self.assertTrue(out['ok'], out.get('error'))
                self.assertEqual(out['completedCycles'], 2)
                self.assertEqual(len(out['samples']), 24)
                self.assertEqual(elapsed, 72)
                self.assertEqual(requests, [min(15, budget)] * 24)
                self.assertEqual(ports[0].ecu_payloads.count('1089'), 1)
                self.assertTrue(ports[0].closed)
                self.assertEqual(out['transport']['closeErrors'], [])

    def test_slow_grouped_stream_does_not_extend_original_batch_deadline(self):
        out, ports, elapsed, requests = self.slow_grouped_stream(cycles=None, batch_deadline=148)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'deadline-expired')
        self.assertEqual(out.get('stopReason'), 'time-limit')
        self.assertEqual(out['completedCycles'], 1)
        self.assertEqual(len(out['samples']), 15)
        self.assertEqual(elapsed, 48)
        self.assertEqual(requests[-1], 3)
        self.assertTrue(ports[0].closed)
        # The simulated write reaches the hard cutoff. A poisoned link must
        # still close without sending further cleanup requests on that link.
        self.assertTrue(all(error == 'poisoned:write-deadline' for error in out['transport']['closeErrors']))

    def test_ten_minutes_expiring_mid_cycle_preserves_partial_data_and_time_limit_reason(self):
        out, ports, elapsed, requests = self.slow_grouped_stream(cycles=None)
        self.assertEqual(elapsed, 600)
        self.assertEqual(out.get('stopReason'), 'time-limit')
        self.assertEqual(out['error'], 'deadline-expired')
        self.assertEqual(out['completedCycles'], 16)
        self.assertEqual(len(out['samples']), 199)
        self.assertEqual(len(requests), 200)
        self.assertEqual(out['connectionEvents'], [])
        self.assertTrue(ports[0].closed)

    def test_qualification_timeout_before_batch_expiry_remains_failure_after_slow_cleanup(self):
        out, ports, elapsed, requests = self.slow_grouped_stream(cycles=None, adapter_delay=16, cleanup_delay=600)
        self.assertEqual(out['error'], 'port-io:write-deadline')
        self.assertNotEqual(out.get('stopReason'), 'time-limit')
        self.assertEqual(elapsed, 616)
        self.assertEqual(len(requests), 0)
        self.assertEqual(out['samples'], [])
        self.assertTrue(ports[0].closed)

    def test_batch_expiry_does_not_hide_release_failure(self):
        out, ports, _, _ = self.slow_grouped_stream(cycles=None, batch_deadline=148, cleanup_error='close:fixture-error')
        self.assertEqual(out['error'], 'recovery-close-failed')
        self.assertNotEqual(out.get('stopReason'), 'time-limit')
        self.assertTrue(ports[0].closed)

    def test_expired_or_invalid_absolute_batch_deadline_never_opens_a_port(self):
        from scripts.diagnostics.manufacturer_transport import _client_cycles
        p,_,_=fixtures()
        for cutoff,error in [(0,'deadline-expired'),(False,'manufacturer-budget-invalid'),(float('nan'),'manufacturer-budget-invalid')]:
            with self.subTest(cutoff=cutoff):
                factory=mock.Mock(side_effect=AssertionError('port must stay closed'))
                out=_client_cycles(p,['a'*64],factory,streaming=True,cycles=None,batch_deadline=cutoff)
                self.assertFalse(out['ok']);self.assertEqual(out['error'],error)
                self.assertEqual(out['samples'],[]);factory.assert_not_called()

    def test_transient_invalid_values_are_polled_again_and_recover_without_fake_samples(self):
        p, groups, _ = fixtures()
        for param in p['parameters']:
            param['formula']['text'] += 'Ic:[0xFF]->0xF0005B27'
        events, ports = [], []
        def factory():
            port = SessionSimPort(CATALOG_PROFILE); ports.append(port)
            original = port.write
            replies = iter(('62F1002A07', '62F100FFFF', '62F1002B08'))
            def write(data):
                if data.startswith(b'03 22 F1 00'):
                    port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex(next(replies))))
                return original(data)
            port.write = write
            return port
        out = transport_rehearsal(p, ['a'*64, 'b'*64], groups, port_factory=factory,
                                  cycles=3, streaming=True, on_cycle=events.append)
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['completedCycles'], 3)
        self.assertEqual([len(e['samples']) for e in events], [2, 0, 2])
        self.assertEqual([len(e['invalidParameters']) for e in events], [0, 2, 0])
        self.assertEqual([s['value'] for s in out['samples']], [42, 7, 43, 8])
        self.assertEqual(out['invalidParameters'], [])
        self.assertEqual(out['unavailableParameters'], [])
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 3)
        self.assertTrue(ports[0].closed)

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

    def test_continuous_session_identity_once_events_keep_every_cycle(self):
        events = []
        out, ports = self.run_case(cycles=3, streaming=True, on_cycle=events.append)
        self.assertTrue(out['ok'], out)
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0].ecu_payloads.count('1089'), 1)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 3)
        self.assertEqual([e['cycle'] for e in events], [1, 2, 3])
        self.assertEqual(out['samples'], [sample for e in events for sample in e['samples']])
        self.assertEqual(out['transactions'], [item for e in events for item in e['transactions']])
        self.assertEqual(out['completedCycles'], 3)
        self.assertTrue(out['transport']['closed'])

    def test_stream_retains_more_than_old_sample_cycle_and_log_limits(self):
        events = []
        cancel = threading.Event()
        # Exercise production framing/retention, not 3001 seconds of UI cadence.
        with mock.patch.object(cancel, 'wait', return_value=False):
            out, ports = self.run_case(cycles=3001, streaming=True, on_cycle=events.append, cancel_event=cancel)
        self.assertTrue(out['ok'], out.get('error'))
        self.assertEqual(out['completedCycles'], 3001)
        self.assertEqual(len(out['samples']), 6002)
        self.assertEqual(len(events), 3001)
        self.assertEqual(out['samples'], [s for e in events for s in e['samples']])
        self.assertGreater(len(__import__('json').dumps(out['transport']['rawLog']).encode()), 1024 * 1024)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 3001)
        self.assertTrue(ports[0].closed)

    def test_stop_between_cycles_retains_values_and_reports_cancelled_failure(self):
        event = threading.Event()
        def stop(_): event.set()
        out, ports = self.run_case(cycles=3, streaming=True, on_cycle=stop, cancel_event=event)
        self.assertFalse(out['ok'], out)
        self.assertEqual(out['error'], 'cancelled')
        self.assertEqual(out['completedCycles'], 1)
        self.assertEqual(len(out['samples']), 2)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)
        self.assertTrue(out['transport']['closed'])
        self.assertEqual(out['transport']['closeErrors'], [])
        self.assertTrue(ports[0].closed)

    def test_ten_minute_deadline_stops_before_next_cycle_and_closes_transport(self):
        actual_clock = time.monotonic
        elapsed = [0]
        events = []
        def completed(value):
            events.append(value)
            elapsed[0] = 600
        with mock.patch('scripts.diagnostics.manufacturer_transport.time.monotonic',
                        side_effect=lambda: actual_clock() + elapsed[0]):
            out, ports = self.run_case(cycles=None, streaming=True, on_cycle=completed)
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['stopReason'], 'time-limit')
        self.assertEqual(out['completedCycles'], 1)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)
        self.assertEqual(out['samples'], events[0]['samples'])
        self.assertTrue(out['transport']['closed'])
        self.assertEqual(out['transport']['closeErrors'], [])
        self.assertTrue(ports[0].closed)

    def test_later_unavailable_response_retains_first_cycle_and_stops_when_all_unavailable(self):
        events = []
        def modify(port):
            original = port.write
            calls = 0
            def write(data):
                nonlocal calls
                if data.startswith(b'03 22 F1 00'):
                    calls += 1
                    if calls == 2:
                        port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('7F2231')))
                return original(data)
            port.write = write
        out, ports = self.run_case(modify, cycles=3, streaming=True, on_cycle=events.append)
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['completedCycles'], 2)
        self.assertEqual(len(out['samples']), 2)
        self.assertEqual([e['ok'] for e in events], [True, True])
        self.assertEqual(out['stopReason'], 'all-parameters-unavailable')
        self.assertEqual([p['parameterId'] for p in out['unavailableParameters']], ['a'*64, 'b'*64])
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 2)
        self.assertTrue(out['transport']['closed'])

    def test_live_denied_before_factory(self):
        p, groups, _ = fixtures()
        def forbidden(): raise AssertionError('port opened')
        self.assertEqual(transport_rehearsal(p, ['a' * 64], groups, mode='live', port_factory=forbidden)['error'],
                         'manufacturer-transport-unqualified')

    def test_unqualified_flags_preserved_for_success_and_failure(self):
        p, groups, _ = fixtures()
        for out in (transport_rehearsal(p, ['a' * 64], groups),
                    transport_rehearsal(p, ['a' * 64], groups, mode='live'),
                    transport_rehearsal(p, ['a' * 64], [])):
            for key in ('executionEnabled', 'independentLiveVerified', 'liveApproved', 'fittedClaim'):
                self.assertIs(out[key], False)
            self.assertIsNone(out['writePayload'])

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
        values = [bytes.fromhex('7F2233'), bytes.fromhex('7F2278'), bytes.fromhex('62F1012A07'),
                  bytes.fromhex('62F1002A')]
        for pdu in values:
            with self.subTest(pdu=pdu):
                out, ports = self.run_case(lambda port: port.ecu_map.update(
                    {'22F100': ath1_prompt(isotp_ath1_lines(0x7E8, pdu))}))
                self.assertFalse(out['ok']); self.assertEqual(out['samples'], [])
                self.assertEqual(len(ports), 1); self.assertTrue(ports[0].closed)
        out, _ = self.run_case(lambda p: p.ecu_map.pop('22F100'))
        self.assertFalse(out['ok'])

    def test_unavailable_group_is_not_repeated_while_healthy_group_keeps_sampling(self):
        from scripts.diagnostics.tests.test_realtime_preparation import field
        p, _, _ = fixtures(); p['parameters'][1] = field('b', '22F101')
        ports, events = [], []
        def factory():
            port = SessionSimPort(CATALOG_PROFILE)
            port.ecu_map.update({'22F100': ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('7F2231'))),
                                '22F101': ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('62F10107')))})
            ports.append(port); return port
        from scripts.diagnostics.manufacturer_transport import synthetic_transport_cycles
        out = synthetic_transport_cycles(p, ['a'*64, 'b'*64], port_factory=factory, cycles=3)
        self.assertTrue(out['ok'], out)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)
        self.assertEqual(ports[0].ecu_payloads.count('22F101'), 3)
        self.assertEqual([s['value'] for s in out['samples']], [7, 7, 7])
        self.assertEqual(out['completedCycles'], 3)
        self.assertTrue(out['transport']['closed'])

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
