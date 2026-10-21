"""Clocked serial-byte fixtures; no adapter or vehicle is opened."""
from collections import deque
import threading
import unittest

from scripts.diagnostics.bench import FakeClock
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode30_elm import Mode30ElmIo, ELM_HOST_PARAMETERS
from scripts.diagnostics.mode30_transport import ChannelOffer, Mode30Channel
from scripts.diagnostics.tests.test_mode30_transport import data_frames


class RawElmPort:
    """Independent AT/serial peer; TP blocks advance only on host ACK."""
    def __init__(self, clock):
        self.clock = clock
        self.writes, self.frames = [], []
        self.buffer = bytearray()
        self.pending = deque()
        self.reply = deque()
        self.sequence = 0
        self.expected_ack = None
        self.monitor = False
        self.close_count = 0
        self.timeout, self.write_timeout = .1, 1
        self.baudrate = 38400
        self.read_timeouts = []
        self.tx, self.rx = 0x200, 0x226
        self.reject_at = None
        self.hang_at = None
        self.stop_prompt = True
        self.delayed = False
        self.response = lambda req: bytes((req[0]+0x40,))+req[1:]+b'\x2A'
        self.on_frame = None
        self.monitor_extra = b''
        self.no_raw_prompt = False
        self.read_error = False
        self.short_write = False

    def line(self, frame):
        return f'{self.rx:03X} '.encode()+frame.hex(' ').upper().encode()+b'\r'

    def write(self, data):
        if self.close_count:
            raise OSError('fixture closed')
        self.writes.append(bytes(data))
        if self.short_write:
            return len(data)-1
        if data == b'\x1B':
            if not self.monitor:
                raise AssertionError('stop without monitor')
            self.monitor = False
            self.buffer.extend(self.monitor_extra)
            if self.stop_prompt:
                self.buffer.extend(b'STOPPED\r>')
            return 1
        if self.monitor:
            raise AssertionError('command before monitor prompt')
        if self.buffer:
            raise AssertionError('command before previous prompt consumed')
        cmd = data.decode().strip()
        if cmd == self.hang_at:
            pass
        elif cmd == self.reject_at:
            self.buffer.extend(b'?\r>')
        elif cmd == 'ATMA':
            self.monitor = True
            self.buffer.extend(b''.join(self.pending))
            self.pending.clear()
        elif cmd.startswith('AT'):
            if cmd.startswith('ATSH'):
                self.tx = int(cmd[4:], 16)
            elif cmd.startswith('ATCRA'):
                self.rx = int(cmd[5:], 16)
            self.buffer.extend(b'OK\r>')
        else:
            frame = bytes.fromhex(cmd)
            self.frames.append(frame)
            if len(frame) == 7 and frame[1] == 0xC0:
                outgoing = [bytes.fromhex('00D00203410501')]
            elif frame == ELM_HOST_PARAMETERS:
                outgoing = [bytes.fromhex('A10F8AFF0AFF')]
            elif frame == b'\xA8':
                outgoing = [b'\xA8']
            elif len(frame) == 1 and frame[0] >> 4 == 0xB:
                if self.expected_ack is None or frame[0] != self.expected_ack:
                    raise AssertionError('wrong TP ACK')
                outgoing = self.next_frame()
            elif frame[0] >> 4 == 1:
                outgoing = [bytes((0xB0|((frame[0]+1)&15),))]
                pdu = self.response(frame[3:])
                messages = pdu if isinstance(pdu, list) else [pdu]
                for pdu in messages:
                    frames = data_frames(pdu, self.sequence, block_size=1,
                        length_flags=0x80 if pdu == b'\x7F\x22\x78' else 0)
                    self.sequence = (self.sequence+len(frames)) & 15
                    self.reply.extend(frames)
                outgoing += self.next_frame()
            else:
                outgoing = []  # Pong, adapter-local commands do not read data.
            if self.on_frame:
                self.on_frame(frame)
            for raw in outgoing:
                if self.delayed:
                    self.pending.append(self.line(raw))
                else:
                    self.buffer.extend(self.line(raw))
            if not outgoing or self.delayed:
                self.buffer.extend(b'NO DATA\r')
            if not self.no_raw_prompt:
                self.buffer.extend(b'>')
        return len(data)

    def next_frame(self):
        if not self.reply:
            self.expected_ack = None
            return []
        frame = self.reply.popleft()
        self.expected_ack = 0xB0|((frame[0]+1)&15)
        return [frame]

    def read(self, size=1):
        if self.read_error:
            raise OSError('fixture read failed')
        self.read_timeouts.append(self.timeout)
        self.clock.sleep(.00001 if self.buffer else self.timeout)
        chunk = bytes(self.buffer[:size])
        del self.buffer[:size]
        return chunk

    def close(self):
        self.close_count += 1


