import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.piwis_catalog import audit_catalog, inspect_catalog


def catalog(project='9x1', blocks=None):
    blocks = blocks or [(None, 'RuntimeBlock', ['0.0.0@DME.bv.db', '0.0.0@DME.bv.key'])]
    rows = []
    for update, name, files in blocks:
        attribute = '' if update is None else ' UPD="' + update + '"'
        rows.append('<ABLOCK' + attribute + '><SHORT-NAME>' + name + '</SHORT-NAME><CATEGORY>RUNTIME</CATEGORY><FILES>'
                    + ''.join('<FILE>' + p + '</FILE>' for p in files) + '</FILES></ABLOCK>')
    return ('<?xml version="1.0"?><CATALOG><SHORT-NAME>' + project + '</SHORT-NAME><ABLOCKS>'
            + ''.join(rows) + '</ABLOCKS></CATALOG>').encode()


def snapshot(folder, files):
    rows = []
    for relative, raw in files.items():
        target = folder / 'sources' / relative
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        rows.append({'relativePath': relative, 'present': True, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    path = folder / 'manifest.json'
    path.write_text(json.dumps({'files': rows}), encoding='utf8')
    return path


class PiwisCatalogTests(unittest.TestCase):
    def test_update_conflict_is_preserved_and_complete_retained_pair_not_active(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = catalog(blocks=[('NEW', 'Original', ['SG_Daten/DME.odx'])])
            runtime = catalog(blocks=[(None, 'RuntimeBlock', ['0.0.0@DME.bv.db', '0.0.0@DME.bv.key']),
                                      ('DELETED', 'Deleted', ['0.0.0@DME.bv.db', '0.0.0@DME.bv.key'])])
            manifest = snapshot(root, {'Projects-V/9x1/index.xml': source, 'Projects-V/9x1/rt_index.xml': runtime,
                'Projects-V/9x1/0.0.0@DME.bv.db': b'private compiled source', 'Projects-V/9x1/0.0.0@DME.bv.key': b'private key index'})
            result = audit_catalog([manifest], '9x1', root / 'audit')
            self.assertEqual(result['counts']['ambiguousUpdateFiles'], 2)
            self.assertEqual(result['counts']['retainedCompleteDbKeyPairs'], 1)
            self.assertEqual(result['counts']['notRetainedFiles'], 1)
            pair = result['databaseKeyPairs'][0]
            self.assertTrue(pair['deletionDeclarationPresent'])
            for field in ('runtimeActivationVerified', 'ecuIdentityVerified', 'vehicleApplicabilityVerified', 'executionEnabled'):
                self.assertFalse(pair[field])
            rows = [json.loads(l) for l in (root / 'audit/catalog-file-declarations.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows[0]['declarations']), 2)
            self.assertTrue(rows[0]['retainedHashVerified'])
            self.assertFalse(rows[0]['installedPresenceVerified'])

    def test_declared_pair_without_sources_and_missing_key_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); raw = catalog(blocks=[(None, 'RuntimeBlock', ['A.db', 'A.key', 'B.db'])])
            manifest = snapshot(root, {'Projects-V/9x1/index.xml': catalog(), 'Projects-V/9x1/rt_index.xml': raw})
            result = audit_catalog([manifest], '9x1', root / 'audit')
            self.assertEqual(result['counts']['runtimeDeclaredKeyPairs'], 1)
            self.assertEqual(result['counts']['runtimeMissingKeyDeclarations'], 1)
            self.assertEqual(result['counts']['notRetainedCompleteDbKeyPairs'], 2)
            self.assertFalse(result['complete981982InventoryVerified'])

    def test_identity_unsafe_paths_entities_and_hidden_files_are_rejected(self):
        for raw in (catalog('OBD'), catalog(blocks=[(None, 'RuntimeBlock', ['../outside.db'])]),
                    catalog(blocks=[(None, 'RuntimeBlock', ['C:/outside.db'])]),
                    catalog(blocks=[(None, 'RuntimeBlock', ['folder/./A.db'])]),
                    catalog(blocks=[('UNKNOWN', 'RuntimeBlock', ['A.db'])]),
                    b'<!DOCTYPE CATALOG [<!ENTITY e "value">]>' + catalog(),
                    catalog().decode().encode('utf-16'),
                    catalog().replace(b'</CATALOG>', b'<FILE>A.db</FILE></CATALOG>'), b'<bad>'):
            with self.assertRaises(ValueError):
                inspect_catalog(raw, project='9x1', catalog_kind='runtime')
        with self.assertRaises(ValueError):
            inspect_catalog(catalog(), project='../escape', catalog_kind='runtime')

    def test_catalog_contact_data_is_not_projected(self):
        raw = catalog().replace(b'<ABLOCKS>', b'<COMPANY-DATAS><EMAIL>private@example.test</EMAIL></COMPANY-DATAS><ABLOCKS>')
        parsed = inspect_catalog(raw, project='9x1', catalog_kind='runtime')
        self.assertNotIn('private@example.test', json.dumps(parsed))
        self.assertEqual(parsed['declarationCount'], 2)

    def test_source_hash_conflicts_missing_catalog_and_existing_output_block(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = snapshot(root / 'a', {'Projects-V/9x1/index.xml': catalog(), 'Projects-V/9x1/rt_index.xml': catalog()})
            other = snapshot(root / 'b', {'Projects-V/9x1/index.xml': catalog(blocks=[(None, 'RuntimeBlock', ['different.db'])])})
            with self.assertRaisesRegex(ValueError, 'catalog-conflicting-retained-sources'):
                audit_catalog([manifest, other], '9x1', root / 'audit-conflict')
            with self.assertRaisesRegex(ValueError, 'catalog-source-and-runtime-originals-required'):
                audit_catalog([other], '9x1', root / 'audit-missing')
            output = root / 'existing'; output.mkdir()
            with self.assertRaises(FileExistsError):
                audit_catalog([manifest], '9x1', output)
            (root / 'a/sources/Projects-V/9x1/index.xml').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'catalog-retained-source-hash-mismatch'):
                audit_catalog([manifest], '9x1', root / 'audit-corrupt')


if __name__ == '__main__':
    unittest.main()
