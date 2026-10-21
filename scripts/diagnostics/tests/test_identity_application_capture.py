import json
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.identity_application_capture import review, selected_profiles
from scripts.diagnostics.tests import test_identity_capture as historical_fixtures
from scripts.diagnostics.tests import test_identity_version_capture as version_fixtures


class ApplicationIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        script = """import fs from 'node:fs';import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const root=JSON.parse(fs.readFileSync(0,'utf8'));
for(const synthetic of [true,false]){const w=createDiagnosticPduWriter(root+'/'+(synthetic?'sim':'observed')+'.pcapng',synthetic);
const pdus=['62F1002A','62F1002B','7F2231','7F2278','62F100','62F1002A','62F1002A','62F1002A','62F1002A'];
for(const [i,pduHex] of pdus.entries())w.append({requestHex:'22F100',pduHex,timestampUs:1000000+i,timestampSource:'host-pdu-observed',
transportKind:'d-pdu-iso-tp',observation:'native-result-data',primitive:i===5?2:1,expectedPrimitive:1,uniqueId:1,flagsHex:'00000000',duringCleanup:i===6},
{targetProfileId:i===8?'other-target':'source-pdk',runId:i===7?'other-run':'selected',simulation:synthetic});w.finish('injected fixture; no device IO');}
"""
        subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps(str(cls.root)),
            capture_output=True, text=True, check=True, timeout=15)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def bindings(self):
        unit, profile, _, _ = historical_fixtures.IdentityCaptureTests().fixtures()
        second = {**profile, 'profileId': 'another-pdk-source', 'variant': 'other-source-version'}
        return [(unit, profile), (unit, second)]

    def run_review(self, file='observed', capture=None):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'result'
            result = review(capture or self.root / (file + '.pcapng'), self.bindings(), {},
                '981', 2, 'source-pdk', 'selected', output)
            files = {p.name: p.read_bytes() for p in output.iterdir()}
        return result, files

    def test_all_candidate_versions_keep_repeats_failures_and_conflicting_values(self):
        result, files = self.run_review()
        self.assertEqual(result['candidateVersionCount'], 2)
        self.assertEqual(result['matchedPackets'], 7)
        self.assertEqual(len(files['responses.jsonl'].splitlines()), 7)
        values = [json.loads(line) for line in files['identity-values.jsonl'].splitlines()]
        self.assertEqual(len(values), 14)
        for profile in result['profiles']:
            self.assertEqual(profile['observedFieldsWithAgreement'], 1)
            self.assertEqual(profile['conflictingFields'], [{'source': 'observed', 'requestHex': '22F100', 'fieldIndex': 0}])
        for pid in ('source-pdk', 'another-pdk-source'):
            selected = [v for v in values if v['sourceProfileId'] == pid]
            self.assertEqual([v['value']['value'] for v in selected[:2]], [42, 43])
            self.assertTrue(all(not v['value']['ok'] for v in selected[2:]))
            self.assertTrue(all(v['captureTargetProfileId'] == 'source-pdk' and v['runId'] == 'selected' for v in selected))
        self.assertIsNone(result['selectedVehicleVariant'])
        self.assertFalse(result['completeIdentityQualified'])
        self.assertFalse(result['ecuVersionQualified'])
        self.assertEqual(set(files), {'responses.jsonl', 'identity-values.jsonl', 'identity-profiles.json', 'report.json'})

    def test_synthetic_agreement_never_supplies_observed_identity(self):
        result, files = self.run_review('sim')
        for profile in result['profiles']:
            self.assertEqual(profile['observedFieldsWithAgreement'], 0)
            self.assertEqual(profile['syntheticFieldsWithAgreement'], 1)
            self.assertEqual(len(profile['missingObservedFields']), 1)
        self.assertTrue(all(json.loads(line)['synthetic'] for line in files['identity-values.jsonl'].splitlines()))

    def test_unknown_target_unit_and_generation_reject_before_output(self):
        for generation, ecu, target in [('982', 2, 'source-pdk'), ('981', True, 'source-pdk'),
                ('981', 2, 'porsche-981-2014-dme'), ('981', 9, 'source-pdk')]:
            with self.assertRaises(ValueError):
                selected_profiles(self.bindings(), generation, ecu, target)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'none'
            with self.assertRaisesRegex(ValueError, 'target-source-mismatch'):
                review(self.root / 'sim.pcapng', self.bindings(), {}, '981', 2, 'other-target', 'selected', output)
            self.assertFalse(output.exists())

    def test_incomplete_tail_keeps_full_prefix_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'truncated.pcapng'
            capture.write_bytes((self.root / 'sim.pcapng').read_bytes()[:-1])
            result, files = self.run_review(capture=capture)
            self.assertFalse(result['capture']['prefixComplete'])
            self.assertEqual(len(files['responses.jsonl'].splitlines()), 7)

    def test_missing_end_statistics_keeps_values_but_blocks_version_rule_input(self):
        data=(self.root/'observed.pcapng').read_bytes();pos=0
        while pos<len(data):
            kind,length=struct.unpack_from('<II',data,pos)
            if kind==5:break
            pos+=length
        self.assertEqual(kind,5)
        with tempfile.TemporaryDirectory() as tmp:
            capture=Path(tmp)/'unclosed.pcapng';capture.write_bytes(data[:pos])
            result,files=self.run_review(capture=capture)
        self.assertTrue(result['capture']['prefixComplete'])
        self.assertFalse(result['capture']['endStatisticsPresent'])
        self.assertFalse(result['versionRuleComparison']['captureIntegrityComplete'])
        self.assertEqual(len(files['responses.jsonl'].splitlines()),7)
        self.assertEqual(len(files['identity-values.jsonl'].splitlines()),14)

    def test_complete_version_candidates_are_withheld_without_the_closing_block(self):
        profiles,language=version_fixtures.CaptureVersionRuleTests().fixture()
        bindings=self.bindings()
        bindings[0][1]['versionRoutes']=profiles[0]['versionRoutes']
        script="""import fs from 'node:fs';import {createDiagnosticPduWriter} from './apps/desktop/electron/diagnostic-pdu-data.mjs';
const file=JSON.parse(fs.readFileSync(0,'utf8'));const writer=createDiagnosticPduWriter(file,false);
for(const [requestHex,pduHex] of [['22F187','62F1874100424344'],['22F189','62F18901']])writer.append({requestHex,pduHex,timestampUs:1000000,
timestampSource:'host-pdu-observed',transportKind:'elm-iso-tp',observation:'reassembled-complete-pdu',duringCleanup:false},
{targetProfileId:'source-pdk',runId:'selected',simulation:false});writer.finish('software fixture');"""
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);capture=root/'closed.pcapng'
            subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(str(capture)),
                capture_output=True,text=True,check=True,timeout=15)
            with patch('scripts.diagnostics.x431_variant_selection._load_version_language',return_value=language):
                closed=review(capture,bindings,{},'981',2,'source-pdk','selected',root/'closed-output')
            report=closed['versionRuleComparison']['reports'][0]
            self.assertTrue(report['calculationComplete']);self.assertEqual(report['matchingCandidateCount'],2)
            data=capture.read_bytes();pos=0
            while pos<len(data):
                kind,length=struct.unpack_from('<II',data,pos)
                if kind==5:break
                pos+=length
            capture=root/'unclosed.pcapng';capture.write_bytes(data[:pos])
            with patch('scripts.diagnostics.identity_version_capture.audit_application_version_observations') as calculate:
                opened=review(capture,bindings,{},'981',2,'source-pdk','selected',root/'open-output')
            calculate.assert_not_called()
            report=opened['versionRuleComparison']['reports'][0]
            self.assertEqual(report['status'],'capture-integrity-incomplete')
            self.assertFalse(report['calculationComplete']);self.assertEqual(report['matchingCandidates'],[])
            self.assertEqual(len((root/'open-output/responses.jsonl').read_text().splitlines()),2)

    def test_independent_reference_failure_and_nonfinite_values_do_not_lose_raw(self):
        with patch('scripts.diagnostics.identity_capture.reference_value', side_effect=ValueError('unresolved')):
            result, files = self.run_review()
            self.assertEqual(result['profiles'][0]['observedFieldsWithAgreement'], 0)
            self.assertEqual(len(files['responses.jsonl'].splitlines()), 7)
        with patch('scripts.diagnostics.identity_application_capture.field_result', return_value={
                'value': {'ok': True, 'value': float('nan')}, 'independentDecoderAgreement': True}):
            result, files = self.run_review()
            self.assertEqual(result['profiles'][0]['observedFieldsWithAgreement'], 0)
            self.assertNotIn(b'NaN', files['identity-values.jsonl'])
            self.assertEqual(len(files['responses.jsonl'].splitlines()), 7)


if __name__ == '__main__':
    unittest.main()
