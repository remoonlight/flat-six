import hashlib
import unittest
from scripts.diagnostics.coding_sequence import FLAGS, sequence, service27_carrier, vehicle_information_carrier, vehicle_information_report, periodic_link_carrier, periodic_link_report, read_context_report
from scripts.diagnostics.tests.test_x431_capture import fix, long_return


def pair(request='2703',pdu='670301020304',tx=0x7e0,rx=0x7e8,record=10,stream=2):
    req=bytes.fromhex(request);reply=bytes.fromhex(pdu)
    q=bytearray.fromhex('55aaf0f80016012701640001ff020d610108')
    q+=((tx<<5).to_bytes(2,'big')+bytes([len(req)])+req+bytes(7-len(req))+bytes(1))
    if len(reply)>7:
        a=bytearray(long_return(reply));a[16:18]=(rx<<5).to_bytes(2,'big')
    else:
        a=bytearray.fromhex('55aaf8f0001601670100000155aa0b08')
        a+=((rx<<5).to_bytes(2,'big')+bytes([len(reply)])+reply+bytes(7-len(reply))+bytes(3))
    rows=[]
    for n,(raw,direction) in enumerate(((q,'host_to_controller'),(a,'controller_to_host'))):
        b=fix(raw)
        rows.append(dict(frameId=f'f{record+n}',frameHex=b.hex(),frameSha256=hashlib.sha256(b).hexdigest(),
            stream=[stream,direction,2],sequenceByte=b[6],commandByte=b[7],startRecord=record+n))
    return rows


