from contextlib import closing
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.diagnostics.hashutil import sha256_file
from scripts.diagnostics.precar import check_prerequisites, main, prepare, serial_runtime


class PreparationSnapshotTests(unittest.TestCase):
    def test_prepare_forwards_explicit_source_paths_and_cli_cannot_silently_ignore_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);output = root / '.local/ready'
            with patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok':True}), patch(
                    'scripts.diagnostics.precar.build_pack', return_value={'ok':True}) as build:
                prepare(output, repo=root, logical_sysdata=root/'SYS', decoded_dsn=root/'DSN', language_ggp=root/'GGP')
            build.assert_called_once_with(unittest.mock.ANY, output.resolve()/'collection',
                logical_sysdata=root/'SYS', decoded_dsn=root/'DSN', language_ggp=root/'GGP')
            for flag in ('--sysdata-logical', '--decoded-dsn', '--language-ggp'):
                with patch('sys.argv', ['tool', '--check-only', flag, 'unreviewed']), patch(
                        'scripts.diagnostics.precar.check_prerequisites') as check, patch('sys.stderr', io.StringIO()):
                    with self.assertRaises(SystemExit) as result:main()
                    self.assertEqual(result.exception.code, 2)
                    check.assert_not_called()
            with patch('sys.argv', ['tool', '--language-ggp', 'unreviewed']), patch(
                    'scripts.diagnostics.precar.prepare') as generate, patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as result:main()
                self.assertEqual(result.exception.code, 2);generate.assert_not_called()

    def test_snapshot_is_consistent_source_unchanged_and_original_code_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / '.local/diagnostics/coding-backups').mkdir(parents=True)
            db = root / '.local/garage.db'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('create table sample(id integer primary key, value text)')
                conn.execute("insert into sample(value) values ('private-test-only')"); conn.commit()
            backup = root / '.local/diagnostics/coding-backups/original.json'; backup.write_text('{"test":true}')
            db_hash = sha256_file(db); original_hash = sha256_file(backup)
            output = root / '.local/ready'
            with patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                 patch('scripts.diagnostics.precar.build_pack', return_value={'ok': True}):
                result = prepare(output, repo=root)
            self.assertEqual(result['developmentDb']['integrity'], 'ok')
            self.assertEqual(sha256_file(db), db_hash); self.assertEqual(sha256_file(backup), original_hash)
            self.assertEqual(sha256_file(output / 'coding-backups/original.json'), original_hash)
            with closing(sqlite3.connect(output / 'db-backup/garage.db')) as conn:
                self.assertEqual(conn.execute('select value from sample').fetchone()[0], 'private-test-only')
            self.assertTrue((output / '首次接车清单.md').is_file())
            self.assertFalse(result['vehicleValidated']); self.assertTrue(result['noDeviceIO'])
            with self.assertRaisesRegex(ValueError, 'already-exists'):
                prepare(output, repo=root)
            with self.assertRaisesRegex(ValueError, 'private-local'):
                prepare(root / 'public', repo=root)

    def test_bad_definitions_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / '.local/ready'
            with patch('scripts.diagnostics.precar.validate_bundle', side_effect=ValueError('hash-mismatch')):
                with self.assertRaisesRegex(ValueError, 'hash-mismatch'):
                    prepare(output, repo=root)
            self.assertFalse(output.exists())


