import hashlib
import io
from contextlib import redirect_stdout
import errno
from types import SimpleNamespace
from unittest.mock import patch
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.diagnostics.definition_bundle import export_bundle, import_bundle

class DefinitionBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"; self.target = self.root / "target"
        self.file = self.source / "data/seed/diagnostics/catalog.v1.json"
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"offline":true}', encoding="utf8")
        private = self.source / ".local/diagnostics/coding-backups/private.json"
        private.parent.mkdir(parents=True); private.write_text('{"vin":"PRIVATE"}')
        self.bundle = self.root / "definitions.zip"

    def test_transfer_checks_hash_and_excludes_private_backups(self):
        result = export_bundle(self.bundle, self.source)
        self.assertEqual(result["files"], 1)
        result = import_bundle(self.bundle, self.target)
        self.assertEqual(result["imported"], 1)
        self.assertFalse((self.target / ".local/diagnostics/coding-backups/private.json").exists())
        self.assertEqual(import_bundle(self.bundle, self.target)["unchanged"], 1)
        with self.assertRaises(FileExistsError):
            export_bundle(self.bundle, self.source)

    def test_conflicting_existing_file_never_overwritten(self):
        export_bundle(self.bundle, self.source)
        file = self.target / self.file.relative_to(self.source)
        file.parent.mkdir(parents=True); file.write_text("existing user changes")
        with self.assertRaisesRegex(ValueError, "existing-file-conflict"):
            import_bundle(self.bundle, self.target)
        self.assertEqual(file.read_text(), "existing user changes")

    def crafted(self, name, content, sha=None):
        with zipfile.ZipFile(self.bundle, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"schemaVersion": 1, "kind": "local-diagnostic-definitions",
                "files": [{"name": name, "bytes": len(content), "sha256": sha or hashlib.sha256(content).hexdigest()}]}))
            archive.writestr(name, content)

    def test_traversal_and_executable_entries_rejected(self):
        for name in ("../outside.json", "data/seed/diagnostics/../../outside.json", "data/seed/diagnostics/run.py", "C:/outside.json"):
            self.crafted(name, b"test")
            with self.assertRaisesRegex(ValueError, "path-invalid"):
                import_bundle(self.bundle, self.target)

    def test_hash_failure_has_no_published_files(self):
        self.crafted("data/seed/diagnostics/test.json", b"changed", "0" * 64)
        with self.assertRaisesRegex(ValueError, "integrity-failed"):
            import_bundle(self.bundle, self.target)
        self.assertFalse((self.target / "data").exists())

    def test_low_space_rejected_before_directory_or_staging_creation(self):
        export_bundle(self.bundle, self.source)
        with patch('scripts.diagnostics.definition_bundle.shutil.disk_usage', return_value=SimpleNamespace(free=1)), patch('scripts.diagnostics.definition_bundle.tempfile.TemporaryDirectory') as temporary:
            with self.assertRaisesRegex(ValueError, 'bundle-insufficient-space') as caught:
                import_bundle(self.bundle, self.target)
            self.assertEqual(caught.exception.available_bytes, 1)
            self.assertEqual(caught.exception.required_payload_bytes, self.file.stat().st_size)
            temporary.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_identical_reimport_needs_no_staging_even_at_zero_reported_free_space(self):
        export_bundle(self.bundle, self.source);import_bundle(self.bundle, self.target)
        installed=self.target/self.file.relative_to(self.source);before=installed.read_bytes()
        with patch('scripts.diagnostics.definition_bundle.shutil.disk_usage', return_value=SimpleNamespace(free=0)) as usage, patch('scripts.diagnostics.definition_bundle.tempfile.TemporaryDirectory') as temporary:
            result=import_bundle(self.bundle,self.target)
            temporary.assert_not_called();usage.assert_not_called()
        self.assertEqual(result['stagedPayloadBytes'],0);self.assertEqual(result['unchanged'],1)
        self.assertEqual(installed.read_bytes(),before)

    def test_identical_installed_target_does_not_hide_corrupted_incoming_archive(self):
        name=self.file.relative_to(self.source).as_posix();self.target.joinpath(name).parent.mkdir(parents=True)
        self.target.joinpath(name).write_bytes(b'installed')
        self.crafted(name,b'corrupted',hashlib.sha256(b'installed').hexdigest())
        with patch('scripts.diagnostics.definition_bundle.tempfile.TemporaryDirectory') as temporary:
            with self.assertRaisesRegex(ValueError,'bundle-integrity-failed'):import_bundle(self.bundle,self.target)
            temporary.assert_not_called()
        self.assertEqual(self.target.joinpath(name).read_bytes(),b'installed')

    def test_mixed_import_stages_only_new_payload_and_keeps_all_files_verified(self):
        export_bundle(self.bundle,self.source);import_bundle(self.bundle,self.target)
        extra=self.source/'data/seed/diagnostics/new.json';extra.write_bytes(b'new')
        self.bundle.unlink();export_bundle(self.bundle,self.source)
        with patch('scripts.diagnostics.definition_bundle.shutil.disk_usage',return_value=SimpleNamespace(free=3)):
            result=import_bundle(self.bundle,self.target)
        self.assertEqual((result['imported'],result['unchanged'],result['stagedPayloadBytes']),(1,1,3))
        self.assertEqual(self.target.joinpath(extra.relative_to(self.source)).read_bytes(),b'new')

    def test_corrupt_existing_archive_entry_prevents_publishing_other_new_file(self):
        old='data/seed/diagnostics/existing.json';new='data/seed/diagnostics/new.json'
        self.target.joinpath(old).parent.mkdir(parents=True);self.target.joinpath(old).write_bytes(b'original')
        with zipfile.ZipFile(self.bundle,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'schemaVersion':1,'kind':'local-diagnostic-definitions','files':[
                {'name':new,'bytes':3,'sha256':hashlib.sha256(b'new').hexdigest()},
                {'name':old,'bytes':8,'sha256':hashlib.sha256(b'original').hexdigest()}]}))
            archive.writestr(new,b'new');archive.writestr(old,b'bad-data')
        with self.assertRaisesRegex(ValueError,'bundle-integrity-failed'):import_bundle(self.bundle,self.target)
        self.assertFalse(self.target.joinpath(new).exists());self.assertEqual(self.target.joinpath(old).read_bytes(),b'original')

    def test_publish_space_failure_rolls_back_new_links_and_preserves_existing(self):
        export_bundle(self.bundle,self.source);import_bundle(self.bundle,self.target)
        for n in ('one','two'):(self.source/f'data/seed/diagnostics/{n}.json').write_bytes(n.encode())
        self.bundle.unlink();export_bundle(self.bundle,self.source)
        import os
        actual=os.link;calls=[]
        def link(source,target):
            calls.append(target)
            if len(calls)==2:raise OSError(errno.ENOSPC,'simulated disk full')
            return actual(source,target)
        with patch('scripts.diagnostics.definition_bundle.os.link',side_effect=link):
            with self.assertRaises(OSError):import_bundle(self.bundle,self.target)
        self.assertFalse(self.target.joinpath('data/seed/diagnostics/one.json').exists());self.assertFalse(self.target.joinpath('data/seed/diagnostics/two.json').exists())
        self.assertEqual(self.target.joinpath(self.file.relative_to(self.source)).read_bytes(),self.file.read_bytes())

    def test_cli_space_error_preserves_required_and_available_bytes(self):
        from scripts.diagnostics.definition_bundle import main,BundleSpaceError
        output=io.StringIO()
        with patch('scripts.diagnostics.definition_bundle.sys.stdin',io.StringIO('{"action":"import","file":"explicit.zip"}')), patch('scripts.diagnostics.definition_bundle.import_bundle',side_effect=BundleSpaceError(2,9)),redirect_stdout(output):
            self.assertEqual(main(),2)
        result=json.loads(output.getvalue());self.assertEqual(result['error'],'bundle-insufficient-space')
        self.assertEqual((result['availableBytes'],result['requiredPayloadBytes']),(2,9));self.assertFalse(result['liveVerified'])

    def test_cli_actual_os_space_error_has_stable_ui_code(self):
        from scripts.diagnostics.definition_bundle import main
        output=io.StringIO()
        with patch('scripts.diagnostics.definition_bundle.sys.stdin',io.StringIO('{"action":"import","file":"explicit.zip"}')), patch('scripts.diagnostics.definition_bundle.import_bundle',side_effect=OSError(errno.ENOSPC,'disk full')),redirect_stdout(output):
            self.assertEqual(main(),2)
        result=json.loads(output.getvalue());self.assertEqual(result['error'],'ENOSPC');self.assertFalse(result['liveVerified'])

    def test_aggregate_size_cap_remains_enforced_before_new_files_are_written(self):
        export_bundle(self.bundle,self.source)
        with patch('scripts.diagnostics.definition_bundle.MAX_TOTAL',1):
            with self.assertRaisesRegex(ValueError,'bundle-size-limit'):import_bundle(self.bundle,self.target)
        self.assertFalse(self.target.exists())
