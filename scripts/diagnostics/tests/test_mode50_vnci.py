"""Owned synthetic RAW CAN primitives exercise the real mode50 assembler."""
from collections import deque
import threading
import unittest

from scripts.diagnostics.bench import FakeClock
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode50_transport import FLOW_CONTROL
from scripts.diagnostics.mode50_vnci import Mode50DpuClient
from scripts.diagnostics.tests.test_mode50_transport import response_frames
from scripts.diagnostics.tests.test_vnci import FakeNative,ID


class RawNative(FakeNative):
    def __init__(self,responses=()):
        super().__init__();self.responses=list(responses);self.frames=deque();self.tx_frames=[]
        self.counter=0;self.tags={};self.rx=None;self.ignore_cancel=False;self.mutate=None;self.response_before_tx_done=False
    def create_mode50_link(self):self.links.append(('ISO_11898_RAW',0x754,0x7BE))
    def status(self,cop,value):return dict(type=0x1301,cop=cop,tag=self.tags[cop],value=value)
    def data(self,frame):
        out=dict(type=0x1300,cop=self.rx,tag=self.tags[self.rx],payload=bytes.fromhex('000007BE')+frame,
            unique_id=1,acceptance_id=1,flags=b'\0'*4,extra_header=None,extra_footer=None)
        if self.mutate:self.mutate(out)
        return out
    def start_mode50_primitive(self,frame=None):
        self.counter+=1;cop=self.counter;self.tags[cop]=cop+100
        self.events.append(self.status(cop,0x8010))
        if frame is None:self.rx=cop
        else:
            self.tx_frames.append(frame)
            if frame==FLOW_CONTROL:
                for _ in range(16):
                    if not self.frames:break
                    self.events.append(self.data(self.frames.popleft()))
                self.events.append(self.status(cop,0x8012))
            else:
                self.sent.append(frame[2:2+frame[1]].hex().upper())
                if self.responses:
                    payload=self.responses.pop(0)
                    if isinstance(payload,bytes):payload=[payload]
                    self.frames=deque(frame for p in payload for frame in response_frames(p))
                if not self.response_before_tx_done:self.events.append(self.status(cop,0x8012))
                if self.frames:
                    first=self.frames.popleft();self.events.append(self.data(first))
                    # Multiple complete SFs (NRC78 then final) require no FC.
                    while self.frames and self.frames[0][1]>>4==0:self.events.append(self.data(self.frames.popleft()))
                if self.response_before_tx_done:self.events.append(self.status(cop,0x8012))
        return cop,self.tags[cop]
    def cancel(self,cop):
        self.cancelled.append(cop)
        if not self.ignore_cancel:self.events.append(self.status(cop,0x8013))


def client(native=None,cancel=None):
    native=native or RawNative();clock=FakeClock()
    c=Mode50DpuClient(ID,native=native,clock=clock.time,sleeper=clock.sleep,cancel_event=cancel)
    c._configured=True;c.allowed={'22F100','3E00','1003'}
    return c,native,clock


