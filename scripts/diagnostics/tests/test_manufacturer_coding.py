"""Full returned coding groups via modeled clients; no device or baseline writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_coding as coding, manufacturer_read as read
from scripts.diagnostics.coding_read_plan import coding_read_plan
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.tests.test_coding_read_origin import record, variant
from scripts.diagnostics.tests.test_manufacturer_read import bundle, request
from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract, model_responses, PROFILES
from scripts.diagnostics.tests.test_manufacturer_mode50 import model_contract as raw_contract, model_replies, RawSourceNative, ModelPort
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_vnci import FakeNative, ID
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines


def source(pid=PROFILES[5]):
    v=variant([record(0x22,0xF100,100),record(0x2E,0xF101,140)])
    v['profile_id']=pid
    plan=coding_read_plan(v)
    return dict(profileId=pid,module=v['module'],variant=v['name'],generations=['981','982'],coding=plan)


def context():
    pid=PROFILES[5]
    return (dict(profileId=pid,selectedEcuId=5,requestedGeneration='981'),
        dict(profileId=pid,ecuId=5,generation='981',sourceCatalogId=coding.SOURCE_CATALOG,
             sourceQualification='uds-source-version-and-identity'),
        dict(freshRead=True,observedProfileMatch=True,sourceCatalogId=coding.SOURCE_CATALOG,
             generation='981',ecuId=5,variantLink=dict(profile_id=pid)))


class Client:
    def __init__(self):
        self.allowed={'1003'};self.sent=[]
        self.replies={'22F100':dict(ok=True,payload_hex='62F100010203FFFF'),
                      '22F101':dict(ok=True,payload_hex='62F1011122334455')}
    def request(self,hx,deadline):
        assert hx in self.allowed
        self.sent.append(hx)
        value=self.replies[hx]
        if isinstance(value,Exception):raise value
        return value


class SourceCodingTests(unittest.TestCase):
    def variable_source(self,pid=PROFILES[5]):
        s=source(pid);group=s['coding']['requestGroups'][1]
        group['minimumKnownBytes']=0
        group['fields'][0]['span']=dict(kind='lowerBound',dataMin=0,unknownRemaining=True)
        canonical=dict(s['coding']);canonical.pop('definitionSha256')
        s['coding']['definitionSha256']=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return s

    def run_read(self,client=None,guard=lambda:None):
        p,c,q=context();client=client or Client()
        with patch.object(coding,'_source_profile',return_value=source()):
            result=coding.read_source_coding(client,p,c,q,time.monotonic()+10,guard)
        return result,client

    def test_all_groups_preserve_unknown_trailing_bytes_never_create_complete_baseline(self):
        result,client=self.run_read()
        self.assertTrue(result['ok']);self.assertTrue(result['allDefinedGroupsReceived'])
        self.assertTrue(result['freshReadProven']);self.assertEqual(result['missing'],[])
        self.assertEqual(client.sent,['22F100','22F101']);self.assertEqual(client.allowed,{'1003'})
        self.assertEqual([b['dataHex'] for b in result['blocks']],['010203FFFF','1122334455'])
        for block in result['blocks']:
            self.assertEqual(block['sha256'],hashlib.sha256(bytes.fromhex(block['dataHex'])).hexdigest())
            self.assertFalse(block['completeLengthQualified'])
        for key in ['completeOriginalBackup','completeVehicleCodingScopeQualified','baselineCreated',
                    'codingWriteQualified','codingRestoreQualified','liveVerified']:
            self.assertIs(result[key],False)
        self.assertIsNone(result['writePayload'])

    def test_negative_truncated_wrong_echo_and_transport_failure_stop_without_replay(self):
        for value in [dict(ok=True,payload_hex=hx) for hx in ['7F2231','7F2278','62F10299','62F101','62F1010','']]+[
                dict(ok=False,error='NO DATA'),OSError('disconnected')]:
            client=Client();client.replies['22F101']=value
            out,_=self.run_read(client)
            self.assertFalse(out['ok']);self.assertEqual(len(out['blocks']),1)
            self.assertEqual(out['missing'],['22F101']);self.assertEqual(client.sent,['22F100','22F101'])
            self.assertEqual(client.allowed,{'1003'})

    def test_cancel_between_groups_keeps_only_completed_group_and_restores_allowlist(self):
        calls=0
        def guard():
            nonlocal calls
            calls+=1
            if calls==4:raise ElmError('cancelled')
        out,client=self.run_read(guard=guard)
        self.assertEqual(out['error'],'cancelled');self.assertEqual(client.sent,['22F100'])
        self.assertEqual(out['missing'],['22F101']);self.assertEqual(client.allowed,{'1003'})

    def test_stale_mismatched_or_missing_identity_never_loads_plan_or_sends(self):
        p,c,q=context()
        for change in [dict(freshRead=False),dict(ecuId=9),dict(generation='982'),
                       dict(sourceCatalogId='0'*64),dict(variantLink={'profile_id':'other'})]:
            with patch.object(coding,'_source_profile',side_effect=AssertionError('source opened')):
                with self.assertRaisesRegex(ElmError,'identity-unqualified'):
                    coding.read_source_coding(Client(),p,c,{**q,**change},time.monotonic()+10,lambda:None)

    def test_partial_table_invalid_service_or_claimed_scope_cannot_expand_read_set(self):
        p,c,_=context()
        for mutate in [lambda p:p['requestGroups'].pop(),lambda p:p['requestGroups'][0].update(requestHex='2EF100'),
                       lambda p:p['requestGroups'][1]['fields'][0].update(sourceRecordIndex=0),
                       lambda p:p.update(definitionCoverageComplete=False),
                       lambda p:p['requestGroups'][0].update(minimumKnownBytes=True),
                       lambda p:p['requestGroups'][0]['readOrigin'].update(sourceSha256='0'*64)]:
            s=source();mutate(s['coding']);canonical=dict(s['coding']);canonical.pop('definitionSha256')
            s['coding']['definitionSha256']=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            with patch.object(coding,'_source_profile',return_value=s):
                with self.assertRaises(ValueError):coding.source_coding_plan(p,c)

    def test_expected_length_is_checked_when_present_and_never_inferred_from_field_span(self):
        p,c,q=context();s=source();s['coding']['requestGroups'][1]['expectedTotalBytes']=6
        canonical=dict(s['coding']);canonical.pop('definitionSha256')
        s['coding']['definitionSha256']=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with patch.object(coding,'_source_profile',return_value=s):
            out=coding.read_source_coding(Client(),p,c,q,time.monotonic()+10,lambda:None)
        self.assertEqual(out['error'],'coding-read-block-length-mismatch');self.assertEqual(len(out['blocks']),1)

    def test_catalog_annotations_preserve_the_underlying_plan_digest(self):
        p,c,_=context();s=source()
        for group in s['coding']['requestGroups']:
            group['namedFields']=[{'name':'Catalog presentation annotation'}]
        with patch.object(coding,'_source_profile',return_value=s):
            plan=coding.source_coding_plan(p,c)
        self.assertEqual(plan['definitionSha256'],s['coding']['definitionSha256'])
        self.assertEqual([g['requestHex'] for g in plan['requestGroups']],['22F100','22F101'])

    def test_declared_variable_remainder_allows_reading_and_preserves_all_returned_bytes(self):
        p,c,q=context();client=Client()
        with patch.object(coding,'_source_profile',return_value=self.variable_source()):
            out=coding.read_source_coding(client,p,c,q,time.monotonic()+10,lambda:None)
        self.assertTrue(out['ok']);self.assertTrue(out['allDefinedGroupsReceived'])
        self.assertEqual(out['blocks'][1]['dataHex'],'1122334455')
        self.assertEqual(client.sent,['22F100','22F101']);self.assertEqual(client.allowed,{'1003'})
        self.assertFalse(out['completeOriginalBackup']);self.assertFalse(out['baselineCreated'])
        self.assertFalse(out['blocks'][1]['completeLengthQualified'])

    def test_variable_lower_bound_never_turns_an_empty_reply_into_original_coding(self):
        p,c,q=context();client=Client();client.replies['22F101']=dict(ok=True,payload_hex='62F101')
        with patch.object(coding,'_source_profile',return_value=self.variable_source()):
            out=coding.read_source_coding(client,p,c,q,time.monotonic()+10,lambda:None)
        self.assertFalse(out['ok']);self.assertEqual(out['error'],'coding-read-empty-block')
        self.assertEqual(out['missing'],['22F101']);self.assertEqual(len(out['blocks']),1)
        self.assertFalse(out['allDefinedGroupsReceived']);self.assertFalse(out['baselineCreated'])
        self.assertEqual(client.allowed,{'1003'});self.assertEqual(client.sent.count('22F101'),1)

    def test_zero_lower_bound_requires_explicit_variable_extent_and_nonzero_fixed_length(self):
        p,c,_=context()
        for mutation in [lambda g:g['fields'][0]['span'].update(kind='exact'),
                         lambda g:g['fields'][0]['span'].pop('unknownRemaining'),
                         lambda g:g.update(expectedTotalBytes=0)]:
            s=self.variable_source();mutation(s['coding']['requestGroups'][1])
            canonical=dict(s['coding']);canonical.pop('definitionSha256')
            s['coding']['definitionSha256']=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            with patch.object(coding,'_source_profile',return_value=s):
                with self.assertRaises(ValueError):coding.source_coding_plan(p,c)


class CodingWorkerTests(unittest.TestCase):
    def case(self,device='elm',raw=False,bad_identity=False,negative=False,variable=False,empty=False,
             delays=None,budget_seconds=None):
        pid='9x1:SCHEINWERFER:SCHEINWERFER_1' if raw else PROFILES[5]
        ecu=88 if raw else 5
        c=raw_contract(pid) if raw else model_contract(ecu)
        c['sourceCatalogId']=coding.SOURCE_CATALOG
        replies=model_replies(pid) if raw else model_responses(ecu)
        replies.update({'22F100':bytes.fromhex('62F100010203FFFF'),'22F101':bytes.fromhex('7F2231' if negative else '62F1011122334455')})
        if empty:replies['22F101']=bytes.fromhex('62F101')
        if bad_identity:replies['22F1A2']=bytes.fromhex('62F1A2')+b'999999'
        native=RawSourceNative(replies) if raw else FakeNative();ports=[]
        if not raw:native.make_events=lambda payload,cop:FakeNative.reply(replies[payload.hex().upper()],cop)
        elapsed=[0.0];actual_clock=time.monotonic
        if delays:
            make_events=native.make_events
            def delayed_events(payload,cop):
                elapsed[0]+=delays.get(payload.hex().upper(),0)
                return make_events(payload,cop)
            native.make_events=delayed_events
        def opened(*a,**kw):
            port=ModelPort(replies) if raw else SessionSimPort('porsche-981-2014-dme')
            if not raw:port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(c['rxId'],pdu)) for hx,pdu in replies.items()}
            if delays:
                write=port.write
                def delayed_write(data):
                    before=len(port.ecu_payloads);result=write(data)
                    elapsed[0]+=sum(delays.get(hx,0) for hx in port.ecu_payloads[before:])
                    return result
                port.write=delayed_write
            ports.append(port);return port,'synthetic'
        req={**request(), 'action':'manufacturer-read-coding','profileId':pid,'ecuId':ecu,'parameterIds':[],
             'deviceId':ID if device=='vnci' else 'bt:001122334455','cycleCount':1}
        if budget_seconds is not None:req['budgetSeconds']=budget_seconds
        profile=dict(profileId=pid,generation=None,ecuIds=[ecu],status='definition-only',parameters=[field('a','22F102')],
            identity=[dict(requestHex=row['requestHex'],decoderReady=True) for row in c['identityRequests']],checklist=[])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle(root,profile)
            with patch.object(read,'READ_PROFILES',{pid:read.READ_PROFILES[pid]}),\
                 patch('scripts.diagnostics.uds_source_read.source_contract',return_value=c),\
                 patch.object(coding,'_source_profile',return_value=SourceCodingTests().variable_source(pid) if variable else source(pid)),\
                 patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened),\
                 patch('scripts.diagnostics.vnci.NativeDpu',return_value=native),\
                 patch('scripts.diagnostics.manufacturer_read.time.monotonic',side_effect=lambda:actual_clock()+elapsed[0]):
                out=read.read_manufacturer(req,root=root)
        return out,native.sent if device=='vnci' else ports[0].ecu_payloads if ports else []

    def test_healthy_coding_groups_can_finish_after_thirty_seconds(self):
        delays={item['requestHex']:2 for item in model_contract(5)['identityRequests']}
        delays.update({'22F100':11,'22F101':11})
        for device in ('elm','vnci'):
            with self.subTest(device=device):
                out,sent=self.case(device,delays=delays)
                self.assertTrue(out['ok'],out.get('error'))
                self.assertEqual([b['requestHex'] for b in out['codingRead']['blocks']],['22F100','22F101'])
                self.assertEqual(out['codingRead']['missing'],[])
                self.assertFalse(out['codingRead']['completeOriginalRead'])
                self.assertTrue(out['transport']['closed'])
                self.assertEqual(out['transport']['closeErrors'],[])
                self.assertEqual(sent.count('22F100'),1);self.assertEqual(sent.count('22F101'),1)

    def test_explicit_coding_budget_and_identity_budget_remain_bounded(self):
        delays={item['requestHex']:2 for item in model_contract(5)['identityRequests']}
        delays.update({'22F100':11,'22F101':11})
        out,_=self.case(delays=delays,budget_seconds=30)
        self.assertFalse(out['ok']);self.assertFalse(out['codingRead']['allDefinedGroupsReceived'])
        self.assertTrue(out['transport']['closed'])
        out,sent=self.case(delays={'22F1A2':31})
        self.assertFalse(out['ok']);self.assertNotIn('22F100',sent);self.assertNotIn('22F101',sent)
        self.assertTrue(out['transport']['closed'])
        out,sent=self.case(delays={'22F100':16})
        self.assertFalse(out['ok']);self.assertEqual(out['codingRead']['blocks'],[])
        self.assertEqual(sent.count('22F100'),1);self.assertNotIn('22F101',sent)
        self.assertTrue(out['transport']['closed'])

    def test_expired_coding_preparation_never_starts_a_gateway_anchor(self):
        clock=[100.0];calls=[0]
        def verified(request,root):
            calls[0]+=1
            if calls[0]==1:clock[0]+=601
            return dict(profileId=PROFILES[5],generation=None,ecuIds=[5],status='definition-only',
                parameters=[field('a','22F102')],identity=[],checklist=[])
        with patch('scripts.diagnostics.manufacturer_read.time.monotonic',side_effect=lambda:clock[0]),\
                patch.object(read,'verified_profile',side_effect=verified),\
                patch.object(read,'_vehicle_request',side_effect=AssertionError('expired job must not start an anchor')) as anchor:
            out,sent=self.case()
        self.assertEqual(out['error'],'deadline-expired')
        self.assertFalse(out['transport']['openAttempted'])
        self.assertEqual(sent,[]);anchor.assert_not_called()

    def test_fresh_identity_then_all_coding_groups_on_four_real_client_paths(self):
        for raw in [False,True]:
            for device in ['elm','vnci']:
                out,sent=self.case(device,raw)
                self.assertTrue(out['ok'],out.get('error'));self.assertTrue(out['codingOnly'])
                self.assertTrue(out['transport']['closed']);self.assertEqual(out['transport']['closeErrors'],[])
                self.assertLess(sent.index('22F190'),sent.index('22F100'))
                self.assertEqual(sent[-2:],['22F100','22F101']);self.assertNotIn('22F102',sent)
                self.assertEqual(out['samples'],[]);self.assertEqual(out['transactions'],[])
                self.assertEqual(out['codingRead']['blocks'][0]['dataHex'],'010203FFFF')
                self.assertFalse(out['codingRead']['baselineCreated'])

    def test_identity_rejection_and_partial_failure_release_connection_without_retry(self):
        for device in ['elm','vnci']:
            out,sent=self.case(device,bad_identity=True)
            self.assertFalse(out['ok']);self.assertNotIn('22F100',sent);self.assertTrue(out['transport']['closed'])
            out,sent=self.case(device,negative=True)
            self.assertFalse(out['ok']);self.assertEqual(len(out['codingRead']['blocks']),1)
            self.assertEqual(sent.count('22F101'),1);self.assertTrue(out['transport']['closed'])

    def test_variable_source_groups_use_all_existing_carrier_paths(self):
        for raw in [False,True]:
            for device in ['elm','vnci']:
                out,sent=self.case(device,raw,variable=True)
                self.assertTrue(out['ok'],out.get('error'))
                self.assertEqual(out['codingRead']['blocks'][1]['dataHex'],'1122334455')
                self.assertTrue(out['transport']['closed']);self.assertFalse(out['codingRead']['baselineCreated'])
                self.assertEqual(sent[-2:],['22F100','22F101'])

    def test_empty_variable_reply_is_partial_and_released_on_all_carrier_paths(self):
        for raw in [False,True]:
            for device in ['elm','vnci']:
                out,sent=self.case(device,raw,variable=True,empty=True)
                self.assertFalse(out['ok']);self.assertEqual(out['error'],'coding-read-empty-block')
                self.assertEqual(len(out['codingRead']['blocks']),1)
                self.assertEqual(out['codingRead']['missing'],['22F101'])
                self.assertTrue(out['transport']['closed']);self.assertEqual(sent.count('22F101'),1)

    def test_action_never_accepts_caller_requests_or_measurement_selection(self):
        req={**request(),'action':'manufacturer-read-coding','profileId':PROFILES[5],'ecuId':5,'parameterIds':[]}
        for extra in [dict(parameterIds=['a'*64]),dict(requestHex='22F100'),dict(baselineCreated=True),dict(cycleCount=2)]:
            with patch('scripts.diagnostics.connection.open_selected_port',side_effect=AssertionError('opened')):
                out=read.read_manufacturer({**req,**extra})
            self.assertFalse(out['ok']);self.assertFalse(out['transport']['openAttempted'])


if __name__=='__main__':unittest.main()
