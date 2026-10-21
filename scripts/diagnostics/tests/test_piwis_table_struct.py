import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_table_struct import probe_table_structure_child, synthetic_table_structure_child_probes, write_table_structure_child_probes
from scripts.diagnostics.tests.test_piwis_table_key import fixture as key_fixture, reference
from scripts.diagnostics.tests.test_piwis_nested_numeric import nested_fixture
from scripts.diagnostics.tests.test_piwis_texttable import enum_dop


def name(text):
    return {'field': 'shortName', 'pool': 'AStringData', 'resolved': True, 'nullReference': False, 'text': text}


def fixture():
    key, table, row, key_dop, pool = key_fixture(position=1)
    key['fields'] = [name('Selector')]
    parent, structure, dop = nested_fixture()
    dop.update(sourceOffset=300, record=20, recordSha256='6'*64)
    structure['layout']['parameters']['parsedParameters'][0]['objectReference'] = reference(dop, 0x2c, 44)
    # Both static reference targets may live in different databases.
    parent.pop('objectReference'); parent.update(recordType=0xb0, parameterType=0x7010,
        nativeDataType=23, serializedBytePosition=3, fields=[name('Data')],
        nativeField130={k: v for k, v in name('Selector').items() if k != 'field'},
        tableReference={**reference(table, 0xab, 25), 'attributes': []})
    row['layout'].update(fieldLayoutComplete=True, baseParameter={'objectReference': reference(structure, 0xaa, 16)})
    # The reference helper supports this structure's separately paired key file.
    ref = row['layout']['baseParameter']['objectReference']; ref['nativeField0c']['text'] = 'structure'
    ref['indexLookup'].update(container='structure', keyIndexSource={'file': 'structure.key', 'bytes': 4096, 'sha256': 'f'*64})
    response = {'source': table['source'], 'sourceOffset': 500, 'compressedBytes': 100,
        'recordSha256': 'a'*64, 'parameters': {'collectionPresent': True, 'collectionType': 0x92,
            'parameterCollectionLayoutComplete': True, 'declaredParameters': 2, 'parsedParameters': [key, parent]}}
    return response, table, row, key_dop, structure, dop, pool


