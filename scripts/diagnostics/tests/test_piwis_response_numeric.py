import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.decoder_reference import reference_piwis_unsigned
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE, audit
from scripts.diagnostics.piwis_response_numeric import (
    probe_fixed_response_parameter, synthetic_response_parameter_probes, write_response_numeric_probes)
from scripts.diagnostics.tests.test_piwis_numeric import dop
from scripts.diagnostics.tests.test_piwis_texttable import enum_dop


def fixture():
    source = {'file': 'retained.db', 'bytes': 1000, 'sha256': 'a'*64}
    record = {'source': source, 'sourceOffset': 90, 'compressedBytes': 40,
        'recordSha256': 'b'*64, 'record': 3, 'layout': dop(category=0, target=1)}
    target = {k: record[k] for k in ('sourceOffset', 'compressedBytes', 'recordSha256', 'record')}
    target.update({'recordType': 0x2c, 'objectId': 15, 'decompressedBytes': 50})
    parameter = {'recordType': 0xa5, 'parameterType': 0x7001,
        'parameterLayoutProfileMatched': True, 'nativeFlags': 0x28,
        'bytePositionAvailable': True, 'isProtocolParameter': False,
        'bitPosition': 0, 'serializedBytePosition': 3, 'objectReference': {
            'nativeField08': {'pool': 'AStringData', 'storage': 'table-reference', 'id': 15},
            'nativeField0c': {'pool': 'AStringData', 'resolved': True, 'text': 'retained'},
            'indexLookup': {'indexTargetMatched': True, 'target': target,
                'container': 'retained', 'databaseSource': copy.deepcopy(source),
                'keyIndexSource': {'file': 'retained.key', 'bytes': 4096, 'sha256': 'c'*64}}}}
    return parameter, record


