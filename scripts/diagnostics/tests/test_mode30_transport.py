"""TP2.0 wire fixtures on an owned, clocked raw-CAN interface; no devices."""
from collections import deque
import threading
import unittest

from scripts.diagnostics.bench import FakeClock
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode30_transport import (
    ChannelOffer, HOST_PARAMETERS, Mode30Channel, ReplyAssembler, timing_seconds,
)


def data_frames(pdu, sequence=0, block_size=15, length_flags=0):
    """Peer fixture: five PDU bytes in frame1, seven in subsequent frames."""
    chunks = [pdu[:5]] + [pdu[i:i+7] for i in range(5, len(pdu), 7)]
    frames = []
    for i, body in enumerate(chunks):
        kind = 0x10 if i == len(chunks)-1 else 0 if (i+1) % block_size == 0 else 0x20
        header = bytes((kind | ((sequence+i) & 15),))
        if i == 0:
            header += bytes((length_flags | (len(pdu) >> 8), len(pdu) & 255))
        frames.append(header + body)
    return frames


class RawIO:
    """No discovery or fallback path to serial/D-PDU."""
    def __init__(self, clock, responses=()):
        self.clock = clock
        self.responses = deque(responses)
        self.pairs, self.sent = [], []
        self.on_send = None
        self.fail_configuration = None

    def configure_pair(self, tx, rx, deadline):
        self.pairs.append((tx, rx))
        if self.fail_configuration == len(self.pairs):
            raise ElmError('fixture-configuration-failed')

    def send(self, frame, deadline):
        self.sent.append(frame)
        if self.on_send:
            self.on_send(frame)

    def receive(self, deadline):
        self.clock.sleep(.0001 if self.responses else .01)
        return self.responses.popleft() if self.responses else None


