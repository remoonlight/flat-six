"""All-fields post-capture decoding, production JS PCAPNG fixtures, no hardware."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.coding_capture import load_variant
from scripts.diagnostics.measurement_capture import analyze, field_plan, language_lookup
from scripts.diagnostics.tests.test_realtime_preparation import field


class MeasurementCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='flat-six-measurement-capture-');cls.root=Path(cls.temp.name)
        fixture=[('62F1000102',1000000,{}),('62F1000304',1100000,{}),('7F2278',1200000,{}),
            ('62F10005',900000,{}),('62F1000607',1300000,{'runId':'other'}),
            ('62F1000809',1400000,{'targetProfileId':'other'}),
            ('62F1000A0B',1500000,{'duringCleanup':True}),('62F1000C0D',1600000,{'uniqueId':7}),
            ('62F1000E0F',1700000,{'flagsHex':'01000000'}),('62F1010000',1800000,{}),
            ('62F1001011',1900000,{'requestHex':'22F199'})]
        script='''import fs from 'node:fs';
import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const {root,fixture}=JSON.parse(fs.readFileSync(0,'utf8'));
for(const synthetic of [false,true]){
 const writer=createDiagnosticPduWriter(root+'/'+(synthetic?'synthetic':'normal')+'.pcapng',synthetic);
 for(const [pduHex,timestampUs,extra] of fixture){const {runId='selected',targetProfileId='known',...metadata}=extra;
 writer.append({requestHex:'22F100',pduHex,timestampUs,timestampSource:'host-pdu-observed',
 transportKind:'d-pdu-iso-tp',observation:'native-result-data',duringCleanup:false,primitive:1,expectedPrimitive:1,uniqueId:1,flagsHex:'00000000',...metadata},
 {runId,targetProfileId,simulation:false});}writer.finish('synthetic-test-only-no-device');}'''
        run=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'root':str(cls.root),'fixture':fixture}),
            text=True,capture_output=True,timeout=15)
        if run.returncode:raise AssertionError(run.stderr)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def variant(self,count=2):
        records=[field('a',offset=i%2) for i in range(count)]
        for i,record in enumerate(records):record['at']=100+i
        return {'profile_id':'known','generation':'982','module':'test','name':'test',
            'pool_records':{'measurement':{'records':records}}}

    def run_analysis(self,file='normal',variant=None):
        responses=[];values=[];plan=field_plan(variant or self.variant(),'982')
        report=analyze(self.root/(file+'.pcapng'),plan,'selected',responses.append,values.append)
        return report,responses,values

    def test_full_response_expands_all_fields_beyond_selection_twelve(self):
        report,responses,values=self.run_analysis(variant=self.variant(25))
        self.assertEqual(report['sourceFieldCount'],25);self.assertEqual(len(report['missingDecodedParameterIds']),0)
        self.assertEqual(report['decodedSourceFieldCount'],25);self.assertEqual(len(values),8*25)
        self.assertEqual([row['result']['value'] for row in values[:4]],[1,2,1,2])
        self.assertEqual([r['packet'] for r in responses],[1,2,3,4,7,8,9,10,11])
        self.assertEqual(report['hostTimeReversalCount'],1)
        self.assertEqual(values[3*25]['elapsedHostMs'],-100)
        self.assertEqual(report['counts']['request-not-in-measurement-scope'],1)
        self.assertIsNone(report['selectionLimit'])
        self.assertTrue(all(not row['vehicleValueQualified'] for row in values))

    def test_short_negative_and_native_invalid_never_reuse_previous_values(self):
        report,responses,values=self.run_analysis()
        failed={packet:[r for r in values if r['packet']==packet] for packet in (3,4,7,8,9,10)}
        self.assertTrue(all(not r['result']['ok'] and 'value' not in r['result'] for r in failed[3]));self.assertEqual(failed[3][0]['result']['reason'],'pending')
        self.assertTrue(failed[4][0]['result']['ok']);self.assertFalse(failed[4][1]['result']['ok'])
        self.assertNotIn('value',failed[4][1]['result'])
        for packet in (7,8,9,10):self.assertTrue(all(not r['result']['ok'] for r in failed[packet]))
        self.assertEqual(report['fieldCounts']['observed-decoded'],5)
        self.assertEqual(report['fieldCounts']['observed-failed'],11)

    def test_synthetic_header_excludes_all_real_coverage_but_keeps_fixture_decodes(self):
        report,responses,values=self.run_analysis('synthetic')
        self.assertEqual(report['decodedSourceFieldCount'],0);self.assertEqual(report['syntheticDecodedSourceFieldCount'],2)
        self.assertEqual(report['missingReadRequests'],['22F100']);self.assertEqual(len(report['missingDecodedParameterIds']),2)
        self.assertTrue(all(r['synthetic'] for r in values));self.assertEqual(report['fieldCounts']['synthetic-decoded'],5)

    def test_definition_gap_kept_beside_decodable_fields(self):
        variant=self.variant();variant['pool_records']['measurement']['records'][1]['formula']={'text':'UNSUPPORTED:BitLength=8;'}
        report,responses,values=self.run_analysis(variant=variant)
        self.assertEqual(len(report['definitionMissing']),1);self.assertEqual(report['decodedSourceFieldCount'],1)
        self.assertTrue(all(not r['result']['ok'] for r in values if r['sourceOffset']==101))

    def test_truncated_tail_retains_complete_prefix(self):
        path=self.root/'truncated.pcapng';path.write_bytes((self.root/'normal.pcapng').read_bytes()[:-1])
        report,responses,values=self.run_analysis('truncated')
        self.assertFalse(report['capture']['prefixComplete']);self.assertEqual(len(responses),9);self.assertEqual(len(values),16)

    def test_native_foreign_or_missing_expected_handle_cannot_supply_field_values(self):
        data=(self.root/'normal.pcapng').read_bytes()
        for replacement,reason in ((b'"expectedPrimitive":2','native-primitive-mismatch'),
            (b'"unusedPropertyABC":1','native-primitive-context-missing')):
            file=self.root/(reason+'.pcapng');file.write_bytes(data.replace(b'"expectedPrimitive":1',replacement))
            report,responses,values=self.run_analysis(reason)
            self.assertEqual(report['decodedSourceFieldCount'],0);self.assertEqual(report['missingReadRequests'],['22F100'])
            self.assertTrue(all(not row['result']['ok'] for row in values));self.assertIn(reason,report['counts'])
            self.assertEqual(responses[0]['pduHex'],'62F1000102')

    def test_source_generation_pin_shared_variant_and_finite_failure_isolated(self):
        path=self.root/'variants.jsonl';path.write_text(json.dumps(self.variant())+'\n');expected=hashlib.sha256(path.read_bytes()).hexdigest()
        with patch('scripts.diagnostics.coding_capture.VARIANTS_SHA256',expected):
            self.assertEqual(load_variant(path,'known','982')['generation'],'982')
            with self.assertRaisesRegex(ValueError,'generation-mismatch'):load_variant(path,'known','981')
        with self.assertRaisesRegex(ValueError,'hash-mismatch'):load_variant(path,'known','982')
        with patch('scripts.diagnostics.measurement_capture.decode_application_response',return_value={'ok':True,'value':float('nan')}):
            report,responses,values=self.run_analysis()
            self.assertEqual(report['decodedSourceFieldCount'],0)
            self.assertTrue(all(not r['result']['ok'] for r in values))

    def test_names_resolve_only_through_explicit_language_lookup_and_drift_rejected(self):
        variant=self.variant();variant['pool_records']['measurement']['records'][0]['name']='SubIndexNum=2#0x00,0x00,0x00,0x01#0x00,0x00,0x00,0x02'
        unresolved=field_plan(variant,'982');self.assertEqual(len(unresolved['nameMissing']),1)
        resolved=field_plan(variant,'982',lambda key:{1:'节气门',2:'状态'}.get(key))
        self.assertEqual(resolved['nameMissing'],[]);self.assertIn('节气门',resolved['fields'][0]['name'])
        self.assertIn('状态',resolved['fields'][0]['name'])
        file=self.root/'wrong-language.ggp';file.write_bytes(b'not reviewed language')
        with self.assertRaisesRegex(ValueError,'language-hash-mismatch'):language_lookup(file)


if __name__=='__main__':unittest.main()
