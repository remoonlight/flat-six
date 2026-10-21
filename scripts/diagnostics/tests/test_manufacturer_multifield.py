"""Fresh multi-field version checks with explicit modeled PDUs; no hardware."""
import copy
import time
import unittest
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.uds_source_read import qualify_client, decode_version_values, version_matches
from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract, model_responses

DSN = 'IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=48,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'
NAME = 'IDENTICAL:MinMax,MinLength=5,BaseDataType=A_ASCIISTRING,MaxLength=26,BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'
FLAG = 'TEXTTABLE:DataType=A_UINT32,[0x00]->nein;[0x01]->ja;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;'


def contract(name='PASM'):
    c=copy.deepcopy(model_contract(8));c['ecuId']=19
    c['sourceCommands']=['3E00','22F19E','22F1A2','22F1DF','1003']
    c['identityConstraints']={'versionRules':['000003#'+name]}
    c['versionSelectors']=[dict(requestHex=hx,selector=dict(leftWordSigned=0x22,rightWordSigned=int(hx[2:],16)-65536,
        byteOffsetSigned=2,bitOffsetByte=bit,formulaId=fid),formula={'text':text})
        for hx,bit,fid,text in [('22F1A2',0,0x80000002,DSN),('22F19E',0,0x80000819,NAME),('22F1DF',2,0xA0000001,FLAG),('22F1DF',6,0xA0000001,FLAG)]]
    return c


class Client:
    def __init__(self,changes=None):
        self.rows=model_responses(8);self.rows.update({'22F1A2':bytes.fromhex('62F1A2')+b'000003',
            '22F19E':bytes.fromhex('62F19E')+b'PASM\0\0','22F1DF':bytes.fromhex('62F1DF00')});self.rows.update(changes or {});self.sent=[]
    def request(self,hx,deadline):
        self.sent.append(hx);return {'ok':True,'payload_hex':self.rows[hx].hex().upper()}