class ReadContextTests(unittest.TestCase):
    def report(self,frames,pairs=None,boundary=100,ids=None):
        rows,_=sequence(frames,{0x7e0:0x7e8,0x710:0x77a} if pairs is None else pairs,boundary)
        return read_context_report(rows,{0x7e0:0x7e8,0x710:0x77a} if pairs is None else pairs,ids)

    def test_negative_read_retains_preceding_session_and_access_without_unlock_claim(self):
        frames=(pair('1003','500300010002',record=10)+pair('2703','670301020304',record=12)
            +pair('2704AABBCCDD','6704',record=14)+pair('104F','504F00010002',record=16)
            +pair('2703','670305060708',record=18)+pair('270411223344','670499',record=20)
            +pair('2204B6','7F2222',record=22)+pair('2204B9','6204B92A',record=24))
        report=self.report(frames)
        self.assertEqual(report['readObservationCount'],2)
        failed,positive=report['reads']
        self.assertEqual(failed['responseStatus'],'negative');self.assertEqual(failed['nrc'],0x22)
        self.assertEqual(positive['responseStatus'],'positive-read-candidate')
        self.assertEqual(failed['precedingSessionObservationIds'],['f10','f16'])
        self.assertEqual(failed['service27AfterLatestSessionObservationIds'],['f18','f20'])
        self.assertEqual(report['events']['f20']['status'],'key-positive-prefix-observed')
        self.assertNotIn('AABBCCDD',str(report));self.assertNotIn('11223344',str(report))
        for item in (report,failed,positive):
            self.assertFalse(item['activeSessionQualified']);self.assertFalse(item['service27UnlockQualified'])
            self.assertFalse(item['failureCauseQualified']);self.assertFalse(item['executionEnabled'])

    def test_unknown_boundary_target_phase_and_stream_do_not_borrow_context(self):
        start=pair('1003','5003',record=10)
        cases=[(start+pair('3B0100','7B01',record=12)+pair('22F100','62F1002A',record=14),100,'unreviewed-host-boundary'),
            (start+pair('22F100','62F1002A',tx=0x710,rx=0x77a,record=12)+pair('22F100','62F1002A',record=14),100,'target-change'),
            (start+pair('22F100','62F1002A',record=14),11,'capture-phase-change')]
        for frames,boundary,reason in cases:
            with self.subTest(reason=reason):
                last=self.report(frames,boundary=boundary)['reads'][-1]
                self.assertEqual(last['precedingSessionObservationIds'],[])
                self.assertEqual(last['service27AfterLatestSessionObservationIds'],[])
                self.assertEqual(last['continuityBoundary']['reason'],reason)
        last=self.report(start+pair('22F100','62F1002A',record=14,stream=3))['reads'][-1]
        self.assertEqual(last['precedingSessionObservationIds'],[])

    def test_new_session_attempt_clears_old_access_even_if_pending_or_unpaired(self):
        start=pair('2703','670301020304',record=10)+pair('2704AABBCCDD','6704',record=12)
        for session,status in [(pair('1003','7F1078',record=14),'pending'),
                               (pair('1003','5003',record=14)[:1],'return-layout-unqualified')]:
            report=self.report(start+session+pair('22F100','62F1002A',record=16))
            self.assertEqual(report['reads'][0]['service27AfterLatestSessionObservationIds'],[])
            self.assertEqual(report['events']['f14']['status'],status)
            self.assertFalse(report['activeSessionQualified'])

    def test_wrong_or_missing_receiver_is_preserved_without_positive_session_claim(self):
        frames=pair('1003','5003',record=10)+pair('22F100','62F1002A',record=12)
        for pairs,status in [({},'reference-rx-missing'),({0x7e0:0x77a},'address-unqualified')]:
            report=self.report(frames,pairs=pairs)
            self.assertEqual(report['events']['f10']['status'],status)
            self.assertEqual(report['reads'][0]['responseStatus'],status)

    def test_filter_keeps_all_context_commands_and_rejects_unknown_ids_or_order(self):
        frames=pair('1003','5003',record=10)+pair('22F100','62F1002A',record=12)+pair('22F101','62F1012B',record=14)
        report=self.report(frames,ids={'f14'})
        self.assertEqual([r['observationId'] for r in report['reads']],['f14'])
        self.assertEqual(report['reads'][0]['precedingSessionObservationIds'],['f10'])
        for ids in ({'missing'},{'f10'}):
            with self.assertRaisesRegex(ValueError,'read-context-selected-read-missing'):
                self.report(frames,ids=ids)
        rows,_=sequence(frames,{0x7e0:0x7e8},100)
        with self.assertRaisesRegex(ValueError,'read-context-command-order'):
            read_context_report(list(reversed(rows)),{0x7e0:0x7e8})

    def test_periodic_stop_discards_context_without_claiming_ecu_or_device_close(self):
        raw=bytes.fromhex('55aaf0f800083b2701600d00000070')
        f=dict(pair(record=12)[0],frameHex=raw.hex(),frameSha256=hashlib.sha256(raw).hexdigest(),
               sequenceByte=raw[6],commandByte=raw[7])
        report=self.report(pair('1003','5003',record=10)+[f]+pair('22F100','62F1002A',record=14))
        row=report['reads'][0]
        self.assertEqual(row['precedingSessionObservationIds'],[])
        self.assertEqual(row['continuityBoundary']['reason'],'periodic-stop-configuration')
        self.assertEqual(row['precedingPeriodicConfigObservationIds'],['f12'])
        self.assertFalse(report['contextContinuityProven'])

    def test_empty_selected_scope_stays_empty_and_duplicate_commands_are_rejected(self):
        frames=pair('1003','5003',record=10)+pair('22F100','62F1002A',record=12)
        report=self.report(frames,ids=set())
        self.assertEqual(report['reads'],[]);self.assertEqual(report['events'],{})
        rows,_=sequence(frames,{0x7e0:0x7e8},100)
        rows[1]['observationId']=rows[0]['observationId']
        with self.assertRaisesRegex(ValueError,'read-context-command-order'):
            read_context_report(rows,{0x7e0:0x7e8})

    def test_periodic_configuration_keeps_only_matching_target_context(self):
        raw=bytes.fromhex('55aaf0f80019242701600d1155aa0d610108e200023e00aaaaaaaaaa110b78e3')
        f=dict(pair(record=12)[0],frameHex=raw.hex(),frameSha256=hashlib.sha256(raw).hexdigest(),
               sequenceByte=raw[6],commandByte=raw[7])
        for tx,rx,expected in [(0x710,0x77a,['f10']),(0x7e0,0x7e8,[])]:
            report=self.report(pair('1003','5003',tx=tx,rx=rx,record=10)+[f]
                +pair('22F100','62F1002A',tx=0x710,rx=0x77a,record=14))
            self.assertEqual(report['reads'][0]['precedingSessionObservationIds'],expected)
            self.assertEqual(report['reads'][0]['precedingPeriodicConfigObservationIds'],['f12'])
            self.assertFalse(report['contextContinuityProven'])