class Mode30ElmTests(unittest.TestCase):
    def make(self, *, setup='26C00010020301', cancel=None):
        clock = FakeClock()
        port = RawElmPort(clock)
        raw = bytes.fromhex(setup)
        offer = ChannelOffer(0x200, 0x200+raw[0], raw)
        io = Mode30ElmIo(port, offer, clock=clock.time, sleeper=clock.sleep)
        channel = Mode30Channel(io, offer, {'22F100', '22F101', '1089'}, clock=clock.time,
            sleeper=clock.sleep, cancel_event=cancel, receive_block_size=1)
        return channel, io, port, clock

    def opened(self, **kwargs):
        c, io, p, clock = self.make(**kwargs)
        c.open(clock.time()+3)
        return c, io, p, clock

    def finish(self, c, io, p, clock):
        self.assertTrue(c.close_channel(clock.time()+1)['channelClosed'])
        self.assertEqual(io.close_restore(), {'errors': []})
        self.assertEqual(p.close_count, 1)

    def test_four_source_channels_use_variable_dlc_and_only_delivered_dynamic_pair(self):
        for setup in ['26C00010020301', '35C00010030301', '22C00010040301', '23C00010050301']:
            c, io, p, clock = self.opened(setup=setup)
            self.assertEqual((p.tx, p.rx), (0x541, 0x302))
            self.assertEqual(p.frames, [bytes.fromhex(setup), ELM_HOST_PARAMETERS])
            self.assertLess(p.writes.index(b'ATV1\r'), p.writes.index(setup.encode()+b'\r'))
            for req in ['22F100', '22F101', '1089']:
                self.assertTrue(c.request(req, clock.time()+1)['ok'])
            self.finish(c, io, p, clock)

    def test_single_frame_receive_blocks_ack_every_frame_and_preserve_sequence_wrap(self):
        c, io, p, clock = self.opened()
        p.response = lambda req: b'\x62'+req[1:]+b'X'*109
        for _ in range(3):
            self.assertTrue(c.request('22F100', clock.time()+2)['ok'])
        acks = [f for f in p.frames if len(f) == 1 and f[0] >> 4 == 0xB]
        expected_count = len(data_frames(b'\x62\xF1\x00'+b'X'*109, block_size=1))*3
        self.assertEqual(len(acks), expected_count)
        self.assertEqual(acks, [bytes((0xB0|((n+1)&15),)) for n in range(expected_count)])
        self.finish(c, io, p, clock)

    def test_maximum_pdu_uses_bounded_blocks_and_one_application_request(self):
        c, io, p, clock = self.opened()
        p.response = lambda req: b'\x62\xF1\x00'+bytes(range(256))*15+b'Z'*252
        result = c.request('22F100', clock.time()+5)
        self.assertEqual(len(bytes.fromhex(result['payload_hex'])), 4095)
        self.assertEqual(p.frames.count(bytes.fromhex('10000322F100')), 1)
        self.assertFalse(p.reply)
        self.finish(c, io, p, clock)

    def test_late_replies_monitor_stop_before_ack_and_never_reissue_request(self):
        c, io, p, clock = self.opened()
        p.delayed = True
        p.response = lambda req: b'\x62'+req[1:]+b'X'*21
        self.assertTrue(c.request('22F100', clock.time()+2)['ok'])
        self.assertIn(b'ATMA\r', p.writes)
        self.assertEqual(p.frames.count(bytes.fromhex('10000322F100')), 1)
        self.assertEqual(p.writes.count(b'ATMA\r'), p.writes.count(b'\x1B'))
        self.assertFalse(p.monitor)
        self.finish(c, io, p, clock)

    def test_frames_during_monitor_stop_are_retained_with_real_observation_times(self):
        c, io, p, clock = self.opened()
        p.monitor_extra = p.line(b'\xA3')
        self.assertEqual(io.receive(clock.time()+.1), (0x302, b'\xA3'))
        self.assertGreater(io.last_observation['timestampUs'], 0)
        self.assertLessEqual(io.last_observation['startedTimestampUs'], io.last_observation['timestampUs'])
        self.assertEqual(io.last_observation['timestampSource'], 'host-chunk-arrival')
        p.monitor_extra = b''
        self.finish(c, io, p, clock)

    def test_quiet_monitor_timeout_is_not_a_poisoned_port_and_preserves_host_timeout(self):
        c, io, p, clock = self.opened()
        p.read_timeouts.clear()
        for _ in range(3):
            self.assertIsNone(io.receive(clock.time()+.1))
            self.assertFalse(io.elm._poisoned)
            self.assertEqual(p.timeout, .1)
        self.assertTrue(all(0 <= t <= .005 for t in p.read_timeouts))
        self.assertTrue(c.request('22F100', clock.time()+1)['ok'])
        self.finish(c, io, p, clock)

    def test_pending_and_ping_keep_one_application_send(self):
        c, io, p, clock = self.opened()
        p.response = lambda req: [b'\x7F\x22\x78', b'\x62\xF1\x00\x2A']
        out = c.request('22F100', clock.time()+2)
        self.assertEqual(out['pending'], ['7F2278'])
        p.pending.append(p.line(b'\xA3'))
        until = clock.time()+.1
        c.idle_until(until, until+1)
        self.assertIn(b'\xA1'+ELM_HOST_PARAMETERS[1:], p.frames)
        self.assertGreaterEqual(clock.time(), until)
        self.assertEqual(p.frames.count(bytes.fromhex('10000322F100')), 1)
        self.finish(c, io, p, clock)

    def test_cancel_after_request_still_allows_channel_close_and_local_restore(self):
        cancel = threading.Event()
        c, io, p, clock = self.opened(cancel=cancel)
        p.on_frame = lambda f: cancel.set() if f[0] >> 4 == 1 else None
        with self.assertRaisesRegex(ElmError, 'cancelled'):
            c.request('22F100', clock.time()+1)
        self.finish(c, io, p, clock)
        self.assertEqual(p.frames.count(bytes.fromhex('10000322F100')), 1)

    def test_unknown_writable_frames_and_wrong_host_block_size_never_reach_wire(self):
        for frame in ['1000042EF10001', '1000022701', '1000021101',
                      '1000021001', 'A00F8AFF0AFF', 'A3', '20', '10000322F10000']:
            c, io, p, clock = self.opened()
            before = list(p.frames)
            with self.subTest(frame=frame), self.assertRaises(ElmError):
                io.send(bytes.fromhex(frame), clock.time()+1)
            self.assertEqual(p.frames, before)
            self.assertEqual(io.close_restore()['errors'], [])

    def test_negotiated_pair_cannot_be_guessed_before_delivering_setup_reply(self):
        c, io, p, clock = self.make()
        io.configure_pair(0x200, 0x226, 1)
        io.send(c.offer.payload, 1)
        before = list(p.writes)
        with self.assertRaisesRegex(ElmError, 'address-unqualified'):
            io.configure_pair(0x541, 0x302, 1)
        self.assertEqual(p.writes, before)
        self.assertEqual(io.close_restore()['errors'], [])

    def test_extra_setup_reply_or_third_pair_cannot_change_filter(self):
        c, io, p, clock = self.make()
        io.configure_pair(0x200, 0x226, 1)
        io.send(c.offer.payload, 1)
        io._frames.append(io._frames[0])
        io.receive(1)
        with self.assertRaisesRegex(ElmError, 'desynchronized'):
            io.configure_pair(0x541, 0x302, 1)
        io.close_restore()
        c, io, p, clock = self.opened()
        with self.assertRaisesRegex(ElmError, 'address-unqualified'):
            io.configure_pair(0x542, 0x302, 1)
        io.close_restore()

    def test_rejected_variable_dlc_stops_before_any_can_send(self):
        c, io, p, clock = self.make()
        p.reject_at = 'ATV1'
        with self.assertRaisesRegex(ElmError, 'at-failed:ATV1'):
            c.open(1)
        self.assertEqual(p.frames, [])
        self.assertEqual(io.close_restore()['errors'], [])
        c, io, p, clock = self.make()
        p.hang_at = 'ATV1'
        with self.assertRaisesRegex(ElmError, 'prompt-timeout'):
            c.open(clock.time()+600)
        self.assertLess(clock.time(), 3.2)
        self.assertEqual(p.frames, [])
        self.assertTrue(io.close_restore()['errors'])

    def test_wrong_id_malformed_lines_and_adapter_errors_are_not_samples(self):
        for raw in [b'301 A3\r', b'302 A3 01 02 03 04 05 06 07 08\r',
                    b'CAN ERROR\r', b'?\r', b'BUFFER FULL\r', b'garbage\r']:
            c, io, p, clock = self.opened()
            p.pending.append(raw)
            with self.subTest(raw=raw), self.assertRaises(ElmError):
                c.idle_until(clock.time()+.1, clock.time()+1)
            self.assertFalse(p.monitor)
            self.assertFalse(io.elm._poisoned)
            self.assertEqual(io.close_restore()['errors'], [])

    def test_missing_stop_prompt_closes_port_and_does_not_claim_restore(self):
        c, io, p, clock = self.opened()
        p.stop_prompt = False
        before = list(p.frames)
        with self.assertRaisesRegex(ElmError, 'stop-unconfirmed'):
            io.receive(clock.time()+.03)
        self.assertTrue(io.elm._poisoned)
        self.assertEqual(p.close_count, 1)
        self.assertTrue(io.close_restore()['errors'])
        self.assertEqual(p.frames, before)

    def test_missing_send_prompt_io_error_short_write_cannot_replay(self):
        for failure in ['no_raw_prompt', 'read_error', 'short_write']:
            c, io, p, clock = self.opened()
            setattr(p, failure, True)
            with self.subTest(failure=failure), self.assertRaises(ElmError):
                started = clock.time()
                c.request('22F100', clock.time()+600)
            self.assertLess(clock.time()-started, 1.1)
            self.assertTrue(io.elm._poisoned)
            self.assertTrue(io.close_restore()['errors'])
            self.assertEqual(p.close_count, 1)
            self.assertLessEqual(p.frames.count(bytes.fromhex('10000322F100')), 1)

    def test_restore_resets_timer_and_variable_dlc_without_baud_or_pp_writes(self):
        c, io, p, clock = self.opened()
        self.finish(c, io, p, clock)
        self.assertEqual(io.close_restore(), {'errors': []})
        for cmd in ['ATV0', 'ATST00', 'ATAT1', 'ATCTM1', 'ATCSM1', 'ATCAF1', 'ATCFC1', 'ATM1']:
            self.assertIn(cmd.encode()+b'\r', p.writes)
        self.assertEqual(p.baudrate, 38400)
        self.assertFalse(any(x.startswith((b'ATPP', b'ATBR', b'ATZ', b'ATSP')) for x in p.writes))

    def test_failed_restoration_is_reported_and_port_is_still_closed_once(self):
        c, io, p, clock = self.opened()
        p.reject_at = 'ATV0'
        report = io.close_restore()
        self.assertTrue(any('ATV0' in e for e in report['errors']))
        self.assertEqual(report, io.close_restore())
        self.assertEqual(p.close_count, 1)

    def test_concurrent_io_or_restore_does_not_touch_port(self):
        c, io, p, clock = self.opened()
        before = list(p.writes)
        io._lock.acquire()
        try:
            with self.assertRaisesRegex(ElmError, 'io-in-flight'):
                io.send(b'\xA8', clock.time()+1)
            self.assertEqual(io.close_restore()['errors'], ['close-in-progress'])
        finally:
            io._lock.release()
        self.assertEqual(p.writes, before)
        self.finish(c, io, p, clock)


if __name__ == '__main__':
    unittest.main()
