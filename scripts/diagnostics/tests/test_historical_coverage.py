from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.historical_coverage import HistoricalCoverage
from scripts.diagnostics.manufacturer_acquisition import DME_PROFILE
from scripts.diagnostics.tests.test_realtime_preparation import field


class HistoricalCoverageTests(unittest.TestCase):
    def mapped_fixture(self, root):
        source, bundle = root / 'source', root / 'bundle'
        match = source / 'analysis'; match.mkdir(parents=True); bundle.mkdir()
        record, parameter, group = self.fixtures()
        (match / 'parameters.jsonl').write_text(json.dumps(parameter) + '\n')
        (match / 'request-groups.json').write_text(json.dumps([group]))
        capture = source / 'capture.bin'; capture.write_bytes(b'original-private-fixture')
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        manifest = {'inputs': [{'path': 'D:/old/capture.bin', 'sha256': sha(capture)}],
                    'outputs': [{'path': 'D:/old/analysis/' + name, 'sha256': sha(match / name)}
                                for name in ('parameters.jsonl', 'request-groups.json')]}
        (match / 'manifest.json').write_text(json.dumps(manifest))
        def bind():
            (bundle / 'manifest.json').write_text(json.dumps({'inputs': [
                {'path': str(match / 'manifest.json'), 'sha256': sha(match / 'manifest.json')}]}))
        bind()
        return source, bundle, match, manifest, bind, record

    def test_migrated_originals_and_selected_outputs_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            source, bundle, match, _, _, record = self.mapped_fixture(Path(temp))
            loaded = HistoricalCoverage.load(bundle, match, original_source_root='D:/old', source_root=source)
            result = loaded.check(record, DME_PROFILE, '981', 1)
            self.assertEqual(result['status'], 'capture-consistent')
            self.assertFalse(result['liveVerified'])
            (match / 'parameters.jsonl').write_text('{}\n')
            with self.assertRaisesRegex(ValueError, 'source-hash-mismatch'):
                HistoricalCoverage.load(bundle, match, original_source_root='D:/old', source_root=source)

    def test_same_name_foreign_or_duplicate_output_never_binds_selected_file(self):
        for mutation in ('foreign', 'duplicate'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                source, bundle, match, manifest, bind, _ = self.mapped_fixture(Path(temp))
                if mutation == 'foreign':
                    manifest['outputs'][0]['path'] = 'D:/old/other/parameters.jsonl'
                else:
                    manifest['outputs'].append(dict(manifest['outputs'][0]))
                (match / 'manifest.json').write_text(json.dumps(manifest)); bind()
                with self.assertRaisesRegex(ValueError, 'selected-output-binding-required'):
                    HistoricalCoverage.load(bundle, match, original_source_root='D:/old', source_root=source)

    def test_foreign_input_and_wrong_bundle_manifest_binding_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source, bundle, match, manifest, bind, _ = self.mapped_fixture(Path(temp))
            manifest['inputs'][0]['path'] = 'D:/elsewhere/capture.bin'
            (match / 'manifest.json').write_text(json.dumps(manifest)); bind()
            with self.assertRaises(ValueError):
                HistoricalCoverage.load(bundle, match, original_source_root='D:/old', source_root=source)
            (bundle / 'manifest.json').write_text(json.dumps({'inputs': []}))
            with self.assertRaisesRegex(ValueError, 'match-manifest-binding-required'):
                HistoricalCoverage.load(bundle, match, original_source_root='D:/old', source_root=source)

    def fixtures(self):
        record = dict(field(), at=42)
        raw = bytes.fromhex('62F1002A')
        parameter = {**record, 'profileId': DME_PROFILE, 'definitionOffset': 42, 'requestHex': '22F100',
                     'identityQualified': True, 'ecuIds': [1], 'examples': [{'groupId': 'g', 'responseFrameId': 'r',
                     'pduSha256': hashlib.sha256(raw).hexdigest()}]}
        group = {'groupId': 'g', 'requestHex': '22F100', 'txId': 0x7E0, 'rxId': 0x7E8,
                 'adapterStream': [2, 2], 'capturePhase': 'before-coding',
                 'samples': [{'responseFrameId': 'r', 'pduHex': raw.hex(), 'requestFrameId': 'q',
                              'requestFrameSha256': 'a' * 64, 'responseFrameSha256': 'b' * 64}]}
        return record, parameter, group

    def test_exact_join_has_hashes_and_remains_unqualified_for_live(self):
        record, parameter, group = self.fixtures()
        result = HistoricalCoverage([parameter], [group]).check(record, DME_PROFILE, '981', 1)
        self.assertEqual(result['status'], 'capture-consistent')
        self.assertEqual(result['references'][0]['responseFrameSha256'], 'b' * 64)
        self.assertFalse(result['liveVerified']); self.assertFalse(result['independentAdapterVerified'])

    def test_changed_definition_request_pdu_and_offset_cannot_inherit_evidence(self):
        record, parameter, group = self.fixtures()
        coverage = HistoricalCoverage([parameter], [group])
        for key, value in [('bitOffset', 1), ('unit', 'other'), ('formula', 'IDENTICAL:BitLength=8;')]:
            changed = deepcopy(record); changed[key] = value
            self.assertEqual(coverage.check(changed, DME_PROFILE, '981', 1)['status'], 'conflict')
        self.assertEqual(coverage.check(dict(record, at=43), DME_PROFILE, '981', 1)['status'], 'definition-only')
        for key, value in [('requestHex', '22F101'), ('samples', [{'responseFrameId': 'r', 'pduHex': '62F1002B'}])]:
            changed = deepcopy(group); changed[key] = value
            self.assertEqual(HistoricalCoverage([parameter], [changed]).check(record, DME_PROFILE, '981', 1)['status'], 'conflict')

    def test_other_generation_unit_address_phase_and_stream_are_candidates(self):
        record, parameter, group = self.fixtures()
        coverage = HistoricalCoverage([parameter], [group])
        for generation, ecu in [('982', 1), ('981', 2)]:
            result = coverage.check(record, DME_PROFILE, generation, ecu)
            self.assertEqual(result['status'], 'capture-candidate')
            self.assertEqual(result['references'][0]['captureGeneration'], '981')
        for key, value in [('txId', 0x715), ('rxId', 0x77F), ('capturePhase', 'coding-workflow'), ('adapterStream', [1, 1])]:
            changed = deepcopy(group); changed[key] = value
            self.assertEqual(HistoricalCoverage([parameter], [changed]).check(record, DME_PROFILE, '981', 1)['status'], 'capture-candidate')
        parameter['profileId'] = '9x1:other-version'
        self.assertEqual(HistoricalCoverage([parameter], [group]).check(record, parameter['profileId'], '981', 1)['status'], 'capture-candidate')


if __name__ == '__main__': unittest.main()