class CodingSequenceTests(unittest.TestCase):
    def periodic_pair(self,stop=False):
        # Retained carrier shapes; only fixture frame metadata/sequence differ.
        raw=bytes.fromhex('55aaf0f800083b2701600d00000070' if stop else
            '55aaf0f80019242701600d1155aa0d610108e200023e00aaaaaaaaaa110b78e3')
        reply=bytes.fromhex('55aaf8f000073b6701ff002f6cee' if stop else '55aaf8f00007246701ff00302cae')
        rows=pair()
        for row,b in zip(rows,(raw,reply)):
            row.update(frameHex=b.hex(),frameSha256=hashlib.sha256(b).hexdigest(),
                sequenceByte=b[6],commandByte=b[7])
        return rows

    def test_periodic_configuration_and_controller_reply_are_not_stop_or_ecu_success(self):
        rows,r=sequence(self.periodic_pair()+self.periodic_pair(True),{},100)
        report=periodic_link_report(rows)
        self.assertEqual(report['actions'],{'periodic-configuration-observed':1,'stop-configuration-observed':1})
        self.assertTrue(all(o['pairedControllerReplyObserved'] for o in report['observations']))
        self.assertTrue(all(not o['replyMeaningQualified'] for o in report['observations']))
        self.assertEqual(rows[0]['periodicLinkObservation']['nestedPayloadHex'],'3E00')
        self.assertEqual(rows[0]['periodicLinkObservation']['nestedTxId'],0x710)
        self.assertEqual(rows[0]['periodicLinkObservation']['sourceCallerIntervalArgument'],1200)
        for row in rows:
            self.assertIsNone(row['requestHex']);self.assertIsNone(row['txId'])
            self.assertIsNone(row['pairedReply']['returnShape'])
            self.assertEqual(row['kind'],'periodic-link-config-carrier')
            for key,value in FLAGS.items():self.assertEqual(row['periodicLinkObservation'][key],value)
        for k in ('actualProviderBindingQualified','actualPeriodicTransmissionQualified','actualStopQualified',
                'actualCadenceQualified','actualChannelCloseQualified','actualDeviceReleaseQualified'):
            self.assertFalse(report[k])
        self.assertTrue(r['unknownBoundariesPreventWriteAbsenceClaim'])

    def test_periodic_missing_reply_does_not_erase_request_or_infer_success(self):
        rows,_=sequence(self.periodic_pair()[:1],{},100)
        report=periodic_link_report(rows)
        self.assertEqual(report['observationCount'],1)
        self.assertFalse(report['observations'][0]['pairedControllerReplyObserved'])
        self.assertFalse(report['actualStopQualified'])

    def test_periodic_boundary_does_not_join_access_requests(self):
        rows,r=sequence(pair()+self.periodic_pair(True)+pair('2704AABBCCDD','6704',record=14),{0x7e0:0x7e8},100)
        self.assertEqual(r['adjacentSeedKeyObservations'],[])
        self.assertEqual(periodic_link_report(rows)['observationCount'],1)

    def test_periodic_length_checksum_and_source_parameters_are_exact(self):
        original=bytes.fromhex(self.periodic_pair()[0]['frameHex'])
        for index,value in ((11,16),(29,12),(30,119),(12,0),(28,0)):
            raw=bytearray(original);raw[index]=value
            self.assertIsNone(periodic_link_carrier(fix(raw)))
        for raw in (original[:-1],original+bytes(1),original[:-1]+bytes(1)):
            self.assertIsNone(periodic_link_carrier(raw))
        stop=bytearray.fromhex(self.periodic_pair(True)[0]['frameHex']);stop[13]=1
        self.assertIsNone(periodic_link_carrier(fix(stop)))

    def test_periodic_unknown_nested_payload_or_unaligned_address_remains_unknown(self):
        original=bytes.fromhex(self.periodic_pair()[0]['frameHex'])
        for index,value in ((21,0x3b),(22,1),(19,1),(20,3)):
            raw=bytearray(original);raw[index]=value
            raw[28]=0
            for b in raw[14:28]:raw[28]^=b
            rows=self.periodic_pair();b=fix(raw);rows[0].update(frameHex=b.hex(),frameSha256=hashlib.sha256(b).hexdigest())
            result,_=sequence(rows,{},100)
            self.assertEqual(result[0]['kind'],'unreviewed-host-boundary')
            self.assertEqual(periodic_link_report(result)['observationCount'],0)
            self.assertIsNotNone(result[0]['pairedReply'])

    def test_functional_information_keeps_actual_receiver_and_never_claims_all_replies(self):
        vin=(b'\x49\x02\x01'+b'TESTONLY000000001').hex()
        frames=pair('0900','490040000000',tx=0x7df)+pair('0902',vin,tx=0x7df,record=12)
        rows,_=sequence(frames,{0x7e0:0x7e8},100)
        self.assertEqual(len(vehicle_information_report(rows)['adjacentAssociations']),1)
        for r in rows:
            info=r['vehicleInformationObservation'];self.assertTrue(info['functionalRequestObserved'])
            self.assertEqual(info['observedReplyRxId'],0x7e8);self.assertFalse(info['allFunctionalRepliesCaptured'])
        frames=pair('0900','490040000000',tx=0x7df)+pair('0902',vin,tx=0x7df,rx=0x7e9,record=12)
        rows,_=sequence(frames,{0x7e0:0x7e8,0x7e1:0x7e9},100)
        self.assertEqual(vehicle_information_report(rows)['adjacentAssociations'],[])
        rows,_=sequence(frames,{},100)
        self.assertTrue(all(r['vehicleInformationObservation']['status']=='reference-rx-missing' for r in rows))

    def test_vehicle_information_is_observed_without_identity_promotion_or_vin_text(self):
        vin=b'TESTONLY000000001'
        rows,_=sequence(pair('0900','490040000000')+pair('0902',(b'\x49\x02\x01'+vin).hex(),record=12),{0x7e0:0x7e8},100)
        report=vehicle_information_report(rows)
        self.assertEqual(report['observationCount'],2)
        self.assertEqual(report['statuses'],{'supported-pids-observed':1,'vin-17-ascii-observed':1})
        self.assertEqual(report['adjacentAssociations'][0]['vinSha256'],hashlib.sha256(vin).hexdigest())
        self.assertTrue(report['adjacentAssociations'][0]['vinPidAdvertised'])
        self.assertFalse(report['vehicleIdentityQualified']);self.assertFalse(report['exactEcuVersionQualified'])
        self.assertNotIn(vin.decode(),str(report));self.assertFalse(report['vinTextIncluded'])
        self.assertEqual(rows[1]['kind'],'vehicle-information-carrier')
        self.assertEqual(rows[1]['pairedReply']['returnShape']['pduHex'],(b'\x49\x02\x01'+vin).hex().upper())

    def test_vehicle_information_lengths_prefix_and_printable_bytes_are_exact(self):
        for request,pdu,status in [('0900','4900000000','supported-pids-length-unreviewed'),
                ('0900','49000000000000','supported-pids-length-unreviewed'),('0900','7F0978','pending'),
                ('0902','7F0931','negative'),('0902','4901','echo-mismatch')]:
            rows,_=sequence(pair(request,pdu),{0x7e0:0x7e8},100)
            self.assertEqual(rows[0]['vehicleInformationObservation']['status'],status)
        for data in (b'\x01'+b'A'*16,b'\x01'+b'A'*18,b'\x02'+b'A'*17,b'\x01'+b'A'*16+b'\x00'):
            rows,_=sequence(pair('0902',(b'\x49\x02'+data).hex()),{0x7e0:0x7e8},100)
            result=rows[0]['vehicleInformationObservation'];self.assertEqual(result['status'],'vin-layout-unreviewed')
            self.assertNotIn('vinSha256',result)

    def test_vehicle_information_uses_only_explicit_matching_reply_address(self):
        for pairs,status in (({},'reference-rx-missing'),({0x7e0:0x77a},'address-unqualified')):
            rows,_=sequence(pair('0900','490040000000'),pairs,100)
            self.assertEqual(rows[0]['vehicleInformationObservation']['status'],status)
            self.assertNotIn('supportedPids',rows[0]['vehicleInformationObservation'])

    def test_vehicle_information_relation_does_not_skip_unknown_or_cross_scope(self):
        vin=(b'\x49\x02\x01'+b'TESTONLY000000001').hex()
        supported=pair('0900','490040000000')
        for extra,end,boundary in [(pair('3B0100','7B01',record=12),{},100),([],{ 'stream':3},100),
                ([],{'tx':0x710,'rx':0x77a},100),([],{},11)]:
            rows,r=sequence(supported+extra+pair('0902',vin,record=14,**end),{0x7e0:0x7e8,0x710:0x77a},boundary)
            self.assertEqual(vehicle_information_report(rows)['adjacentAssociations'],[])
            self.assertTrue(r['unknownBoundariesPreventWriteAbsenceClaim'])

    def test_vehicle_information_carrier_rejects_unknown_pid_count_and_bad_frame(self):
        for request in ('0904','0902FF','09','2EF10000'):
            self.assertIsNone(vehicle_information_carrier(bytes.fromhex(pair(request)[0]['frameHex'])))
        raw=bytearray.fromhex(pair('0900','490000000000')[0]['frameHex']);raw[-1]^=1
        self.assertIsNone(vehicle_information_carrier(raw))
        raw[19]|=1;self.assertIsNone(vehicle_information_carrier(fix(raw)))

    def test_historical_prefix_extra_data_and_raw_frames_do_not_qualify_access(self):
        frames=pair()+pair('2704AABBCCDD','670499',record=12)
        rows,report=sequence(frames,{0x7e0:0x7e8},100)
        self.assertEqual(rows[1]['accessObservation']['uninterpretedResponseDataHex'],'99')
        self.assertEqual(report['keyResponsesWithUninterpretedData'],1)
        self.assertEqual(len(report['adjacentSeedKeyObservations']),1)
        self.assertEqual(rows[0]['requestFrameHex'],frames[0]['frameHex'])
        self.assertEqual(rows[1]['pairedReply']['frameHex'],frames[3]['frameHex'])
        for result in (report,rows[0],rows[1]['accessObservation'],report['adjacentSeedKeyObservations'][0]):
            for k,v in FLAGS.items():self.assertEqual(result[k],v)

    def test_unknown_host_boundary_preserves_reply_and_breaks_adjacency(self):
        frames=pair()+pair('3B010000','7B0100',record=12)+pair('2704AABBCCDD','6704',record=14)
        rows,report=sequence(frames,{0x7e0:0x7e8},100)
        self.assertEqual(rows[1]['kind'],'unreviewed-host-boundary')
        self.assertIsNone(rows[1]['requestHex'])
        self.assertEqual(rows[1]['pairedReply']['returnShape']['pduHex'],'7B0100')
        self.assertEqual(report['adjacentSeedKeyObservations'],[])
        self.assertTrue(report['unknownBoundariesPreventWriteAbsenceClaim'])

    def test_cross_stream_tx_or_phase_never_linked(self):
        for kwargs,boundary in (({'stream':3},100),({'tx':0x710,'rx':0x77a},100),({},11)):
            frames=pair()+pair('2704AABBCCDD','6704',record=12,**kwargs)
            _,r=sequence(frames,{0x7e0:0x7e8,0x710:0x77a},boundary)
            self.assertEqual(r['adjacentSeedKeyObservations'],[])

    def test_zero_or_empty_seed_does_not_establish_unlock_or_seed_key_relation(self):
        for pdu in ('670300000000','6703'):
            rows,r=sequence(pair(pdu=pdu)+pair('2704AABBCCDD','6704',record=12),{0x7e0:0x7e8},100)
            self.assertEqual(r['adjacentSeedKeyObservations'],[])
            self.assertFalse(rows[0]['accessQualified'])
            self.assertEqual(rows[0]['accessObservation']['zeroSeedObserved'],pdu=='670300000000')

    def test_key_negative_pending_and_wrong_echo_are_not_positive_prefix(self):
        for pdu,status in (('7F2735','negative'),('7F2778','pending'),('6706','echo-mismatch')):
            _,r=sequence(pair()+pair('2704AABBCCDD',pdu,record=12),{0x7e0:0x7e8},100)
            a=r['adjacentSeedKeyObservations'][0]
            self.assertEqual(a['keyResponseStatus'],status);self.assertFalse(a['historicalPositivePrefixObserved'])

    def test_missing_or_wrong_reference_rx_keeps_bytes_without_interpreting_response(self):
        for pairs,status in (({},'reference-rx-missing'),({0x7e0:0x77a},'address-unqualified')):
            rows,r=sequence(pair(),pairs,100)
            self.assertEqual(rows[0]['accessObservation']['status'],status)
            self.assertIsNotNone(rows[0]['pairedReply'])
            self.assertEqual(r['adjacentSeedKeyObservations'],[])

    def test_reply_sequence_stream_and_command_must_match(self):
        for key,value in (('sequenceByte',2),('stream',[3,'controller_to_host',2]),('commandByte',0x61)):
            frames=pair();frames[1][key]=value
            rows,_=sequence(frames,{0x7e0:0x7e8},100)
            self.assertIsNone(rows[0]['pairedReply'])
            self.assertEqual(rows[0]['accessObservation']['status'],'return-layout-unqualified')

    def test_carrier_rejects_bad_checksum_unaligned_address_and_unreviewed_subfunction(self):
        raw=bytes.fromhex(pair()[0]['frameHex']);self.assertIsNotNone(service27_carrier(raw))
        broken=bytearray(raw);broken[-1]^=1;self.assertIsNone(service27_carrier(broken))
        broken=bytearray(raw);broken[19]|=1;self.assertIsNone(service27_carrier(fix(broken)))
        for req in ('2700','2783','27','3B010000'):
            self.assertIsNone(service27_carrier(bytes.fromhex(pair(req)[0]['frameHex'])))


if __name__=='__main__':unittest.main()
