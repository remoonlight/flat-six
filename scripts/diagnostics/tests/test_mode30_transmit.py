"""Bounded TP sender, independent receive assembler, and literal wire fixtures."""
import threading
import unittest

from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode30_transport import ReplyAssembler
from scripts.diagnostics.tests import test_mode30_transport as fixtures


class TransmitTests(unittest.TestCase):
    def opened(self, block_size=15, sequence=0, *, cancel=None):
        channel, io, clock = fixtures.Mode30Tests().opened(cancel=cancel)
        channel.peer_parameters['blockSize'] = block_size
        channel.tx_sequence = sequence
        times = []
        def acknowledge(frame):
            times.append(clock.time())
            if frame[0] >> 4 in (0, 1):
                io.responses.append((channel.rx_id, bytes((0xb0 | ((frame[0]+1)&15),))))
        io.on_send = acknowledge
        return channel, io, clock, times

    def transmit(self, channel, data, deadline):
        # Operation callers must own the same lock as the read request path.
        with channel._lock:
            channel._transmit_request(data, deadline)

    def test_literal_six_byte_key_and_twenty_byte_request(self):
        c, io, clock, _ = self.opened()
        self.transmit(c, bytes.fromhex('270412345678'), clock.time()+2)
        self.assertEqual(io.sent[2:], [bytes.fromhex('2000062704123456'), bytes.fromhex('1178')])
        self.assertEqual(c.tx_sequence, 2)
        self.assertEqual(c.state, 'response')
        c, io, clock, _ = self.opened(block_size=2, sequence=15)
        self.transmit(c, bytes(range(20)), clock.time()+2)
        self.assertEqual([f.hex() for f in io.sent[2:]], [
            '2f00140001020304', '0005060708090a0b', '210c0d0e0f101112', '1213'])
        self.assertEqual(c.tx_sequence, 3)

    def test_lengths_blocks_and_wrap_reassemble_exactly_without_padding(self):
        sizes = [*range(1,34),109,110,111,112,113,254,255,256,257,258,4094,4095]
        for block in (1,2,3,15,16,255):
            for sequence in (0,15):
                for size in sizes:
                    c, io, clock, times = self.opened(block,sequence)
                    data = bytes((i*73+size)&255 for i in range(size))
                    self.transmit(c,data,clock.time()+30)
                    receiver = ReplyAssembler(sequence,block)
                    result = None
                    for frame in io.sent[2:]:
                        self.assertLessEqual(len(frame),8)
                        result = receiver.feed(frame)
                        if receiver.ack_needed:receiver.acknowledge()
                    self.assertEqual(result,data,(size,block,sequence))
                    self.assertEqual(c.tx_sequence,receiver.next_sequence)
                    for a,b in zip(times,times[1:]):
                        self.assertGreaterEqual(b-a+1e-10,c.peer_parameters['separationTimeS'])

    def test_first_block_failure_does_not_send_or_repeat_later_frames(self):
        for ack in (b'\x90',b'\xb0',b'\xb2',b'\xb1\0'):
            c, io, clock, _ = self.opened(1)
            io.on_send = lambda frame:io.responses.append((c.rx_id,ack))
            with self.assertRaisesRegex(ElmError,'ack-mismatch'):
                self.transmit(c,bytes(30),clock.time()+2)
            self.assertEqual(len(io.sent),3)
            self.assertEqual(c.state,'failed')
            with self.assertRaisesRegex(ElmError,'not-ready'):
                self.transmit(c,bytes(30),clock.time()+2)
            self.assertEqual(len(io.sent),3)

    def test_missing_ack_is_bounded_and_never_retransmits(self):
        c, io, clock, _ = self.opened(2)
        io.on_send = None
        start = clock.time()
        with self.assertRaises(ElmError):self.transmit(c,bytes(30),start+2)
        self.assertLess(clock.time()-start,.2)
        self.assertEqual(len(io.sent),4)
        self.assertEqual(c.state,'failed')

    def test_peer_close_is_acknowledged_and_stops_the_request(self):
        c, io, clock, _ = self.opened(1)
        io.on_send = lambda frame:io.responses.append((c.rx_id,b'\xa8')) if frame!=b'\xa8' else None
        with self.assertRaisesRegex(ElmError,'peer-closed'):
            self.transmit(c,bytes(30),clock.time()+2)
        self.assertEqual(c.state,'closed');self.assertTrue(c.channel_close_verified)
        self.assertEqual([f.hex() for f in io.sent[2:]],['00001e0000000000','a8'])

    def test_cancellation_during_separation_stops_without_sending_next_frame(self):
        cancel = threading.Event();c, io, clock, _ = self.opened(15,cancel=cancel)
        io.on_send = lambda frame:cancel.set()
        with self.assertRaisesRegex(ElmError,'cancelled'):
            self.transmit(c,bytes(30),clock.time()+2)
        self.assertEqual(len(io.sent),3);self.assertEqual(c.state,'failed')

    def test_invalid_size_or_state_has_no_wire_effect(self):
        for data in (b'',bytes(4096),bytearray(6),None):
            c, io, clock, _ = self.opened()
            with self.assertRaisesRegex(ElmError,'length-invalid'):
                self.transmit(c,data,clock.time()+2)
            self.assertEqual(len(io.sent),2)
        c, io, clock, _ = self.opened();c.state='response'
        with self.assertRaisesRegex(ElmError,'not-ready'):
            self.transmit(c,b'\x22\xf1\0',clock.time()+2)
        self.assertEqual(len(io.sent),2)

    def test_public_read_path_still_rejects_access_and_coding_before_io(self):
        c, io, clock, _ = self.opened()
        for hx in ('2703','270412345678','2EF100'+'00'*100):
            with self.assertRaisesRegex(ElmError,'unknown-request'):
                c.request(hx,clock.time()+2)
        self.assertEqual(len(io.sent),2)


if __name__ == '__main__':unittest.main()
