from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.hashutil import sha256_file
from scripts.diagnostics.precar import prepare


class PreparationSnapshotTests(unittest.TestCase):
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


if __name__ == '__main__': unittest.main()
