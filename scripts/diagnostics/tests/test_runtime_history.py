from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / 'scripts/restore-obd-runtime-history.py'


class RuntimeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.local', prefix='runtime-history-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, path='runtime.txt', corrupt=False):
        payload = b'verified-runtime'; sha = hashlib.sha256(payload).hexdigest()
        manifest = json.dumps(dict(files=[dict(path=path, bytes=len(payload), sha256=sha)])).encode()
        index = dict(kind='verified-obd-runtime-history', packages=[dict(path='old-package',
            manifestEntry='manifests/000.json', manifestSha256=hashlib.sha256(manifest).hexdigest())])
        archive = self.root / 'history.zip'
        with zipfile.ZipFile(archive, 'x') as z:
            z.writestr('index.json', json.dumps(index)); z.writestr('manifests/000.json', manifest)
            z.writestr('objects/' + sha, b'x' * len(payload) if corrupt else payload)
        return archive

    def run_restore(self, archive):
        return subprocess.run([sys.executable, '-B', str(TOOL), '--archive', str(archive),
            '--package', 'old-package', '--output', str(self.root / 'restored')],
            capture_output=True, text=True, timeout=15)

    def test_restores_exact_bytes_without_starting_runtime(self):
        result = self.run_restore(self.archive())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'restored/runtime.txt').read_bytes(), b'verified-runtime')
        self.assertFalse(json.loads(result.stdout)['runtimeStarted'])

    def test_existing_output_is_never_replaced(self):
        output = self.root / 'restored'; output.mkdir(); (output / 'user.txt').write_text('keep')
        result = self.run_restore(self.archive())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((output / 'user.txt').read_text(), 'keep')
        self.assertEqual(list(output.iterdir()), [output / 'user.txt'])

    def test_archive_traversal_rejected_before_any_output(self):
        result = self.run_restore(self.archive('../outside.txt'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'restored').exists())
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_corrupt_object_removes_only_new_partial_output(self):
        result = self.run_restore(self.archive(corrupt=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'restored').exists())
        self.assertTrue((self.root / 'history.zip').is_file())


if __name__ == '__main__':
    unittest.main()
