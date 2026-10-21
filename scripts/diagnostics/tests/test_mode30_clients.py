"""Production client lifecycle over explicit raw byte/native event fixtures."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import patch

from scripts.diagnostics.bench import FakeClock
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode30_clients import Mode30ElmClient, Mode30DpuClient
from scripts.diagnostics.tests.test_mode30_elm import RawElmPort
from scripts.diagnostics.tests.test_mode30_vnci import RawNative
from scripts.diagnostics.tests.test_vnci import FakeNative, ID


class NativePeer(RawNative, FakeNative):
    def __init__(self):
        FakeNative.__init__(self)
        RawNative.__init__(self)


def contract(ecu=76):
    rx, offer = {76:(0x226,'26C00010020301'),78:(0x235,'35C00010030301'),
                 30:(0x222,'22C00010040301'),31:(0x223,'23C00010050301')}[ecu]
    return dict(profileId='test-source',generation='981',catalogProfileId='porsche-981-tp20-source',
        sourceQualification='uds-source-version-and-identity',sourceCatalogId='fixture',
        ecuId=ecu,txId=0x200,rxId=rx,groups=[dict(requestHex='2102')],
        identityRequests=[dict(requestHex='22F1A2')],sourceCommands=['22F1A2','1089'],
        session=dict(requestHex='1089',positivePrefixHex='5089'),applicationProtocol='kwp',
        transportAddressing=dict(kind='porsche-tp20',offerHex=offer))


class Mode30ClientTests(unittest.TestCase):
    def test_unprogrammed_states_are_fault_only_on_exact_shared_targets(self):
        from scripts.diagnostics.manufacturer_acquisition import READ_PROFILES, matched_read_profile, read_profile_targets
        from scripts.diagnostics.manufacturer_transport import prepared_read_contract
        from scripts.diagnostics.mode30_source import FAULT_ONLY_TARGETS
        for pid, targets in FAULT_ONLY_TARGETS.items():
            self.assertNotIn(pid, READ_PROFILES)
            self.assertEqual(read_profile_targets(pid), targets)
            for generation in ('981','982'):
                for ecu in targets:
                    profile = dict(profileId=pid,ecuIds=list(targets),selectedEcuId=ecu,
                        generation=None,requestedGeneration=generation,status='definition-only',parameters=[])
                    self.assertEqual(matched_read_profile(profile),(ecu,'porsche-981-tp20-source',None))
                    with self.assertRaisesRegex(ValueError,'manufacturer-parameters-unavailable'):
                        prepared_read_contract(profile,[])
                    with self.assertRaisesRegex(ValueError,'manufacturer-profile-unqualified'):
                        matched_read_profile({**profile,'selectedEcuId':30 if ecu in (76,78) else 76})

    def opened(self, kind='elm', *, cancel=None, sink=None, frame_sink=None, ecu=76, identity_only=False):
        clock = FakeClock()
        if kind == 'elm':
            peer = RawElmPort(clock)
            client = Mode30ElmClient(peer, clock=clock.time, sleeper=clock.sleep,
                cancel_event=cancel, pdu_sink=sink, frame_sink=frame_sink)
        else:
            peer = NativePeer()
            client = Mode30DpuClient(ID, native=peer, clock=clock.time, sleeper=clock.sleep,
                cancel_event=cancel, pdu_sink=sink)
            client.validate_adapter()
        source=contract(ecu)
        if identity_only:source['groups']=[]
        with patch('scripts.diagnostics.manufacturer_transport.prepared_read_contract', return_value=source):
            client.configure_manufacturer({}, [], clock.time()+3, identity_only=identity_only)
        return client, peer, clock

    def test_both_clients_read_only_on_the_same_owned_channel_then_close_once(self):
        for kind in ['elm','vnci']:
            for ecu in [76,78,30,31]:
                c, peer, clock = self.opened(kind, ecu=ecu)
                for hx in ['1089','22F1A2','2102','2102']:
                    self.assertTrue(c.request(hx,clock.time()+1)['ok'])
                first = c.close_restore()
                self.assertEqual(first['errors'],[])
                self.assertTrue(first['channelClosed'])
                self.assertEqual(c.close_restore(),first)
                self.assertEqual(peer.close_count if kind=='elm' else peer.closed,1)
                if kind=='vnci':self.assertEqual(peer.active,{})

    def test_complete_pdu_metadata_keeps_first_and_last_observation(self):
        for kind in ['elm','vnci']:
            pdus=[];c,p,clock=self.opened(kind,sink=pdus.append)
            p.response=lambda req:b'\x61\x02'+b'X'*40 if kind=='elm' else None
            if kind=='vnci':p.make_reply=lambda req:b'\x61\x02'+b'X'*40
            self.assertTrue(c.request('2102',clock.time()+2)['ok'])
            self.assertEqual(len(pdus),1);row=pdus[0]
            self.assertEqual(row['transportKind'],'elm-mode30' if kind=='elm' else 'd-pdu-mode30')
            self.assertEqual(row['observation'],'reassembled-complete-pdu')
            self.assertEqual(row['timestampSource'],'host-pdu-observed')
            self.assertFalse(row['duringCleanup'])
            self.assertLessEqual(row['startedMonotonicTimestampUs'],row['monotonicTimestampUs'])
            self.assertEqual(row['pduHex'],'6102'+'58'*40)
            self.assertEqual(c.close_restore()['errors'],[])

    def test_elm_frame_sink_receives_only_actual_can_frames(self):
        frames=[];c,p,clock=self.opened(frame_sink=frames.append)
        self.assertTrue(c.request('2102',clock.time()+1)['ok'])
        self.assertTrue(frames)
        self.assertTrue(all(f['canId'] in (0x226,0x302) and not f['extended'] for f in frames))
        self.assertTrue(any(f['dataHex'].startswith('1000036102') for f in frames))
        self.assertEqual(c.close_restore()['errors'],[])

    def test_writes_and_unbound_reads_never_reach_raw_wire(self):
        for kind in ['elm','vnci']:
            for hx in ['2701','2EF10000','1101','2103','22F190']:
                c,p,clock=self.opened(kind)
                sent=list(p.frames if kind=='elm' else p.sent)
                with self.subTest(kind=kind,hx=hx),self.assertRaises(ElmError):c.request(hx,clock.time()+1)
                self.assertEqual(p.frames if kind=='elm' else p.sent,sent)
                self.assertEqual(c.close_restore()['errors'],[])

    def test_cancel_does_not_skip_channel_and_adapter_cleanup(self):
        for kind in ['elm','vnci']:
            cancel=threading.Event();c,p,clock=self.opened(kind,cancel=cancel)
            cancel.set()
            with self.assertRaisesRegex(ElmError,'cancelled'):c.request('2102',clock.time()+1)
            report=c.close_restore()
            self.assertTrue(report['channelClosed']);self.assertEqual(report['errors'],[])

    def test_no_close_ack_is_reported_and_not_disguised_as_success(self):
        for kind in ['elm','vnci']:
            c,p,clock=self.opened(kind)
            if kind=='elm':
                original=p.write
                def write(raw):
                    if raw==b'A8\r':p.writes.append(raw);p.buffer.extend(b'NO DATA\r>');return len(raw)
                    return original(raw)
                p.write=write
            else:
                original=p.start_mode30_primitive
                def start(frame=None):
                    answer=original(frame)
                    if frame==b'\xA8':p.events=type(p.events)(e for e in p.events if e['type']!=0x1300)
                    return answer
                p.start_mode30_primitive=start
            report=c.close_restore()
            self.assertTrue(report['errors']);self.assertFalse(report['channelClosed'])
            self.assertEqual(p.close_count if kind=='elm' else p.closed,1)

    def test_source_bind_can_expand_only_identity_reads_without_changing_channel(self):
        c,p,clock=self.opened('vnci',identity_only=True);before=list(p.links)
        actual=contract();actual['groups']=[];actual['identityRequests'].append(dict(requestHex='22F18C'))
        with patch('scripts.diagnostics.manufacturer_transport.prepared_read_contract',return_value=actual):
            c.bind_manufacturer_identity({},clock.time()+1)
        self.assertTrue(c.request('22F18C',clock.time()+1)['ok'])
        self.assertEqual(p.links,before)
        self.assertEqual(c.close_restore()['errors'],[])

    def test_second_configuration_never_opens_another_channel(self):
        for kind in ['elm','vnci']:
            c,p,clock=self.opened(kind)
            before=list(p.frames if kind=='elm' else p.sent)
            with patch('scripts.diagnostics.manufacturer_transport.prepared_read_contract',return_value=contract(30)):
                with self.assertRaises(ElmError):c.configure_manufacturer({},[],clock.time()+1)
            self.assertEqual(p.frames if kind=='elm' else p.sent,before)
            self.assertEqual(c.close_restore()['errors'],[])

    def test_idle_ping_is_answered_without_an_application_request(self):
        for kind in ['elm','vnci']:
            c,p,clock=self.opened(kind)
            if kind=='elm':p.pending.append(p.line(b'\xA3'))
            else:p.events.append(p.result(b'\xA3'))
            c.idle_until(clock.time()+.05,clock.time()+1)
            frames=p.frames if kind=='elm' else p.sent
            self.assertTrue(any(f[0]==0xA1 for f in frames))
            self.assertFalse(any(f[0]>>4==1 for f in frames))
            self.assertTrue(c.request('2102',clock.time()+1)['ok'])
            self.assertEqual(c.close_restore()['errors'],[])


if __name__=='__main__':unittest.main()
