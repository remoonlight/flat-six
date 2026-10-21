"""Fault source/transport checks with explicit synthetic PDUs; no hardware."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,tempfile,time,unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_dtc as dtc,manufacturer_read as read
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.session_simulator import SessionSimPort,ath1_prompt,isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_read import bundle,request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract,model_responses,PROFILES
from scripts.diagnostics.tests.test_manufacturer_mode50 import model_contract as raw_contract,model_replies,RawSourceNative,ModelPort
from scripts.diagnostics.tests.test_vnci import FakeNative,ID
from scripts.diagnostics.manufacturer_transport import identity_details
from scripts.diagnostics.decode import decode_payload


def source(pid=PROFILES[5],ecu=5,kwp=False,reader=9,text='Original factory English'):
    kind='03000102FF00'+f'{reader:02X}' if kwp else '04FF0203FF0009'
    request_hex='1800FF00' if kwp else '190208';session='1089' if kwp else '1003'
    def command(hx,role):return dict(role=role,wireCandidateHex=hx,commandFormat='complete-source-command',
        nativeDescriptorHexCandidate='01'+f'{len(hx)//2:02X}'+hx)
    commands=([command(session,'sessionPrecondition')] if reader==9 else [])+[command(request_hex,'dtcRead')]
    dictionary=dict(profileId=pid,records=[dict(numericKey=0xC447 if kwp else 0xC13002,text=text,definitionIssue=None)])
    dictionary['definitionSha256']=hashlib.sha256(json.dumps(dictionary,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    return dict(profileId=pid,variant=pid.rsplit(':',1)[1],generations=['981','982'],dtcDefinition=dictionary,
        diagnosticSources=[dict(requestedEcuId=ecu,variantName=pid.rsplit(':',1)[1],links=[dict(linkIndex=0,
            dtc=dict(styleByte=int(kind[:2],16),metadataHex=kind[2:],readCommandSlots=commands))])])


def context(kwp=False):
    pid=PROFILES[5];profile=dict(profileId=pid,selectedEcuId=5,requestedGeneration='981')
    contract=dict(profileId=pid,ecuId=5,generation='981',sourceCatalogId=dtc.SOURCE_CATALOG,
        sourceQualification='kwp-source-version-and-identity' if kwp else 'uds-source-version-and-identity',
        session=dict(requestHex='1089' if kwp else '1003'))
    qualified=dict(freshRead=True,observedProfileMatch=True,sourceCatalogId=dtc.SOURCE_CATALOG,
        generation='981',ecuId=5,variantLink=dict(profile_id=pid))
    return profile,contract,qualified


class Client:
    def __init__(self,kwp=False,pdu=None):
        self.allowed=set();self.sent=[];self.replies={'1089':'5089','1003':'5003',
            '1800FF00':pdu or '5801C44728','190208':pdu or '5902FFC1300209'}
    def request(self,hx,deadline):
        assert hx in self.allowed;self.sent.append(hx)
        return dict(ok=True,payload_hex=self.replies[hx])


class SourceDtcTests(unittest.TestCase):
    def test_reader11_derives_count_from_complete_records_and_skips_the_prefix_byte(self):
        for header in [0,1,2,255]:
            for count in [0,1,2,20,255]:
                pdu=bytes((0x58,header))+bytes.fromhex('C44728')*count
                out=dtc.decode_source_dtc(pdu.hex(),'kwp-dtc-18-length')
                self.assertTrue(out['ok']);self.assertEqual(len(out['records']),count)
                if count:
                    self.assertEqual(out['records'][0]['dtcHex'],'C447')
                    self.assertEqual(out['records'][0]['statusHex'],'28')
        # A traditional count-prefixed response must retain its old validation.
        self.assertFalse(decode_payload('58FFC44728',{'type':'kwp-dtc-18'})['ok'])

    def test_reader11_rejects_missing_prefix_partial_records_and_wrong_service(self):
        for payload in ['','58','5902','5800C4','5800C447','5800C4472801','7F1878']:
            out=dtc.decode_source_dtc(payload,'kwp-dtc-18-length')
            self.assertFalse(out['ok'],payload);self.assertEqual(out.get('records',[]),[])

    def test_reader11_plan_requires_its_source_target_transport_and_read_only_command(self):
        pid='9x1:Sitzsteuergeraet:Sitz_A2'
        for ecu in [30,31,76,78]:
            p,c,q=context(True)
            p.update(profileId=pid,selectedEcuId=ecu);c.update(profileId=pid,ecuId=ecu,transportAddressing={'kind':'porsche-tp20'})
            q.update(ecuId=ecu,variantLink={'profile_id':pid})
            s=source(pid,ecu,True,reader=11);table=s['diagnosticSources'][0]['links'][0]['dtc']
            table['metadataHex']='FF0102FF000B';cmd=table['readCommandSlots'][0]
            cmd.update(wireCandidateHex='1802FF00',nativeDescriptorHexCandidate='01041802FF00')
            client=Client(True);client.replies['1802FF00']='58FFC44728010209'
            with patch.object(dtc,'_source_profile',return_value=s):
                result=dtc.read_source_dtcs(client,p,c,q,time.monotonic()+5,lambda:None)
                self.assertEqual(client.sent,['1802FF00']);self.assertEqual(len(result['decoded']['records']),2)
                for other in [{**c,'ecuId':5},{**c,'transportAddressing':{'kind':'iso-tp'}}]:
                    with self.assertRaises(ValueError):dtc.source_dtc_plan(p,other)
            self.assertFalse(any(h.startswith(('14','2E','31')) for h in client.sent))

    def test_three_original_layouts_read_one_source_sequence_and_decode_exact_records(self):
        for kwp,reader in [(False,9),(True,9),(True,1)]:
            profile,contract,qualified=context(kwp);client=Client(kwp)
            with patch.object(dtc,'_source_profile',return_value=source(kwp=kwp,reader=reader)):
                out=dtc.read_source_dtcs(client,profile,contract,qualified,time.monotonic()+5,lambda:None)
            self.assertTrue(out['ok']);self.assertEqual(len(out['decoded']['records']),1)
            self.assertEqual(client.sent,(['1089' if kwp else '1003'] if reader==9 else [])+['1800FF00' if kwp else '190208'])
            self.assertEqual(out['decoded']['records'][0]['originalEnglish'],'Original factory English')

    def test_zero_faults_is_distinct_from_truncated_wrong_service_and_pending(self):
        for kwp,valid,bad in [(True,'5800',['58','5802C44728','5900','7F1878']),
                              (False,'5902FF',['5902','5902FFC13002','5901FF','7F1978'])]:
            p,c,q=context(kwp)
            with patch.object(dtc,'_source_profile',return_value=source(kwp=kwp)):
                out=dtc.read_source_dtcs(Client(kwp,valid),p,c,q,time.monotonic()+5,lambda:None)
                self.assertEqual(out['decoded']['records'],[])
                for payload in bad:
                    with self.assertRaises(ElmError):dtc.read_source_dtcs(Client(kwp,payload),p,c,q,time.monotonic()+5,lambda:None)

    def test_unqualified_identity_never_loads_source_or_dispatches(self):
        p,c,q=context();client=Client()
        for extra in [dict(freshRead=False),dict(ecuId=9),dict(generation='982'),dict(variantLink={'profile_id':'other'})]:
            with patch.object(dtc,'_source_profile',side_effect=AssertionError('source loaded')):
                with self.assertRaisesRegex(ElmError,'identity-unqualified'):
                    dtc.read_source_dtcs(client,p,c,{**q,**extra},time.monotonic()+5,lambda:None)
        self.assertEqual(client.sent,[])

    def test_source_write_unknown_descriptor_sequence_or_dictionary_changes_never_dispatch(self):
        mutations=[lambda s:s['diagnosticSources'][0]['links'][0]['dtc'].update(metadataHex='FF0203FF000C'),
            lambda s:s['diagnosticSources'][0]['links'][0]['dtc']['readCommandSlots'][-1].update(wireCandidateHex='14FFFFFF'),
            lambda s:s['diagnosticSources'][0]['links'][0]['dtc']['readCommandSlots'].reverse(),
            lambda s:s['dtcDefinition']['records'][0].update(text='changed')]
        p,c,q=context()
        for mutate in mutations:
            s=source();mutate(s);client=Client()
            with patch.object(dtc,'_source_profile',return_value=s),self.assertRaises(ValueError):
                dtc.read_source_dtcs(client,p,c,q,time.monotonic()+5,lambda:None)
            self.assertEqual(client.sent,[]);self.assertEqual(client.allowed,set())

    def test_source_links_must_agree_unless_the_live_route_is_explicitly_bound(self):
        s=source();links=s['diagnosticSources'][0]['links'];links.append(deepcopy(links[0]));links[1]['linkIndex']=1
        links[1]['dtc']['readCommandSlots'][-1].update(wireCandidateHex='19020C',nativeDescriptorHexCandidate='010319020C')
        p,c,_=context()
        with patch.object(dtc,'_source_profile',return_value=s):
            with self.assertRaisesRegex(ValueError,'source-route-ambiguous'):dtc.source_dtc_plan(p,c)
            self.assertEqual(dtc.source_dtc_plan({**p,'selectedSourceLink':1},c)['commands'][-1]['requestHex'],'19020C')

    def test_launch_chinese_or_ambiguous_dictionary_is_not_a_manual_or_english_title(self):
        p,c,q=context()
        for text,duplicate in [('中文译文',False),('Original English',True)]:
            s=source(text=text)
            if duplicate:
                d=s['dtcDefinition'];d['records'].append(deepcopy(d['records'][0]));d.pop('definitionSha256')
                d['definitionSha256']=hashlib.sha256(json.dumps(d,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
            with patch.object(dtc,'_source_profile',return_value=s):out=dtc.read_source_dtcs(Client(),p,c,q,time.monotonic()+5,lambda:None)
            self.assertIsNone(out['decoded']['records'][0]['originalEnglish'])


class ManufacturerDtcIntegrationTests(unittest.TestCase):
    def run_case(self,device='elm',raw=False,bad_identity=False,bad_dtc=False,vin=None,expected_hash=None):
        ecu=88 if raw else 5;pid='9x1:SCHEINWERFER:SCHEINWERFER_1' if raw else PROFILES[5]
        contract=raw_contract(pid) if raw else model_contract(5);contract['sourceCatalogId']=dtc.SOURCE_CATALOG
        for row in contract['identityRequests']:
            if row['requestHex'] in ('22F189','22F1A3'):
                row['record']['labels']=[dict(text='软件版本' if row['requestHex']=='22F189' else '硬件版本')]
        responses=model_replies(pid) if raw else model_responses(5)
        if vin is not None:responses['22F190']=bytes.fromhex('62F190')+vin.encode('ascii')
        responses['190208']=bytes.fromhex('5902FFC130' if bad_dtc else '5902FFC1300209')
        if bad_identity:responses['22F1A2']=bytes.fromhex('62F1A2')+b'999999'
        native=RawSourceNative(responses) if raw else FakeNative();ports=[]
        if not raw:native.make_events=lambda payload,cop:FakeNative.reply(responses[payload.hex().upper()],cop)
        def opened(*args,**kwargs):
            port=ModelPort(responses) if raw else SessionSimPort('porsche-981-2014-dme')
            if not raw:port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(contract['rxId'],pdu)) for hx,pdu in responses.items()}
            ports.append(port);return port,'synthetic'
        req={**request(),'action':'manufacturer-read-dtc',
            'ecuId':ecu,'profileId':pid,'parameterIds':[],'cycleCount':1,'deviceId':ID if device=='vnci' else 'bt:001122334455'}
        if expected_hash is not None:req['expectedVehicleIdentitySha256']=expected_hash
        p=dict(profileId=pid,generation=None,ecuIds=[ecu],status='definition-only',parameters=[field('a','22F102')],
            identity=[dict(requestHex=row['requestHex'],decoderReady=True) for row in contract['identityRequests']],checklist=[])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle(root,p)
            with patch.object(read,'READ_PROFILES',{pid:read.READ_PROFILES[pid]}),\
                    patch.object(read,'FAULT_READ_PROFILES',{pid:read.FAULT_READ_PROFILES[pid]}),\
                 patch('scripts.diagnostics.uds_source_read.source_contract',return_value=contract),\
                 patch.object(dtc,'_source_profile',return_value=source(pid,ecu)),\
                 patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened),\
                 patch('scripts.diagnostics.vnci.NativeDpu',return_value=native):out=read.read_manufacturer(req,root=root)
        sent=native.sent if device=='vnci' else ports[0].ecu_payloads
        return out,sent

    def test_fresh_identity_then_faults_on_ordinary_and_mode50_transports(self):
        for raw in (False,True):
            for device in ('elm','vnci'):
                out,sent=self.run_case(device,raw);self.assertTrue(out['ok'],out)
                self.assertTrue(out['dtcOnly']);self.assertFalse(out['identityOnly']);self.assertTrue(out['transport']['closed'])
                self.assertEqual(out['dtcRead']['decoded']['records'][0]['dtcHex'],'C13002')
                self.assertEqual(sent.count('190208'),1);self.assertNotIn('22F102',sent)
                self.assertLess(sent.index('22F190'),sent.index('190208'));self.assertEqual(out['samples'],[])

    def test_wrong_identity_or_bad_fault_pdu_never_publishes_zero_faults(self):
        for device in ('elm','vnci'):
            out,sent=self.run_case(device,bad_identity=True);self.assertFalse(out['ok']);self.assertNotIn('190208',sent)
            out,sent=self.run_case(device,bad_dtc=True);self.assertFalse(out['ok']);self.assertNotIn('dtcRead',out)
            self.assertTrue(out['transport']['closed']);self.assertEqual(sent.count('190208'),1)

    def test_fresh_vin_binds_generation_and_scan_vehicle_before_fault_request(self):
        vin='WP0ZZZ98ZES123456';digest=hashlib.sha256(vin.encode()).hexdigest()
        for raw in (False,True):
            for device in ('elm','vnci'):
                out,sent=self.run_case(device,raw,vin=vin,expected_hash=digest)
                self.assertTrue(out['ok'],out);self.assertEqual(out['vehicleIdentitySha256'],digest)
                self.assertEqual(out['vehicleGeneration'],'981')
                for other,expected in [('WP0ZZZ98ZHS223456',digest),(vin,'f'*64)]:
                    out,sent=self.run_case(device,raw,vin=other,expected_hash=expected)
                    self.assertFalse(out['ok']);self.assertEqual(out['error'],'vehicle-identity-mismatch')
                    self.assertNotIn('190208',sent);self.assertTrue(out['transport']['closed'])

    def test_vehicle_binding_is_typed_and_only_accepted_for_fault_reads(self):
        for action,value in [('manufacturer-identify','a'*64),('manufacturer-read-dtc','bad'),('manufacturer-read-dtc',7)]:
            req={**request(),'action':action,'parameterIds':[],'expectedVehicleIdentitySha256':value}
            with patch('scripts.diagnostics.connection.open_selected_port',side_effect=AssertionError('opened')):
                out=read.read_manufacturer(req)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],'manufacturer-vehicle-binding-invalid')

    def test_dtc_action_cannot_select_measurements_or_skip_source_bound_dme(self):
        req={**request(),'action':'manufacturer-read-dtc','parameterIds':[]}
        with patch('scripts.diagnostics.connection.open_selected_port',side_effect=AssertionError('opened')):
            self.assertFalse(read.read_manufacturer(req)['ok'])
            self.assertFalse(read.read_manufacturer({**req,'ecuId':5,'profileId':PROFILES[5],'parameterIds':['a'*64]})['ok'])

    def test_details_reuse_fresh_software_and_hardware_reads_without_extra_requests(self):
        for raw in (False,True):
            for device in ('elm','vnci'):
                out,sent=self.run_case(device,raw);self.assertTrue(out['ok'],out)
                self.assertEqual({row['label']:row['value'] for row in out['identityInfo']},{'软件版本':'1234','硬件版本':'123'})
                self.assertEqual(sent.count('22F189'),1);self.assertEqual(sent.count('22F1A3'),1)
                self.assertTrue(all(len(row['pduSha256'])==64 for row in out['identityInfo']))


class IdentityDetailTests(unittest.TestCase):
    def test_grouped_source_fields_keep_labels_order_and_values_and_skip_unqualified_fields(self):
        def record(label,offset):return dict(readSID=0x22,pid=0xF18B,requestHex='22F18B',read_request_candidate_hex='22F18B',
            byteOffset=offset,bitOffset=0,labels=[dict(text=label)] if label else [],enumText={},
            formula=dict(text='IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=8,Encoding=BCD-P,HighLow=1,Radix=10,DataType=A_UINT32,Precision=null;'))
        contract=dict(identityRequests=[dict(requestHex='22F18B',records=[record('制造年份',0),record('制造月份',1),record('',1)])])
        rows=identity_details(contract,{'22F18B':'62F18B2401'})
        self.assertEqual([(row['label'],row['value'],row['recordIndex']) for row in rows],[('制造年份','24',0),('制造月份','1',1)])
        self.assertEqual(len({row['pduSha256'] for row in rows}),1)
        self.assertEqual(identity_details(contract,{'22F18B':'7F2231'}),[])


class GatewayAnchoredDtcTests(unittest.TestCase):
    """Units without a source VIN are bound through a fresh, owned gateway read."""
    def case(self,device='elm',expected_hash=None,bad_gateway=False,close_failure=False,coding=False,anchors=None):
        gateway=read.GATEWAY_PROFILE;pid=PROFILES[5]
        unit=model_contract(5);unit['sourceCatalogId']=dtc.SOURCE_CATALOG
        unit['identityRequests']=[row for row in unit['identityRequests'] if row['requestHex']!='22F190']
        gate=model_contract(5);gate.update(ecuId=9,txId=0x710,rxId=0x77A,sourceCatalogId=dtc.SOURCE_CATALOG)
        replies_unit=model_responses(5);replies_unit.pop('22F190');replies_unit['190208']=bytes.fromhex('5902FFC1300209')
        replies_gate=model_responses(5)
        if bad_gateway:replies_gate['22F190']=bytes.fromhex('7F2231')
        profiles={key:dict(profileId=key,generation=None,ecuIds=[ecu],status='definition-only',parameters=[],checklist=[],
            identity=[dict(requestHex=row['requestHex'],decoderReady=True) for row in contract['identityRequests']])
            for key,ecu,contract in [(pid,5,unit),(gateway,9,gate)]}
        ports=[];natives=[]
        def opened(*args,**kwargs):
            first=not ports;replies=replies_gate if first else replies_unit;rx=0x77A if first else unit['rxId']
            port=SessionSimPort('porsche-981-2014-gateway' if first else 'porsche-981-2014-dme')
            port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(rx,pdu)) for hx,pdu in replies.items()}
            if first and close_failure:port.close=lambda:(_ for _ in ()).throw(OSError('modeled close failure'))
            ports.append(port);return port,'synthetic'
        def native(*args,**kwargs):
            replies=replies_gate if not natives else replies_unit;instance=FakeNative()
            instance.make_events=lambda payload,cop:FakeNative.reply(replies[payload.hex().upper()],cop)
            natives.append(instance);return instance
        def verified(req,root=None):return {**profiles[req['profileId']],'selectedEcuId':req['ecuId'],
            'requestedGeneration':req['generation'],**({'sourceBound':True} if req.get('sourceBound') else {})}
        req={
            **request(),'action':'manufacturer-read-coding' if coding else 'manufacturer-read-dtc','profileId':pid,'ecuId':5,'parameterIds':[],'cycleCount':1,
            'deviceId':ID if device=='vnci' else 'bt:001122334455'}
        if expected_hash is not None:req['expectedVehicleIdentitySha256']=expected_hash
        with patch.object(read,'READ_PROFILES',{key:read.READ_PROFILES[key] for key in profiles}),\
                patch.object(read,'FAULT_READ_PROFILES',{key:read.FAULT_READ_PROFILES[key] for key in profiles}),\
                patch.object(read,'verified_profile',side_effect=verified),\
                patch('scripts.diagnostics.uds_source_read.source_contract',side_effect=lambda p,selected,**kwargs:gate if p['profileId']==gateway else unit),\
                patch.object(dtc,'_source_profile',return_value=source(pid,5)),\
                patch('scripts.diagnostics.manufacturer_coding.read_source_coding',return_value=dict(
                    ok=True,error=None,blocks=[dict(requestHex='220600',pduHex='620600A5')],completeOriginalRead=False)),\
                patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened),\
                patch('scripts.diagnostics.vnci.NativeDpu',side_effect=native):out=read.read_manufacturer(req,
                    on_coding_anchor=(lambda value:anchors.append(dict(result=deepcopy(value),
                        openedClients=len(natives) if device=='vnci' else len(ports)))) if anchors is not None else None)
        sent=[value.sent for value in natives] if device=='vnci' else [port.ecu_payloads for port in ports]
        return out,sent

    def test_source_without_vin_reads_gateway_first_and_never_sends_unit_vin_request(self):
        expected=hashlib.sha256(b'WP0ZZZ98ZES123456').hexdigest()
        for device in ('elm','vnci'):
            out,sent=self.case(device,expected);self.assertTrue(out['ok'],out)
            self.assertEqual(len(sent),2);self.assertIn('22F190',sent[0]);self.assertNotIn('190208',sent[0])
            self.assertNotIn('22F190',sent[1]);self.assertEqual(sent[1].count('190208'),1)
            self.assertEqual(out['vehicleIdentitySource'],'gateway-vin');self.assertEqual(out['vehicleIdentitySha256'],expected)
            self.assertTrue(out['vehicleAnchor']['closed']);self.assertTrue(out['transport']['closed'])

    def test_wrong_or_unreadable_gateway_identity_stops_before_opening_unit(self):
        for device in ('elm','vnci'):
            for extra in [dict(expected_hash='f'*64),dict(bad_gateway=True)]:
                out,sent=self.case(device,**extra);self.assertFalse(out['ok']);self.assertEqual(len(sent),1)
                self.assertNotIn('190208',sent[0]);self.assertTrue(out['transport']['openAttempted'])
                self.assertTrue(out['transport']['closed']);self.assertNotIn('dtcRead',out)

    def test_gateway_close_failure_never_opens_unit_or_certifies_release(self):
        out,sent=self.case(close_failure=True);self.assertFalse(out['ok']);self.assertEqual(len(sent),1)
        self.assertFalse(out['transport']['closed']);self.assertTrue(out['transport']['openAttempted'])
        self.assertNotIn('dtcRead',out)

    def test_coding_anchor_boundary_is_after_gateway_release_and_before_target_open(self):
        for device in ('elm','vnci'):
            anchors=[];out,sent=self.case(device,coding=True,anchors=anchors)
            self.assertTrue(out['ok'],out);self.assertEqual(len(sent),2);self.assertEqual(len(anchors),1)
            anchor=anchors[0]['result'];self.assertEqual(anchors[0]['openedClients'],1)
            self.assertTrue(anchor['identityOnly']);self.assertTrue(anchor['transport']['closed'])
            self.assertEqual(anchor['transport']['closeErrors'],[])
            self.assertTrue(anchor['transport']['qualification']['freshRead'])
            self.assertIsInstance(anchor['identityInfo'],list)
            self.assertEqual(out['vehicleIdentitySource'],'gateway-vin')
            self.assertNotIn('22F190',sent[1])

    def test_failed_coding_gateway_never_emits_a_boundary_or_opens_the_target(self):
        for device in ('elm','vnci'):
            for extra in [dict(expected_hash='f'*64),dict(bad_gateway=True),dict(close_failure=True)]:
                if device=='vnci' and extra.get('close_failure'):continue  # ELM-only injected close failure.
                anchors=[];out,sent=self.case(device,coding=True,anchors=anchors,**extra)
                self.assertFalse(out['ok']);self.assertEqual(len(sent),1);self.assertEqual(anchors,[])

    def test_fault_read_keeps_its_existing_flow_without_a_coding_boundary(self):
        for device in ('elm','vnci'):
            anchors=[];out,sent=self.case(device,anchors=anchors)
            self.assertTrue(out['ok']);self.assertEqual(anchors,[]);self.assertEqual(len(sent),2)
