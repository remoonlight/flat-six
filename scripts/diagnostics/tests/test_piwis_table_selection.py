import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE, audit
from scripts.diagnostics.piwis_table_selection import probe_table_row_selection, write_table_selection_probes
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture as response_fixture


def fixture(text='61840'):
    parameter, row = response_fixture()
    field = {'pool': 'UStringData', 'resolved': True, 'nullReference': False, 'text': text}
    row['layout'] = {'recordType': 0xac, 'rowPrefixLayoutComplete': True, 'key': copy.deepcopy(field)}
    ref = parameter['objectReference']; ref['indexLookup']['target']['recordType'] = 0xac
    table = {'source': copy.deepcopy(row['source']), 'recordSha256': 'd'*64, 'sourceOffset': 10,
        'layout': {'recordType': 0xab, 'fieldLayoutComplete': True, 'declaredKeys': 1,
            'keyMap': [{'key': field, 'reference': ref}]}}
    return table, row


class PiwisTableSelectionTests(unittest.TestCase):
    def probe(self, table, row, value, native_type=14, locale=None):
        return probe_table_row_selection(table, row, {'nativeType': native_type, 'value': value},
            kernel_sha256=PROFILE, fixture_locale=locale)

    def test_unicode_and_explicit_C_uint32_select_same_exact_row(self):
        table, row = fixture()
        for value, kind, locale in (('61840', 14, None), (61840, 11, 'C')):
            result = self.probe(table, row, value, kind, locale)
            self.assertTrue(result['ok'])
            self.assertEqual(result['candidateKeyText'], '61840')
            self.assertEqual(result['selectedRowTarget']['recordSha256'], row['recordSha256'])
            for field in ('runtimeLocaleVerified', 'referenceResolutionVerified', 'tableBindingVerified',
                    'keyParameterBindingVerified', 'fallbackSelectionVerified', 'rowValueDecoded',
                    'audienceAndPreconditionsVerified', 'physicalConversionQualified', 'executionEnabled'):
                self.assertFalse(result[field])

    def test_uint32_boundaries_and_invalid_values_require_explicit_locale(self):
        for value in (0, 0xffffffff):
            table, row = fixture(str(value))
            self.assertTrue(self.probe(table, row, value, 11, 'C')['ok'])
            for locale in (None, '', 'en-US', 'de-DE'):
                self.assertEqual(self.probe(table, row, value, 11, locale)['reason'],
                    'table-selection-uint32-fixture-locale-required')
        table, row = fixture()
        for value in (-1, 0x100000000, True, 61840.0, '61840'):
            self.assertEqual(self.probe(table, row, value, 11, 'C')['reason'], 'table-selection-uint32-key-invalid')

    def test_direct_unicode_preserves_zeroes_and_unknown_key_never_guesses_row(self):
        table, row = fixture('01')
        self.assertTrue(self.probe(table, row, '01')['ok'])
        for value, kind, locale in ((1, 11, 'C'), ('1', 14, None), ('0x01', 14, None)):
            result = self.probe(table, row, value, kind, locale)
            self.assertEqual(result['reason'], 'table-selection-direct-key-unmapped-fallback-unqualified')
            self.assertNotIn('selectedRowTarget', result)

    def test_invalid_unicode_and_other_native_types_are_rejected(self):
        table, row = fixture()
        for value in (None, 61840, '\ud800', 'x'*4097):
            self.assertFalse(self.probe(table, row, value)['ok'])
        for kind in (True, 1, 4, 255, '14'):
            self.assertFalse(self.probe(table, row, '61840', kind)['ok'])

    def test_ambiguous_or_incomplete_table_is_rejected_before_selected_row(self):
        for change in ('duplicate', 'missing', 'wrongpool', 'unresolved', 'incomplete', 'wrongtype', 'boolcount'):
            table, row = fixture(); layout = table['layout']
            if change == 'duplicate':
                layout['keyMap'].append(copy.deepcopy(layout['keyMap'][0])); layout['declaredKeys'] = 2
            elif change == 'missing': layout['declaredKeys'] = 2
            elif change == 'wrongpool': layout['keyMap'][0]['key']['pool'] = 'AStringData'
            elif change == 'unresolved': layout['keyMap'][0]['key']['resolved'] = False
            elif change == 'incomplete': layout['fieldLayoutComplete'] = False
            elif change == 'wrongtype': layout['recordType'] = 0xaa
            else: layout['declaredKeys'] = True
            result = self.probe(table, row, '61840')
            self.assertFalse(result['ok']); self.assertNotIn('selectedRowTarget', result)

    def test_index_identity_drift_and_mixed_sources_never_bind(self):
        for change in ('hash', 'source', 'object', 'container', 'keyfile', 'tag', 'offset', 'boolid'):
            table, row = fixture(); ref = table['layout']['keyMap'][0]['reference']; target = ref['indexLookup']['target']
            if change == 'hash': row['recordSha256'] = 'e'*64
            elif change == 'source': row['source']['sha256'] = 'e'*64
            elif change == 'object': ref['nativeField08']['id'] += 1
            elif change == 'container': ref['nativeField0c']['text'] = 'other'
            elif change == 'keyfile': ref['indexLookup']['keyIndexSource']['file'] = 'other.key'
            elif change == 'tag': target['recordType'] = 0x2c
            elif change == 'offset': row['sourceOffset'] += 1
            else: target['objectId'] = True
            self.assertFalse(self.probe(table, row, '61840')['ok'])

    def test_row_prefix_key_and_kernel_profile_must_match(self):
        for change in ('prefix', 'key', 'type', 'kernel', 'malformed-key'):
            table, row = fixture(); kernel = PROFILE
            if change == 'prefix': row['layout']['rowPrefixLayoutComplete'] = False
            elif change == 'key': row['layout']['key']['text'] = '61841'
            elif change == 'type': row['layout']['recordType'] = 0x2c
            elif change == 'kernel': kernel = 'f'*64
            else: row['layout']['key'] = '61840'
            result = probe_table_row_selection(table, row, {'nativeType': 14, 'value': '61840'}, kernel_sha256=kernel)
            self.assertFalse(result['ok']); self.assertNotIn('selectedRowTarget', result)

    def test_writer_preserves_sources_and_separate_unmapped_result(self):
        table, row = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'layouts.jsonl'; output = root / 'selection.jsonl'
            source.write_text('\n'.join(json.dumps(r) for r in (table, row)) + '\n', encoding='utf8')
            original = source.read_bytes()
            counts = write_table_selection_probes(source, output, kernel_sha256=PROFILE)
            self.assertEqual(counts, {'tables': 1, 'keyPaths': 1, 'case:unicode-exact': 1,
                'case:uint32-decimal-C-fixture': 1, 'case:table-selection-direct-key-unmapped-fallback-unqualified': 1})
            rows = [json.loads(line) for line in output.read_text(encoding='utf8').splitlines()]
            self.assertEqual(rows[0]['rowRecordSha256'], row['recordSha256'])
            self.assertIsNone(rows[1]['keyMapIndex'])
            self.assertNotIn('selectedRowTarget', rows[1]['probes'][0]['result'])
            self.assertEqual(source.read_bytes(), original)

    def test_database_switch_requires_layouts_and_indexes_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for layouts, indexes in ((False, None), (True, None), (False, [])):
                with self.assertRaisesRegex(ValueError, 'table-selection-probes-require'):
                    audit([], [], root / 'missing', parse_data_layouts=layouts,
                        key_indexes=indexes, table_selection_probes=True)
                self.assertFalse((root / 'missing').exists())