class Mode30Tests(unittest.TestCase):
    def make(self, *, responses=(), cancel=None, sink=None, setup=bytes.fromhex('26C00010020301')):
        clock = FakeClock()
        offer = ChannelOffer(0x200, 0x200+setup[0], setup)
        io = RawIO(clock, [(offer.rx_id, bytes.fromhex('00D00203410501')),
                           (0x302, bytes.fromhex('A10F8AFF0AFF')), *responses])
        client = Mode30Channel(io, offer, {'22F100', '22F101', '3E00'}, clock=clock.time,
                              sleeper=clock.sleep, cancel_event=cancel, pdu_sink=sink)
        return client, io, clock

    def opened(self, **kwargs):
        client, io, clock = self.make(**kwargs)
        client.open(clock.time()+3)
        return client, io, clock

    def test_each_source_offer_opens_then_uses_only_fresh_negotiated_addresses(self):
        for setup in ['26C00010020301', '35C00010030301', '22C00010040301', '23C00010050301']:
            c, io, clock = self.opened(setup=bytes.fromhex(setup))
            self.assertEqual(io.pairs, [(0x200, 0x200+int(setup[:2], 16)), (0x541, 0x302)])
            self.assertEqual(io.sent, [bytes.fromhex(setup), HOST_PARAMETERS])
            self.assertEqual(c.state, 'idle')
            self.assertAlmostEqual(c.peer_parameters['ackTimeS'], .1)
            self.assertAlmostEqual(c.peer_parameters['separationTimeS'], .001)

    def test_invalid_offer_or_writable_allowlist_never_configures_io(self):
        for tx, rx, raw in [(0x201,0x226,'26C00010020301'),(0x200,0x227,'26C00010020301'),
                            (0x200,0x226,'26C00010020302'),(0x200,0x226,'26C00010000001')]:
            with self.subTest(raw=raw), self.assertRaises(ElmError):
                ChannelOffer(tx, rx, bytes.fromhex(raw))
        for allowed in [{'2EF10001'}, {'2701'}, {'1001'}, {'3E80'}, {'1901FF'}, {'22f100'}, set(), ['22F100']]:
            c, io, clock = self.make()
            with self.subTest(allowed=allowed), self.assertRaises(ElmError):
                Mode30Channel(io, c.offer, allowed)
            self.assertEqual(io.pairs, [])
            self.assertEqual(io.sent, [])

    def test_receive_block_size_is_bounded_and_does_not_change_default_native_timing(self):
        c, io, clock = self.make()
        self.assertEqual(c.host_parameters, HOST_PARAMETERS)
        for size in [True, 0, -1, 16, 255, 1.0, None]:
            with self.subTest(size=size), self.assertRaisesRegex(ElmError, 'block-size-invalid'):
                Mode30Channel(io, c.offer, {'22F100'}, receive_block_size=size)
        for size in [1, 2, 15]:
            channel = Mode30Channel(io, c.offer, {'22F100'}, receive_block_size=size)
            self.assertEqual(channel.host_parameters[1], size)
            self.assertEqual(channel.host_parameters[2:], HOST_PARAMETERS[2:])
        self.assertEqual(io.sent, [])

    def test_wrong_setup_opcode_bad_ids_and_missing_parameters_never_send_a_read(self):
        for raw in ['00D10203410501','00D00000410501','00D00008410501','00D00203020301','00D00203410502']:
            c, io, clock = self.make()
            io.responses[0] = (0x226, bytes.fromhex(raw))
            with self.subTest(raw=raw), self.assertRaises(ElmError):
                c.open(clock.time()+1)
            self.assertEqual(io.sent, [c.offer.payload])
            self.assertEqual(c.state, 'failed')
            with self.assertRaises(ElmError):
                c.request('22F100', clock.time()+1)
        for raw in ['A1008AFF0AFF', 'A10F8A000AFF', 'A10F8AFF0A00', 'A10F8A']:
            c, io, clock = self.make()
            io.responses[1] = (0x302, bytes.fromhex(raw))
            with self.subTest(raw=raw), self.assertRaises(ElmError):
                c.open(clock.time()+1)
            self.assertEqual(len(io.sent), 2)

    def test_literal_multiframe_reply_and_acknowledgements(self):
        # 20-byte response; TP sequence 0,1,2,3. Last frame requests ACK4.
        frames = [bytes.fromhex(x) for x in ['20001462F1003132','2133343536373839','2230313233343536','1337']]
        seen = []
        c, io, clock = self.opened(responses=[(0x302,b'\xB1')]+[(0x302,f) for f in frames], sink=seen.append)
        result = c.request('22F100', clock.time()+2)
        self.assertTrue(result['ok'])
        self.assertEqual(result['payload_hex'], '62F1003132333435363738393031323334353637')
        self.assertEqual(io.sent[2:], [bytes.fromhex('10000322F100'), b'\xB4'])
        self.assertEqual(c.rx_sequence, 4)
        self.assertEqual(len(seen), 1)

    def test_reply_boundaries_maximum_and_sequence_wrap_require_each_block_ack(self):
        for size in [1,2,3,4,5,6,7,8,12,13,14,16,17,109,110,111,112,113,4094,4095]:
            pdu = bytes((n & 255 for n in range(size)))
            frames = data_frames(pdu, sequence=14)
            a = ReplyAssembler(14, 15)
            acknowledgements = 0
            for frame in frames:
                result = a.feed(frame)
                if a.ack_needed:
                    acknowledgements += 1
                    a.acknowledge()
            self.assertEqual(result, pdu)
            self.assertEqual(a.next_sequence, (14+len(frames)) & 15)
            self.assertEqual(acknowledgements, (len(frames)+14)//15)
        pdu = b'\x62\xF1\x00'+b'X'*4092
        frames = data_frames(pdu)
        c, io, clock = self.opened(responses=[(0x302,b'\xB1')]+[(0x302,f) for f in frames])
        result = c.request('22F100', clock.time()+2)
        self.assertEqual(bytes.fromhex(result['payload_hex']), pdu)
        self.assertEqual(io.sent.count(bytes.fromhex('10000322F100')), 1)
        self.assertEqual(len(io.sent)-3, (len(frames)+14)//15)

    def test_short_overlong_early_last_bad_sequence_and_overrun_are_not_published(self):
        bad_frames = ['100000', '10000062', '10000462F100', '10000362F10001',
                      '11000362F100', '20000362F100', '20400362F100', '10000662F1000000']
        for raw in bad_frames:
            seen = []
            c, io, clock = self.opened(responses=[(0x302,b'\xB1'),(0x302,bytes.fromhex(raw))],sink=seen.append)
            with self.subTest(raw=raw), self.assertRaises(ElmError):
                c.request('22F100',clock.time()+1)
            self.assertEqual(seen, [])
            self.assertEqual(c.state, 'failed')
        a = ReplyAssembler(0, 1)
        with self.assertRaisesRegex(ElmError, 'block-overrun'):
            a.feed(bytes.fromhex('20000662F1000000'))

    def test_pending_keepalive_and_foreign_ids_do_not_replay_or_publish_foreign_data(self):
        c, io, clock = self.opened(responses=[(0x302,b'\xB1'),(0x301,b'\xA8'),
            (0x302,b'\xA3'),(0x302,bytes.fromhex('1080037F2278')),
            (0x302,bytes.fromhex('11000462F1002A'))])
        out = c.request('22F100',clock.time()+2)
        self.assertEqual(out['pending'], ['7F2278'])
        self.assertEqual(out['payload_hex'], '62F1002A')
        self.assertEqual(io.sent[2:], [bytes.fromhex('10000322F100'),b'\xA1'+HOST_PARAMETERS[1:],b'\xB1',b'\xB2'])

    def test_bad_ack_echo_or_pending_flag_never_replays(self):
        for frames in [[b'\xB2'],[b'\xB1',bytes.fromhex('10000462F1012A')],
                       [b'\xB1',bytes.fromhex('10800462F1002A')]]:
            c, io, clock = self.opened(responses=[(0x302,f) for f in frames])
            with self.subTest(frames=frames),self.assertRaises(ElmError):
                c.request('22F100',clock.time()+1)
            before = list(io.sent)
            with self.assertRaises(ElmError):
                c.request('22F100',clock.time()+1)
            self.assertEqual(io.sent,before)

    def test_request_and_reply_sequences_persist_and_wrap_across_requests(self):
        c,io,clock = self.opened()
        for i in range(34):
            seq = i & 15
            io.responses.extend([(0x302,bytes((0xB0|((seq+1)&15),))),
                (0x302,bytes((0x10|seq,0,4,0x62,0xF1,0,42)))])
            out = c.request('22F100',clock.time()+1)
            self.assertTrue(out['ok'])
            self.assertEqual(io.sent[-2][0],0x10|seq)
            self.assertEqual(io.sent[-1],bytes((0xB0|((seq+1)&15),)))
        self.assertEqual((c.tx_sequence,c.rx_sequence),(2,2))

    def test_peer_close_is_acknowledged_and_never_returns_a_sample(self):
        c,io,clock = self.opened(responses=[(0x302,b'\xA8')])
        with self.assertRaisesRegex(ElmError,'peer-closed'):
            c.request('22F100',clock.time()+1)
        self.assertEqual(io.sent[-1],b'\xA8')
        count = len(io.sent)
        self.assertTrue(c.close_channel(clock.time()+1)['channelClosed'])
        self.assertEqual(len(io.sent),count)

    def test_cancel_still_requires_bounded_channel_close_and_discards_buffered_replies(self):
        cancel = threading.Event()
        c,io,clock = self.opened(cancel=cancel)
        io.on_send = lambda frame: cancel.set() if frame[0] == 0x10 else None
        with self.assertRaisesRegex(ElmError,'cancelled'):
            c.request('22F100',clock.time()+1)
        io.responses.extend([(0x302,b'\xB1'),(0x302,bytes.fromhex('10000462F1002A')),(0x302,b'\xA8')])
        self.assertTrue(c.close_channel(clock.time()+1)['channelClosed'])
        self.assertEqual(io.sent[-1],b'\xA8')
        self.assertEqual(c.state,'closed')

    def test_request_timeout_or_close_timeout_cannot_claim_channel_closed(self):
        c,io,clock = self.opened()
        with self.assertRaisesRegex(ElmError,'deadline-expired'):
            c.request('22F100',clock.time()+.03)
        self.assertEqual(io.sent.count(bytes.fromhex('10000322F100')),1)
        with self.assertRaises(ElmError):
            c.close_channel(clock.time()+.03)
        self.assertFalse(c.channel_close_verified)

    def test_response_phase_timeout_does_not_expire_or_extend_batch_deadline(self):
        c,io,clock=self.opened(responses=[(0x302,b'\xB1')])
        c.phase_timeout_s=.02
        deadline=clock.time()+1
        with self.assertRaisesRegex(ElmError,'mode30-response-timeout'):
            c.request('22F100',deadline)
        self.assertLess(clock.time(),deadline)
        self.assertEqual(io.sent.count(bytes.fromhex('10000322F100')),1)
        io.responses.append((0x302,b'\xA8'))
        self.assertTrue(c.close_channel(deadline)['channelClosed'])

    def test_request_ack_timeout_is_distinct_from_the_fixed_batch_deadline(self):
        c,io,clock=self.opened()
        deadline=clock.time()+1
        with self.assertRaisesRegex(ElmError,'mode30-request-ack-timeout'):
            c.request('22F100',deadline)
        self.assertLess(clock.time(),deadline)
        self.assertEqual(io.sent.count(bytes.fromhex('10000322F100')),1)
        io.responses.append((0x302,b'\xA8'))
        self.assertTrue(c.close_channel(deadline)['channelClosed'])
        self.assertEqual(io.sent.count(b'\xA8'),1)

    def test_unknown_channel_or_failed_pair_switch_never_sends_close_on_setup_id(self):
        for failure in ['setup','configuration']:
            c,io,clock = self.make()
            if failure=='setup':io.responses.clear()
            else:io.fail_configuration=2
            with self.assertRaises(ElmError):c.open(clock.time()+.03)
            before=list(io.sent)
            with self.assertRaisesRegex(ElmError,'close-address-unresolved'):
                c.close_channel(clock.time()+1)
            self.assertEqual(io.sent,before)
            self.assertNotIn(b'\xA8',io.sent)

    def test_cancel_before_setup_and_closing_unused_channel_have_no_wire_side_effect(self):
        event=threading.Event();event.set()
        c,io,clock=self.make(cancel=event)
        with self.assertRaisesRegex(ElmError,'cancelled'):c.open(clock.time()+1)
        self.assertEqual(io.sent,[])
        self.assertTrue(c.close_channel(clock.time()+1)['channelNeverOpened'])
        self.assertTrue(c.close_channel(clock.time()+1)['channelClosed'])
        self.assertEqual(io.sent,[])

    def test_concurrent_calls_and_unknown_reads_never_touch_io(self):
        c,io,clock=self.opened();before=list(io.sent)
        for hx in ['14FF00','2EF10001','2701','22F102',True,None]:
            with self.assertRaises(ElmError):c.request(hx,clock.time()+1)
        c._lock.acquire()
        try:
            for action in [lambda:c.request('22F100',clock.time()+1),lambda:c.close_channel(clock.time()+1),lambda:c.bind_reads({'22F101'})]:
                with self.assertRaisesRegex(ElmError,'request-in-flight'):action()
        finally:c._lock.release()
        self.assertEqual(io.sent,before)

    def test_timing_units_and_data_separation_are_respected(self):
        for raw,expected in [(0x0A,.001),(0x4A,.01),(0x8A,.1),(0xCA,1.0),(0,0)]:
            self.assertAlmostEqual(timing_seconds(raw),expected)
        c,io,clock=self.opened();c.peer_parameters['separationTimeS']=.05
        for seq in [0,1]:
            io.responses.extend([(0x302,bytes((0xB0|seq+1,))),
                (0x302,bytes((0x10|seq,0,4,0x62,0xF1,0,42)))])
            start=clock.time();out=c.request('22F100',clock.time()+1)
            self.assertTrue(out['ok'])
            if seq:self.assertGreaterEqual(clock.time()-start,.049)

    def test_matched_negative_remains_an_explicit_result_and_allows_other_reads(self):
        c,io,clock=self.opened(responses=[(0x302,b'\xB1'),(0x302,bytes.fromhex('1000037F2231')),
            (0x302,b'\xB2'),(0x302,bytes.fromhex('11000462F1012A'))])
        rejected=c.request('22F100',clock.time()+1)
        self.assertFalse(rejected['ok'])
        self.assertEqual(rejected['error'],'negative-response')
        self.assertEqual(c.state,'idle')
        self.assertTrue(c.request('22F101',clock.time()+1)['ok'])

    def test_failure_to_send_required_ack_cannot_publish_complete_data(self):
        seen=[]
        c,io,clock=self.opened(responses=[(0x302,b'\xB1'),(0x302,bytes.fromhex('10000462F1002A'))],sink=seen.append)
        def fail_ack(frame):
            if frame==b'\xB1':raise OSError('fixture-send-failed')
        io.on_send=fail_ack
        with self.assertRaises(OSError):c.request('22F100',clock.time()+1)
        self.assertEqual(seen,[])
        self.assertEqual(c.state,'failed')

    def test_keepalive_does_not_extend_ack_or_request_deadline(self):
        c,io,clock=self.opened()
        io.responses.extend([(0x302,b'\xA3')]*2000)
        start=clock.time()
        with self.assertRaisesRegex(ElmError,'deadline-expired'):
            c.request('22F100',clock.time()+.03)
        self.assertLess(clock.time()-start,.031)
        self.assertEqual(io.sent.count(bytes.fromhex('10000322F100')),1)

    def test_cancel_during_separation_wait_sends_no_next_request(self):
        event=threading.Event()
        c,io,clock=self.opened(responses=[(0x302,b'\xB1'),(0x302,bytes.fromhex('10000462F1002A'))],cancel=event)
        self.assertTrue(c.request('22F100',clock.time()+1)['ok'])
        before=list(io.sent);c.peer_parameters['separationTimeS']=.05
        def wait(seconds):
            clock.sleep(seconds);event.set()
        c.sleep=wait
        with self.assertRaisesRegex(ElmError,'cancelled'):
            c.request('22F101',clock.time()+1)
        self.assertEqual(io.sent,before)

    def test_rebinding_is_idle_only_and_cannot_open_a_write_path(self):
        c,io,clock=self.opened();before=list(io.sent)
        c.bind_reads({'22F101'})
        with self.assertRaises(ElmError):c.request('22F100',clock.time()+1)
        with self.assertRaises(ElmError):c.bind_reads({'2EF10001'})
        self.assertEqual(io.sent,before)
        self.assertEqual(c.allowed,frozenset({'22F101'}))


if __name__ == '__main__':
    unittest.main()
