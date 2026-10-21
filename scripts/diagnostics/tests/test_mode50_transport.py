"""Source data windows and bounded framing only; no physical adapter or car."""
import unittest
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode50_transport import request_frame, ReplyAssembler, FLOW_CONTROL, Mode50ElmClient
from scripts.diagnostics.bench import FakeClock
import threading


def lines(frames):
    return b''.join(b'7BE '+frame.hex(' ').upper().encode()+b'\r' for frame in frames)


def response_frames(payload,address=0xF1):
    if len(payload)<=6:return [bytes((address,len(payload)))+payload]
    out=[bytes((address,0x10|(len(payload)>>8),len(payload)&255))+payload[:5]]
    offset=5;sequence=1
    while offset<len(payload):
        data=payload[offset:offset+6];out.append(bytes((address,0x20|sequence))+data)
        offset+=len(data);sequence=(sequence+1)&15
    return out


class Port:
    """Explicit raw-CAN prompt/monitor fixture; never a serial transport."""
    def __init__(self,packets=(),monitors=(),*,stop_ack=True):
        self.buf=bytearray();self.packets=list(packets);self.monitors=list(monitors)
        self.writes=[];self.closed=0;self.stop_ack=stop_ack;self.after_write=None
    def write(self,data):
        self.writes.append(data)
        if data==b'\x1B':
            if self.stop_ack:self.buf.extend(b'STOPPED\r>')
        elif data==b'ATMA\r':
            if self.monitors:self.buf.extend(self.monitors.pop(0))
        elif data.startswith(b'AT'):self.buf.extend(b'OK\r>')
        elif self.packets:self.buf.extend(self.packets.pop(0))
        else:self.buf.extend(b'NO DATA\r>')
        if self.after_write:self.after_write(data)
        return len(data)
    def read(self,n=1):
        value=bytes(self.buf[:n]);del self.buf[:n];return value
    def close(self):self.closed+=1


