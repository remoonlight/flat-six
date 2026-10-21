"""Source UDS client tests with modeled byte ports/native events, no hardware."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.session_simulator import SessionSimPort,ath1_prompt,isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_read import bundle,request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_vnci import FakeNative,ID
from scripts.diagnostics.uds_source_read import PAIRS,VERSIONS

PROFILES={8:'9x1:Kombiinstrument_MM:Kombiinstrument_A2_6',5:'9x1:PSM:PSM_a1',32:'9x1:BCM_vorne:BCM_vorne_9x1_Basis_GT4',35:'9x1:BCM_hinten:BCM_hinten_9x1_GT4',54:'9x1:Lenkstockschalter:Kombilenkstockmodul_A2_4',18:'9x1:PDC:PDC_4_Kanal_A2_4',50:'9x1:EPB:Parkbremse_a7',55:'9x1:RDK:Reifendruckkontrolle_A2_4'}


def model_contract(ecu):
    specs=[('22F1A2',6),('22F189',4),('22F1A3',3),('22F187',11),('22F190',17)]
    if ecu not in (35,54):specs.insert(3,('22F191',11))
    rows=[]
    for hx,size in specs:
        rows.append({'field':hx,'requestHex':hx,'record':{'readSID':0x22,'pid':int(hx[2:],16),
            'read_request_candidate_hex':hx,'byteOffset':0,'bitOffset':0,'formula':{'text':
            f'IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength={size*8},Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'}}})
    tx,rx=PAIRS[ecu]
    return {'sourceQualification':'uds-source-version-and-identity','catalogProfileId':'porsche-981-uds-source',
        'ecuId':ecu,'txId':tx,'rxId':rx,'applicationProtocol':'uds','identityRequests':rows,
        'identityConstraints':{'dsnText':VERSIONS[PROFILES[ecu]][1]},
        'sourceCommands':['3E00','22F1A2','22F19E','22F1DF','1003'] if ecu==8 else ['3E00','22F1DF','22F1A2','22F19E','1003'] if ecu==55 else ['3E00','22F1A2']+(['22F1DF'] if ecu!=35 else [])+['1003'],
        'session':{'requestHex':'1003','positivePrefixHex':'5003'}}


def model_responses(ecu):
    rows={'3E00':b'\x7e\x00','1003':b'\x50\x03','22F1DF':b'\x62\xf1\xdf\x01',
          '22F102':bytes.fromhex('62F1022A07'),'22F19E':bytes.fromhex('62F19E')+b'EV_RDKBERU' }
    for item in model_contract(ecu)['identityRequests']:
        hx=item['requestHex'];text={'22F1A2':VERSIONS[PROFILES[ecu]][1],'22F189':'1234','22F1A3':'123',
            '22F191':'12345678901','22F187':'12345678902','22F190':'WP0ZZZ98ZES123456'}[hx]
        rows[hx]=b'\x62'+bytes.fromhex(hx[2:])+text.encode()
    return rows


class UdsTests(unittest.TestCase):
    def test_vehicle_probe_can_identify_fault_only_gateway_states(self):
        seen=[]
        def profile(req,root):
            seen.append(req['profileId'])
            return dict(profileId=req['profileId'],ecuIds=[9],generation=None,parameters=[])
        req=dict(action='manufacturer-identify-vehicle',deviceId='bt:001122334455',
                 confirmedReadOnly=True,x431Inactive=True)
        with patch.object(read,'verified_profile',side_effect=profile),\
                patch.object(read,'prepared_read_contract',return_value={}):
            out=read.read_manufacturer(req,check_only=True)
        self.assertTrue(out['ok'],out)
        self.assertIn('9x1:CAN_CAN_Gateway:Werksdatensatz_Gateway',seen)
        self.assertIn('9x1:CAN_CAN_Gateway:unprogrammiertes_Gateway',seen)
        self.assertNotIn('9x1:Steering_Control_Unit:unprogrammierte_EPS',seen)

    def test_fault_only_states_cannot_create_parameter_contracts(self):
        from scripts.diagnostics.manufacturer_acquisition import FAULT_ONLY_PROFILES, read_profile_targets
        from scripts.diagnostics.manufacturer_transport import prepared_read_contract
        for pid,(ecu,_,generation) in FAULT_ONLY_PROFILES.items():
            p=dict(profileId=pid,ecuIds=list(read_profile_targets(pid)),selectedEcuId=ecu,
                generation=generation,status='definition-only',parameters=[])
            with self.assertRaisesRegex(ValueError,'manufacturer-parameters-unavailable'):
                prepared_read_contract(p,[])

    def test_zero_measurement_states_are_only_fault_read_candidates(self):
        from scripts.diagnostics.manufacturer_acquisition import READ_PROFILES,read_profile_targets
        nominal=next(pid for pid in READ_PROFILES if 64 in read_profile_targets(pid))
        for action in ('manufacturer-identify','manufacturer-read-dtc'):
            seen=[]
            def profile(req,root):
                seen.append(req['profileId'])
                return dict(profileId=req['profileId'],ecuIds=[64],generation=None,parameters=[])
            req={**request(), 'action':action,'generation':'981','ecuId':64,'profileId':nominal,'parameterIds':[]}
            with patch.object(read,'verified_profile',side_effect=profile),\
                    patch.object(read,'prepared_read_contract',return_value={}):
                out=read.read_manufacturer(req,check_only=True)
            self.assertTrue(out['ok'],out)
            self.assertEqual('9x1:ACC3:ACC3_unprogrammiert' in seen,action=='manufacturer-read-dtc')

    def test_rdk_unprogrammed_precedes_normal_dsn_match(self):
        from scripts.diagnostics.uds_source_read import version_matches
        selectors=[{},{}]
        normal={'versionSelectors':selectors,'identityConstraints':{
            'versionRules':['200001'],'conflictingVersionRules':['#ja']}}
        unprogrammed={'versionSelectors':selectors,'identityConstraints':{'versionRules':['#ja']}}
        for dsn in ('200001','000000','FFFFFF'):
            for state in ('ja','nein'):
                values=[dsn,state]
                self.assertEqual(version_matches(unprogrammed,values),state=='ja')
                self.assertEqual(version_matches(normal,values),dsn=='200001' and state!='ja')
        for invalid in ([],['200001'],['200001','ja','extra'],None):
            self.assertFalse(version_matches(normal,invalid))
            self.assertFalse(version_matches(unprogrammed,invalid))

    def run_case(self,ecu=5,device='elm',overrides=None,mutate=None,retry=None):
        p={'profileId':PROFILES[ecu],'generation':'9x1' if ecu in (32,35) else None,'ecuIds':[ecu],
           'status':'definition-only','parameters':[field('a','22F102'),field('b','22F102',1)],'identity':[],'checklist':[]}
        responses=model_responses(ecu);responses.update(overrides or {});ports=[];tx,rx=PAIRS[ecu]
        native=FakeNative();native.make_events=lambda payload,cop:FakeNative.reply(responses[payload.hex().upper()],cop)
        def opened(*args,**kwargs):
            port=SessionSimPort('porsche-981-2014-dme');port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(rx,pdu)) for hx,pdu in responses.items()}
            if mutate:mutate(port,len(ports),rx)
            ports.append(port);return port,'synthetic'
        req={**request(),'ecuId':ecu,'profileId':PROFILES[ecu],'cycleCount':2}
        if device=='vnci':req['deviceId']=ID
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle(root,p)
            with patch('scripts.diagnostics.uds_source_read.source_contract',return_value=model_contract(ecu)),\
                    patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened),\
                    patch('scripts.diagnostics.vnci.NativeDpu',return_value=native):
                out=read.read_manufacturer(req,root=root,wait_retry=retry)
        return out,native if device=='vnci' else ports

    def test_source_order_identity_and_dedup_on_eight_units_and_two_transports(self):
        for ecu in PROFILES:
            for device in ['elm','vnci']:
                out,clients=self.run_case(ecu,device);self.assertTrue(out['ok'],out)
                c=model_contract(ecu);expected=c['sourceCommands']+[x['requestHex'] for x in c['identityRequests'][1:]]+['22F102','22F102']
                sent=clients.sent if device=='vnci' else clients[0].ecu_payloads
                self.assertEqual(sent,expected);self.assertEqual([s['value'] for s in out['samples']],[42,7,42,7]);self.assertTrue(out['transport']['closed']);self.assertFalse(out['liveVerified'])
                if device=='vnci':self.assertEqual(clients.links,[('ISO_15765_3_on_ISO_15765_2',*PAIRS[ecu])])

    def test_wrong_version_stops_before_extended_session_and_parameters(self):
        for ecu in PROFILES:
            for device in ['elm','vnci']:
                out,clients=self.run_case(ecu,device,{'22F1A2':b'\x62\xf1\xa2BAD000'})
                self.assertEqual(out['error'],'identity-mismatch');self.assertEqual(out['samples'],[])
                sent=clients.sent if device=='vnci' else clients[0].ecu_payloads
                commands=model_contract(ecu)['sourceCommands'];self.assertEqual(sent,commands[:commands.index('22F1A2')+1]);self.assertTrue(out['transport']['closed'])

    def test_invalid_identity_and_wrong_service_echo_stop_before_sampling(self):
        for hx,pdu in [('22F189',b'\x62\xf1\x90ABCD'),('22F1A3',b'\x62\xf1\xa3X'),
                       ('22F189',b'\x62\xf1\x89\0\0\0\0'),('22F190',b'\x62\xf1\x90'+b'I'*17),
                       ('3E00',b'\x7e\x01'),('1003',b'\x50\x89')]:
            out,ports=self.run_case(overrides={hx:pdu});self.assertFalse(out['ok']);self.assertEqual(out['samples'],[])
            self.assertNotIn('22F102',ports[0].ecu_payloads);self.assertTrue(out['transport']['closed'])

    def test_reconnect_checks_fingerprint_but_auxiliary_status_is_not_identity(self):
        for changed_identity in [False,True]:
            def mutate(port,index,rx):
                if index:
                    port.ecu_map['22F1DF']=ath1_prompt(isotp_ath1_lines(rx,b'\x62\xf1\xdf\x02'))
                    if changed_identity:port.ecu_map['22F189']=ath1_prompt(isotp_ath1_lines(rx,b'\x62\xf1\x89DIFF'))
                else:
                    write=port.write;calls=[]
                    def interrupted(data):
                        if data.strip().replace(b' ',b'')==b'0322F102':
                            calls.append(True)
                            if len(calls)==2:raise OSError('disconnected')
                        return write(data)
                    port.write=interrupted
            decisions=[]
            def retry(*args):decisions.append(args);return True
            out,ports=self.run_case(mutate=mutate,retry=retry);self.assertEqual(len(ports),2);self.assertEqual(len(decisions),1);self.assertTrue(out['transport']['closed'])
            if changed_identity:
                self.assertEqual(out['error'],'identity-mismatch');self.assertEqual([s['value'] for s in out['samples']],[42,7]);self.assertNotIn('22F102',ports[1].ecu_payloads)
            else:self.assertTrue(out['ok'],out);self.assertEqual([s['value'] for s in out['samples']],[42,7,42,7])

    def test_additional_source_did_wrong_echo_stops_before_session_and_parameters(self):
        for ecu in (55,8):
            for device in ('elm','vnci'):
                out,clients=self.run_case(ecu,device,{'22F19E':bytes.fromhex('62F190')+b'WRONG_ECHO'})
                self.assertFalse(out['ok']);self.assertEqual(out['samples'],[]);self.assertTrue(out['transport']['closed'])
                sent=clients.sent if device=='vnci' else clients[0].ecu_payloads
                expected=model_contract(ecu)['sourceCommands']
                self.assertEqual(sent,expected[:expected.index('22F19E')+1])

    def test_unknown_base_unprogrammed_and_unsupported_generation_never_open_a_source(self):
        for change in [{'profileId':'9x1:PSM:PSM_BaseSys'},{'profileId':'9x1:BCM_hinten:BCM_hinten_unprogrammiertes_SG'},
                       {'generation':'983'},{'ecuId':True},{'ecuId':9}]:
            with patch.object(read,'verified_profile',side_effect=AssertionError('must not load')):
                out=read.read_manufacturer({**request(),'ecuId':5,'profileId':PROFILES[5],**change})
            self.assertEqual(out['error'],'manufacturer-profile-unqualified')


if __name__=='__main__':unittest.main()
