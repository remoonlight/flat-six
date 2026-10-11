import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.identity_application_capture import review, selected_profiles
from scripts.diagnostics.tests import test_identity_capture as historical_fixtures


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
