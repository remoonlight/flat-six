"""Protocol plus owned raw-DPU lifecycle. Native events are explicit fixtures."""
from collections import deque
import unittest

from scripts.diagnostics.bench import FakeClock
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode30_transport import ChannelOffer, Mode30Channel, HOST_PARAMETERS
from scripts.diagnostics.mode30_vnci import Mode30RawIo
from scripts.diagnostics.tests.test_mode30_transport import data_frames
from scripts.diagnostics.vnci import UNDEF


class RawNative:
    def __init__(self):
        self.events=deque();self.active={};self.links=[];self.sent=[];self.cancelled=[];self.released=0
        self.next_cop=10;self.next_tag=0;self.rx=None;self.pair=None;self.receive_sequence=0
        self.make_reply=lambda request:bytes((request[0]+0x40,))+request[1:]+b'\x2A'
        self.mutate=None;self.no_tx_terminal=False;self.no_cancel_terminal=False
        self.release_error=False;self.fail_second_link=False;self.extra_setup_reply=False
        self.reply_remaining=deque();self.messages_remaining=deque();self.expected_ack=None
    def create_mode30_link(self,tx,rx,*,offer,setup):
        if len(self.links)==1 and self.fail_second_link:raise ElmError('fixture-create-failed')
        self.links.append((tx,rx,setup));self.pair=(tx,rx)
        self.events.append(dict(type=0x1301,cop=UNDEF,value=0x8050))
    def status(self,cop,state):
        return dict(type=0x1301,cop=cop,tag=self.active[cop],value=state)
    def result(self,frame,*,cop=None):
        cop=self.rx if cop is None else cop
        event=dict(type=0x1300,cop=cop,tag=self.active[cop],payload=self.pair[1].to_bytes(4,'big')+frame,
                   flags=b'\0'*4,unique_id=1,acceptance_id=1)
        return self.mutate(event) if self.mutate else event
    def start_mode30_primitive(self,frame=None):
        self.next_cop+=1;self.next_tag+=1;cop=self.next_cop;tag=self.next_tag;self.active[cop]=tag
        if frame is None:self.rx=cop;return cop,tag
        self.sent.append(frame);replies=[]
        if len(frame)==7 and frame[1]==0xC0:
            replies=[bytes.fromhex('00D00203410501')]
            if self.extra_setup_reply:replies.append(replies[0])
        elif frame==HOST_PARAMETERS:replies=[bytes.fromhex('A10F8AFF0AFF')]
        elif frame==b'\xA8':replies=[b'\xA8']
        elif len(frame)==1 and frame[0]>>4==0xB:
            if self.expected_ack is not None:
                if frame[0]!=(0xB0|self.expected_ack):raise AssertionError('fixture wrong block acknowledgement')
                replies=self.next_block()
        elif frame[0]>>4==1:
            replies=[bytes((0xB0|((frame[0]+1)&15),))]
            response=self.make_reply(frame[3:])
            self.messages_remaining=deque(response if isinstance(response,list) else [response])
            replies+=self.next_block()
        self.events.extend(self.result(x) for x in replies)
        if not self.no_tx_terminal:self.events.append(self.status(cop,0x8012))
        return cop,tag
    def next_block(self):
        if not self.reply_remaining and self.messages_remaining:
            frames=data_frames(self.messages_remaining.popleft(),self.receive_sequence)
            self.receive_sequence=(self.receive_sequence+len(frames))&15
            self.reply_remaining.extend(frames)
        frames=[self.reply_remaining.popleft() for _ in range(min(15,len(self.reply_remaining)))]
        self.expected_ack=((frames[-1][0]+1)&15) if frames else None
        return frames
    def event(self):
        event=self.events.popleft() if self.events else None
        if event and event['type']==0x1301 and event.get('value') in (0x8012,0x8013):
            self.active.pop(event['cop'],None)
        return event
    def cancel(self,cop):
        self.cancelled.append(cop)
        if not self.no_cancel_terminal:self.events.append(self.status(cop,0x8013))
    def release_link(self):
        self.released+=1
        if self.active:raise AssertionError('release with live primitive')
        return dict(errors=['fixture-release-failed'] if self.release_error else [],moduleRetained=True)