class Mode50ClientTests(unittest.TestCase):
    def client(self,port,cancel=None,sink=None):
        clock=FakeClock();client=Mode50ElmClient(port,clock=clock.time,sleeper=clock.sleep,
            cancel_event=cancel,pdu_sink=sink)
        client.configure_pair('754','7BE',clock.time()+2);return client,clock

    def test_raw_configuration_owns_address_bytes_and_disables_auto_flow_control(self):
        port=Port();client,clock=self.client(port)
        self.assertIn(b'ATCEA\r',port.writes);self.assertIn(b'ATCAF0\r',port.writes)
        self.assertIn(b'ATCFC0\r',port.writes);self.assertIn(b'ATCRA 7BE\r',port.writes)
        self.assertFalse(any(b'ATCEA55' in w for w in port.writes))
        client.close_restore();self.assertEqual(port.closed,1)

    def test_only_read_requests_can_reach_the_port(self):
        port=Port();client,clock=self.client(port);before=list(port.writes)
        for request in ('14FF00','2EF10001','2701','3101','3400','1001','3E80','22F10000',None,True):
            with self.subTest(request=request),self.assertRaises(ElmError):client.request(request,clock.time()+1)
        self.assertEqual(port.writes,before);client.close_restore()

    def test_single_response_echo_and_raw_pdu_observation(self):
        port=Port([lines(response_frames(bytes.fromhex('62F1002A')))+b'>']);seen=[]
        client,clock=self.client(port,sink=seen.append);out=client.request('22F100',clock.time()+2)
        self.assertTrue(out['ok']);self.assertEqual(out['payload_hex'],'62F1002A')
        self.assertIn(b'550322F100AAAAAA\r',port.writes);self.assertEqual(client.receive_address,0xF1)
        self.assertEqual(len(seen),1);self.assertEqual(seen[0]['pduHex'],'62F1002A')
        self.assertFalse(any(w==b'ATMA\r' for w in port.writes));client.close_restore()

    def test_multiframe_maximum_uses_exact_source_flow_control_and_no_request_replay(self):
        payload=b'\x62\xF1\x8C'+b'S'*4092;frames=response_frames(payload)
        packets=[lines(frames[:1])+b'>']+[lines(frames[i:i+16])+b'>' for i in range(1,len(frames),16)]
        port=Port(packets);client,clock=self.client(port);out=client.request('22F18C',clock.time()+15)
        self.assertEqual(bytes.fromhex(out['payload_hex']),payload)
        self.assertEqual(port.writes.count(b'550322F18CAAAAAA\r'),1)
        self.assertEqual(port.writes.count(FLOW_CONTROL.hex().upper().encode()+b'\r'),43)
        client.close_restore()

    def test_late_reply_is_received_in_monitor_and_monitor_is_acknowledged_stopped(self):
        payload=bytes.fromhex('62F1002A');port=Port([b'NO DATA\r>'],[lines(response_frames(payload))])
        client,clock=self.client(port);out=client.request('22F100',clock.time()+2)
        self.assertTrue(out['ok']);self.assertEqual(port.writes.count(b'550322F100AAAAAA\r'),1)
        self.assertEqual(port.writes.count(b'ATMA\r'),1);self.assertEqual(port.writes.count(b'\x1B'),1)
        self.assertFalse(client._monitoring);self.assertEqual(client.close_restore()['errors'],[])

    def test_delayed_first_frame_and_delayed_consecutive_frames_stop_monitor_before_flow_control(self):
        payload=bytes.fromhex('62F191')+b'12345678901';frames=response_frames(payload)
        cases=[([b'NO DATA\r>',lines(frames[1:])+b'>'],[lines(frames[:1])]),
               ([lines(frames[:1])+b'>',b'NO DATA\r>'],[lines(frames[1:])])]
        for packets,monitors in cases:
            port=Port(packets,monitors);client,clock=self.client(port)
            out=client.request('22F191',clock.time()+2)
            self.assertEqual(bytes.fromhex(out['payload_hex']),payload)
            self.assertEqual(port.writes.count(b'550322F191AAAAAA\r'),1)
            self.assertEqual(port.writes.count(FLOW_CONTROL.hex().upper().encode()+b'\r'),1)
            self.assertEqual(port.writes.count(b'\x1B'),1);self.assertFalse(client._monitoring)
            self.assertEqual(client.close_restore()['errors'],[])

    def test_bad_monitor_sequence_is_stopped_and_never_replays_the_request(self):
        payload=bytes.fromhex('62F191')+b'12345678901';frames=response_frames(payload)
        bad=bytes((frames[1][0],0x23))+frames[1][2:]
        port=Port([lines(frames[:1])+b'>',b'NO DATA\r>'],[lines([bad])]);client,clock=self.client(port)
        with self.assertRaises(ElmError):client.request('22F191',clock.time()+2)
        self.assertIn(b'\x1B',port.writes);self.assertEqual(port.writes.count(b'550322F191AAAAAA\r'),1)
        self.assertEqual(port.closed,1);self.assertFalse(client._monitoring)

    def test_pending_waits_for_a_final_reply_without_retransmitting(self):
        pending=lines(response_frames(bytes.fromhex('7F2278')))
        final=lines(response_frames(bytes.fromhex('62F1002A')))
        port=Port([pending+b'>'],[pending+final]);client,clock=self.client(port)
        out=client.request('22F100',clock.time()+2)
        self.assertEqual(out['pending'],['7F2278','7F2278']);self.assertEqual(out['payload_hex'],'62F1002A')
        self.assertEqual(port.writes.count(b'550322F100AAAAAA\r'),1);client.close_restore()

    def test_no_data_timeout_stops_monitor_before_closing_and_reports_unit_no_data(self):
        port=Port();client,clock=self.client(port)
        with self.assertRaisesRegex(ElmError,'NO DATA'):client.request('22F100',clock.time()+.1)
        self.assertIn(b'\x1B',port.writes);self.assertFalse(client._monitoring)
        self.assertEqual(port.closed,1);self.assertFalse(any('monitor-stop' in e for e in client.close_restore()['errors']))

    def test_cancelled_monitor_is_stopped_and_vehicle_request_is_not_replayed(self):
        cancel=threading.Event();port=Port();client,clock=self.client(port,cancel)
        port.after_write=lambda data:cancel.set() if data==b'ATMA\r' else None
        with self.assertRaisesRegex(ElmError,'cancelled'):client.request('22F100',clock.time()+2)
        self.assertIn(b'\x1B',port.writes);self.assertFalse(client._monitoring)
        self.assertEqual(port.writes.count(b'550322F100AAAAAA\r'),1);client.close_restore()

    def test_unacknowledged_monitor_stop_survives_close_reports(self):
        port=Port(stop_ack=False);client,clock=self.client(port)
        with self.assertRaises(ElmError):client.request('22F100',clock.time()+.1)
        for _ in range(2):self.assertIn('mode50:monitor-stop-unconfirmed',client.close_restore()['errors'])
        self.assertEqual(port.closed,1);self.assertEqual(port.writes.count(b'\x1B'),1)

    def test_address_change_wrong_echo_and_terminal_negative_are_distinct(self):
        for payload,ok,error in [(bytes.fromhex('62F1012A'),False,'wrong-sid-or-did-echo'),
                (bytes.fromhex('7F2231'),False,None)]:
            port=Port([lines(response_frames(payload))+b'>']);client,clock=self.client(port)
            if error:
                with self.assertRaisesRegex(ElmError,error):client.request('22F100',clock.time()+2)
            else:
                out=client.request('22F100',clock.time()+2);self.assertFalse(out['ok']);self.assertEqual(out['error'],'negative-response')
            client.close_restore()
        port=Port([lines(response_frames(bytes.fromhex('62F1002A'),0x55))+b'>',
                   lines(response_frames(bytes.fromhex('62F1002A'),0xF1))+b'>'])
        client,clock=self.client(port);self.assertTrue(client.request('22F100',clock.time()+2)['ok'])
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):client.request('22F100',clock.time()+2)
        client.close_restore()


