"""Application capture loss boundaries; injected bytes, no vehicle/device IO."""
import threading
import unittest
from unittest.mock import patch

from scripts.diagnostics.elm import ElmClient, ElmError, parse_ath1_response
from scripts.diagnostics.tests.test_dpu_terminal import Clock
from scripts.diagnostics.session_simulator import isotp_ath1_lines
from scripts.diagnostics.manufacturer_acquisition import acquire_cycle
from scripts.diagnostics.tests.test_manufacturer_acquisition import profile
from scripts.diagnostics.tests import test_manufacturer_read
from scripts.diagnostics.tests.test_dpu_events import client, indicator
from scripts.diagnostics.capture_clock import CAPTURE_CLOCK, valid_capture_clock


class PduCaptureTests(unittest.TestCase):
    def interrupted(self, data, mode, *, on_pdu=None, frame_sink=None):
        stop=threading.Event(); clock=Clock(); observed=[]
        class Port:
            def __init__(self): self.buffer=bytearray();self.sent=[];self.after=False
            def write(self, value): self.sent.append(value);self.buffer.extend(data);self.after=True;return len(value)
            def read(self, _):
                if self.buffer:
                    value=bytes([self.buffer.pop(0)])
                    if not self.buffer and mode=='cancel':stop.set()
                    return value
                if self.after and mode=='disconnect':raise OSError('link-lost')
                return b''
            def close(self):pass
        port=Port()
        def sink(pdu):
            observed.append(pdu)
            if on_pdu:on_pdu(pdu,port,clock)
        transport=ElmClient(port,clock=clock,sleeper=clock.sleep,cancel_event=stop,pdu_sink=sink,frame_sink=frame_sink)
        transport._configured=True;transport._tx_id=0x7e0;transport._rx_id=0x7e8
        error={'timeout':'prompt-timeout','cancel':'cancelled','disconnect':'port-io','prompt':None}[mode]
        if error:
            with self.assertRaisesRegex(ElmError,error):transport.request('22F100',1)
            with self.assertRaisesRegex(ElmError,'client-poisoned'):transport.request('22F100',2)
        else:
            self.assertTrue(transport.request('22F100',1)['ok'])
        self.assertEqual(len(port.sent),1,'no retransmission after missing prompt/cancel/disconnect')
        transport.close_restore()
        return observed

    def test_actual_elm_fragment_observations_keep_start_across_wall_rollback(self):
        first=b'7E8 10 0A 62 F1 00 01 02 03\r'
        final=b'7E8 21 04 05 06 07\r>'
        count=0
        def observation():
            nonlocal count
            count+=1
            return {'timestampUs':9000000 if count<=len(first) else 1000000,
                    'monotonicClock':CAPTURE_CLOCK,'monotonicTimestampUs':2000000+count}
        frames=[]
        with patch('scripts.diagnostics.elm.capture_timestamp',side_effect=observation):
            pdus=self.interrupted(first+final,'prompt',frame_sink=frames.append)
        self.assertEqual(len(pdus),1);pdu=pdus[0]
        self.assertEqual(pdu['pduHex'],'62F10001020304050607')
        self.assertGreater(pdu['startedTimestampUs'],pdu['timestampUs'])
        self.assertEqual(pdu['startedMonotonicTimestampUs'],2000001)
        self.assertEqual(pdu['monotonicTimestampUs'],2000000+len(first)+len(final)-1)
        # This fake port also returns its same two frames during AT cleanup;
        # the first pair are the actual request observations under test.
        self.assertEqual([p['dataHex'] for p in frames[:2]],['100A62F100010203','2104050607'])
        self.assertEqual(frames[1]['startedMonotonicTimestampUs'],2000000+len(first)+1)
        self.assertTrue(all(valid_capture_clock(p) for p in [*frames,*pdus]))

    def test_complete_before_missing_prompt_cancel_or_disconnect_is_saved_once(self):
        for mode in ('timeout','cancel','disconnect'):
            for ending in (b'\r',b''):
                with self.subTest(mode=mode,ending=ending):
                    observed=self.interrupted(b'7E8 04 62 F1 00 2A'+ending,mode)
                    self.assertEqual([p['pduHex'] for p in observed],['62F1002A'])
                    self.assertLessEqual(observed[0]['startedTimestampUs'],observed[0]['timestampUs'])

    def test_received_response_is_streamed_before_prompt_and_not_duplicated(self):
        early=[]
        observed=self.interrupted(b'7E8 04 62 F1 00 2A\r>', 'prompt',
            on_pdu=lambda _,port,clock:early.append((bytes(port.buffer),clock.now)))
        self.assertEqual(early,[(b'>',0)]);self.assertEqual(len(observed),1)

    def test_partial_or_sequence_gap_never_becomes_a_complete_record(self):
        for data in (b'7E8 10 0A 62 F1 00 01 02 03\r',
                     b'7E8 10 0A 62 F1 00 01 02 03\r7E8 22 04 05 06 07\r',
                     b'7E8 04 62 F1 00',b'7E8 03 7F 22',b'7E8 00 00',
                     b'7E8 10 0A 62 F1 00 01 02 03\r7E8 21\r7E8 21 04 05 06 07\r'):
            self.assertEqual(self.interrupted(data,'timeout'),[])

    def test_overlong_line_interrupts_candidate_and_capture_failure_is_not_replayed(self):
        data=b'7E8 10 0A 62 F1 00 01 02 03\r'+b'X'*4097+b'\r7E8 21 04 05 06 07\r'
        self.assertEqual(self.interrupted(data,'timeout'),[])
        def failed(*_):raise OSError('capture-output-failed')
        observed=self.interrupted(b'7E8 04 62 F1 00 2A\r>','disconnect',on_pdu=failed)
        self.assertEqual(len(observed),1,'capture callback failure must not replay the same response')

    def test_maximum_and_small_consecutive_frames_use_exact_existing_reassembly(self):
        payload=bytes.fromhex('62F100')+bytes(i%256 for i in range(4092))
        normal=('\r'.join(isotp_ath1_lines(0x7e8,payload))+'\r').encode()
        # Valid 4095-byte payload with one payload byte per CF (4090 frames),
        # covering SN wrap without a guessed small frame-count cap.
        tiny=['7E8 1F FF '+payload[:6].hex(' ').upper()]
        tiny += [f'7E8 {0x20|((i+1)&15):02X} {b:02X}' for i,b in enumerate(payload[6:])]
        for data in (normal,('\r'.join(tiny)+'\r').encode()):
            observed=self.interrupted(data,'timeout')
            self.assertEqual([p['pduHex'] for p in observed],[payload.hex().upper()])

    def test_elm_complete_bytes_survive_later_failure(self):
        for suffix, error in [('7E8 10 14 62 F1 00 01 02 03', 'isotp-incomplete-or-malformed'),
                              ('STOPPED', 'STOPPED'), ('NO DATA', 'NO DATA'), ('?', 'elm-query-mark')]:
            observed = []
            result = parse_ath1_response('7E8 03 7F 22 78\r7E8 04 62 F1 00 2A\r' + suffix,
                req_hex='22F100', rx_id=0x7e8, tx_id=0x7e0, sent_sf='', on_pdu=observed.append)
            self.assertFalse(result['ok']); self.assertEqual(result['error'], error)
            self.assertEqual(observed, [bytes.fromhex('7F2278'), bytes.fromhex('62F1002A')])

    def test_elm_wrong_echo_negative_and_short_are_observed_before_rejection(self):
        for payload in ('62F1012A', '7F2231', '62'):
            observed = []
            parse_ath1_response(f'7E8 {len(payload)//2:02X} ' + ' '.join(payload[i:i+2] for i in range(0,len(payload),2)),
                req_hex='22F100', rx_id=0x7e8, tx_id=0x7e0, sent_sf='', on_pdu=observed.append)
            self.assertEqual(observed, [bytes.fromhex(payload)])

    def test_cancel_after_receive_preserves_full_transaction_without_value(self):
        stop = threading.Event()
        def receive(_):
            stop.set()
            return bytes.fromhex('62F1002A07')
        result = acquire_cycle(profile(), ['a'*64], receive, cancelled=stop.is_set)
        self.assertEqual(result['error'], 'cancelled'); self.assertEqual(result['samples'], [])
        self.assertEqual(result['transactions'][0]['pduHex'], '62F1002A07')

    def test_dpu_preserves_unqualified_result_and_cleanup_without_invented_packets(self):
        transport, native, _ = client()
        observed = []; transport._pdu_sink = observed.append; transport._pdu_request = '22F100'
        result = indicator(flags='FF000000', unique=77, cop=88)
        result['payload'] = bytes.fromhex('7F2231')
        transport._record_event(result, expected_cop=1)
        transport._record_event(result, during_cleanup=True, expected_cop=1)
        transport._record_event(indicator())
        transport._record_event({'type': 0x1302, 'cop': 1, 'value': 7})
        self.assertEqual(len(observed), 2)
        self.assertEqual([(p['pduHex'],p['primitive'],p['uniqueId'],p['flagsHex'],p['duringCleanup']) for p in observed],
            [('7F2231',88,77,'FF000000',False),('7F2231',88,77,'FF000000',True)])
        self.assertTrue(all(p['timestampSource']=='host-pdu-observed' for p in observed))
        self.assertTrue(all(p['expectedPrimitive']==1 for p in observed))
        self.assertTrue(all(p['monotonicClock']==CAPTURE_CLOCK and valid_capture_clock(p) for p in observed))
        transport.close_restore()

    def test_actual_dpu_request_records_expected_handle_before_foreign_result_rejection(self):
        transport,native,_=client();observed=[];transport._pdu_sink=observed.append
        native.make_events=lambda _payload,_cop:[{**indicator(flags='00000000',unique=1,cop=88),'payload':bytes.fromhex('4100181A8000')}]
        with self.assertRaisesRegex(ElmError,'foreign-com-primitive'):transport.request('0100',10)
        self.assertEqual(len(observed),1);self.assertEqual(observed[0]['expectedPrimitive'],1)
        self.assertEqual(observed[0]['primitive'],88);self.assertEqual(observed[0]['pduHex'],'4100181A8000')
        transport.close_restore()

    def test_real_elm_path_emits_identity_and_failed_measurement_bytes(self):
        from scripts.diagnostics.session_simulator import ath1_prompt, isotp_ath1_lines
        harness = test_manufacturer_read.ReadTests()
        observed = []
        def short(port):
            port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('62F1002A')))
        result, ports = harness.run_session(modify=short, on_pdu=observed.append)
        self.assertFalse(result['ok']); self.assertEqual(result['samples'], [])
        self.assertTrue(any(p['requestHex']=='1089' for p in observed))
        self.assertTrue(any(p['requestHex']=='22F100' and p['pduHex']=='62F1002A' for p in observed))
        self.assertTrue(ports[0].closed)


if __name__ == '__main__':
    unittest.main()
