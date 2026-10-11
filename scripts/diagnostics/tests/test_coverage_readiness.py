from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.coverage_readiness import function_rows, build
from scripts.diagnostics.historical_coverage import HistoricalCoverage
from scripts.diagnostics.tests.test_realtime_preparation import field


class CoverageReadinessTests(unittest.TestCase):
    def test_selected_variants_are_rechecked_instead_of_missing_default_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); variants = root / 'selected.jsonl'
            variants.write_text(json.dumps(self.variant()) + '\n')
            coverage = root / 'coverage.json'
            coverage.write_text(json.dumps({'menu_ecus': [{'ecu_id': 1, 'dsn_modules': ['test']}]}))
            language = root / 'language'; language.write_bytes(b'fixture-only')
            verified = []
            def verify(path, expected=None):
                path = Path(path); verified.append(path)
                data = path.read_bytes()
                return {'path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
            with patch('scripts.diagnostics.coverage_readiness.ROOT', root), \
                    patch('scripts.diagnostics.coverage_readiness.DEFAULT_VARIANTS', root / 'missing-default'), \
                    patch('scripts.diagnostics.coverage_readiness.DEFAULT_COVERAGE', coverage), \
                    patch('scripts.diagnostics.coverage_readiness.GGP_PATH', language), \
                    patch('scripts.diagnostics.coverage_readiness.GgpLanguage') as words, \
                    patch('scripts.diagnostics.coverage_readiness.verify_source', side_effect=verify), \
                    patch('scripts.diagnostics.coverage_readiness.validate_bundle') as validate, \
                    patch('scripts.diagnostics.coverage_readiness.HistoricalCoverage.load', return_value=HistoricalCoverage([], [])):
                words.return_value.lookup_id.return_value = None
                result = build(root / '.local/matrix', variants=variants, bundle=root / 'explicit-bundle',
                               match=root / 'explicit-match')
            self.assertTrue(result['noDeviceIO'])
            self.assertFalse(result['coverageComplete'])
            self.assertEqual(verified.count(variants), 2)
            self.assertNotIn(root / 'missing-default', verified)
            validate.assert_called_once_with(root / 'explicit-bundle')

    def variant(self):
        records = [dict(field('a'), at=10), dict(field('b', offset=1), at=20)]
        return {'profile_id': '9x1:test', 'module': 'test', 'name': 'shared', 'generation': '9x1',
                'membership': 'candidate', 'pool_records': {'measurement': {'records': records}}}

    def test_shared_request_keeps_fields_source_and_version_boundaries(self):
        variant = self.variant()
        rows = list(function_rows(variant, '981', 1))
        measurement = next(r for r in rows if r['category'] == 'measurement')
        self.assertEqual(measurement['functionKey'], '22F100')
        self.assertEqual([r['sourceOffset'] for r in measurement['references']], [10, 20])
        self.assertEqual(measurement['definitionStatus'], 'parsed-source')
        other = next(r for r in function_rows(variant, '982', 1) if r['category'] == 'measurement')
        self.assertNotEqual(other['rowId'], measurement['rowId'])
        self.assertTrue(measurement['sharedVariantCandidate'])
        self.assertFalse(measurement['liveVerified']); self.assertFalse(measurement['executionEnabled'])
        self.assertIn('model/variant-applicability-unverified', other['qualificationGaps'])

    def test_missing_categories_and_unmapped_ecu_are_explicit(self):
        rows = list(function_rows(self.variant(), '981', None))
        self.assertEqual({r['category'] for r in rows}, {'identity', 'measurement', 'coding', 'routine', 'dtc', 'firmware'})
        firmware = next(r for r in rows if r['category'] == 'firmware')
        self.assertEqual(firmware['definitionStatus'], 'missing')
        self.assertIn('menu-ecu-mapping-missing', firmware['qualificationGaps'])
        self.assertIn('wired-transport-and-recovery-required', firmware['qualificationGaps'])

    def test_unresolved_name_decoder_and_invalid_request_cannot_be_complete(self):
        v = self.variant(); r = v['pool_records']['measurement']['records'][0]
        r.update(name=None, labels=[], read_request_candidate_hex='2EF100', byteOffset=None)
        rows = list(function_rows(v, '981', 1))
        unresolved = next(row for row in rows if row['category'] == 'measurement' and row['functionKey'] == 'offset:10')
        self.assertEqual(unresolved['definitionStatus'], 'unresolved')
        self.assertIn('name-unresolved', unresolved['definitionGaps'])
        self.assertIn('field-decoder-unresolved', unresolved['definitionGaps'])
        self.assertIsNone(unresolved['references'][0]['requestHex'])

    def test_exact_code_and_recipe_remain_separate_from_reading(self):
        v = self.variant(); v['pool_records']['dtc'] = {'records': [{'at': 44, 'code': 'P0123', 'text': 'description'}]}
        row = next(r for r in function_rows(v, '981', 1) if r['category'] == 'dtc')
        self.assertEqual(row['functionKey'], 'P0123')
        self.assertIsNone(row['references'][0]['requestHex'])
        self.assertIn('complete-repair-procedure-unverified', row['qualificationGaps'])

    def test_dynamic_name_resolution_is_source_based(self):
        v = self.variant(); v['pool_records']['measurement']['records'][0]['name'] = 'SubIndexNum=1#0xF0,0x00,0x00,0x01'
        row = next(r for r in function_rows(v, '981', 1, lambda _: 'source name') if r['category'] == 'measurement')
        self.assertEqual(row['references'][0]['name'], 'source name')
        self.assertTrue(row['references'][0]['nameResolved'])
        changed = deepcopy(v); changed['pool_records']['measurement']['records'][0]['unit'] = 'changed'
        other = next(r for r in function_rows(changed, '981', 1, lambda _: 'source name') if r['category'] == 'measurement')
        self.assertNotEqual(row['references'][0]['recordSha256'], other['references'][0]['recordSha256'])

    def test_empty_source_entry_is_distinct_from_missing_name_entry(self):
        v = self.variant()
        for record in v['pool_records']['measurement']['records']:
            record.update(name='', labels=[], nameID='F0010824')
        for text, expected in [('', 'source-empty'), (None, 'source-entry-missing')]:
            row = next(r for r in function_rows(v, '981', 1, lambda _: text) if r['category'] == 'measurement')
            self.assertTrue(all(ref['nameStatus'] == expected for ref in row['references']))
            self.assertTrue(all(not ref['nameResolved'] for ref in row['references']))
            self.assertEqual(row['definitionStatus'], 'unresolved')
            self.assertFalse(row['executionEnabled'])

    def test_dynamic_source_empty_component_retains_its_exact_id(self):
        v = self.variant()
        record = v['pool_records']['measurement']['records'][0]
        record.update(name='SubIndexNum=1#0xF0,0x01,0x08,0x24', nameID='F0027C9B')
        row = next(r for r in function_rows(v, '981', 1, lambda _: '') if r['category'] == 'measurement')
        self.assertEqual(row['references'][0]['nameStatus'], 'source-component-empty')
        self.assertEqual(row['references'][0]['emptySourceNameIds'], ['F0010824'])


if __name__ == '__main__': unittest.main()