class MultiFieldTests(unittest.TestCase):
    def rdk_contract(self):
        c=contract()
        c['ecuId']=55
        c['sourceCommands']=['3E00','22F1DF','22F1A2','22F19E','1003']
        c['versionSelectors']=[c['versionSelectors'][0],c['versionSelectors'][2]]
        c['identityConstraints']={'versionRules':['200001'],'conflictingVersionRules':['#ja']}
        return c

    def test_known_unsupported_state_stops_direct_reads_before_session_and_identity(self):
        c=self.rdk_contract()
        for flag in range(256):
            client=Client({'22F1A2':bytes.fromhex('62F1A2')+b'200001',
                           '22F1DF':bytes([0x62,0xF1,0xDF,flag])})
            with self.subTest(flag=flag):
                if flag & 4:
                    with self.assertRaisesRegex(ElmError,'identity-mismatch'):
                        qualify_client(client,c,'rdk',time.monotonic()+2,lambda:None)
                    self.assertEqual(client.sent,['3E00','22F1DF','22F1A2'])
                else:
                    _,qualified=qualify_client(client,c,'rdk',time.monotonic()+2,lambda:None)
                    self.assertEqual(qualified['ecuId'],55)
                    self.assertIn('1003',client.sent)

    def test_discovery_cannot_hide_conflict_by_considering_only_enabled_versions(self):
        from scripts.diagnostics.uds_source_read import resolve_version_responses
        c=self.rdk_contract();candidates=[({'profileId':'normal-rdk'},c)]
        replies={'22F1A2':'62F1A2'+b'200001'.hex()}
        self.assertIsNone(resolve_version_responses(candidates,replies))
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):
            resolve_version_responses(candidates,{**replies,'22F1DF':'62F1DF04'})
        self.assertEqual(resolve_version_responses(candidates,{**replies,'22F1DF':'62F1DF00'})[0]['profileId'],'normal-rdk')

    def kwp_ascii_case(self, changes=None):
        records=[]
        for hx,size in [('1A9F',4),('1A90',17)]:
            record={'readSID':0x1A,'pid':int(hx[2:],16),'read_request_candidate_hex':hx,
                'byteOffset':0,'bitOffset':0,'formula':{'text':DSN.replace('BitLength=48',f'BitLength={size*8}')}}
            records.append({'field':hx,'requestHex':hx,'record':record})
        c={'ecuId':1,'sourceCommands':['1089','1A9F','1089'],'identityRequests':records,
            'identityConstraints':{'versionRules':['P200']},'versionIdentityRequests':[],
            'versionSelectors':[{'requestHex':'1A9F','selector':{'leftWordSigned':0x1A,
                'rightWordSigned':0x9F,'byteOffsetSigned':1,'bitOffsetByte':0,'formulaId':0x80000007},
                'formula':records[0]['record']['formula']}]}
        client=Client({'1089':b'\x50\x89','1A9F':b'\x5A\x9FP200',
            '1A90':b'\x5A\x90WP0ZZZ98ZES123456',**(changes or {})})
        return client,c

    def test_kwp_ascii_version_uses_one_byte_identifier_and_source_session_order(self):
        client,c=self.kwp_ascii_case()
        identity,qualified=qualify_client(client,c,'dme',time.monotonic()+2,lambda:None)
        self.assertEqual(client.sent,['1089','1A9F','1089','1A90'])
        self.assertEqual(identity['1A9F'],'5A9F50323030');self.assertEqual(qualified['ecuId'],1)

    def test_kwp_ascii_unknown_version_stops_before_remaining_identity(self):
        for pdu in (b'\x5A\x9FX200',b'\x5A\x9FP20',b'\x5A\x9EP200'):
            client,c=self.kwp_ascii_case({'1A9F':pdu})
            with self.subTest(pdu=pdu),self.assertRaises(ElmError):
                qualify_client(client,c,'dme',time.monotonic()+2,lambda:None)
            self.assertNotIn('1A90',client.sent)

    def test_kwp_source_identity_requires_a_valid_vin(self):
        for data in (b'WP0ZZZ98ZES12345',b'WP0ZZZ98ZES12345I',b'\0'*17):
            client,c=self.kwp_ascii_case({'1A90':b'\x5A\x90'+data})
            with self.subTest(data=data),self.assertRaisesRegex(ElmError,'identity-decode-failed'):
                qualify_client(client,c,'dme',time.monotonic()+2,lambda:None)

    def test_each_version_uses_its_own_length_after_all_selector_replies_arrive(self):
        from scripts.diagnostics.uds_source_read import resolve_version_responses
        long=contract();short=copy.deepcopy(long)
        short['versionSelectors'][0]['formula']['text']=DSN.replace('BitLength=48','BitLength=24')
        short['identityConstraints']['versionRules']=['005#PASM']
        candidates=[({'profileId':'long'},long),({'profileId':'short'},short)]
        responses={'22F1A2':'62F1A2'+b'005'.hex(),'22F1DF':'62F1DF00'}
        before=copy.deepcopy(candidates)
        self.assertIsNone(resolve_version_responses(candidates,responses))
        responses['22F19E']='62F19E'+b'PASM\0'.hex()
        self.assertEqual(resolve_version_responses(candidates,responses)[0]['profileId'],'short')
        self.assertEqual(candidates,before)

    def test_complete_version_reply_set_requires_one_matching_definition(self):
        from scripts.diagnostics.uds_source_read import resolve_version_responses
        c=contract();responses={'22F1A2':'62F1A2'+b'000003'.hex(),'22F19E':'62F19E'+b'PASM\0'.hex(),'22F1DF':'62F1DF00'}
        for candidates in ([({'profileId':'a'},c),({'profileId':'b'},copy.deepcopy(c))],
                           [({'profileId':'a'},contract('PADM'))]):
            with self.assertRaisesRegex(ElmError,'identity-mismatch'):
                resolve_version_responses(candidates,responses)

    def test_source_sized_serial_identity_is_retained_within_isotp_limit(self):
        c=contract();record=copy.deepcopy(c['identityRequests'][1]['record'])
        record.update(pid=0xF18C,read_request_candidate_hex='22F18C',byteOffset=0,
            formula={'text':'IDENTICAL:MinMax,MinLength=1,BaseDataType=A_ASCIISTRING,MaxLength=4062,BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'})
        c['identityRequests'].append({'field':'serial','requestHex':'22F18C','record':record})
        pdu=b'\x62\xF1\x8C'+b'S'*4061+b'\0'
        _,(identity,_)=self.qualify(c,{'22F18C':pdu})
        self.assertEqual(identity['22F18C'],pdu.hex().upper())
        with self.assertRaisesRegex(ElmError,'identity-request-failed'):
            self.qualify(c,{'22F18C':b'\x62\xF1\x8C'+b'S'*4093})

    def test_all_fields_in_a_shared_identity_reply_are_required_and_retained(self):
        c=contract();base=copy.deepcopy(c['identityRequests'][1]['record'])
        base.update(pid=0xF15B,read_request_candidate_hex='22F15B',byteOffset=0)
        second={**base,'byteOffset':4}
        c['identityRequests'].append({'field':'software','requestHex':'22F15B','records':[base,second]})
        for raw in (b'SW01SW02',b'SW01SW03'):
            client,(identity,_)=self.qualify(c,{'22F15B':b'\x62\xF1\x5B'+raw})
            self.assertEqual(identity['22F15B'],(b'\x62\xF1\x5B'+raw).hex().upper())
            self.assertEqual(client.sent.count('22F15B'),1)
        with self.assertRaisesRegex(ElmError,'identity-decode-failed'):
            self.qualify(c,{'22F15B':b'\x62\xF1\x5BSW01'})

    def test_end_of_pdu_identity_retains_the_complete_bounded_serial_reply(self):
        c=contract();record=copy.deepcopy(c['identityRequests'][1]['record'])
        record.update(pid=0xF18C,read_request_candidate_hex='22F18C',byteOffset=0,
            formula={'text':'IDENTICAL:MinMax,MinLength=1,BaseDataType=A_ASCIISTRING,MaxLength=4093,BitMask=0,Encoding=ISO-8859-2,Termination=END-OF-PDU,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'})
        c['identityRequests'].append({'field':'serial','requestHex':'22F18C','record':record})
        for data in (b'S',b'S'*4092,b'SERIAL\0RETAINED'):
            pdu=b'\x62\xF1\x8C'+data
            _,(identity,_)=self.qualify(c,{'22F18C':pdu})
            self.assertEqual(identity['22F18C'],pdu.hex().upper())

    def test_end_of_pdu_identity_rejects_empty_or_oversized_wire_replies(self):
        c=contract();record=copy.deepcopy(c['identityRequests'][1]['record'])
        record.update(pid=0xF18C,read_request_candidate_hex='22F18C',byteOffset=0,
            formula={'text':'IDENTICAL:MinMax,MinLength=1,BaseDataType=A_ASCIISTRING,MaxLength=4093,BitMask=0,Encoding=ISO-8859-2,Termination=END-OF-PDU,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'})
        c['identityRequests'].append({'field':'serial','requestHex':'22F18C','record':record})
        for data,reason in [(b'','identity-decode-failed'),(b'S'*4093,'identity-request-failed')]:
            with self.subTest(length=len(data)),self.assertRaisesRegex(ElmError,reason):
                self.qualify(c,{'22F18C':b'\x62\xF1\x8C'+data})

    def test_multifield_identity_source_binding_rejects_missing_or_changed_fields(self):
        from scripts.diagnostics.pdk_read import source_records
        first={'requestHex':'22F15B','decoderReady':True,'byteOffset':0,'bitOffset':0,'formula':{'text':DSN}}
        second={**first,'byteOffset':6}
        source={'measurementGroups':[],'identityGroups':[{'requestHex':'22F15B',
            'fields':[{'decodingDefinition':first},{'decodingDefinition':second}]}]}
        p={'parameters':[],'identity':[first,second]}
        self.assertEqual(source_records(p,source,[],['22F15B'])[0]['records'],[first,second])
        for fields in ([first],[first,{**second,'byteOffset':5}],[first,first]):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                source_records({**p,'identity':fields},source,[],['22F15B'])

    def qualify(self,c=None,changes=None,resolver=None):
        client=Client(changes)
        result=qualify_client(client,c or contract(),'nominal',time.monotonic()+60,lambda:None,resolve_version=resolver)
        return client,result

    def test_same_dsn_distinct_features_choose_only_the_observed_version(self):
        seen=[]
        def resolve(values):
            seen.append(values)
            candidates=[(contract(name),name) for name in ('PADM','PASM','PASM+PADM')]
            matches=[c for c in candidates if version_matches(c[0],values)]
            self.assertEqual(len(matches),1);return matches[0]
        client,(identity,qualified)=self.qualify(resolver=resolve)
        self.assertEqual(seen,[['000003','PASM','nein','nein']]);self.assertEqual(qualified['variantLink']['profile_id'],'PASM')
        self.assertTrue(qualified['freshRead']);self.assertIn('22F190',identity)
        self.assertLess(client.sent.index('22F1DF'),client.sent.index('1003'))

    def test_normal_read_does_not_borrow_the_other_same_dsn_profile(self):
        client=Client()
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):
            qualify_client(client,contract('PADM'),'PADM',time.monotonic()+60,lambda:None)
        self.assertNotIn('1003',client.sent)

    def test_incomplete_selector_set_never_calls_the_resolver(self):
        client=Client({'22F1DF':bytes.fromhex('62F1DF')});seen=[]
        with self.assertRaisesRegex(ElmError,'identity-decode-failed'):
            qualify_client(client,contract(),'nominal',time.monotonic()+60,lambda:None,resolve_version=lambda values:seen.append(values))
        self.assertEqual(seen,[]);self.assertNotIn('1003',client.sent)
        self.assertIsNone(decode_version_values(contract(),{'22F1A2':'62F1A2303030303033'}))

    def test_minimum_string_extent_cannot_be_supplied_by_initialized_padding(self):
        client=Client({'22F19E':bytes.fromhex('62F19E')+b'PASM'})
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):
            qualify_client(client,contract(),'nominal',time.monotonic()+60,lambda:None)
        self.assertNotIn('1003',client.sent)

    def test_declared_status_field_is_compared_when_the_rule_requires_it(self):
        c=contract();c['identityConstraints']['versionRules']=['000003#PASM#nein']
        client=Client({'22F1DF':bytes.fromhex('62F1DF04')})
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):
            qualify_client(client,c,'nominal',time.monotonic()+60,lambda:None)
        self.assertNotIn('1003',client.sent)
        _,(first,_)=self.qualify(changes={'22F1DF':bytes.fromhex('62F1DF00')})
        _,(second,_)=self.qualify(changes={'22F1DF':bytes.fromhex('62F1DF80')})
        self.assertEqual(first,second,'auxiliary bits outside version rules are not identity fingerprints')
        self.assertNotIn('22F1DF',first)

    def test_wrong_echo_and_invalid_vin_cannot_qualify(self):
        for changes in ({'22F19E':bytes.fromhex('62F190')+b'PASM\0\0'}, {'22F190':bytes.fromhex('62F190')+b'0000000000000000I'}):
            with self.assertRaises(ElmError):self.qualify(changes=changes)

    def test_verified_version_selector_retains_the_whole_actual_reply_without_display_padding(self):
        c=contract();c['versionSelectors'][0]['formula']['text']=DSN.replace('BitLength=48','BitLength=24')
        c['identityConstraints']['versionRules']=['001#PASM']
        c['identityRequests']=c['identityRequests'][1:];c['versionIdentityRequests']=['22F1A2']
        replies=[]
        for body in (b'001',b'001739',b'001740'):
            client,(identity,qualified)=self.qualify(c,{'22F1A2':bytes.fromhex('62F1A2')+body})
            self.assertTrue(qualified['freshRead']);self.assertEqual(identity['22F1A2'],(bytes.fromhex('62F1A2')+body).hex().upper())
            self.assertEqual(client.sent.count('22F1A2'),1);replies.append(identity)
        self.assertNotEqual(replies[1],replies[2],'a change beyond the compared prefix remains in the reconnect fingerprint')
        with self.assertRaisesRegex(ElmError,'identity-decode-failed'):
            self.qualify(c,{'22F1A2':bytes.fromhex('62F1A2')+b'00'})

    def test_multiple_patterns_and_prefix_rules_preserve_source_comparison(self):
        c=contract();c['identityConstraints']['versionRules']=['999999#PADM','000003#PASM']
        self.assertTrue(version_matches(c,['000003','PASM','nein','ja']))
        self.assertFalse(version_matches(c,['000003','PADM','nein','ja']))
        self.assertFalse(version_matches(c,'000003'))
        self.assertFalse(version_matches(c,['000003']))


if __name__=='__main__':unittest.main()