class PiwisResponseNumericTests(unittest.TestCase):
    def test_source_bound_enum_uses_declared_root_byte_and_bit_positions(self):
        parameter, record = fixture();record['layout'] = enum_dop()
        parameter['bitPosition'] = 5
        probes = synthetic_response_parameter_probes(parameter, record, kernel_sha256=PROFILE)
        successes = [p['result'] for p in probes if p['result']['ok']]
        self.assertEqual([r['candidateValue'] for r in successes], ['关', '开', '条件未满足'])
        self.assertEqual([r['rawValue'] for r in successes], [1, 4, 128])
        for p in probes:
            self.assertEqual(bytes.fromhex(p['pduHex'])[:3], b'\xa7'*3)
            self.assertFalse(p['result']['parameterBindingVerified'])
            self.assertFalse(p['result']['executionEnabled'])
        self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')

    def test_enum_default_keeps_reference_and_service_qualification_false(self):
        parameter, record = fixture();record['layout'] = enum_dop('未列出状态')
        result = probe_fixed_response_parameter(parameter, record, b'\xa7'*3 + b'\x00', kernel_sha256=PROFILE)
        self.assertEqual(result['candidateValue'], '未列出状态')
        self.assertEqual(result['enumSelection']['kind'], 'explicit-forward-default')
        for field in ('parameterBindingVerified', 'referenceResolutionVerified', 'responseEnvelopeVerified',
                      'serviceResponseMatchingVerified', 'physicalConversionQualified', 'executionEnabled'):
            self.assertFalse(result[field])

    def test_declared_nonzero_position_and_order_match_independent_reference(self):
        for position in (0, 1, 3, 255):
            for order in (0, 1):
                parameter, record = fixture()
                parameter['serializedBytePosition'] = position
                record['layout']['codedType']['nativeEncodingFieldsRaw'][2] = order
                pdu = b'\xa7'*position + b'\x12\x34' + b'\xd9\xd9'
                value = probe_fixed_response_parameter(parameter, record, pdu, kernel_sha256=PROFILE)
                self.assertTrue(value['ok'], value)
                self.assertEqual(value['rawValue'], reference_piwis_unsigned(record['layout']['codedType'], pdu, position))
                self.assertEqual(value['declaredBytePosition'], position)
                self.assertTrue(value['declaredPositionRuleVerified'])
                for key in ('parameterBindingVerified', 'referenceResolutionVerified',
                            'serviceResponseMatchingVerified', 'responseEnvelopeVerified',
                            'physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled'):
                    self.assertFalse(value[key])

    def test_missing_declared_position_never_uses_serialized_zero_or_runtime_guess(self):
        for change in ({'bytePositionAvailable': False}, {'nativeFlags': 8},
                       {'serializedBytePosition': True}, {'serializedBytePosition': -1},
                       {'serializedBytePosition': 4095}, {'bitPosition': 8}):
            parameter, record = fixture(); parameter.update(change)
            result = probe_fixed_response_parameter(parameter, record, b'\0'*20, kernel_sha256=PROFILE)
            self.assertFalse(result['ok'])
            self.assertNotIn('candidateValue', result)
            self.assertFalse(result['staticIndexTargetMatched'])

    def test_struct_table_protocol_inline_encoding_and_other_parameter_kinds_rejected(self):
        for change in ({'recordType': 0xb0}, {'parameterType': 0x7003},
                       {'isProtocolParameter': True}, {'nativeFlags': 0xa8},
                       {'codedType': {'present': True}}, {'parameterLayoutProfileMatched': False}):
            parameter, record = fixture(); parameter.update(change)
            self.assertFalse(probe_fixed_response_parameter(parameter, record, b'\0'*20, kernel_sha256=PROFILE)['ok'])
        parameter, record = fixture()
        parameter['objectReference']['indexLookup']['target']['recordType'] = 0xaa
        self.assertEqual(probe_fixed_response_parameter(parameter, record, b'', kernel_sha256=PROFILE)['reason'],
                         'response-probe-simple-dop-target-only')

    def test_exact_record_source_reference_and_container_identity_are_required(self):
        changes = [('sourceOffset', 91), ('compressedBytes', 39), ('recordSha256', 'c'*64),
                   ('record', 4), ('source', {'file': 'retained.db', 'bytes': 1000, 'sha256': 'd'*64})]
        for field, value in changes:
            parameter, record = fixture(); record[field] = value
            self.assertEqual(probe_fixed_response_parameter(parameter, record, b'', kernel_sha256=PROFILE)['reason'],
                             'response-probe-dop-record-identity-mismatch')
        for field, value in (('id', 16), ('storage', 'inline'), ('pool', 'UStringData')):
            parameter, record = fixture(); parameter['objectReference']['nativeField08'][field] = value
            self.assertEqual(probe_fixed_response_parameter(parameter, record, b'', kernel_sha256=PROFILE)['reason'],
                             'response-probe-reference-index-identity-mismatch')
        parameter, record = fixture()
        parameter['objectReference']['nativeField0c']['text'] = 'other'
        self.assertFalse(probe_fixed_response_parameter(parameter, record, b'', kernel_sha256=PROFILE)['ok'])
        parameter, record = fixture()
        self.assertFalse(probe_fixed_response_parameter(parameter, record, b'', kernel_sha256='0'*64)['ok'])
        self.assertFalse(probe_fixed_response_parameter(parameter, None, b'', kernel_sha256=PROFILE)['ok'])

    def test_missing_index_record_hash_or_typed_lengths_cannot_match_empty_metadata(self):
        for change in ({'recordSha256': None}, {'recordSha256': 'g'*64},
                       {'compressedBytes': False}, {'decompressedBytes': 0}, {'objectId': 0}):
            parameter, record = fixture()
            parameter['objectReference']['indexLookup']['target'].update(change)
            result = probe_fixed_response_parameter(parameter, record, b'\0'*10, kernel_sha256=PROFILE)
            self.assertEqual(result['reason'], 'response-probe-index-source-identity-invalid')
            self.assertNotIn('candidateValue', result)
        parameter, record = fixture()
        parameter['objectReference']['indexLookup']['keyIndexSource']['file'] = 'other.key'
        self.assertFalse(probe_fixed_response_parameter(parameter, record, b'\0'*10, kernel_sha256=PROFILE)['ok'])

    def test_fixture_prefix_suffix_and_truncation_are_not_vehicle_responses(self):
        parameter, record = fixture()
        probes = synthetic_response_parameter_probes(parameter, record, kernel_sha256=PROFILE)
        self.assertEqual(len(probes), 6)
        self.assertEqual(bytes.fromhex(probes[3]['pduHex']), b'\xa7'*3 + b'\x12\x34\xd9\xd9')
        self.assertEqual(probes[3]['result']['rawValue'], 0x1234)
        self.assertFalse(probes[-1]['result']['ok'])
        self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')
        self.assertTrue(all(p['actualVehicleResponse'] is False for p in probes))
        parameter['bytePositionAvailable'] = False
        rejected = synthetic_response_parameter_probes(parameter, record, kernel_sha256=PROFILE)
        self.assertEqual(len(rejected), 1)
        self.assertNotIn('pduHex', rejected[0])

    def test_declared_bit_position_fixtures_encode_exact_field_and_reject_short_slice(self):
        for width, bit in ((1, 7), (7, 2), (12, 4), (31, 7), (32, 7)):
            for order in (0, 1):
                parameter, record = fixture(); parameter['bitPosition'] = bit
                record['layout']['codedType']['bitLength'] = width
                record['layout']['codedType']['nativeEncodingFieldsRaw'][2] = order
                probes = synthetic_response_parameter_probes(parameter, record, kernel_sha256=PROFILE)
                self.assertEqual(probes[1]['result']['candidateValue'], 1)
                self.assertEqual(probes[2]['result']['candidateValue'], (1 << width)-1)
                self.assertEqual(probes[4]['result']['candidateValue'], 1 << (width-1))
                for p in probes[:-1]:
                    self.assertEqual(p['result']['rawValue'], reference_piwis_unsigned(
                        record['layout']['codedType'], bytes.fromhex(p['pduHex']), 3, bit))
                self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')

    def test_generated_audit_join_records_gaps_source_and_does_not_overwrite(self):
        parameter, record = fixture()
        response = {**record, 'parameters': {'parsedParameters': [parameter, {**parameter, 'recordType': 0xb0}]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); response_path, layout_path = root/'responses.jsonl', root/'layouts.jsonl'
            response_path.write_text(json.dumps(response)+'\n', encoding='utf8')
            layout_path.write_text(json.dumps(record)+'\n', encoding='utf8')
            before = (response_path.read_bytes(), layout_path.read_bytes())
            counts = write_response_numeric_probes(response_path, layout_path, root/'probes.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['parameters'], 2)
            self.assertEqual(counts['supportedParameters'], 1)
            rows = [json.loads(line) for line in (root/'probes.jsonl').read_text(encoding='utf8').splitlines()]
            self.assertEqual(rows[0]['dopRecordSha256'], record['recordSha256'])
            self.assertEqual(rows[1]['probes'][0]['result']['reason'], 'response-probe-plain-value-parameter-only')
            self.assertEqual((response_path.read_bytes(), layout_path.read_bytes()), before)
            with self.assertRaises(FileExistsError):
                write_response_numeric_probes(response_path, layout_path, root/'probes.jsonl', kernel_sha256=PROFILE)
            layout_path.write_text((json.dumps(record)+'\n')*2, encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'duplicate-layout-record'):
                write_response_numeric_probes(response_path, layout_path, root/'duplicate.jsonl', kernel_sha256=PROFILE)

    def test_audit_opt_in_requires_source_outputs_and_indexes(self):
        for options in ({}, {'parse_response_parameters': True}, {'parse_data_layouts': True},
                        {'parse_response_parameters': True, 'parse_data_layouts': True}):
            with self.assertRaisesRegex(ValueError, 'require-response-parameters-layouts-key-indexes'):
                audit([], [], Path('unused-output'), response_numeric_probes=True, **options)


if __name__ == '__main__':
    unittest.main()