class Mode30DpuIoTests(unittest.TestCase):
    def make(self,native=None):
        clock=FakeClock();native=native or RawNative();offer=ChannelOffer(0x200,0x226,bytes.fromhex('26C00010020301'))
        io=Mode30RawIo(native,offer,clock=clock.time,sleeper=clock.sleep)
        channel=Mode30Channel(io,offer,{'22F100','22F101'},clock=clock.time,sleeper=clock.sleep)
        return channel,io,native,clock

    def opened(self,native=None):
        c,io,n,clock=self.make(native);c.open(clock.time()+3);return c,io,n,clock

    def test_whole_channel_requests_acknowledgements_and_close_release_owned_tasks(self):
        c,io,n,clock=self.opened()
        self.assertEqual(n.links,[(0x200,0x226,True),(0x541,0x302,False)])
        self.assertEqual(n.released,1);self.assertEqual(len(n.cancelled),1)
        rx=io._rx_cop
        for request in ['22F100','22F101']:
            out=c.request(request,clock.time()+2)
            self.assertTrue(out['ok']);self.assertEqual(out['payload_hex'],'62'+request[2:]+'2A')
            self.assertEqual(io._rx_cop,rx);self.assertIn(rx,io.active)
        self.assertTrue(c.close_channel(clock.time()+1)['channelClosed'])
        self.assertTrue(io.close_primitives(clock.time()+1)['primitivesClosed'])
        self.assertEqual(io.active,{});self.assertEqual(n.active,{})
        self.assertEqual(n.sent.count(bytes.fromhex('10000322F100')),1)
        self.assertTrue(io.close_primitives(clock.time()+1)['primitivesClosed'])

    def test_negotiated_pair_must_come_from_delivered_setup_reply(self):
        c,io,n,clock=self.make();io.configure_pair(0x200,0x226,clock.time()+1)
        before=list(n.links)
        with self.assertRaisesRegex(ElmError,'address-unqualified'):
            io.configure_pair(0x541,0x302,clock.time()+1)
        self.assertEqual(n.links,before)
        io.close_primitives(clock.time()+1)
        self.assertEqual(n.active,{})

    def test_wrong_negotiated_pair_or_repeated_pair_switch_never_creates_another_link(self):
        c,io,n,clock=self.opened();before=list(n.links)
        with self.assertRaises(ElmError):io.configure_pair(0x542,0x302,clock.time()+1)
        self.assertEqual(n.links,before)
        io.close_primitives(clock.time()+1)

    def test_cancelled_receiver_must_be_terminal_before_releasing_setup_link(self):
        n=RawNative();n.no_cancel_terminal=True;c,io,n,clock=self.make(n)
        with self.assertRaisesRegex(ElmError,'deadline-expired'):c.open(clock.time()+2)
        self.assertEqual(len(n.links),1);self.assertEqual(n.released,0)
        self.assertTrue(io.active)
        n.no_cancel_terminal=False;io.close_primitives(clock.time()+1)
        self.assertEqual(n.active,{})

    def test_extra_setup_reply_release_failure_and_second_configuration_failure_stop(self):
        for failure in ['extra_setup_reply','release_error','fail_second_link']:
            n=RawNative();setattr(n,failure,True);c,io,n,clock=self.make(n)
            with self.subTest(failure=failure),self.assertRaises(ElmError):c.open(clock.time()+2)
            self.assertFalse(any(frame and frame[0]>>4==1 for frame in n.sent))
            io.close_primitives(clock.time()+1)

    def test_foreign_primitive_or_tag_wrong_id_and_bad_result_metadata_stop(self):
        mutations=[lambda e:{**e,'cop':999},lambda e:{**e,'tag':e['tag']+1},
                   lambda e:{**e,'payload':bytes.fromhex('00000303')+e['payload'][4:]},
                   lambda e:{**e,'flags':b'\1\0\0\0'},lambda e:{**e,'unique_id':2},
                   lambda e:{**e,'acceptance_id':2},lambda e:{**e,'extra_footer':b'X'}]
        for mutate in mutations:
            c,io,n,clock=self.opened();n.mutate=mutate;before=len(n.sent)
            with self.subTest(mutate=mutate),self.assertRaises(ElmError):c.request('22F100',clock.time()+1)
            self.assertEqual(len(n.sent),before+1)
            self.assertEqual(c.state,'failed')
            # Discard the injected foreign metadata in this explicit native
            # model; the production close cannot silently turn it into a PDU.
            n.mutate=None;n.events.clear()
            io.close_primitives(clock.time()+1)

    def test_a_result_on_the_transmit_primitive_is_not_an_ecu_reply(self):
        c,io,n,clock=self.opened()
        def changed(e):
            tx=max(n.active);return {**e,'cop':tx,'tag':n.active[tx]}
        n.mutate=changed
        with self.assertRaisesRegex(ElmError,'malformed-or-unmatched'):c.request('22F100',clock.time()+1)
        n.events.clear();n.mutate=None;io.close_primitives(clock.time()+1)

    def test_missing_transmit_terminal_does_not_publish_queued_good_data_or_retry(self):
        c,io,n,clock=self.opened();n.no_tx_terminal=True
        with self.assertRaisesRegex(ElmError,'deadline-expired'):c.request('22F100',clock.time()+2)
        self.assertEqual(n.sent.count(bytes.fromhex('10000322F100')),1)
        self.assertTrue(io.active)
        io.close_primitives(clock.time()+1)
        self.assertEqual(n.active,{})

    def test_unexpected_receiver_end_and_native_error_cannot_be_samples(self):
        for event in [dict(type=0x1301,value=0x8012),dict(type=0x1302,value=0x10)]:
            c,io,n,clock=self.opened();rx=io._rx_cop
            n.events.append({**event,'cop':rx,'tag':n.active[rx]})
            with self.assertRaises(ElmError):c.request('22F100',clock.time()+1)
            n.events.clear();io.close_primitives(clock.time()+1)

    def test_maximum_reply_obeys_block_flow_control_and_uses_one_persistent_receiver(self):
        c,io,n,clock=self.opened();rx=io._rx_cop
        n.make_reply=lambda _:bytes.fromhex('62F100')+b'X'*4092
        result=c.request('22F100',clock.time()+2)
        self.assertEqual(len(bytes.fromhex(result['payload_hex'])),4095)
        self.assertEqual(io._rx_cop,rx);self.assertEqual(len(n.links),2)
        self.assertEqual(len([frame for frame in n.sent if len(frame)==1 and frame[0]>>4==0xB]),40)
        self.assertIsNotNone(io.last_observation)
        self.assertEqual(io.last_observation['primitive'],rx)
        self.assertTrue(c.close_channel(clock.time()+1)['channelClosed'])
        io.close_primitives(clock.time()+1)

    def test_pending_message_is_acknowledged_before_final_without_an_application_retry(self):
        c,io,n,clock=self.opened();n.make_reply=lambda _:[bytes.fromhex('7F2278'),bytes.fromhex('62F1002A')]
        result=c.request('22F100',clock.time()+1)
        self.assertEqual(result['pending'],['7F2278'])
        self.assertEqual(result['payload_hex'],'62F1002A')
        self.assertEqual(n.sent.count(bytes.fromhex('10000322F100')),1)
        self.assertEqual(n.sent[-2:],[b'\xB1',b'\xB2'])
        io.close_primitives(clock.time()+1)

    def test_close_drops_payloads_observed_during_primitive_cancellation(self):
        c,io,n,clock=self.opened();n.events.append(n.result(bytes.fromhex('10000462F1002A')))
        io.close_primitives(clock.time()+1)
        self.assertEqual(list(io._frames),[])
        self.assertEqual(io.active,{})

    def test_idle_peer_keepalive_is_serviced_without_collecting_or_sending_read_requests(self):
        c,io,n,clock=self.opened();before=list(n.sent);n.events.append(n.result(b'\xA3'))
        until=clock.time()+.03;c.idle_until(until,clock.time()+1)
        self.assertEqual(n.sent[len(before):],[b'\xA1'+HOST_PARAMETERS[1:]])
        self.assertGreaterEqual(clock.time(),until)
        self.assertEqual(c.state,'idle');self.assertFalse(io._failed)
        self.assertTrue(c.request('22F100',clock.time()+1)['ok'])
        io.close_primitives(clock.time()+1)

    def test_idle_peer_close_is_confirmed_and_unsolicited_data_is_not_a_sample(self):
        for frame,error in [(b'\xA8','peer-closed'),(bytes.fromhex('10000462F1002A'),'unsolicited-frame')]:
            c,io,n,clock=self.opened();n.events.append(n.result(frame))
            with self.assertRaisesRegex(ElmError,error):c.idle_until(clock.time()+.03,clock.time()+1)
            self.assertEqual(c.channel_close_verified,frame==b'\xA8')
            self.assertFalse(any(x and x[0]>>4==1 for x in n.sent))
            io.close_primitives(clock.time()+1)


if __name__=='__main__':unittest.main()
