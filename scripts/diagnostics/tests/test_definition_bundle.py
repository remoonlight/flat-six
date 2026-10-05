import hashlib
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
