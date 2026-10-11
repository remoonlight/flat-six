"""Cross-language PCAPNG fixtures from the production JS writer; no device IO."""
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from scripts.diagnostics.coding_capture import analyze_capture, scan_capture, load_plan
from scripts.diagnostics.coding_read_plan import coding_read_plan
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.capture_clock import CAPTURE_CLOCK


class CodingCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='flat-six-coding-capture-');cls.root=Path(cls.temp.name)
        fixture=[('62F100010203FFFF',{}),('62F100010203AABB',{}),('7F2231',{}),('62F101010203',{}),
            ('62F10001',{}),('61A400',{'requestHex':'21A4'}),('61A401',{'requestHex':'21A4','runId':'other'}),
            ('61A402',{'requestHex':'21A4','targetProfileId':'other'}),
            ('62F100010203',{'uniqueId':7}),('62F100010203',{'duringCleanup':True})]
        script='''import fs from 'node:fs';
import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const {root,fixture}=JSON.parse(fs.readFileSync(0,'utf8'));
for (const synthetic of [false,true]) {
 const writer=createDiagnosticPduWriter(root+'/'+(synthetic?'synthetic':'normal')+'.pcapng',synthetic);
 for(const [pduHex,extra] of fixture){
  const {runId='selected',targetProfileId='known',...metadata}=extra;
  writer.append({requestHex:'22F100',pduHex,timestampUs:1000000,timestampSource:'host-pdu-observed',
   transportKind:'d-pdu-iso-tp',observation:'native-result-data',duringCleanup:false,primitive:1,
   uniqueId:1,expectedPrimitive:1,flagsHex:'00000000',...metadata},{runId,targetProfileId,simulation:false});
 }
 writer.finish('software-fixture-no-device');
}
const clocked=createDiagnosticPduWriter(root+'/clocked.pcapng',false);
clocked.append({requestHex:'22F100',pduHex:'62F100010203',timestampUs:1000000,startedTimestampUs:9000000,
 monotonicClock:process.platform==='darwin'?'darwin-continuous':process.platform==='win32'?'windows-qpc':'posix-monotonic',
 monotonicTimestampUs:3000000,startedMonotonicTimestampUs:2000000,timestampSource:'host-pdu-observed',
 transportKind:'elm-iso-tp',observation:'reassembled-complete-pdu',duringCleanup:false},
 {runId:'selected',targetProfileId:'known',simulation:false});
clocked.finish('software-clock-rollback');'''
        run=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps({'root':str(cls.root),'fixture':fixture}),
            text=True,capture_output=True,timeout=15)
        if run.returncode:raise AssertionError(run.stderr)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def plan(self):
        lid=field('b','21A4');lid.update(readSID=0x21,pid=0xA4)
        return coding_read_plan({'profile_id':'known','module':'DME','name':'version','generation':'981',
            'pool_records':{'coding':{'records':[field('a',offset=2),lid]}}})

    def test_clock_rollback_metadata_survives_actual_writer_and_parser(self):
        rows=[];report=scan_capture(self.root/'clocked.pcapng',rows.append)
        self.assertTrue(report['prefixComplete']);self.assertEqual(report['packetErrors'],0)
        self.assertEqual(report['packets'],1);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['context']['timestampUs'],1000000)
        self.assertEqual(rows[0]['context']['startedTimestampUs'],9000000)
        self.assertEqual(rows[0]['context']['monotonicClock'],CAPTURE_CLOCK)
        self.assertEqual(rows[0]['context']['startedMonotonicTimestampUs'],2000000)

    def test_groups_repeats_changes_and_failures_preserved_without_baseline(self):
        rows=[];report=analyze_capture(self.root/'normal.pcapng',self.plan(),'selected',rows.append)
        self.assertTrue(report['capture']['prefixComplete']);self.assertEqual(report['capture']['packets'],10)
        self.assertTrue(report['capture']['endStatisticsPresent']);self.assertEqual(len(rows),8)
        self.assertEqual(report['counts']['candidate-block'],3);self.assertTrue(report['allDefinedGroupsObserved'])
        self.assertEqual(len(report['candidateDataHashes']['22F100']),2)
        self.assertEqual([r['dataHex'] for r in rows if r['status']=='candidate-block'],['010203FFFF','010203AABB','00'])
        self.assertTrue(any(r['pduHex']=='7F2231' for r in rows))
        for key in ('identityQualified','requestedTargetIsResponderProof','freshReadProven','completeOriginalBackup','baselineCreated','executionEnabled'):
            self.assertFalse(report[key])
        self.assertEqual(report['counts']['native-result-unqualified'],1)
        self.assertEqual(report['counts']['cleanup-response-unqualified'],1)
        self.assertTrue(all(r['context']['runId']=='selected' for r in rows))

    def test_no_cross_run_fill_and_synthetic_header_overrides_context_claim(self):
        rows=[];report=analyze_capture(self.root/'normal.pcapng',self.plan(),'other',rows.append)
        self.assertEqual(report['missingReadRequests'],['22F100']);self.assertEqual(len(rows),1)
        rows=[];report=analyze_capture(self.root/'synthetic.pcapng',self.plan(),'selected',rows.append)
        self.assertEqual(report['counts'],{'synthetic-capture-excluded':8});self.assertFalse(report['allDefinedGroupsObserved'])
        self.assertTrue(report['capture']['syntheticSection'])

    def test_truncated_packet_and_statistics_keep_valid_prefix(self):
        data=(self.root/'normal.pcapng').read_bytes();pos=0;packets=[]
        while pos<len(data):
            kind,length=struct.unpack_from('<II',data,pos)
            if kind==6:packets.append(pos)
            pos+=length
        for cut,count in ((packets[-1]+30,9),(len(data)-1,10)):
            file=self.root/f'truncated-{cut}.pcapng';file.write_bytes(data[:cut]);rows=[]
            report=scan_capture(file,rows.append)
            self.assertFalse(report['prefixComplete']);self.assertEqual(len(rows),count)
            self.assertIn('truncated',report['error']);self.assertFalse(report['endStatisticsPresent'])

    def test_wrong_layer_bad_tlv_or_bad_context_does_not_become_coding_data(self):
        data=(self.root/'normal.pcapng').read_bytes()
        for old,new,packets,errors in ((b'\xfc\x00\x00\x00\x00\x20\x00\x00',b'\xe3\x00\x00\x00\x00\x20\x00\x00',0,0),
            (b'\x00\x0c\x00\x04data',b'\x00\x0c\x00\x04xxxx',10,10),
            (b'"timestampUs":1000000',b'"timestampUs":2000000',10,10)):
            self.assertIn(old,data);file=self.root/f'bad-{errors}-{packets}-{new.hex()[:10]}.pcapng'
            file.write_bytes(data.replace(old,new));rows=[];report=scan_capture(file,rows.append)
            self.assertEqual(report['packets'],packets);self.assertEqual(report['packetErrors'],errors)
            self.assertTrue(all('error' in row for row in rows))

    def test_source_hash_is_computed_from_parsed_bytes_not_caller_plan(self):
        file=self.root/'variants.jsonl';file.write_text(json.dumps({'profile_id':'known'})+'\n')
        with self.assertRaisesRegex(ValueError,'source-hash-mismatch'):load_plan(file,'known','981')

    def test_foreign_or_legacy_primitive_preserves_raw_without_candidate_blocks(self):
        data=(self.root/'normal.pcapng').read_bytes()
        for replacement,reason in ((b'"expectedPrimitive":2','native-primitive-mismatch'),
            (b'"unusedPropertyABC":1','native-primitive-context-missing')):
            self.assertEqual(len(replacement),len(b'"expectedPrimitive":1'))
            file=self.root/(reason+'.pcapng');file.write_bytes(data.replace(b'"expectedPrimitive":1',replacement))
            rows=[];report=analyze_capture(file,self.plan(),'selected',rows.append)
            self.assertEqual(report['missingReadRequests'],['22F100','21A4']);self.assertEqual(len(rows),8)
            self.assertEqual(report['counts'][reason],7);self.assertEqual(report['candidateDataHashes'],{})
            self.assertEqual(rows[0]['pduHex'],'62F100010203FFFF')

    def test_cli_inventory_requires_no_identity_assertion_or_definition_file(self):
        run=subprocess.run([sys.executable,'-B','-m','scripts.diagnostics.coding_capture',
            '--capture',str(self.root/'normal.pcapng'),'--list-sessions'],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr);result=json.loads(run.stdout)
        self.assertEqual(len(result['sessions']),3);self.assertEqual(result['capture']['packets'],10)
        self.assertFalse(result['baselineCreated']);self.assertFalse(result['freshReadProven'])

    def test_file_change_fails_without_qualifying_prefix(self):
        file=self.root/'changing.pcapng';shutil.copyfile(self.root/'normal.pcapng',file)
        def change(row):
            if row['packet']==1:
                with file.open('ab') as output:output.write(b'changed')
        with self.assertRaisesRegex(ValueError,'changed-during-analysis'):scan_capture(file,change)


if __name__=='__main__':unittest.main()