class Mode50TransportTests(unittest.TestCase):
    def test_five_original_native_request_data_windows(self):
        for request, expected in [('3E00','55023E00AAAAAAAA'),('22F19E','550322F19EAAAAAA'),
                ('22F1A2','550322F1A2AAAAAA'),('22F1DF','550322F1DFAAAAAA'),('1003','55021003AAAAAAAA')]:
            with self.subTest(request=request):self.assertEqual(request_frame(bytes.fromhex(request)).hex().upper(),expected)
        self.assertEqual(FLOW_CONTROL.hex().upper(),'55301005AAAAAAAA')

    def test_source_seven_byte_drop_is_not_an_executable_request(self):
        for payload in (b'',b'A'*7,b'A'*8,bytearray(b'A'),None):
            with self.subTest(payload=payload),self.assertRaises(ElmError):request_frame(payload)
        self.assertEqual(request_frame(b'123456'),b'\x55\x06123456')

    def test_receive_address_is_observed_and_bound_without_a_default(self):
        for address in (0,0x55,0xF1,0xFF):
            a=ReplyAssembler();self.assertEqual(a.feed(bytes((address,3,0x62,0xF1,0xA2))),bytes.fromhex('62F1A2'))
            self.assertEqual(a.receive_address,address)
            with self.assertRaisesRegex(ElmError,'identity-mismatch'):ReplyAssembler(address).feed(bytes((address^1,2,0x50,3)))

    def test_multiframe_boundaries_sequence_wrap_and_every_flow_control_block(self):
        for size in (7,11,12,101,102,103,4095):
            payload=bytes(i%256 for i in range(size));a=ReplyAssembler()
            self.assertIsNone(a.feed(bytes((0xF1,0x10|(size>>8),size&255))+payload[:5]))
            offset=5;sequence=1;controls=0
            while offset<size:
                if a.flow_control_needed:a.flow_control_sent();controls+=1
                data=payload[offset:offset+6];result=a.feed(bytes((0xF1,0x20|sequence))+data)
                offset+=len(data);sequence=(sequence+1)&15
            self.assertEqual(result,payload);self.assertTrue(a.complete);self.assertFalse(a.flow_control_needed)
            self.assertEqual(controls,((size-5+5)//6+15)//16)

    def test_gaps_duplicates_changed_address_and_missing_flow_control_fail_closed(self):
        first=bytes.fromhex('F1100C62F1A20102')
        for frame,control in [(bytes.fromhex('F121030405060708'),False),
                (bytes.fromhex('F122030405060708'),True),(bytes.fromhex('F120030405060708'),True),
                (bytes.fromhex('5521030405060708'),True),(bytes.fromhex('F1210304'),True)]:
            a=ReplyAssembler();a.feed(first)
            if control:a.flow_control_sent()
            with self.subTest(frame=frame,control=control),self.assertRaises(ElmError):a.feed(frame)

    def test_bad_lengths_and_extra_frames_never_make_a_pdu(self):
        for frame in (b'',b'\xF1',b'\xF1\x00\x62',b'\xF1\x07'+b'A'*6,
                b'\xF1\x10\x06'+b'A'*5,b'\xF1\x10\x07A',b'\xF1\x20A',b'A'*9):
            with self.subTest(frame=frame),self.assertRaises(ElmError):ReplyAssembler().feed(frame)
        a=ReplyAssembler();a.feed(bytes.fromhex('F1025003'))
        with self.assertRaises(ElmError):a.feed(bytes.fromhex('F1025003'))
        with self.assertRaises(ElmError):a.flow_control_sent()


if __name__=='__main__':unittest.main()