class PiwisTableStructureTests(unittest.TestCase):
    def probes(self, values):
        response, table, row, key_dop, structure, dop, pool = values
        return synthetic_table_structure_child_probes(response, 1, table, row, key_dop, structure, 0, dop,
            ascii_strings=pool, kernel_sha256=PROFILE)

    def probe(self, values, data, **options):
        response, table, row, key_dop, structure, dop, pool = values
        return probe_table_structure_child(response, 1, table, row, key_dop, structure, 0, dop, data,
            ascii_strings=pool, kernel_sha256=options.pop('kernel_sha256', PROFILE),
            fixture_locale=options.pop('fixture_locale', 'C'), **options)

    def test_key_then_parent_and_child_positions_with_separate_structure_source(self):
        values = fixture(); rows = self.probes(values); result = rows[0]['result']
        self.assertTrue(result['ok'])
        self.assertEqual(result['absoluteBytePosition'], 6)
        self.assertEqual(result['byteOffset'], 3)
        self.assertEqual(result['candidateKeyValue'], 12)
        self.assertEqual(result['keyParameterIndex'], 0)
        self.assertEqual(result['selectedStructureCandidate'], values[4]['recordSha256'])
        self.assertEqual(bytes.fromhex(rows[0]['pduHex'])[:6], b'\x5a\x00\x0c\xa7\xa7\xa7')
        for flag in ('runtimeOwnerCollectionVerified', 'expectedKeyValidated', 'runtimeLocaleVerified',
            'keyParameterBindingVerified', 'rowValueDecoded', 'responseEnvelopeVerified', 'physicalConversionQualified',
            'ecuApplicabilityVerified', 'executionEnabled'):
            self.assertFalse(result[flag])
        self.assertTrue(result['candidateChildValueDecoded'])

    def test_wrong_and_truncated_keys_never_select_row_or_decode_child(self):
        rows = self.probes(fixture())
        self.assertEqual(rows[-2]['result']['reason'], 'table-structure-key:table-key-probe-declared-row-key-mismatch')
        self.assertEqual(rows[-1]['result']['reason'], 'table-structure-key:numeric-probe-truncated-field')
        for p in rows[-2:]:
            self.assertNotIn('candidateValue', p['result'])
            self.assertNotIn('selectedRowCandidate', p['result'])
            self.assertFalse(p['result']['candidateChildValueDecoded'])

    def test_enum_child_bit_shift_both_orders_and_truncation(self):
        for order in (0, 1):
            values = fixture(); values[5]['layout'] = enum_dop()
            values[5]['layout']['codedType']['nativeEncodingFieldsRaw'][2] = order
            values[4]['layout']['parameters']['parsedParameters'][0]['bitPosition'] = 5
            rows = self.probes(values)
            self.assertEqual([p['result']['candidateValue'] for p in rows if p['result']['ok']], ['关', '开', '条件未满足'])
            self.assertEqual(next(p['result']['reason'] for p in rows if p['case'] == 'truncated'), 'numeric-probe-truncated-field')

    def test_signed_child_extends_its_width_after_key_selection_and_positions(self):
        for order in (0, 1):
            values = fixture()
            values[5]['layout']['codedType'].update(bitLength=13, nativeEncodingFieldsRaw=[0, 3, order, 0])
            values[5]['layout']['physicalType']['nativeBaseDataType'] = 0
            values[4]['layout']['parameters']['parsedParameters'][0]['bitPosition'] = 5
            rows = self.probes(values)
            results = {p['case']: p['result'] for p in rows}
            for case, expected in (('negative-minimum', -4096), ('minus-one', -1), ('positive-maximum', 4095)):
                self.assertTrue(results[case]['ok'], results[case]['reason'])
                self.assertEqual(results[case]['candidateValue'], expected)
                self.assertEqual(results[case]['absoluteBytePosition'], 6)
                self.assertFalse(results[case]['rowValueDecoded'])
            for case in ('truncated', 'different-key', 'truncated-key'):
                self.assertFalse(results[case]['ok'])
                self.assertNotIn('candidateValue', results[case])

    def test_duplicate_key_names_missing_names_and_nonpreceding_keys_rejected(self):
        for change in ('duplicate', 'missing', 'following', 'unknown-pool'):
            values = fixture(); params = values[0]['parameters']['parsedParameters']
            if change == 'duplicate': params.append(copy.deepcopy(params[0])); values[0]['parameters']['declaredParameters'] = 3
            elif change == 'missing': params[1]['nativeField130']['text'] = 'Absent'
            elif change == 'following': params[0]['fields'][0]['text'] = 'Other'; params[1]['nativeField130']['text'] = 'Data'
            else: params[0]['fields'][0]['resolved'] = False
            row = self.probes(values)[0]
            self.assertFalse(row['result']['ok']); self.assertNotIn('pduHex', row)

    def test_parent_role_position_generated_name_and_table_identity_are_required(self):
        for change in ({'recordType': 0xa5}, {'nativeDataType': 17}, {'bitPosition': 1},
                {'bytePositionAvailable': False}, {'codedType': {'present': True}}, {'nativeFlags': 0xa2},
                {'fields': [name('#RtGen_Data_1')]}):
            values = fixture(); values[0]['parameters']['parsedParameters'][1].update(change)
            self.assertFalse(self.probes(values)[0]['result']['ok'])
        values = fixture(); values[0]['parameters']['parsedParameters'][1]['tableReference']['nativeField08']['id'] = 26
        self.assertFalse(self.probes(values)[0]['result']['ok'])

    def test_all_target_records_and_sources_remain_pinned(self):
        for index in range(1, 6):
            values = fixture(); values[index]['recordSha256'] = 'f'*64
            self.assertFalse(self.probes(values)[0]['result']['ok'])
        values = fixture(); values[4]['source']['sha256'] = 'b'*64
        self.assertFalse(self.probes(values)[0]['result']['ok'])

    def test_incomplete_response_or_structure_recursive_child_and_interleaved_key_are_rejected(self):
        for change in ({'collectionType': 0x79}, {'declaredParameters': 3}, {'parameterCollectionLayoutComplete': False}):
            values = fixture(); values[0]['parameters'].update(change)
            self.assertFalse(self.probes(values)[0]['result']['ok'])
        values = fixture(); values[4]['layout']['parameters']['parameterCollectionLayoutComplete'] = False
        self.assertFalse(self.probes(values)[0]['result']['ok'])
        values = fixture(); values[5]['layout']['recordType'] = 0xaa
        self.assertFalse(self.probes(values)[0]['result']['ok'])
        values = fixture(); values[0]['parameters']['parsedParameters'][1]['serializedBytePosition'] = 2
        self.assertEqual(self.probes(values)[0]['result']['reason'], 'table-structure-interleaved-key-layout-unqualified')

    def test_kernel_locale_pdu_and_combined_positions_are_bounded(self):
        values = fixture(); rows = self.probes(values); data = bytes.fromhex(rows[0]['pduHex'])
        for options in ({'fixture_locale': None}, {'kernel_sha256': 'f'*64}):
            self.assertFalse(self.probe(values, data, **options)['ok'])
        for pdu in (bytearray(data), data + bytes(4096)):
            self.assertFalse(self.probe(values, pdu)['ok'])
        values[0]['parameters']['parsedParameters'][1]['serializedBytePosition'] = 4094
        self.assertEqual(self.probes(values)[0]['result']['reason'], 'table-structure-combined-position-invalid')

    def test_writer_preserves_six_sources_and_blocked_parent(self):
        response, table, row, key_dop, structure, dop, pool = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); src = root / 'response.jsonl'; layout = root / 'layouts.jsonl'
            src.write_text(json.dumps(response)+'\n', encoding='utf8')
            layout.write_text('\n'.join(map(json.dumps, (table, row, key_dop, structure, dop)))+'\n', encoding='utf8')
            original = (src.read_bytes(), layout.read_bytes())
            counts = write_table_structure_child_probes(src, layout, root / 'out.jsonl', ascii_strings=pool, kernel_sha256=PROFILE)
            self.assertEqual(counts['parents'], 1); self.assertEqual(counts['supportedChildPaths'], 1)
            output = json.loads((root / 'out.jsonl').read_text(encoding='utf8'))
            self.assertEqual(output['structureRecordSha256'], structure['recordSha256'])
            self.assertEqual(output['dopRecordSha256'], dop['recordSha256'])
            self.assertEqual(output['responseRecordSha256'], response['recordSha256'])
            self.assertEqual((src.read_bytes(), layout.read_bytes()), original)
            response['parameters']['parsedParameters'][1]['bytePositionAvailable'] = False
            src.write_text(json.dumps(response)+'\n', encoding='utf8')
            counts = write_table_structure_child_probes(src, layout, root / 'blocked.jsonl', ascii_strings=pool, kernel_sha256=PROFILE)
            output = json.loads((root / 'blocked.jsonl').read_text(encoding='utf8'))
            self.assertEqual(counts['blockedChildPaths'], 1)
            self.assertIsNone(output['childParameter']); self.assertNotIn('pduHex', output['probes'][0])
