import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.diagnostics import coding_capture_bridge as bridge
from scripts.diagnostics.tests import test_coding_capture as fixtures
from scripts.diagnostics.tests import test_measurement_capture as measurement_fixtures
from scripts.diagnostics import measurement_capture
from scripts.diagnostics.tests import test_dtc_capture as dtc_fixtures
import os
import subprocess
import sys


class CodingCaptureBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.CodingCaptureTests.setUpClass()
    @classmethod
    def tearDownClass(cls):fixtures.CodingCaptureTests.tearDownClass()

    def analysis(self, root):
        capture=fixtures.CodingCaptureTests.root/'normal.pcapng'
        request={'capture':str(capture),'captureSha256':bridge.digest(capture),'output':str(root/'result'),
            'profileId':'known','generation':'981','runId':'selected'}
        with patch.object(bridge,'load_plan',return_value=fixtures.CodingCaptureTests().plan()):
            result=bridge.analyze(request)
        return {**request,'artifact':result['artifact']},result

    def test_full_artifact_page_export_and_no_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,result=self.analysis(root)
            self.assertEqual(result['report']['changedGroups'],[{'requestHex':'22F100','distinctValues':2}])
            self.assertFalse(result['report']['baselineCreated'])
            page=bridge.page({**request,'cursor':0});self.assertEqual(len(page['rows']),8);self.assertIsNone(page['nextCursor'])
            target=root/'saved.zip';target.write_bytes(b'previous file retained until complete ZIP')
            bridge.export({**request,'destination':str(target)})
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(set(archive.namelist()),{'report.json','responses.jsonl','blocks.jsonl'})
                self.assertEqual(len(archive.read('responses.jsonl').splitlines()),8)
                report=json.loads(archive.read('report.json'));self.assertIn('candidateDataHashes',report)
                self.assertFalse(report['completeOriginalBackup'])

    def test_tampered_artifact_refuses_page_and_preserves_existing_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,_=self.analysis(root);target=root/'saved.zip';target.write_bytes(b'keep')
            (root/'result/responses.jsonl').write_text('changed')
            with self.assertRaisesRegex(ValueError,'artifact-changed'):bridge.page({**request,'cursor':0})
            with self.assertRaisesRegex(ValueError,'artifact-changed'):bridge.export({**request,'destination':str(target)})
            self.assertEqual(target.read_bytes(),b'keep');self.assertEqual(list(root.glob('.flat-six-capture-*')),[])

    def test_reopened_source_hash_and_byte_cursor_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,_=self.analysis(root)
            with self.assertRaisesRegex(ValueError,'cursor-invalid'):bridge.page({**request,'cursor':1})
            request.update(output=str(root/'changed'),captureSha256='0'*64)
            with patch.object(bridge,'load_plan',return_value=fixtures.CodingCaptureTests().plan()):
                with self.assertRaisesRegex(ValueError,'changed-reopen'):bridge.analyze(request)


class MeasurementCaptureBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):measurement_fixtures.MeasurementCaptureTests.setUpClass()
    @classmethod
    def tearDownClass(cls):measurement_fixtures.MeasurementCaptureTests.tearDownClass()

    def analysis(self, root, **changes):
        capture=measurement_fixtures.MeasurementCaptureTests.root/'normal.pcapng'
        request={'kind':'measurement','capture':str(capture),'captureSha256':bridge.digest(capture),'output':str(root/'result'),
            'profileId':'known','generation':'982','runId':'selected',**changes}
        variant=measurement_fixtures.MeasurementCaptureTests().variant(25)
        with patch.object(measurement_capture,'load_variant',return_value=variant):result=bridge.analyze(request)
        return {**request,'artifact':result['artifact']},result

    def test_both_streams_and_complete_export_beyond_first_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,result=self.analysis(root)
            first=bridge.page({**request,'stream':'values','cursor':0})
            self.assertEqual(len(first['rows']),20);self.assertIsNotNone(first['nextCursor'])
            second=bridge.page({**request,'stream':'values','cursor':first['nextCursor']})
            self.assertEqual(second['rows'][0]['parameterId'],measurement_capture.field_plan(measurement_fixtures.MeasurementCaptureTests().variant(25),'982')['fields'][20]['id'])
            self.assertEqual(len(bridge.page({**request,'stream':'responses','cursor':0})['rows']),9)
            self.assertEqual(result['report']['definitionFieldCount'],25)
            target=root/'full.zip';bridge.export({**request,'destination':str(target)})
            with zipfile.ZipFile(target) as z:
                self.assertEqual(set(z.namelist()),{'report.json','responses.jsonl','values.jsonl','fields.json'})
                rows=[json.loads(l) for l in z.read('values.jsonl').splitlines()]
                self.assertEqual(len(rows),200);self.assertEqual(len(json.loads(z.read('fields.json'))['fields']),25)
                self.assertTrue(any(not r['result']['ok'] for r in rows))
                self.assertTrue(all(not r['vehicleValueQualified'] for r in rows))
                report=json.loads(z.read('report.json'));self.assertFalse(report['identityQualified'])
                self.assertTrue(any(r['path'].endswith('coding_capture_bridge.py') for r in report['code']))

    def test_catalog_tamper_stream_escape_and_source_change_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,_=self.analysis(root)
            with self.assertRaisesRegex(ValueError,'stream-invalid'):bridge.page({**request,'stream':'../report','cursor':0})
            with self.assertRaisesRegex(ValueError,'changed-reopen'):self.analysis(root,output=str(root/'changed'),captureSha256='0'*64)
            target=root/'keep.zip';target.write_bytes(b'keep')
            (root/'result/fields.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'artifact-changed'):bridge.page({**request,'stream':'values','cursor':0})
            with self.assertRaisesRegex(ValueError,'artifact-changed'):bridge.export({**request,'destination':str(target)})
            self.assertEqual(target.read_bytes(),b'keep')


class DtcCaptureBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):dtc_fixtures.DtcCaptureTests.setUpClass()
    @classmethod
    def tearDownClass(cls):dtc_fixtures.DtcCaptureTests.tearDownClass()

    def analysis(self, root, capture=None):
        catalog,_=dtc_fixtures.DtcCaptureTests().library(root/'library')
        capture=capture or dtc_fixtures.DtcCaptureTests.root/'synthetic.pcapng'
        request={'kind':'dtc','capture':str(capture),'captureSha256':bridge.digest(capture),'output':str(root/'result'),
            'sourceCatalogId':catalog,'ecuId':9,'linkIndex':0,'profileId':'version-a','generation':'981','runId':'selected'}
        with patch.dict(os.environ,{'PORSCHE981_DEFINITION_ROOT':str(root/'library')}):result=bridge.analyze(request)
        return {**request,'artifact':result['artifact']},result

    def test_faults_responses_dictionary_and_complete_export_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,result=self.analysis(root)
            faults=bridge.page({**request,'cursor':0,'stream':'faults'})['rows']
            self.assertEqual(len(faults),3);self.assertEqual(faults[0]['statusHex'],'28')
            self.assertEqual(result['report']['recordCounts'],{'synthetic':3})
            responses=bridge.page({**request,'cursor':0,'stream':'responses'})['rows']
            self.assertEqual(len(responses),12);self.assertEqual(responses[0]['decodedObservation']['recordCount'],1)
            self.assertNotIn('records',responses[0]['decodedObservation'])
            dictionary=json.loads((root/'result/dictionary.json').read_text())
            self.assertEqual(dictionary['records'][faults[0]['definitionRecordIndices'][0]]['numericKey'],int(faults[0]['dtcHex'],16))
            target=root/'full.zip';bridge.export({**request,'destination':str(target)})
            with zipfile.ZipFile(target) as z:
                self.assertEqual(set(z.namelist()),{'report.json','responses.jsonl','faults.jsonl','dictionary.json'})
                self.assertEqual(len(z.read('faults.jsonl').splitlines()),3)
                self.assertFalse(json.loads(z.read('report.json'))['identityQualified'])
            with self.assertRaisesRegex(ValueError,'stream-invalid'):bridge.page({**request,'cursor':0,'stream':'values'})
            (root/'result/dictionary.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'artifact-changed'):bridge.export({**request,'destination':str(target)})

    def test_real_stdin_scope_and_strict_shapes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,_=dtc_fixtures.DtcCaptureTests().library(root)
            req={'kind':'dtc','action':'scope','sourceCatalogId':catalog,'generation':'982','ecuId':9,'profileId':'version-a'}
            env={**os.environ,'PORSCHE981_DEFINITION_ROOT':str(root),'PYTHONDONTWRITEBYTECODE':'1'}
            def call(r):
                p=subprocess.run([sys.executable,'-B','-m','scripts.diagnostics.coding_capture_bridge'],input=json.dumps(r),
                    capture_output=True,text=True,timeout=15,env=env,check=True)
                return json.loads(p.stdout)
            result=call(req);self.assertTrue(result['ok']);self.assertEqual(result['links'][0]['requests'],['1800FF00','190208'])
            self.assertFalse(result['identityQualified'])
            for change in ({'kind':'coding'},{'ecuId':True},{'extra':'bad'},{'generation':'991'}):self.assertFalse(call({**req,**change})['ok'])

    def test_maximum_complete_pdu_faults_paginate_without_one_huge_response_row(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);capture=root/'full.pcapng'
            script="""import fs from 'node:fs';import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const file=JSON.parse(fs.readFileSync(0,'utf8'));const w=createDiagnosticPduWriter(file,true);
w.append({requestHex:'190208',pduHex:'590219'+'C1300209'.repeat(1023),timestampUs:1000000,timestampSource:'host-pdu-observed',
transportKind:'elm-iso-tp',observation:'reassembled-complete-pdu',duringCleanup:false},
{runId:'selected',targetProfileId:'version-a',simulation:true});w.finish('synthetic maximum complete PDU');"""
            subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(str(capture)),capture_output=True,text=True,check=True,timeout=15)
            request,result=self.analysis(root,capture)
            self.assertEqual(result['report']['recordCounts'],{'synthetic':1023})
            first=bridge.page({**request,'cursor':0,'stream':'faults'});self.assertEqual(len(first['rows']),20)
            self.assertIsNotNone(first['nextCursor'])
            response=bridge.page({**request,'cursor':0,'stream':'responses'})['rows'][0]
            self.assertEqual(len(response['pduHex']),8190);self.assertEqual(response['decodedObservation']['recordCount'],1023)
            self.assertLess(len(json.dumps(response)),65536)
            bridge.export({**request,'destination':str(root/'full.zip')})
            with zipfile.ZipFile(root/'full.zip') as z:self.assertEqual(len(z.read('faults.jsonl').splitlines()),1023)


if __name__=='__main__':unittest.main()
