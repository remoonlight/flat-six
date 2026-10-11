from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.coding_capture import CodingBlockSet, FLAGS, analyze_capture
from scripts.diagnostics import coding_capture_bridge as bridge
from scripts.diagnostics.tests import test_coding_capture as fixtures


class CodingBlockSetTests(unittest.TestCase):
    def setUp(self):
        self.plan=fixtures.CodingCaptureTests().plan()
        self.groups={g['requestHex']:g for g in self.plan['requestGroups']}
        self.capture={'prefixComplete':True,'packetErrors':0,'endStatisticsPresent':True}

    def row(self,request='22F100',synthetic=False,packet=1):
        return {'requestHex':request,'synthetic':synthetic,'packet':packet,'timestampUs':1000000+packet,
                'pduHex':'62F100010203FFFF' if request=='22F100' else '61A400'}

    def finish(self,collector,capture=None,missing=None):
        blocks=[];result=collector.finish(capture or self.capture,missing or [],blocks.append)
        return result,blocks

    def test_full_payload_trailing_bytes_repeats_and_missing_are_distinct(self):
        c=CodingBlockSet(self.groups)
        for packet in range(10000):c.observe(self.row(packet=packet),bytes.fromhex('010203FFFF'),None)
        result,blocks=self.finish(c)
        self.assertEqual(len(c.inputs['observed']),1)
        self.assertEqual(len(blocks),4)
        self.assertEqual(blocks[0]['representative']['dataHex'],'010203FFFF')
        self.assertEqual(blocks[0]['observations'],10000)
        self.assertEqual(blocks[1]['status'],'missing')
        self.assertFalse(result['reports'][0]['consistentReadSetAvailable'])
        for row in (result,*result['reports'],*blocks):
            for key,value in FLAGS.items():self.assertEqual(row[key],value)
            self.assertFalse(row['atomicReadProven'])

    def test_identical_groups_available_but_never_complete_original_or_atomic(self):
        c=CodingBlockSet(self.groups)
        for request in self.groups:c.observe(self.row(request),b'\1\2\3',None)
        result,blocks=self.finish(c)
        self.assertTrue(result['reports'][0]['consistentReadSetAvailable'])
        self.assertFalse(result['reports'][1]['consistentReadSetAvailable'])
        self.assertTrue(all(b['completeLengthQualified'] is False for b in blocks))

    def test_changed_then_original_response_does_not_erase_conflict_or_choose_latest(self):
        c=CodingBlockSet(self.groups)
        for data in (b'\1\2\3',b'\1\2\4',b'\1\2\3'):c.observe(self.row(),data,None)
        _,blocks=self.finish(c)
        self.assertEqual(blocks[0]['status'],'conflicting')
        self.assertEqual(blocks[0]['representative']['dataHex'],'010203')
        self.assertEqual(blocks[0]['validResponses'],3)

    def test_failures_remain_after_valid_response_and_never_fill_another_source(self):
        c=CodingBlockSet(self.groups)
        c.observe(self.row(),None,'negative_response')
        c.observe(self.row(),b'\1\2\3',None)
        c.observe(self.row('21A4',True),b'\0',None)
        result,blocks=self.finish(c)
        self.assertEqual(blocks[0]['status'],'failed')
        self.assertEqual(blocks[0]['failures'],{'negative_response':1})
        self.assertEqual(blocks[1]['status'],'missing')
        self.assertEqual(blocks[2]['status'],'missing')
        self.assertFalse(any(r['consistentReadSetAvailable'] for r in result['reports']))

    def test_bad_capture_and_missing_definition_do_not_qualify_consistent_groups(self):
        c=CodingBlockSet(self.groups)
        for request in self.groups:c.observe(self.row(request),b'\1\2\3',None)
        for capture,missing in [({**self.capture,'prefixComplete':False},[]),
                                ({**self.capture,'packetErrors':1},[]),
                                ({**self.capture,'endStatisticsPresent':False},[]),(self.capture,[{'reason':'gap'}])]:
            result,_=self.finish(c,capture,missing)
            self.assertFalse(result['reports'][0]['consistentReadSetAvailable'])

    def test_source_catalog_scope_is_bound_to_profile_and_current_generator(self):
        source={'generatorCodeMatchesCurrent':True}
        profile={'profileId':'known','coding':self.plan}
        request={'sourceCatalogId':'a'*64,'generation':'981','ecuId':1,'profileId':'known'}
        with patch('scripts.diagnostics.dtc_capture.load_profile',return_value=(profile,source)):
            result=bridge.coding_scope(request)
            self.assertEqual([t['targetProfileId'] for t in result['captureTargets']],['known','porsche-981-2014-dme'])
        with patch('scripts.diagnostics.dtc_capture.load_profile',return_value=(profile,{'generatorCodeMatchesCurrent':False})):
            with self.assertRaisesRegex(ValueError,'generator-drift'):bridge.coding_scope(request)
        profile=deepcopy(profile);profile['coding']['profileId']='other'
        with patch('scripts.diagnostics.dtc_capture.load_profile',return_value=(profile,source)):
            with self.assertRaisesRegex(ValueError,'plan-profile-mismatch'):bridge.coding_scope(request)

    def test_portable_unit_lookup_uses_existing_system_mapping_and_no_legacy_file(self):
        request={'sourceCatalogId':'a'*64,'generation':'981','systemId':'dme'}
        data={'generatorCodeMatchesCurrent':True,'units':[{'ecuId':1,'variants':[{'profileId':'known'}]},
                                                       {'ecuId':9,'variants':[{'profileId':'gateway'}]}]}
        with patch('scripts.diagnostics.source_catalog.handle_source_catalog',return_value=data) as query:
            result=bridge.coding_units(request)
            self.assertEqual(result['unit']['ecuId'],1)
            self.assertEqual(query.call_args.args[0]['action'],'source-units')
            data['generatorCodeMatchesCurrent']=False
            with self.assertRaisesRegex(ValueError,'generator-drift'):bridge.coding_units(request)
            with self.assertRaisesRegex(ValueError,'system-invalid'):bridge.coding_units({**request,'systemId':'guess'})

    def test_empty_scope_is_never_consistent_read_set(self):
        result,blocks=self.finish(CodingBlockSet({}))
        self.assertEqual(blocks,[])
        self.assertFalse(any(r['consistentReadSetAvailable'] for r in result['reports']))


class CodingCapturedBlocksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.CodingCaptureTests.setUpClass()
    @classmethod
    def tearDownClass(cls):fixtures.CodingCaptureTests.tearDownClass()

    def test_explicit_target_and_run_do_not_borrow_other_capture_scope(self):
        plan=fixtures.CodingCaptureTests().plan();plan['profileId']='selected-source-version'
        rows=[];blocks=[]
        r=analyze_capture(fixtures.CodingCaptureTests.root/'normal.pcapng',plan,'selected',rows.append,
            capture_target_profile_id='known',emit_block=blocks.append)
        self.assertEqual(len(rows),8)
        self.assertEqual(r['sourceProfileId'],'selected-source-version')
        self.assertEqual(r['captureTargetProfileId'],'known')
        self.assertEqual(blocks[0]['status'],'conflicting')
        self.assertEqual(blocks[0]['failures'],{'negative':1,'wrong_did':1,'coding-read-short-block':1,
            'native-result-unqualified':1,'cleanup-response-unqualified':1})
        self.assertFalse(r['blockSet']['reports'][0]['consistentReadSetAvailable'])
        other=analyze_capture(fixtures.CodingCaptureTests.root/'normal.pcapng',plan,'other',lambda row:None,
            capture_target_profile_id='known')
        self.assertEqual(other['missingReadRequests'],['22F100'])

    def test_scoped_bridge_writes_pages_and_exports_all_blocks_without_legacy_variants(self):
        plan=fixtures.CodingCaptureTests().plan();plan['profileId']='selected-source-version'
        scope={'plan':plan,'captureTargets':[{'targetProfileId':'known'}],'source':{'sourceCatalogId':'a'*64}}
        capture=fixtures.CodingCaptureTests.root/'normal.pcapng'
        with tempfile.TemporaryDirectory() as temp:
            request={'capture':str(capture),'captureSha256':bridge.digest(capture),'output':str(Path(temp)/'result'),
                'profileId':plan['profileId'],'generation':'981','runId':'selected','sourceCatalogId':'a'*64,
                'ecuId':1,'captureTargetProfileId':'known','kind':'coding'}
            with patch.object(bridge,'coding_scope',return_value=scope),patch.object(bridge,'load_plan',side_effect=AssertionError('legacy input opened')):
                result=bridge.analyze(request)
            self.assertEqual(set(result['artifact']),{'report.json','responses.jsonl','blocks.jsonl'})
            page=bridge.page({**request,'artifact':result['artifact'],'cursor':0,'stream':'blocks'})
            self.assertEqual(len(page['rows']),4)
            self.assertEqual(page['rows'][0]['representative']['dataHex'],'010203FFFF')
            rows=[json.loads(line) for line in (Path(request['output'])/'responses.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),8)
            bad={**request,'output':str(Path(temp)/'bad'),'captureTargetProfileId':'other'}
            with patch.object(bridge,'coding_scope',return_value=scope):
                with self.assertRaisesRegex(ValueError,'target-source-mismatch'):bridge.analyze(bad)
            self.assertFalse(Path(bad['output']).exists())
            blockfile=Path(request['output'])/'blocks.jsonl';blockfile.write_text('tampered')
            with self.assertRaisesRegex(ValueError,'artifact-changed'):
                bridge.page({**request,'artifact':result['artifact'],'cursor':0,'stream':'blocks'})


if __name__=='__main__':unittest.main()
