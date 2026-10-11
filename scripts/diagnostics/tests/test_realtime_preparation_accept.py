from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.realtime_preparation import dump, FLAGS, sha
from scripts.diagnostics.realtime_preparation_accept import validate_bundle, runtime_path
from scripts.diagnostics.tests.test_realtime_preparation import profile


class PortableAcceptanceTests(unittest.TestCase):
    def bundle(self, root):
        project = root / '程序 with spaces'; project.mkdir()
        code = project / 'scripts/client.py'; code.parent.mkdir(); code.write_text('# pinned code')
        bundle = root / '资料 bundle'; bundle.mkdir(); (bundle / 'profiles').mkdir()
        p = profile()
        for parameter in p['parameters']:
            parameter.update(replay=[], decodedSampleCount=0, nameResolved=True, **FLAGS)
            parameter['categories'][0]['sourceNamespace'] = 'DSTREAM_CN.GAG'
        file = 'profiles/' + 'a' * 64 + '.json'; dump(bundle / file, p)
        dump(bundle / 'index.json', {'profiles': [{'file': file, 'sha256': sha(bundle / file)}],
            'units': [2], 'counts': {'parameters': 2, 'identityRecords': 0, 'units': 1}})
        manifest = {'schemaVersion': 2, 'inputsAreProvenanceOnlyAtRuntime': True,
            'inputs': [{'path': '/absent/historical/Mac/source.json', 'sha256': '0' * 64}],
            'code': [{'path': 'scripts/client.py', 'sha256': sha(code)}],
            'outputs': [{'path': f, 'sha256': sha(bundle / f)} for f in (file, 'index.json')]}
        dump(bundle / 'manifest.json', manifest)
        return project, bundle, manifest

    def test_migrated_bundle_verifies_installed_code_and_outputs_without_original_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            project, bundle, _ = self.bundle(Path(temp))
            with patch('scripts.diagnostics.realtime_preparation_accept.ROOT', project):
                result = validate_bundle(bundle)
            self.assertTrue(result['ok']); self.assertEqual(result['counts']['parameters'], 2)
            self.assertTrue(all(result[key] == value for key, value in FLAGS.items()))

    def test_code_index_and_profile_corruption_are_rejected(self):
        for target in ('scripts/client.py', 'index.json', 'profiles/' + 'a' * 64 + '.json'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                project, bundle, _ = self.bundle(Path(temp))
                file = (project if target.startswith('scripts/') else bundle) / target
                file.write_bytes(file.read_bytes() + b' ')
                with patch('scripts.diagnostics.realtime_preparation_accept.ROOT', project):
                    with self.assertRaisesRegex(ValueError, 'source-hash-mismatch'): validate_bundle(bundle)

    def test_portable_contract_paths_and_profile_binding_are_required(self):
        for change, expected in [
                (lambda m: m.pop('inputsAreProvenanceOnlyAtRuntime'), 'contract-missing'),
                (lambda m: m['outputs'].append(m['outputs'][0]), 'duplicate-runtime-output'),
                (lambda m: m['outputs'].pop(0), 'profile-not-bound'),
                (lambda m: m['outputs'].pop(), 'index-not-bound'),
                (lambda m: m['outputs'][0].update(path='../escape.json'), 'manifest-path'),
                (lambda m: m['code'][0].update(path='scripts/../../escape.py'), 'manifest-path')]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                project, bundle, manifest = self.bundle(Path(temp)); change(manifest)
                dump(bundle / 'manifest.json', manifest)
                with patch('scripts.diagnostics.realtime_preparation_accept.ROOT', project):
                    with self.assertRaisesRegex(ValueError, expected): validate_bundle(bundle)

    def test_legacy_bundle_still_requires_original_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            project, bundle, manifest = self.bundle(Path(temp)); manifest['schemaVersion'] = 1
            dump(bundle / 'manifest.json', manifest)
            with self.assertRaises(FileNotFoundError): validate_bundle(bundle)

    def test_paths_reject_cross_platform_escapes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'root'; root.mkdir()
            for path in ('/abs/file', 'C:/file', 'C:file', '../file', 'a/../file', 'a//file', 'a/./file', 'a\\file', 'file\x00'):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'manifest-path'): runtime_path(root, path)
            try: (root / 'link').symlink_to(Path(temp), target_is_directory=True)
            except OSError as error:
                if isinstance(error, PermissionError) or getattr(error, 'winerror', None) == 1314: return
                raise
            with self.assertRaisesRegex(ValueError, 'outside-root'): runtime_path(root, 'link/escape')
