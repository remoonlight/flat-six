import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_table_key import probe_declared_table_key, synthetic_declared_table_key_probes, write_declared_table_key_probes
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture as response_fixture
from scripts.diagnostics.tests.test_piwis_numeric import dop, coded


def reference(row, tag, identifier):
    parameter, _ = response_fixture(); ref = parameter['objectReference']
    ref['indexLookup']['databaseSource'] = copy.deepcopy(row['source'])
    ref['indexLookup']['target'].update({k: row[k] for k in ('sourceOffset', 'compressedBytes', 'record', 'recordSha256')})
    ref['indexLookup']['target'].update(recordType=tag, objectId=identifier)
    ref['nativeField08']['id'] = identifier
    return ref


def fixture(width=16, position=3):
    _, row = response_fixture()
    field = {'pool': 'UStringData', 'resolved': True, 'nullReference': False, 'text': '12'}
    row['layout'] = {'recordType': 0xac, 'rowPrefixLayoutComplete': True, 'key': field}
    key_dop = {**copy.deepcopy(row), 'sourceOffset': 200, 'compressedBytes': 45,
        'record': 15, 'recordSha256': 'e'*64, 'layout': dop(category=0, target=1)}
    key_dop['layout']['codedType'] = coded(width)
    row_ref = reference(row, 0xac, 15); row_ref['nativeField10'] = {'raw': 102}
    table = {**copy.deepcopy(row), 'sourceOffset': 10, 'compressedBytes': 35, 'record': 1,
        'recordSha256': 'd'*64, 'layout': {'recordType': 0xab, 'fieldLayoutComplete': True,
            'declaredKeys': 1, 'keyMap': [{'key': copy.deepcopy(field), 'reference': row_ref}],
            'keyDopReference': reference(key_dop, 0x2c, 35)}}
    parameter, _ = response_fixture()
    parameter.pop('objectReference'); parameter.update(recordType=0xb2, parameterType=0x7009,
        nativeDataType=25, nativeFlags=0x22, serializedBytePosition=position,
        tableReference={**reference(table, 0xab, 25), 'attributes': []}, nativeTableFlag=1,
        nativeTableString={'pool': 'AStringData', 'resolved': True, 'nullReference': False,
            'token': 501, 'text': 'DeclaredRow'})
    pool = {102: {'text': 'DeclaredRow'}, 501: {'text': 'DeclaredRow'}}
    return parameter, table, row, key_dop, pool