class Mode50DpuTests(unittest.TestCase):
    def test_single_reply_uses_separate_owned_receive_and_one_transmit(self):
        c,n,clock=client(RawNative([bytes.fromhex('62F1002A')]))
        out=c.request('22F100',clock.time()+1)
        self.assertTrue(out['ok']);self.assertEqual(out['payload_hex'],'62F1002A')
        self.assertEqual(n.sent,['22F100']);self.assertEqual(n.cancelled,[1]);self.assertEqual(c._raw_active,set())
        self.assertEqual(c.receive_address,0xF1);self.assertEqual(c.close_restore()['errors'],[])

    def test_long_reply_wraps_sequence_and_sends_only_source_flow_controls(self):
        for size in [7,101,150,4095]:
            pdu=bytes.fromhex('62F100')+bytes(i%256 for i in range(size-3))
            c,n,clock=client(RawNative([pdu]));pdus=[];c._pdu_sink=pdus.append
            out=c.request('22F100',clock.time()+3)
            self.assertEqual(out['payload_hex'],pdu.hex().upper());self.assertEqual(n.sent,['22F100'])
            self.assertEqual(n.tx_frames[1:],[FLOW_CONTROL]*((len(response_frames(pdu))-2)//16+1))
            self.assertEqual(len(pdus),1);self.assertEqual(pdus[0]['transportKind'],'d-pdu-mode50')
            self.assertEqual(pdus[0]['observation'],'reassembled-complete-pdu');self.assertEqual(pdus[0]['pduHex'],pdu.hex().upper())
            self.assertEqual(c._raw_active,set());self.assertEqual(c.close_restore()['errors'],[])

    def test_response_before_transmit_terminal_waits_then_sends_flow_control(self):
        pdu=b'\x62\xF1\0'+b'1234567890'*4;n=RawNative([pdu]);n.response_before_tx_done=True
        c,n,clock=client(n);self.assertEqual(c.request('22F100',clock.time()+1)['payload_hex'],pdu.hex().upper())
        self.assertEqual(n.tx_frames[1:],[FLOW_CONTROL]);self.assertEqual(n.sent,['22F100'])

    def test_pending_reply_keeps_receiving_without_replaying_request(self):
        c,n,clock=client(RawNative([[b'\x7F\x22\x78',b'\x62\xF1\0\x2A']]))
        out=c.request('22F100',clock.time()+1);self.assertTrue(out['ok']);self.assertEqual(out['pending'],['7F2278'])
        self.assertEqual(n.sent,['22F100']);self.assertEqual(len(n.tx_frames),1)

    def test_negative_reply_is_preserved_after_receive_primitive_stops(self):
        c,n,clock=client(RawNative([b'\x7F\x22\x31']))
        out=c.request('22F100',clock.time()+1);self.assertFalse(out['ok']);self.assertEqual(out['error'],'negative-response')
        self.assertEqual(out['payload_hex'],'7F2231');self.assertEqual(c._raw_active,set())

    def test_wrong_can_id_flags_owner_tag_or_acceptance_never_publish_values(self):
        for change in [lambda e:e.update(payload=bytes.fromhex('000007DF')+e['payload'][4:]),
                       lambda e:e.update(flags=b'\0\0\0\1'),lambda e:e.update(cop=999),
                       lambda e:e.update(tag=e['tag']-1),lambda e:e.update(unique_id=2),
                       lambda e:e.update(acceptance_id=0),lambda e:e.update(extra_header=b'foreign')]:
            n=RawNative([bytes.fromhex('62F1002A')]);n.mutate=change;c,n,clock=client(n);pdus=[];c._pdu_sink=pdus.append
            with self.assertRaises(ElmError):c.request('22F100',clock.time()+1)
            self.assertEqual(pdus,[]);self.assertEqual(n.sent,['22F100']);self.assertTrue(c._poisoned)
            with self.assertRaisesRegex(ElmError,'client-poisoned'):c.request('22F100',clock.time()+1)

    def test_wrong_did_echo_and_changed_receive_address_stop(self):
        c,n,clock=client(RawNative([bytes.fromhex('62F1012A')]))
        with self.assertRaisesRegex(ElmError,'wrong-sid-or-did-echo'):c.request('22F100',clock.time()+1)
        c,n,clock=client(RawNative([bytes.fromhex('62F1002A'),bytes.fromhex('62F1002B')]))
        c.request('22F100',clock.time()+1)
        def address_changed(e):e['payload']=e['payload'][:4]+b'\xFA'+e['payload'][5:]
        n.mutate=address_changed
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):c.request('22F100',clock.time()+1)

    def test_bad_sequence_and_extra_complete_reply_are_rejected(self):
        for problem in ['sequence','extra']:
            n=RawNative([b'\x62\xF1\0'+b'1'*30] if problem=='sequence' else [[bytes.fromhex('62F1002A'),bytes.fromhex('62F1002B')]])
            if problem=='sequence':
                def corrupt(e):
                    if e['payload'][5]>>4==2:e['payload']=e['payload'][:5]+b'\x25'+e['payload'][6:]
                n.mutate=corrupt
            c,n,clock=client(n)
            with self.assertRaises(ElmError):c.request('22F100',clock.time()+1)
            self.assertEqual(n.sent,['22F100']);self.assertEqual(c._raw_active,set())

    def test_cancel_and_deadline_confirm_all_owned_primitive_terminals(self):
        for cancelled in [False,True]:
            stop=threading.Event();c,n,clock=client(RawNative(),stop)
            original=n.event
            def event():
                value=original()
                if cancelled and value is None and n.rx is not None:stop.set()
                return value
            n.event=event
            with self.assertRaises(ElmError):c.request('22F100',clock.time()+.1)
            self.assertTrue(n.cancelled);self.assertEqual(c._raw_active,set());self.assertEqual(c.close_restore()['errors'],[])

    def test_unconfirmed_cancel_keeps_cleanup_failure_and_no_response_success(self):
        c,n,clock=client(RawNative());n.ignore_cancel=True
        with self.assertRaises(ElmError):c.request('22F100',clock.time()+.1)
        self.assertTrue(c._raw_active);report=c.close_restore()
        self.assertTrue(any('primitive-terminal-timeout' in e for e in report['errors']))
        self.assertEqual(c.close_restore(),report)

    def test_old_queued_response_or_unallowed_request_cannot_start_transmission(self):
        c,n,clock=client();n.events.append(dict(type=0x1300,cop=999,tag=999,payload=b'old'))
        with self.assertRaisesRegex(ElmError,'desynchronized-rx'):c.request('22F100',clock.time()+1)
        self.assertEqual(n.tx_frames,[])
        for request in ['2EF10001','2701','3101','220000','1101']:
            c,n,clock=client()
            with self.assertRaises(ElmError):c.request(request,clock.time()+1)
            self.assertEqual(n.tx_frames,[])

    def test_cancel_during_queue_check_or_receive_start_never_sends_a_vehicle_frame(self):
        for point in ['queue','receive']:
            stop=threading.Event();c,n,clock=client(cancel=stop)
            if point=='queue':
                original=n.event
                def event():stop.set();return original()
                n.event=event
            else:
                original=n.start_mode50_primitive
                def start(frame=None):
                    result=original(frame)
                    if frame is None:stop.set()
                    return result
                n.start_mode50_primitive=start
            with self.assertRaisesRegex(ElmError,'cancelled'):c.request('22F100',clock.time()+1)
            self.assertEqual(n.tx_frames,[]);self.assertEqual(c._raw_active,set())
