"""Complete coding read boundaries with explicit modeled source and client data."""
from copy import deepcopy
import hashlib,json,time,unittest
from unittest.mock import patch
from scripts.diagnostics import manufacturer_coding as coding
from scripts.diagnostics import swsg_source as swsg
from scripts.diagnostics.coding_read_plan import coding_read_plan
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.tests.test_coding_read_origin import record,variant


def context(pid=next(iter(swsg.VERSIONS)),ecu=47,generation='981'):
    name=pid.rsplit(':',1)[1];sizes={h:n for h,n in coding.SWSG_CODING_LENGTHS.items()
        if not (name in coding._SWSG_VR06 and h=='2204AB' or name in coding._SWSG_SLAVES and h=='2204B0')}
    records=[]
    for index,(hx,size) in enumerate(sizes.items()):
        r=record(0x22,int(hx[2:],16),100+40*index);r['byteOffset']=size-1
        r['source'].update(module='SWSG',variant=name);records.append(r)
    v=variant(records);v.update(profile_id=pid,module='SWSG',name=name)
    source=dict(profileId=pid,module='SWSG',variant=name,ecuIds=[47,48],generations=['981','982'],coding=coding_read_plan(v))
    p=dict(profileId=pid,selectedEcuId=ecu,requestedGeneration=generation)
    c=dict(profileId=pid,ecuId=ecu,generation=generation,sourceCatalogId=coding.SOURCE_CATALOG,
        sourceQualification='uds-source-version-and-identity',applicationProtocol='uds',
        session=dict(requestHex='1003',positivePrefixHex='5003'),
        sourceBridge=dict(project='9x1',version='2830.1.0',sources=deepcopy(swsg.PIWIS_SOURCES)),
        sourceCommands=['1003','22F1A2'],identityConstraints={'dsnText':swsg.VERSIONS[pid]})
    c['txId'],c['rxId']=swsg.PAIRS[ecu]
    q=dict(freshRead=True,observedProfileMatch=True,sourceCatalogId=coding.SOURCE_CATALOG,generation=generation,
        ecuId=ecu,variantLink={'profile_id':pid})
    return p,c,q,source,sizes


class Client:
    def __init__(self,sizes):
        self.allowed={'1003'};self.sent=[]
        self.replies={h:dict(ok=True,payload_hex='62'+h[2:]+'A5'*n) for h,n in sizes.items()}
    def request(self,hx,deadline):
        assert hx in self.allowed;self.sent.append(hx);return self.replies[hx]


def digest(source):
    plan=source['coding'];plan.pop('definitionSha256');plan['definitionSha256']=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class CompleteCodingScopeTests(unittest.TestCase):
    def test_all_reviewed_variants_read_complete_blocks_but_never_create_a_backup_or_grant_writes(self):
        for pid in swsg.VERSIONS:
            for ecu in (47,48):
                for gen in ('981','982'):
                    p,c,q,s,sizes=context(pid,ecu,gen);client=Client(sizes)
                    with patch.object(coding,'_source_profile',return_value=s):out=coding.read_source_coding(client,p,c,q,time.monotonic()+5,lambda:None)
                    self.assertTrue(out['ok']);self.assertTrue(out['completeOriginalRead']);self.assertTrue(out['completeVehicleCodingScopeQualified'])
                    self.assertTrue(all(b['completeLengthQualified'] for b in out['blocks']))
                    self.assertEqual(client.sent,sorted(sizes));self.assertEqual(client.allowed,{'1003'})
                    self.assertTrue(all(b['dataHex']=='A5'*sizes[b['requestHex']] for b in out['blocks']))
                    for key in ['completeOriginalBackup','baselineCreated','codingWriteQualified','codingRestoreQualified','liveVerified']:self.assertIs(out[key],False)
                    self.assertIsNone(out['writePayload'])

    def test_missing_extra_or_changed_group_extent_cannot_produce_a_complete_plan(self):
        p,c,q,s,_=context()
        for mutate in [lambda plan:plan['requestGroups'].pop(),
                       lambda plan:plan['requestGroups'][0].update(expectedTotalBytes=1),
                       lambda plan:plan['requestGroups'][0].update(minimumKnownBytes=1),
                       lambda plan:plan['requestGroups'][0].update(requestHex='22FFFF',identifierHex='FFFF')]:
            invalid=deepcopy(s);mutate(invalid['coding']);digest(invalid)
            with patch.object(coding,'_source_profile',return_value=invalid),self.assertRaises(ValueError):coding.source_coding_plan(p,c)

    def test_wrong_session_origin_pair_or_version_rule_never_dispatches(self):
        p,c,q,s,sizes=context()
        for changes in [dict(sourceBridge={}),dict(txId=0x700),dict(identityConstraints={'dsnText':'999999'}),
                        dict(applicationProtocol='kwp'),dict(sourceCommands=['22F1A2']),dict(session={}),dict(sourceQualification='kwp-source-version-and-identity')]:
            client=Client(sizes)
            with patch.object(coding,'_source_profile',return_value=s),self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                coding.read_source_coding(client,p,{**c,**changes},q,time.monotonic()+5,lambda:None)
            self.assertEqual(client.sent,[])

    def test_short_and_surplus_replies_do_not_become_complete_originals(self):
        p,c,q,s,sizes=context();last=sorted(sizes)[-1]
        for n in (sizes[last]-1,sizes[last]+1):
            client=Client(sizes);client.replies[last]=dict(ok=True,payload_hex='62'+last[2:]+'A5'*n)
            with patch.object(coding,'_source_profile',return_value=s):out=coding.read_source_coding(client,p,c,q,time.monotonic()+5,lambda:None)
            self.assertFalse(out['ok']);self.assertFalse(out['completeOriginalRead']);self.assertFalse(out['completeVehicleCodingScopeQualified'])
            self.assertEqual(out['missing'],[last]);self.assertEqual(client.sent.count(last),1);self.assertEqual(client.allowed,{'1003'})

    def test_source_authority_binds_its_own_dataset_and_request_lengths(self):
        p,c,_,s,sizes=context()
        with patch.object(coding,'_source_profile',return_value=s):plan=coding.source_coding_plan(p,c)
        scope=plan['completeScopeSource'];unsigned=dict(scope);h=unsigned.pop('sha256')
        self.assertEqual(hashlib.sha256(json.dumps(unsigned,sort_keys=True,ensure_ascii=True).encode()).hexdigest(),h)
        self.assertEqual(scope['datasetSha256'],coding._SWSG_DATASET_SHA256[p['profileId'].rsplit(':',1)[1]])
        self.assertEqual({g['requestHex']:g['expectedTotalBytes'] for g in plan['requestGroups']},sizes)

    def test_cancel_preserves_partial_blocks_without_a_complete_claim_or_retry(self):
        p,c,q,s,sizes=context();client=Client(sizes);calls=0
        def guard():
            nonlocal calls
            calls+=1
            if calls==4:raise ElmError('cancelled')
        with patch.object(coding,'_source_profile',return_value=s):out=coding.read_source_coding(client,p,c,q,time.monotonic()+5,guard)
        self.assertEqual(out['error'],'cancelled');self.assertEqual(len(client.sent),1)
        self.assertFalse(out['completeOriginalRead']);self.assertFalse(out['completeOriginalBackup'])
