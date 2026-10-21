"""PIWIS fault-plan boundaries; source/transport objects below are explicit models."""
from copy import deepcopy
import hashlib,json,time,unittest
from unittest.mock import patch
from scripts.diagnostics import manufacturer_dtc as dtc
from scripts.diagnostics import swsg_source as swsg,bose_source as bose
from scripts.diagnostics.tests.test_manufacturer_dtc import Client
from scripts.diagnostics.elm import ElmError


def fixture(pid,ecu,generation='981'):
    head=pid in swsg.VERSIONS;sources=swsg.PIWIS_SOURCES if head else bose.PIWIS_SOURCES
    p=dict(profileId=pid,selectedEcuId=ecu,requestedGeneration=generation)
    c=dict(profileId=pid,ecuId=ecu,generation=generation,sourceCatalogId=dtc.SOURCE_CATALOG,
        sourceQualification='uds-source-version-and-identity',applicationProtocol='uds',
        sourceBridge=dict(project='9x1',version='2830.1.0',sources=dict(sources)),
        session=dict(requestHex='1003',positivePrefixHex='5003'),
        identityConstraints={'dsnText':swsg.VERSIONS[pid]} if head else {'versionRules':[bose.RULE]},
        sourceCommands=['1003','22F1A2'] if head else ['1003','22F19E','22F1A2'])
    c['txId'],c['rxId']=swsg.PAIRS[ecu] if head else (0x76F,0x7D9)
    q=dict(freshRead=True,observedProfileMatch=True,sourceCatalogId=dtc.SOURCE_CATALOG,generation=generation,
        ecuId=ecu,variantLink={'profile_id':pid})
    dictionary=dict(profileId=pid,records=[dict(numericKey=0xC13002,text='Original English',definitionIssue=None)])
    dictionary['definitionSha256']=hashlib.sha256(json.dumps(dictionary,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    s=dict(profileId=pid,variant=pid.rsplit(':',1)[1],module='SWSG' if head else 'BV_SoundSysteUDS',
        ecuIds=[47,48] if head else [65],generations=['981','982'],dtcDefinition=dictionary,
        diagnosticSources=[dict(requestedEcuId=ecu,variantName=pid.rsplit(':',1)[1],bindingStatus='source-name-missing',
            links=[],sourceSha256='18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522')])
    return p,c,q,s


class PiwisDtcTests(unittest.TestCase):
    def test_all_normal_variants_keep_their_own_route_source_and_complete_records(self):
        for pid in [*swsg.VERSIONS,bose.PROFILE]:
            for ecu in ((47,48) if pid in swsg.VERSIONS else (65,)):
                for gen in ('981','982'):
                    p,c,q,s=fixture(pid,ecu,gen);client=Client()
                    with patch.object(dtc,'_source_profile',return_value=s):out=dtc.read_source_dtcs(client,p,c,q,time.monotonic()+5,lambda:None)
                    self.assertEqual(client.sent,['1003','190208']);self.assertIsNone(out['descriptorHex'])
                    self.assertEqual(out['piwisReadSource'],dtc.PIWIS_DTC_SOURCES['SWSG' if pid in swsg.VERSIONS else 'BOSE002'])
                    self.assertEqual(out['decoded']['records'][0]['dtcHex'],'C13002')
                    self.assertEqual(out['decoded']['records'][0]['statusHex'],'09')

    def test_source_target_rule_session_and_bridge_changes_never_dispatch(self):
        for pid,ecu in [(next(iter(swsg.VERSIONS)),47),(bose.PROFILE,65)]:
            p,c,q,s=fixture(pid,ecu)
            for change in [dict(txId=0x700),dict(rxId=0x701),dict(applicationProtocol='kwp'),dict(sourceBridge={}),
                           dict(identityConstraints={}),dict(sourceCommands=['190208']),dict(session={'requestHex':'1001'})]:
                client=Client()
                with self.subTest(pid=pid,change=change),patch.object(dtc,'_source_profile',return_value=s),self.assertRaises(ValueError):
                    dtc.read_source_dtcs(client,p,{**c,**change},q,time.monotonic()+5,lambda:None)
                self.assertEqual(client.sent,[])

    def test_changed_dictionary_or_diagnostic_source_does_not_fall_back(self):
        p,c,q,s=fixture(bose.PROFILE,65)
        for mutate in [lambda s:s['dtcDefinition']['records'][0].update(numericKey=1),
            lambda s:s['diagnosticSources'][0].update(links=[{}]),
            lambda s:s['diagnosticSources'][0].update(sourceSha256='0'*64),
            lambda s:s.update(ecuIds=[66]),lambda s:s.update(module='Verstaerker')]:
            invalid=deepcopy(s);mutate(invalid);client=Client()
            with patch.object(dtc,'_source_profile',return_value=invalid),self.assertRaises(ValueError):
                dtc.read_source_dtcs(client,p,c,q,time.monotonic()+5,lambda:None)
            self.assertEqual(client.sent,[])

    def test_no_route_link_or_request_can_be_supplied_by_the_caller(self):
        p,c,q,s=fixture(bose.PROFILE,65)
        with patch.object(dtc,'_source_profile',return_value=s),self.assertRaises(ValueError):
            dtc.source_dtc_plan({**p,'selectedSourceLink':0},c)
        from scripts.diagnostics.manufacturer_read import _validate
        for key,value in [('sourceBridge',c['sourceBridge']),('piwisReadSource',dtc.PIWIS_DTC_SOURCES['BOSE002']),('requestHex','1902FF')]:
            with self.assertRaisesRegex(ValueError,'forbidden-field'):_validate(dict(action='manufacturer-read-dtc',profileId=bose.PROFILE,**{key:value}))

    def test_zero_faults_is_valid_and_partial_or_wrong_responses_fail(self):
        p,c,q,s=fixture(bose.PROFILE,65)
        with patch.object(dtc,'_source_profile',return_value=s):
            out=dtc.read_source_dtcs(Client(pdu='590209'),p,c,q,time.monotonic()+5,lambda:None)
            self.assertEqual(out['decoded']['records'],[])
            for payload in ('5902','590208C13002','5901FFC1300209','7F1978'):
                with self.subTest(payload=payload),self.assertRaises(ElmError):
                    dtc.read_source_dtcs(Client(pdu=payload),p,c,q,time.monotonic()+5,lambda:None)

    def test_unprogrammed_and_unknown_versions_remain_unavailable(self):
        from scripts.diagnostics.manufacturer_acquisition import FAULT_READ_PROFILES
        for pid in ('9x1:SWSG:unprogrammiertes_SG',bose.PROFILE+'-unknown'):
            self.assertNotIn(pid,FAULT_READ_PROFILES)
