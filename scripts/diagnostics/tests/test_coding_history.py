from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.coding_history import GUARDS, build, candidate_index, field_observation, profile_evidence
from scripts.diagnostics.coding_read_plan import coding_read_plan
from scripts.diagnostics.offline_match import ANCHORS
from scripts.diagnostics.tests.test_coding_read_origin import record, variant
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_coding_sequence import pair
from scripts.diagnostics.coding_sequence import sequence


class CodingHistoryTests(unittest.TestCase):
    def fixture(self):
        unit={'generation':'981','ecuId':1}
        group={'requestHex':'22F100','minimumKnownBytes':1,'fields':[{'sourceOffset':42}]}
        profile={'profileId':'source','module':'DME','variant':'version','sourceGeneration':None,
            'coding':{'requestGroups':[group],'definitionFieldCount':1,'missing':[]}}
        routes={1:{'txId':0x7e0,'rxId':0x7e8,'liveAddressQualified':False}}
        row={'txId':0x7e0,'rxId':0x7e8,'requestHex':'22F100','pduHex':'62F1002AFFFF',
            'observationId':'q1','status':'positive-candidate','capturePhase':'before-coding','adapterStream':[2,2]}
        return unit,profile,routes,row

    def result(self,unit,profile,routes,rows):return profile_evidence(unit,profile,candidate_index(rows),routes)

    def test_full_tail_preserved_without_inferring_complete_length_or_backup(self):
        u,p,r,row=self.fixture();result=self.result(u,p,r,[row]);g=result['requestGroups'][0]
        self.assertEqual(g['samples'][0]['dataHex'],'2AFFFF')
        self.assertEqual(g['observedDataLengths'],[3]);self.assertIsNone(g['expectedTotalBytes'])
        self.assertEqual(result['missingReadRequests'],[])
        for value in (result,g,g['samples'][0]):
            for k,v in GUARDS.items():self.assertEqual(value[k],v)

    def test_repeated_values_different_phases_and_lengths_never_overwrite(self):
        u,p,r,row=self.fixture()
        rows=[row,dict(row,observationId='q2'),dict(row,observationId='q3',pduHex='62F1002B',capturePhase='coding-workflow')]
        g=self.result(u,p,r,rows)['requestGroups'][0]
        self.assertEqual(len(g['samples']),3);self.assertTrue(g['observedLengthConflict'])
        self.assertEqual(g['observedDataLengths'],[1,3])
        self.assertEqual(g['phases']['before-coding']['observations'],2)
        self.assertEqual(len(g['phases']['before-coding']['distinctDataSha256']),1)
        self.assertEqual(g['phases']['coding-workflow']['observations'],1)

    def test_other_generation_ecu_and_tx_do_not_inherit_capture(self):
        u,p,r,row=self.fixture()
        for changed in (dict(u,generation='982'),dict(u,ecuId=2)):
            result=self.result(changed,p,r,[row]);self.assertTrue(result['routeMissing'])
            self.assertEqual(result['requestGroups'][0]['samples'],[])
        self.assertEqual(self.result(u,p,r,[dict(row,txId=0x710)])['missingReadRequests'],['22F100'])

    def test_negative_pending_missing_or_unparsed_results_are_kept_without_block(self):
        u,p,r,row=self.fixture()
        for status in ('negative','pending','unpaired','unqualified-return','address-unqualified','echo-mismatch'):
            with self.subTest(status=status):
                g=self.result(u,p,r,[dict(row,status=status,pduHex='7F2231')])['requestGroups'][0]
                self.assertEqual(g['samples'][0]['status'],status)
                self.assertIsNone(g['samples'][0]['dataHex'])
                self.assertEqual(g['observedDataLengths'],[])

    def test_wrong_rx_echo_and_short_block_not_qualified(self):
        u,p,r,row=self.fixture()
        for changed in (dict(row,rxId=0x77a),dict(row,pduHex='62F1012A'),dict(row,pduHex='62F100')):
            g=self.result(u,p,r,[changed])['requestGroups'][0]
            self.assertEqual(g['status'],'historical-read-missing')
            self.assertNotEqual(g['samples'][0]['status'],'historical-candidate-block')

    def test_source_gap_and_empty_catalog_remain_explicit(self):
        u,p,r,row=self.fixture();p=deepcopy(p)
        p['coding']['missing']=[{'sourceOffset':43,'reason':'coding-field-span-unresolved'}]
        self.assertEqual(self.result(u,p,r,[row])['definitionMissing'],p['coding']['missing'])
        p['coding']['requestGroups']=[]
        result=self.result(u,p,r,[row]);self.assertEqual(result['requestGroups'],[])
        self.assertFalse(result['completeOriginalBackup'])

    def test_field_decode_and_reference_keep_phase_and_no_fresh_identity(self):
        _,_,_,row=self.fixture();record=dict(field(),at=42)
        result=field_observation(record,row)
        self.assertEqual(result['value']['value'],42);self.assertTrue(result['independentDecoderAgreement'])
        self.assertEqual(result['adapterStream'],[2,2])
        for k,v in GUARDS.items():self.assertEqual(result[k],v)

    def test_field_request_mismatch_rejected(self):
        _,_,_,row=self.fixture()
        with self.assertRaisesRegex(ValueError,'field-request-mismatch'):
            field_observation(field(),dict(row,requestHex='22F101'))

    def test_short_block_still_explains_individual_field_failures(self):
        _,_,_,row=self.fixture();row=dict(row,pduHex='62F1002A')
        first=field_observation(field(),row)
        later=field_observation(field(offset=1),row)
        self.assertTrue(first['value']['ok']);self.assertTrue(first['independentDecoderAgreement'])
        self.assertFalse(later['value']['ok']);self.assertFalse(later['independentDecoderAgreement'])
        self.assertFalse(first['completeOriginalBackup'])

    def test_matching_short_response_rejections_are_explicit_without_a_value(self):
        _,_,_,row=self.fixture();row=dict(row,pduHex='62F1002A')
        result=field_observation(field(offset=1),row)
        self.assertFalse(result['value']['ok'])
        self.assertEqual(result['value']['reason'],'truncated')
        self.assertFalse(result['independentDecoderAgreement'])
        self.assertTrue(result['independentFailureAgreement'])
        self.assertEqual(result['independentIssue'],'independent-matching-rejection:truncated')
        self.assertFalse(result['completeOriginalBackup'])

    def test_different_reference_rejection_is_not_matching_failure(self):
        _,_,_,row=self.fixture();row=dict(row,pduHex='62F1002A')
        with patch('scripts.diagnostics.coding_history.reference_value',return_value={'ok':False,'reason':'other-failure'}):
            result=field_observation(field(offset=1),row)
        self.assertFalse(result['independentFailureAgreement'])
        self.assertFalse(result['independentDecoderAgreement'])
        self.assertEqual(result['independentIssue'],'independent-decode-gap-or-disagreement')

    def test_decode_reference_gap_is_explicit(self):
        _,_,_,row=self.fixture()
        with patch('scripts.diagnostics.coding_history.reference_value',side_effect=ValueError('unresolved')):
            result=field_observation(field(),row)
        self.assertFalse(result['independentDecoderAgreement']);self.assertIn('unresolved',result['independentIssue'])

    def test_nonfinite_result_cannot_enter_json_artifact(self):
        _,_,_,row=self.fixture()
        with patch('scripts.diagnostics.coding_history.decode_application_response',return_value={'ok':True,'value':float('nan')}):
            result=field_observation(field(),row)
        self.assertFalse(result['value']['ok']);self.assertFalse(result['independentDecoderAgreement'])
        self.assertEqual(result['independentIssue'],'coding-result-not-json-finite')

    def test_build_binds_full_source_before_joining_mixed_service_fields(self):
        raw=variant([record(0x22,0xF100,100),record(0x21,9,140),record(0x2E,0xF100,180)])
        original=deepcopy(raw)
        unit={'generation':'981','ecuId':1}
        profile={'profileId':raw['profile_id'],'module':raw['module'],'variant':raw['name'],
                 'sourceGeneration':'981','coding':coding_read_plan(raw)}
        _,_,routes,row=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'.local/vehicle-analysis/981-x431-20261001';folder.mkdir(parents=True)
            variants=root/'.local/x431-re/2026-09-27-981982/expansion/variants.jsonl';variants.parent.mkdir(parents=True)
            variants.write_text(json.dumps(raw)+'\n',encoding='utf8')
            (folder/'app-frames.jsonl').write_text('{}\n',encoding='utf8')
            (folder/'transport-audit.json').write_text(json.dumps({'sha256':ANCHORS['hci'],'checkpoints':[{'record':1}]}))
            (folder/'app-frame-summary.json').write_text(json.dumps({'sourceTransportSha256':ANCHORS['hci']}))
            addresses=root/'docs/research/can-data/diagnostic/definitions.json';addresses.parent.mkdir(parents=True);addresses.write_text('{}')
            commands,_=sequence(pair('22F100','62F1002AFFFF',record=10)+pair('220009','620009',record=12),{0x7e0:0x7e8},100)
            for command,oid in zip(commands,('q1','short')):command['observationId']=oid
            with ExitStack() as stack:
                for name,value in [('source_profiles',([(unit,profile)],{})),('verify_source',{}),
                                   ('verify_frames',None),('reference_routes',(routes,{0x7e0:0x7e8})),
                                   ('observations',[row,dict(row,observationId='short',requestHex='220009',pduHex='620009')]),
                                   ('sequence',(commands,{}) )]:
                    stack.enter_context(patch('scripts.diagnostics.coding_history.'+name,return_value=value))
                out=root/'result';report=build(root/'definitions.zip','synthetic',root,out)
            fields=[json.loads(line) for line in (out/'field-values.jsonl').read_text().splitlines()]
            self.assertEqual([f['sourceOffset'] for f in fields],[140,100,180])
            self.assertEqual([f['requestHex'] for f in fields],['220009','22F100','22F100'])
            self.assertEqual(report['fieldObservationChecks'],{'independent-agreement':2,'decode-gap':1})
            self.assertEqual(report['fieldFailureReasons'],{'truncated':1})
            self.assertEqual(report['independentRejectionChecks'],{'matched-rejection':1})
            self.assertEqual(report['readContextObservations'],2)
            contexts=json.loads((out/'read-context-observations.json').read_text())
            self.assertEqual({r['observationId'] for r in contexts['reads']},{'q1','short'})
            manifest=json.loads((out/'manifest.json').read_text())
            self.assertIn('read-context-observations.json',{r['name'] for r in manifest['outputs']})
            self.assertFalse(fields[0]['independentDecoderAgreement'])
            self.assertTrue(fields[0]['independentFailureAgreement'])
            self.assertTrue(all(f['independentDecoderAgreement'] for f in fields[1:]))
            self.assertTrue(all(not f['completeOriginalBackup'] for f in fields))
            self.assertEqual(json.loads(variants.read_text()),original)


if __name__=='__main__':unittest.main()
