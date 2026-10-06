from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.diagnostics.user_data_backup import backup, inventory, restore


class InstalledBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = self.root / '用户目录'; self.user.mkdir()
        with closing(sqlite3.connect(self.user / 'garage.db')) as db:
            db.execute('CREATE TABLE records(value TEXT)')
            db.execute("INSERT INTO records VALUES ('已保存车辆结果')"); db.commit()
        for name in ['diagnostics/connection.json', 'diagnostics/coding-backups/baseline.json',
                     'diagnostics/raw-captures/test.pcapng', 'diagnostic-library/.local/definition.json',
                     'local-assets/model-oem-links.json', 'Cache/not-business-data']:
            path = self.user / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private-test-only', encoding='utf-8')
        self.saved, self.restored = self.root / 'backup', self.root / 'restored'

    def save(self):
        return backup(self.user, self.saved, app_closed=True)

    def test_roundtrip_preserves_all_business_files_and_source(self):
        before = inventory(self.user)
        self.assertTrue(self.save()['ok'])
        self.assertEqual(inventory(self.user), before)
        self.assertTrue(restore(self.saved, self.restored, app_closed=True)['ok'])
        self.assertEqual(inventory(self.restored), before)
        self.assertFalse((self.restored / 'Cache').exists())
        with closing(sqlite3.connect(self.restored / 'garage.db')) as db:
            self.assertEqual(db.execute('SELECT value FROM records').fetchone()[0], '已保存车辆结果')
        with self.assertRaisesRegex(ValueError, 'already-exists'):
            restore(self.saved, self.restored, app_closed=True)
        self.assertEqual(inventory(self.restored), before)
        with self.assertRaisesRegex(ValueError, 'already-exists'):
            self.save()

    def test_close_declaration_and_overlap(self):
        with self.assertRaisesRegex(ValueError, 'close-application'):
            backup(self.user, self.saved)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            backup(self.user, self.user / 'nested', app_closed=True)
        self.assertFalse((self.user / 'nested').exists())

    def test_tamper_missing_file_and_path_escape_do_not_create_destination(self):
        self.save()
        sample = self.saved / 'data/diagnostics/connection.json'
        sample.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'content-mismatch'):
            restore(self.saved, self.restored, app_closed=True)
        sample.unlink()
        with self.assertRaisesRegex(ValueError, 'content-mismatch'):
            restore(self.saved, self.restored, app_closed=True)
        manifest = self.saved / 'manifest.json'; doc = json.loads(manifest.read_text(encoding='utf-8'))
        doc['files'][0]['file'] = 'diagnostics/../../escape'
        manifest.write_text(json.dumps(doc), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid-backup-path'):
            restore(self.saved, self.restored, app_closed=True)
        self.assertFalse(self.restored.exists())

    def test_bad_sqlite_and_live_wal_are_rejected(self):
        (self.user / 'garage.db-wal').write_bytes(b'pending')
        with self.assertRaisesRegex(ValueError, 'WAL-or-journal-present'):
            self.save()
        (self.user / 'garage.db-wal').unlink()
        (self.user / 'garage.db').write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):
            self.save()
        self.assertFalse(self.saved.exists())

    def test_changing_source_is_not_published_as_a_good_backup(self):
        from unittest.mock import patch
        from scripts.diagnostics.user_data_backup import copy_files
        before = (self.user / 'garage.db').read_bytes()
        def changing(source, target, entries):
            copy_files(source, target, entries)
            (source / 'diagnostics/connection.json').write_text('concurrent change')
        with patch('scripts.diagnostics.user_data_backup.copy_files', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'changed-during-backup'):
                self.save()
        self.assertFalse(self.saved.exists())
        self.assertEqual((self.user / 'garage.db').read_bytes(), before)

    def test_directory_link_rejected(self):
        # Windows junctions are available without symlink privileges.
        outside = self.root / 'outside'; outside.mkdir()
        linked = self.user / 'diagnostics/link'
        import os, subprocess
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            linked.symlink_to(outside, target_is_directory=True)
        self.addCleanup(lambda: linked.rmdir() if os.name == 'nt' else linked.unlink())
        with self.assertRaisesRegex(ValueError, 'linked-path'):
            self.save()
        self.assertFalse(self.saved.exists())


if __name__ == '__main__':
    unittest.main()
