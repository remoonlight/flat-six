from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.diagnostics import coding_capture_bridge as bridge
from scripts.diagnostics.identity_application_capture import catalog_scope
from scripts.diagnostics.source_catalog import GUARDS, PREFIX, _json_bytes
from scripts.diagnostics.offline_match import ANCHORS
from scripts.diagnostics.tests import test_identity_application_capture as fixtures


class IdentityCaptureBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ApplicationIdentityTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ApplicationIdentityTests.tearDownClass()

    def library(self, root):
        entries, refs = {}, []
        for _, original in fixtures.ApplicationIdentityTests().bindings():
            profile = deepcopy(original)
            profile.update(ecuIds=[2], generations=['981', '982'])
            profile['identityGroups'][0]['fields'] *= 15
            name = 'profiles/' + hashlib.sha256(profile['profileId'].encode()).hexdigest() + '.json'
            entries[name] = _json_bytes(profile)
            refs.append({'profileId': profile['profileId'], 'file': name})
        entries['all-units.json'] = _json_bytes({'units': [dict(generation=g, ecuId=2, variants=refs) for g in ('981', '982')]})
        code = Path('scripts/diagnostics/identity_application_capture.py')
        manifest = dict(schemaVersion=1, kind='x431-offline-source-catalog',
            sourceFingerprints=[{'sha256': ANCHORS['variants']}],
            generatorCode=[{'name': code.as_posix(), 'sha256': bridge.digest(code)}],
            files=[{'name': n, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()} for n, b in entries.items()], **GUARDS)
        raw = _json_bytes(manifest)
        catalog = hashlib.sha256(raw).hexdigest()
        folder = root / PREFIX / catalog
        for name, data in {**entries, 'manifest.json': raw}.items():
            file = folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        return catalog, folder

    def analysis(self, root, **changes):
        catalog, _ = self.library(root / 'library')
        capture = fixtures.ApplicationIdentityTests.root / 'sim.pcapng'
        request = {'kind': 'identity', 'capture': str(capture), 'captureSha256': bridge.digest(capture),
            'sourceCatalogId': catalog, 'generation': '981', 'ecuId': 2, 'captureTargetProfileId': 'source-pdk',
            'runId': 'selected', 'output': str(root / 'result'), **changes}
        with patch.dict(os.environ, {'PORSCHE981_DEFINITION_ROOT': str(root / 'library')}):
            result = bridge.analyze(request)
        return {**request, 'artifact': result['artifact']}, result

    def test_full_candidate_streams_paging_and_complete_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request, result = self.analysis(root)
            report = result['report']
            self.assertEqual(report['candidateVersionCount'], 2)
            self.assertEqual(report['definitionFieldCount'], 30)
            self.assertEqual(report['fieldObservationCounts']['synthetic-independent-agreement'], 60)
            self.assertEqual(report['fieldObservationCounts']['synthetic-failed-or-disagreed'], 150)
            self.assertEqual(len(report['profiles'][0]['conflictingFields']), 15)
            self.assertTrue(all(not p['observedFieldsWithAgreement'] for p in report['profiles']))
            first = bridge.page({**request, 'stream': 'values', 'cursor': 0})
            self.assertEqual(len(first['rows']), 20)
            second = bridge.page({**request, 'stream': 'values', 'cursor': first['nextCursor']})
            self.assertEqual(second['rows'][0]['sourceFieldIndex'], 5)
            self.assertEqual(len(bridge.page({**request, 'stream': 'responses', 'cursor': 0})['rows']), 7)
            saved = root / 'full.zip'
            bridge.export({**request, 'destination': str(saved)})
            with zipfile.ZipFile(saved) as z:
                self.assertEqual(set(z.namelist()), {'report.json', 'responses.jsonl', 'identity-values.jsonl', 'identity-profiles.json'})
                self.assertEqual(len(z.read('identity-values.jsonl').splitlines()), 210)
                report = json.loads(z.read('report.json'))
                self.assertIsNone(report['selectedVehicleVariant'])
                self.assertFalse(report['completeIdentityQualified'])
                self.assertTrue(any(r['path'].endswith('coding_capture_bridge.py') for r in report['code']))

    def test_scope_shapes_and_both_generations_via_real_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, _ = self.library(root)
            request = {'kind': 'identity', 'action': 'scope', 'sourceCatalogId': catalog, 'generation': '981', 'ecuId': 2}
            def call(changes):
                p = subprocess.run([sys.executable, '-B', '-m', 'scripts.diagnostics.coding_capture_bridge'],
                    input=json.dumps({**request, **changes}), text=True, capture_output=True, timeout=15,
                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PORSCHE981_DEFINITION_ROOT': str(root)}, check=True)
                return json.loads(p.stdout)
            for generation in ('981', '982'):
                result = call({'generation': generation})
                self.assertTrue(result['ok'])
                self.assertEqual(result['candidateVersionCount'], 2)
                self.assertEqual(len(result['captureTargets']), 2)
                self.assertFalse(result['ecuVersionQualified'])
            for change in ({'ecuId': True}, {'ecuId': 1}, {'profileId': 'source-pdk'}, {'generation': '991'}, {'kind': 'coding'}):
                self.assertFalse(call(change)['ok'])

    def test_capture_change_and_cross_unit_reject_before_output(self):
        for change, reason in [({'captureSha256': '0' * 64}, 'changed-reopen'),
                ({'ecuId': 1}, 'unit-not-unique-or-missing'),
                ({'captureTargetProfileId': 'porsche-981-2014-dme'}, 'target-source-mismatch')]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaisesRegex(ValueError, reason):
                    self.analysis(root, **change)
                self.assertFalse((root / 'result').exists())

    def test_tampered_source_and_artifact_never_export_partial_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, folder = self.library(root / 'source')
            next((folder / 'profiles').iterdir()).write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'file-hash-mismatch'):
                catalog_scope(root / 'source', catalog, '981', 2)
            request, _ = self.analysis(root)
            for stream in ('faults', '../report'):
                with self.assertRaisesRegex(ValueError, 'stream-invalid'):
                    bridge.page({**request, 'stream': stream, 'cursor': 0})
            destination = root / 'saved.zip'
            destination.write_bytes(b'keep')
            (root / 'result/identity-profiles.json').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'artifact-changed'):
                bridge.export({**request, 'destination': str(destination)})
            self.assertEqual(destination.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
