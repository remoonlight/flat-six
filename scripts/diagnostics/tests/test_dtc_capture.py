import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.diagnostics.dtc_capture import analyze, capture_targets, load_profile, read_scope, source_scope, write_analysis
from scripts.diagnostics.source_catalog import GUARDS, PREFIX, _json_bytes
from scripts.diagnostics.tests.test_dtc_definitions import profile
from scripts.diagnostics.offline_match import ANCHORS


class DtcCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='flat-six-dtc-capture-');cls.root=Path(cls.temp.name)
        fixture=[('1800FF00','5801C44728',1000000,{}),('1800FF00','5801FFFF21',1100000,{}),
            ('1800FF00','5800',900000,{}),('190208','590219C1300209',1200000,{}),
            ('190208','7F1978',1300000,{}),('1800FF00','7F1831',1400000,{}),
            ('1800FF00','5802C44728',1500000,{}),('190208','590119C1300209',1600000,{}),
            ('1800FF00','5801C44728',1700000,{'duringCleanup':True}),
            ('1800FF00','5801C44728',1800000,{'primitive':2}),
            ('1800FF00','5801C44728',1900000,{'uniqueId':2}),
            ('14FFFFFF','54',2000000,{}),('1800FF00','5801C44728',2100000,{'runId':'other'}),
            ('1800FF00','5801C44728',2200000,{'targetProfileId':'other'})]
        script='''import fs from 'node:fs';
import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const {root,fixture}=JSON.parse(fs.readFileSync(0,'utf8'));
for(const synthetic of [false,true]){
const w=createDiagnosticPduWriter(root+'/'+(synthetic?'synthetic':'normal')+'.pcapng',synthetic);
for(const [requestHex,pduHex,timestampUs,extra] of fixture){const {runId='selected',targetProfileId='version-a',...rest}=extra;
w.append({requestHex,pduHex,timestampUs,timestampSource:'host-pdu-observed',transportKind:'d-pdu-iso-tp',
observation:'native-result-data',duringCleanup:false,primitive:1,expectedPrimitive:1,uniqueId:1,flagsHex:'00000000',...rest},
{runId,targetProfileId,simulation:false});}w.finish('software fixture only, no device');}'''
        subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'root':str(cls.root),'fixture':fixture}),
            text=True,capture_output=True,check=True,timeout=15)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def source(self):return {**profile(),'generations':['981','982'],'sourceGeneration':None}

    def run_analysis(self,file='normal',p=None):
        rows=[];report=analyze(self.root/(file+'.pcapng'),p or self.source(),'981',9,0,'selected',rows.append)
        return report,rows

    def library(self,root):
        p=self.source();raw=_json_bytes(p);name='profiles/'+hashlib.sha256(p['profileId'].encode()).hexdigest()+'.json'
        index={'units':[{'generation':g,'ecuId':9,'variants':[{'profileId':'version-a','file':name}]} for g in ('981','982')]}
        entries={name:raw,'all-units.json':_json_bytes(index)}
        manifest={'schemaVersion':1,'kind':'x431-offline-source-catalog','sourceFingerprints':[{'sha256':ANCHORS['variants']}],
            'generatorCode':[],'files':[{'name':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in entries.items()],**GUARDS}
        data=_json_bytes(manifest);catalog=hashlib.sha256(data).hexdigest();folder=root/PREFIX/catalog
        folder.mkdir(parents=True)
        for n,b in {**entries,'manifest.json':data}.items():
            file=folder/n;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b)
        return catalog,folder

    def test_all_replies_repeats_empty_and_failed_records_remain_separate(self):
        report,rows=self.run_analysis()
        self.assertEqual(len(rows),12);self.assertEqual(report['matchedPackets'],12)
        self.assertEqual(report['recordCounts'],{'observed':3});self.assertEqual(report['observedRequestCount'],2)
        self.assertEqual(report['missingReadRequests'],[]);self.assertEqual(report['hostTimeReversalCount'],1)
        self.assertEqual(rows[2]['decodedObservation']['records'],[])
        self.assertEqual(rows[2]['status'],'decoded-response-observed')
        unknown=rows[1]['decodedObservation']['records'][0]
        self.assertEqual(unknown['definitionBindingStatus'],'source-number-missing');self.assertIsNone(unknown['sourceText'])
        self.assertEqual(rows[0]['decodedObservation']['records'][0]['statusHex'],'28')
        self.assertEqual(rows[3]['decodedObservation']['records'][0]['sourceTextState'],'missing')
        for row in rows:
            self.assertFalse(row['identityQualified']);self.assertFalse(row['liveStatusMeaningQualified'])
            self.assertIsNone(row['writePayload']);self.assertFalse(row['executionEnabled'])

    def test_native_invalid_cleanup_negative_pending_and_truncated_have_no_old_faults(self):
        report,rows=self.run_analysis()
        self.assertEqual(rows[4]['status'],'pending');self.assertEqual(rows[4]['nrc'],0x78)
        self.assertEqual(rows[5]['status'],'negative-response')
        self.assertEqual(rows[8]['status'],'cleanup-response-unqualified')
        self.assertEqual(rows[9]['status'],'native-primitive-mismatch')
        self.assertEqual(rows[10]['status'],'native-result-unqualified')
        self.assertEqual(rows[11]['status'],'request-not-in-dtc-scope')
        for row in rows[4:]:
            self.assertFalse((row['decodedObservation'] or {}).get('ok',False))
            self.assertEqual((row['decodedObservation'] or {}).get('records',[]),[])

    def test_synthetic_decodes_do_not_supply_observed_coverage(self):
        report,rows=self.run_analysis('synthetic')
        self.assertEqual(report['recordCounts'],{'synthetic':3});self.assertEqual(report['observedRequestCount'],0)
        self.assertEqual(report['missingReadRequests'],['1800FF00','190208'])
        self.assertTrue(all(r['synthetic'] for r in rows))

    def test_old_native_context_does_not_supply_fault_records(self):
        path=self.root/'legacy.pcapng';path.write_bytes((self.root/'normal.pcapng').read_bytes().replace(b'"expectedPrimitive":1',b'"unusedPropertyABC":1'))
        report,rows=self.run_analysis('legacy')
        self.assertEqual(report['observedRequestCount'],0);self.assertEqual(report['recordCounts'],{})
        self.assertEqual(rows[0]['status'],'native-primitive-context-missing')
        self.assertEqual(rows[0]['pduHex'],'5801C44728')

    def test_truncated_tail_keeps_complete_prefix_and_never_resyncs(self):
        path=self.root/'truncated.pcapng';path.write_bytes((self.root/'normal.pcapng').read_bytes()[:-1])
        report,rows=self.run_analysis('truncated')
        self.assertFalse(report['capture']['prefixComplete']);self.assertEqual(len(rows),12)

    def test_generation_ecu_link_and_dictionary_fail_before_capture(self):
        p=self.source()
        for g,ecu,link in [('991',9,0),('981',1,0),('981',9,1),('981',True,0)]:
            with self.assertRaises(ValueError):read_scope(p,g,ecu,link)
        p['dtcDefinition']['records'][0]['text']='changed'
        with self.assertRaisesRegex(ValueError,'definition-hash-mismatch'):self.run_analysis(p=p)

    def test_duplicate_dictionary_number_is_ambiguous_not_silently_merged(self):
        from scripts.diagnostics.tests.test_dtc_definitions import variant
        v=variant();v['pool_records']['dtc']['records'].append(v['pool_records']['dtc']['records'][0].copy())
        p={**profile(v),'generations':['981']};report,rows=self.run_analysis(p=p)
        row=rows[0]['decodedObservation']['records'][0]
        self.assertEqual(row['definitionBindingStatus'],'source-number-ambiguous-or-unresolved')
        self.assertEqual(len(row['definitionMatches']),2);self.assertIsNone(row['sourceText'])

    def test_explicit_runtime_target_keeps_same_run_targets_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);capture=root/'targets.pcapng'
            script="""import fs from 'node:fs';import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const file=JSON.parse(fs.readFileSync(0,'utf8'));const w=createDiagnosticPduWriter(file,true);
for(const [targetProfileId,runId,pduHex] of [['version-a','selected','590219C1300209'],
 ['porsche-981-2014-gateway','selected','590219C1300209'],['porsche-981-2014-gateway','other','590219C1300209'],
 ['porsche-981-2014-dme','selected','590219C1300209']])w.append({requestHex:'190208',pduHex,timestampUs:1000000,
 timestampSource:'host-pdu-observed',transportKind:'elm-iso-tp',observation:'reassembled-complete-pdu',duringCleanup:false},
 {targetProfileId,runId,simulation:true});w.finish('synthetic target isolation');"""
            subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(str(capture)),capture_output=True,text=True,check=True,timeout=15)
            rows=[];report=analyze(capture,self.source(),'981',9,0,'selected',rows.append,
                capture_target_profile_id='porsche-981-2014-gateway')
            self.assertEqual(report['matchedPackets'],1);self.assertEqual(len(rows),1)
            self.assertEqual(report['recordCounts'],{'synthetic':1});self.assertEqual(report['observedRequestCount'],0)
            self.assertEqual(report['sourceProfileId'],'version-a')
            self.assertEqual(report['captureTargetProfileId'],'porsche-981-2014-gateway')
            self.assertEqual(report['reviewContext']['association'],'explicit-named-runtime-comparison')
            self.assertFalse(report['reviewContext']['sourceSelectionIsVersionProof'])
            self.assertFalse(rows[0]['reviewContext']['identityQualified'])
            self.assertEqual(rows[0]['context']['targetProfileId'],'porsche-981-2014-gateway')
            catalog,_=self.library(root/'library')
            saved=write_analysis(capture,root/'library',catalog,'981',9,'version-a',0,'selected',root/'out',
                capture_target_profile_id='porsche-981-2014-gateway')
            fault=json.loads((root/'out/faults.jsonl').read_text())
            self.assertEqual(fault['captureTargetProfileId'],saved['captureTargetProfileId'])
            self.assertEqual(fault['sourceProfileId'],'version-a');self.assertEqual(fault['runId'],'selected')
            self.assertFalse(fault['reviewContext']['ecuVersionQualified'])

    def test_unrelated_runtime_target_and_generation_fail_before_output(self):
        p=self.source()
        for generation,target in [('981','porsche-981-2014-dme'),('982','porsche-981-2014-gateway'),('981','unknown'),('981','')]:
            with self.subTest(generation=generation,target=target):
                with self.assertRaisesRegex(ValueError,'dtc-capture-target'):
                    analyze(self.root/'normal.pcapng',p,generation,9,0,'selected',lambda row:None,capture_target_profile_id=target)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,_=self.library(root)
            with self.assertRaisesRegex(ValueError,'target-source-mismatch'):
                write_analysis(self.root/'normal.pcapng',root,catalog,'981',9,'version-a',0,'selected',root/'out',
                    capture_target_profile_id='porsche-981-2014-dme')
            self.assertFalse((root/'out').exists())
            self.assertEqual([r['targetProfileId'] for r in source_scope(root,catalog,'981',9,'version-a')['captureTargets']],
                ['version-a','porsche-981-2014-gateway'])
            self.assertEqual([r['targetProfileId'] for r in source_scope(root,catalog,'982',9,'version-a')['captureTargets']],['version-a'])

    def test_source_catalog_generation_selectors_and_payload_tamper_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,folder=self.library(root)
            self.assertEqual(load_profile(root,catalog,'982',9,'version-a')[0]['profileId'],'version-a')
            with self.assertRaisesRegex(ValueError,'profile-ecu-mismatch'):load_profile(root,catalog,'981',1,'version-a')
            (folder/'all-units.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'hash-mismatch'):load_profile(root,catalog,'981',9,'version-a')

    def test_full_artifacts_and_fingerprints_are_written_not_preview_limited(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,_=self.library(root);out=root/'result'
            r=write_analysis(self.root/'normal.pcapng',root,catalog,'981',9,'version-a',0,'selected',out)
            self.assertEqual({p.name for p in out.iterdir()},{'report.json','responses.jsonl','faults.jsonl','dictionary.json'})
            faults=[json.loads(line) for line in (out/'faults.jsonl').read_text().splitlines()]
            self.assertEqual(len(faults),3);self.assertEqual(faults[0]['definitionRecordIndices'],[0])
            self.assertNotIn('definitionMatches',faults[0])
            self.assertEqual(len((out/'responses.jsonl').read_text().splitlines()),12)
            self.assertEqual(json.loads((out/'dictionary.json').read_text())['definitionCount'],2)
            self.assertEqual(r['source']['sourceCatalogId'],catalog)
            for n,h in r['artifacts'].items():self.assertEqual(hashlib.sha256((out/n).read_bytes()).hexdigest(),h)
            with self.assertRaises(FileExistsError):write_analysis(self.root/'normal.pcapng',root,catalog,'981',9,'version-a',0,'selected',out)
            with self.assertRaisesRegex(ValueError,'capture-changed-reopen'):
                write_analysis(self.root/'normal.pcapng',root,catalog,'981',9,'version-a',0,'selected',root/'changed','0'*64)
            self.assertFalse((root/'changed/report.json').exists())


if __name__=='__main__':unittest.main()