class PrerequisiteTests(unittest.TestCase):
    def test_serial_api_rejects_shadow_package_and_metadata_mismatch_without_opening(self):
        valid = SimpleNamespace(__version__='3.5', Serial=unittest.mock.Mock(), SerialException=OSError)
        for module, metadata, expected in [
                (SimpleNamespace(__version__='3.5'), '3.5', 'api-invalid'),
                (SimpleNamespace(__version__='unknown', Serial=lambda: None, SerialException=OSError), '3.5', 'api-invalid'),
                (valid, '3.4', 'version-mismatch')]:
            with self.subTest(expected=expected), patch('scripts.diagnostics.precar.importlib.util.find_spec', return_value=object()), patch(
                    'scripts.diagnostics.precar.import_module', return_value=module), patch(
                    'scripts.diagnostics.precar.importlib.metadata.version', return_value=metadata):
                with self.assertRaisesRegex(ValueError, expected): serial_runtime()
        valid.Serial.assert_not_called()


    def test_all_adapters_runtime_readiness_does_not_claim_vehicle_or_x431_transport(self):
        def run(command, **kwargs):
            self.assertIn(command[-1], ('version', "require('node:sqlite');process.stdout.write(process.version)"))
            return subprocess.CompletedProcess(command, 0,
                'Android Debug Bridge version 1.0.41' if command[-1] == 'version' else 'v22.22.0')
        with patch('scripts.diagnostics.precar.sys.platform', 'win32'), \
                patch('scripts.diagnostics.precar.platform.machine', return_value='AMD64'), \
                patch('scripts.diagnostics.precar.subprocess.run', side_effect=run), \
                patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                patch('scripts.diagnostics.precar.importlib.util.find_spec', return_value=object()), \
                patch('scripts.diagnostics.precar.import_module', return_value=SimpleNamespace(__version__='3.5', Serial=lambda: None, SerialException=OSError)), \
                patch('scripts.diagnostics.precar.importlib.metadata.version', return_value='3.5'), \
                patch('scripts.diagnostics.precar.shutil.which', return_value='/tools/adb'), \
                patch('scripts.diagnostics.vnci.verify_vendor_files'), \
                patch('scripts.diagnostics.pt3g.verify_vendor_files'), \
                patch('scripts.diagnostics.vnci.NativeDpu') as vnci, \
                patch('scripts.diagnostics.pt3g.NativeAdapter') as pt3g, \
                patch('scripts.diagnostics.connection.collect_snapshot') as devices:
            result = check_prerequisites(env={}, include_all_adapters=True)
        self.assertEqual(len(result['adapters']), 5)
        self.assertTrue(result['allAdapterRuntimePrerequisitesReady'])
        self.assertFalse(result['allAdaptersUsable'])
        self.assertTrue(result['noDeviceIO'])
        self.assertFalse(any(row['vehicleValidated'] for row in result['adapters']))
        x431 = next(row for row in result['adapters'] if row['id'] == 'x431')
        self.assertTrue(x431['captureOnly'])
        self.assertFalse(x431['directProjectTransportImplemented'])
        self.assertFalse(any(row['runtimePrerequisitesReady'] for row in result['referenceCollectors']))
        vnci.assert_not_called(); pt3g.assert_not_called(); devices.assert_not_called()

    def test_all_adapters_collects_independent_missing_runtime_and_driver_failures(self):
        with patch('scripts.diagnostics.precar.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v22.22.0')), \
                patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                patch('scripts.diagnostics.precar.importlib.util.find_spec', return_value=None), \
                patch('scripts.diagnostics.precar.shutil.which', return_value=None), \
                patch('scripts.diagnostics.vnci.verify_vendor_files'), \
                patch('scripts.diagnostics.pt3g.verify_vendor_files', side_effect=ValueError('pt3g-ini-drift')):
            result = check_prerequisites(env={}, include_all_adapters=True)
        errors = {c['id']: c['error'] for c in result['checks'] if not c['ok']}
        self.assertEqual(errors['elm-pyserial-runtime'], 'pyserial-runtime-missing')
        self.assertEqual(errors['pt3g-driver-files'], 'pt3g-ini-drift')
        self.assertEqual(errors['x431-adb-capture-runtime'], 'adb-runtime-missing')
        self.assertTrue(result['offlinePackPrerequisitesReady'])
        self.assertFalse(result['allAdapterRuntimePrerequisitesReady'])

    def test_all_adapters_accepts_bundled_serial_without_metadata_but_rejects_unrecognized_adb(self):
        import importlib.metadata
        with patch('scripts.diagnostics.precar.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'unrelated executable')), \
                patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                patch('scripts.diagnostics.precar.importlib.util.find_spec', return_value=object()), \
                patch('scripts.diagnostics.precar.import_module', return_value=SimpleNamespace(__version__='3.5', Serial=lambda: None, SerialException=OSError)), \
                patch('scripts.diagnostics.precar.importlib.metadata.version', side_effect=importlib.metadata.PackageNotFoundError('pyserial')), \
                patch('scripts.diagnostics.precar.shutil.which', return_value='/tools/adb'), \
                patch('scripts.diagnostics.vnci.verify_vendor_files'), \
                patch('scripts.diagnostics.pt3g.verify_vendor_files'):
            result = check_prerequisites(env={}, include_all_adapters=True)
        errors = {c['id']: c['error'] for c in result['checks'] if not c['ok']}
        self.assertNotIn('elm-pyserial-runtime', errors)
        self.assertEqual(errors['x431-adb-capture-runtime'], 'adb-version-not-recognized')

    def test_all_adapters_requires_check_only_before_creating_pack(self):
        with patch('sys.argv', ['precar', '--all-adapters']), \
                patch('scripts.diagnostics.precar.prepare') as build, \
                patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        build.assert_not_called()

    def test_ready_means_only_prerequisites_and_never_loads_or_discovers(self):
        with patch('scripts.diagnostics.precar.sys.platform', 'win32'), \
                patch('scripts.diagnostics.precar.platform.machine', return_value='AMD64'), \
                patch('scripts.diagnostics.precar.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v22.22.0')), \
                patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                patch('scripts.diagnostics.vnci.verify_vendor_files') as verify, \
                patch('scripts.diagnostics.vnci.NativeDpu') as native, \
                patch('scripts.diagnostics.vnci.discover') as discover:
            result = check_prerequisites(env={'PORSCHE981_VNCI_ROOT': '/private-driver', 'PORSCHE981_NODE': '/private-node'})
            self.assertTrue(result['ok'])
            self.assertTrue(result['offlinePackPrerequisitesReady'])
            self.assertTrue(result['vnciUsbBenchPrerequisitesReady'])
            self.assertFalse(result['driverLoaded'])
            self.assertFalse(result['hardwarePresenceChecked'])
            self.assertFalse(result['vehicleValidated'])
            self.assertFalse(result['manufacturerLiveQualified'])
            verify.assert_called_once_with(Path('/private-driver').resolve())
            native.assert_not_called(); discover.assert_not_called()

    def test_all_independent_failures_are_reported_without_creating_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / 'missing-bundle'
            with patch('scripts.diagnostics.precar.sys.platform', 'darwin'), \
                    patch('scripts.diagnostics.precar.subprocess.run', side_effect=subprocess.TimeoutExpired('node', 10)), \
                    patch('scripts.diagnostics.vnci.verify_vendor_files', side_effect=ValueError('driver-hash-mismatch')):
                result = check_prerequisites(bundle=missing, env={})
            errors = {c['id']: c['error'] for c in result['checks'] if not c['ok']}
            self.assertEqual(len(result['checks']), 5)
            self.assertIn('node-sqlite-runtime', errors)
            self.assertIn('realtime-definitions', errors)
            self.assertIn('requires-Windows', errors['vnci-Windows-x64-runtime'])
            self.assertEqual(errors['vnci-driver-files'], 'driver-hash-mismatch')
            self.assertFalse(result['ok'])
            self.assertFalse(result['offlinePackPrerequisitesReady'])
            self.assertFalse(result['vnciUsbBenchPrerequisitesReady'])
            self.assertFalse(missing.exists())

    def test_arm_windows_does_not_qualify_x64_native_runtime(self):
        with patch('scripts.diagnostics.precar.sys.platform', 'win32'), \
                patch('scripts.diagnostics.precar.platform.machine', return_value='ARM64'), \
                patch('scripts.diagnostics.precar.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v22.22.0')), \
                patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                patch('scripts.diagnostics.vnci.verify_vendor_files'):
            result = check_prerequisites(env={})
            self.assertTrue(result['offlinePackPrerequisitesReady'])
            self.assertFalse(result['vnciUsbBenchPrerequisitesReady'])

    def test_old_python_reports_requirement_before_unsupported_hash_api(self):
        with patch('scripts.diagnostics.precar.sys.version_info', (3, 9, 6)), \
                patch('scripts.diagnostics.precar.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v22.22.0')), \
                patch('scripts.diagnostics.precar.validate_bundle', return_value={'ok': True}), \
                patch('scripts.diagnostics.vnci.verify_vendor_files') as verify:
            result = check_prerequisites(env={})
            errors = {c['id']: c.get('error') for c in result['checks']}
            self.assertEqual(errors['python-runtime'], 'python-3.11-or-newer-required')
            self.assertEqual(errors['vnci-driver-files'], 'python-runtime-required-before-driver-hash-check')
            verify.assert_not_called()

    def test_check_only_uses_requested_bundle_reports_exit_failure_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'must-not-create'
            bundle = Path(temp) / 'installed-definitions'
            stdout = io.StringIO()
            with patch('sys.argv', ['precar', '--check-only', '--bundle', str(bundle), '--output-dir', str(output)]), \
                    patch('scripts.diagnostics.precar.check_prerequisites', return_value={'ok': False}) as check, \
                    patch('scripts.diagnostics.precar.prepare') as build, redirect_stdout(stdout):
                self.assertEqual(main(), 1)
            self.assertEqual(json.loads(stdout.getvalue()), {'ok': False})
            check.assert_called_once_with(bundle=bundle)
            build.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