class PiwisTableKeyTests(unittest.TestCase):
    def probes(self, values):
        parameter, table, row, key_dop, pool = values
        return synthetic_declared_table_key_probes(parameter, table, row, key_dop,
            ascii_strings=pool, kernel_sha256=PROFILE)

    def test_declared_name_uses_resolved_text_not_numeric_token_equality(self):
        values = fixture(); rows = self.probes(values); result = rows[0]['result']
        self.assertTrue(result['ok'])
        self.assertEqual(result['candidateKeyValue'], 12)
        self.assertEqual(result['selectedNameToken'], 102)
        self.assertEqual(result['declaredBytePosition'], 3)
        self.assertEqual(bytes.fromhex(rows[0]['pduHex'])[:3], b'\xa7'*3)
        for flag in ('keyParameterBindingVerified', 'referenceResolutionVerified', 'expectedKeyValidated',
                'runtimeLocaleVerified', 'rowValueDecoded', 'audienceAndPreconditionsVerified',
                'serviceResponseMatchingVerified', 'physicalConversionQualified', 'executionEnabled'):
            self.assertFalse(result[flag])

    def test_wrong_identifier_and_truncated_key_never_select_row(self):
        rows = self.probes(fixture())
        self.assertEqual(rows[1]['result']['reason'], 'table-key-probe-declared-row-key-mismatch')
        self.assertEqual(rows[1]['result']['decodedKeyCandidate'], 13)
        self.assertEqual(rows[2]['result']['reason'], 'numeric-probe-truncated-field')
        for row in rows[1:]:
            self.assertFalse(row['result']['ok']); self.assertNotIn('selectedRowTarget', row['result'])
            self.assertNotIn('candidateKeyValue', row['result'])

    def test_dynamic_protocol_inline_bit_and_non_declared_branch_rejected(self):
        changes = ({'nativeFlags': 2}, {'serializedBytePosition': True}, {'bitPosition': 1},
            {'isProtocolParameter': True}, {'nativeFlags': 0xa2}, {'nativeDataType': 255},
            {'recordType': 0xa5}, {'nativeTableFlag': 0}, {'nativeTableFlag': True},
            {'codedType': {'present': True}})
        for change in changes:
            values = fixture(); values[0].update(change)
            row = self.probes(values)[0]
            self.assertFalse(row['result']['ok']); self.assertNotIn('pduHex', row)
        values = fixture(); values[0]['tableReference']['attributes'] = [{'text': 'unknown'}]
        self.assertFalse(self.probes(values)[0]['result']['ok'])

    def test_duplicate_resolved_names_and_missing_name_pool_are_rejected(self):
        for change in ('duplicate', 'missing', 'wrong-name', 'zero-token'):
            values = fixture(); parameter, table, row, key_dop, pool = values
            if change == 'duplicate':
                entry = copy.deepcopy(table['layout']['keyMap'][0]); entry['reference']['nativeField10']['raw'] = 103
                pool[103] = {'text': 'DeclaredRow'}; table['layout']['keyMap'].append(entry); table['layout']['declaredKeys'] = 2
            elif change == 'missing': pool.pop(102)
            elif change == 'wrong-name': parameter['nativeTableString']['text'] = 'OtherRow'
            else: table['layout']['keyMap'][0]['reference']['nativeField10']['raw'] = 0
            self.assertFalse(self.probes(values)[0]['result']['ok'])

    def test_all_three_reference_targets_must_match_records_and_sources(self):
        for record_index in (1, 2, 3):
            for field, value in (('recordSha256', 'f'*64), ('sourceOffset', 999)):
                values = fixture(); values[record_index][field] = value
                self.assertFalse(self.probes(values)[0]['result']['ok'])
        values = fixture(); values[3]['source']['sha256'] = 'f'*64
        self.assertFalse(self.probes(values)[0]['result']['ok'])

    def test_key_layout_canonical_decimal_and_encoding_constraints_are_kept(self):
        for change in ('padded', 'row-mismatch', 'float', 'mask', 'out-of-range'):
            values = fixture(8); parameter, table, row, key_dop, pool = values
            if change == 'padded': table['layout']['keyMap'][0]['key']['text'] = row['layout']['key']['text'] = '012'
            elif change == 'row-mismatch': row['layout']['key']['text'] = '13'
            elif change == 'float': key_dop['layout']['physicalType']['nativeBaseDataType'] = 2
            elif change == 'mask': key_dop['layout']['codedType']['bitMaskPresent'] = True
            else: table['layout']['keyMap'][0]['key']['text'] = row['layout']['key']['text'] = '256'
            self.assertFalse(self.probes(values)[0]['result']['ok'])

    def test_uint8_at_last_byte_is_bounded_and_uint16_cannot_cross_limit(self):
        rows = self.probes(fixture(8, 4094))
        self.assertTrue(rows[0]['result']['ok']); self.assertEqual(len(bytes.fromhex(rows[0]['pduHex'])), 4095)
        self.assertEqual(rows[2]['result']['reason'], 'numeric-probe-truncated-field')
        row = self.probes(fixture(16, 4094))[0]
        self.assertFalse(row['result']['ok']); self.assertNotIn('pduHex', row)

    def test_explicit_fixture_locale_kernel_and_pdu_type_are_required(self):
        parameter, table, row, key_dop, pool = fixture()
        for locale, kernel, pdu in ((None, PROFILE, b'\0'*5), ('C', 'f'*64, b'\0'*5),
                ('C', PROFILE, bytearray(5)), ('C', PROFILE, b'\0'*4096)):
            result = probe_declared_table_key(parameter, table, row, key_dop, pdu,
                ascii_strings=pool, kernel_sha256=kernel, fixture_locale=locale)
            self.assertFalse(result['ok']); self.assertNotIn('selectedRowTarget', result)

    def test_writer_keeps_four_sources_and_blocked_parameter_without_fabricated_pdu(self):
        parameter, table, row, key_dop, pool = fixture()
        response = {'source': table['source'], 'sourceOffset': 400, 'compressedBytes': 100,
            'recordSha256': 'f'*64, 'parameters': {'parsedParameters': [parameter]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); src = root / 'responses.jsonl'; layout = root / 'layouts.jsonl'
            src.write_text(json.dumps(response) + '\n', encoding='utf8')
            layout.write_text('\n'.join(json.dumps(r) for r in (table, row, key_dop)) + '\n', encoding='utf8')
            original = (src.read_bytes(), layout.read_bytes())
            counts = write_declared_table_key_probes(src, layout, root / 'probes.jsonl', ascii_strings=pool, kernel_sha256=PROFILE)
            self.assertEqual(counts, {'parameters': 1, 'case:declared-table-key': 1,
                'case:table-key-probe-declared-row-key-mismatch': 1, 'case:numeric-probe-truncated-field': 1})
            output = json.loads((root / 'probes.jsonl').read_text(encoding='utf8'))
            self.assertEqual(output['rowRecordSha256'], row['recordSha256'])
            self.assertEqual(output['keyDopRecordSha256'], key_dop['recordSha256'])
            self.assertEqual((src.read_bytes(), layout.read_bytes()), original)
            parameter['nativeFlags'] = 2; src.write_text(json.dumps(response) + '\n', encoding='utf8')
            counts = write_declared_table_key_probes(src, layout, root / 'blocked.jsonl', ascii_strings=pool, kernel_sha256=PROFILE)
            output = json.loads((root / 'blocked.jsonl').read_text(encoding='utf8'))
            self.assertNotIn('pduHex', output['probes'][0])
            self.assertFalse(output['probes'][0]['result']['ok'])
