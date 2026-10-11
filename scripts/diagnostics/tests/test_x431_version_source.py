import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.x431_version_source import VersionRouteSource, GUARDS
from scripts.diagnostics.offline_match import verify_source
from scripts.diagnostics.all_unit_collection import build_all_unit_pack


def text(value):
    data = value.encode('ascii') + b'\0'
    return len(data).to_bytes(2, 'little') + data


def fixture(routes=None, versions=('VersionA',)):
    blob = bytearray(2048)
    top = bytes([1, 13]) + text('9x1') + (64).to_bytes(4, 'little')
    blob[:len(top)] = top
    index = b''
    for ecu, module, offset, flags in routes or [(47, 'M', 512, 0), (48, 'M', 512, 0), (47, 'LED', 1024, 0)]:
        index += flags.to_bytes(2, 'little') + bytes([ecu]) + text(module) + offset.to_bytes(4, 'little')
    blob[64:64+len(index)] = index
    for start in (512, 1024):
        # Independent stored words yield map key F1B2, never a wire request.
        selector = b'\x22\0\x90\xF1\xFE\xFF\x03' + (0x80000001).to_bytes(4, 'little')
        data = b'\x01' + text('Version label') + selector + bytes([len(versions)])
        for name in versions:
            data += text(name) + (1536).to_bytes(4, 'little')
        blob[start:start+len(data)] = data
    rule = (1).to_bytes(2, 'little', signed=True) + text('VR1#')
    blob[1536:1536+len(rule)] = rule
    return bytes(blob)


class VersionSourceTests(unittest.TestCase):
    def source(self, path, blob):
        path.write_bytes(blob)
        with patch('scripts.diagnostics.x431_version_source.REVIEWED_DSN_SHA256', hashlib.sha256(blob).hexdigest()):
            return VersionRouteSource(path)

    def test_shared_definition_stays_bound_to_exact_ecu_module_and_preserves_raw_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self.source(Path(temp)/'DSN', fixture())
            for ecu in (47, 48):
                ref = source.reference(ecu, 'M', 'VersionA', system_key=0xd00+ecu)
                self.assertEqual(ref['bindingStatus'], 'exact-source-version-rule-match')
                row = ref['routes'][0]
                self.assertEqual(row['routeId'], ecu)
                self.assertEqual(row['versionMatches'][0]['rules'], ['VR1#'])
                selector = row['selectors'][0]
                self.assertEqual((selector['leftWordSigned'], selector['rightWordSigned']), (34, -3696))
                self.assertEqual(selector['nativeResponseMapKey'], 0xf1b2)
                self.assertEqual(selector['byteOffsetSigned'], -2)
                self.assertIsNone(selector['wireRequestHex'])
                self.assertIsNone(row['sourcePredicateAudit']['sourceRouteEligibleCandidate'])
                for key, value in GUARDS.items():
                    self.assertEqual(ref[key], value)
            self.assertEqual(source.summary()['routeCount'], 3)
            self.assertEqual(source.summary()['uniqueDefinitionCount'], 2)
            self.assertIsNone(source.reference(47, 'M', 'VersionA')['routes'][0]['sourcePredicateAudit'])

    def test_cross_module_case_and_base_do_not_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self.source(Path(temp)/'DSN', fixture())
            for ecu, module in ((65, 'M'), (47, 'm'), (48, 'LED')):
                ref = source.reference(ecu, module, 'VersionA')
                self.assertEqual(ref['bindingStatus'], 'source-route-missing')
                self.assertFalse(ref['fallbackUsed'])
            for name in ('M_BaseSys', 'versiona', 'Other'):
                self.assertEqual(source.reference(47, 'M', name)['bindingStatus'], 'source-version-name-missing')
            for key in (0xc2f, 0xd30, True):
                with self.assertRaisesRegex(ValueError, 'system-key-mismatch'):
                    source.reference(47, 'M', 'VersionA', system_key=key)

    def test_duplicate_flag_routes_or_version_names_cannot_choose_first(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'DSN'
            source = self.source(p, fixture(routes=[(47, 'M', 512, 0), (47, 'M', 512, 0x0146)]))
            ref = source.reference(47, 'M', 'VersionA', system_key=0xd2f)
            self.assertEqual(ref['bindingStatus'], 'source-route-or-version-ambiguous')
            self.assertEqual([r['flagsWord'] for r in ref['routes']], [0, 0x0146])
            self.assertTrue(all(r['sourcePredicateAudit']['explicitContext'] is None for r in ref['routes']))
            self.assertIsNone(ref['selectedRouteIndexOffset'])
            source = self.source(p, fixture(versions=('VersionA', 'VersionA')))
            self.assertEqual(source.reference(47, 'M', 'VersionA')['bindingStatus'], 'source-route-or-version-ambiguous')

    def test_unreviewed_or_changed_source_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'DSN';path.write_bytes(b'unknown')
            with patch('scripts.diagnostics.x431_version_source.parse_dsn_index') as parser:
                with self.assertRaisesRegex(ValueError, 'hash-mismatch'):VersionRouteSource(path)
                parser.assert_not_called()
            blob = fixture();path.write_bytes(blob);digest = hashlib.sha256(blob).hexdigest()
            def drift(*args):
                evidence = verify_source(*args)
                path.write_bytes(b'changed')
                return evidence
            with patch('scripts.diagnostics.x431_version_source.REVIEWED_DSN_SHA256', digest), patch(
                    'scripts.diagnostics.x431_version_source.verify_source', side_effect=drift):
                with self.assertRaisesRegex(ValueError, 'changed-before-parse'):VersionRouteSource(path)

    def test_all_unit_pack_carries_rules_and_missing_versions_without_vehicle_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);dsn = root/'DSN';dsn.write_bytes(fixture())
            variants = root/'variants'
            variants.write_text('\n'.join(json.dumps({'profile_id': name, 'module': 'M', 'name': name,
                'generation': None, 'on_target_menu': True, 'pool_records': {}}) for name in ('VersionA', 'M_BaseSys')))
            with patch('scripts.diagnostics.x431_version_source.REVIEWED_DSN_SHA256', hashlib.sha256(dsn.read_bytes()).hexdigest()):
                build_all_unit_pack(root/'pack', variants=variants, expected_sha256=hashlib.sha256(variants.read_bytes()).hexdigest(),
                    coverage={'menu_ecus':[{'ecu_id':47, 'dsn_modules':['M']}]}, decoded_dsn=dsn)
            pack = json.loads((root/'pack/all-units.json').read_text(encoding='utf-8'))
            self.assertTrue(pack['versionSource']['present'])
            self.assertEqual(pack['versionRouteBindingCounts'], {'exact-source-version-rule-match':2, 'source-version-name-missing':2})
            self.assertFalse(pack['vehicleVerified'])
            manifest = json.loads((root/'pack/manifest.json').read_text(encoding='utf-8'))
            self.assertIn(str(dsn.resolve()), [r['path'] for r in manifest['inputs']])
            for row in manifest['outputs']:
                self.assertEqual(hashlib.sha256(Path(row['path']).read_bytes()).hexdigest(), row['sha256'])


if __name__ == '__main__':
    unittest.main()
