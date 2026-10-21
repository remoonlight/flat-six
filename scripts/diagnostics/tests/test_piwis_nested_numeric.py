import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_nested_numeric import (
    probe_nested_fixed_response_parameter, synthetic_nested_response_parameter_probes,
    write_nested_response_numeric_probes)
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture
from scripts.diagnostics.tests.test_piwis_texttable import enum_dop


def nested_fixture():
    child, dop = fixture();child['nativeDataType'] = 255
    parent = copy.deepcopy(child);parent['serializedBytePosition'] = 7
    source = {'file': 'structure.db', 'bytes': 2000, 'sha256': 'd'*64}
    structure = {'source': source, 'sourceOffset': 100, 'compressedBytes': 60,
        'recordSha256': 'e'*64, 'record': 4, 'layout': {'recordType': 0xaa,
            'fieldLayoutComplete': True, 'serializedByteLength': 1,
            'parameters': {'collectionPresent': True, 'collectionType': 0x6d,
                'parameterCollectionLayoutComplete': True, 'declaredParameters': 1,
                'parsedParameters': [child]}}}
    parent['objectReference'] = {'nativeField08': {'pool': 'AStringData', 'storage': 'table-reference', 'id': 16},
        'nativeField0c': {'pool': 'AStringData', 'resolved': True, 'text': 'structure'}, 'indexLookup': {
            'indexTargetMatched': True, 'container': 'structure', 'databaseSource': copy.deepcopy(source),
            'keyIndexSource': {'file': 'structure.key', 'bytes': 4096, 'sha256': 'f'*64},
            'target': {**{k: structure[k] for k in ('sourceOffset', 'compressedBytes', 'recordSha256', 'record')},
                'recordType': 0xaa, 'objectId': 16, 'decompressedBytes': 100}}}
    return parent, structure, dop


class PiwisNestedNumericTests(unittest.TestCase):
    def test_parent_slice_then_child_position_neither_double_counts_nor_uses_structure_length(self):
        parent, structure, dop = nested_fixture()
        pdu = b'\x5a'*7 + b'\xa7'*3 + b'\x12\x34' + b'\xd9'*2
        result = probe_nested_fixed_response_parameter(parent, structure, 0, dop, pdu, kernel_sha256=PROFILE)
        self.assertEqual(result['candidateValue'], 0x1234)
        self.assertEqual(result['absoluteBytePosition'], 10)
        self.assertEqual(result['byteOffset'], 3)  # Offset within the native local slice.
        self.assertTrue(result['nestedPositionRuleVerified'])
        for flag in ('structureEnvelopeVerified', 'siblingProcessingVerified', 'parameterBindingVerified',
                     'referenceResolutionVerified', 'responseEnvelopeVerified', 'serviceResponseMatchingVerified',
                     'physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled'):
            self.assertFalse(result[flag])

    def test_cross_byte_enum_child_and_both_orders_use_the_same_source_bound_path(self):
        for order in (0, 1):
            parent, structure, dop = nested_fixture();dop['layout'] = enum_dop()
            dop['layout']['codedType']['nativeEncodingFieldsRaw'][2] = order
            child = structure['layout']['parameters']['parsedParameters'][0];child['bitPosition'] = 5
            pdu = b'\x5a'*7 + b'\xa7'*3 + (128 << 5).to_bytes(2, 'big' if order else 'little')
            result = probe_nested_fixed_response_parameter(parent, structure, 0, dop, pdu, kernel_sha256=PROFILE)
            self.assertEqual(result['candidateValue'], '条件未满足')
            self.assertEqual(result['rawValue'], 128)
            truncated = probe_nested_fixed_response_parameter(parent, structure, 0, dop, pdu[:-1], kernel_sha256=PROFILE)
            self.assertEqual(truncated['reason'], 'numeric-probe-truncated-field')

    def test_each_reference_record_and_kernel_identity_is_required(self):
        for target, field, value in (('structure', 'sourceOffset', 101), ('dop', 'recordSha256', 'f'*64)):
            parent, structure, dop = nested_fixture()
            (structure if target == 'structure' else dop)[field] = value
            result = probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*20, kernel_sha256=PROFILE)
            self.assertFalse(result['ok'])
            self.assertNotIn('candidateValue', result)
        parent, structure, dop = nested_fixture()
        self.assertFalse(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*20, kernel_sha256='0'*64)['ok'])

    def test_dynamic_positions_table_selection_recursive_child_and_parent_bit_offsets_rejected(self):
        for who, change in (('parent', {'bitPosition': 1}), ('parent', {'bytePositionAvailable': False}),
            ('parent', {'nativeDataType': 17}), ('child', {'bytePositionAvailable': False}),
            ('child', {'nativeDataType': 11}), ('child', {'recordType': 0xb0})):
            parent, structure, dop = nested_fixture()
            (parent if who == 'parent' else structure['layout']['parameters']['parsedParameters'][0]).update(change)
            self.assertFalse(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*20, kernel_sha256=PROFILE)['ok'])
        parent, structure, dop = nested_fixture();dop['layout']['recordType'] = 0xaa
        self.assertFalse(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*20, kernel_sha256=PROFILE)['ok'])
        parent, structure, dop = nested_fixture();parent['objectReference']['indexLookup']['target']['recordType'] = 0xab
        self.assertEqual(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*20, kernel_sha256=PROFILE)['reason'], 'nested-probe-structure-target-only')

    def test_incomplete_collection_and_invalid_child_indices_rejected(self):
        for change in ({'declaredParameters': 2}, {'parameterCollectionLayoutComplete': False},
            {'collectionType': 0x92}, {'collectionPresent': False}, {'parsedParameters': []}):
            parent, structure, dop = nested_fixture();structure['layout']['parameters'].update(change)
            self.assertFalse(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*20, kernel_sha256=PROFILE)['ok'])
        for child_index in (-1, 1, True, None):
            parent, structure, dop = nested_fixture()
            self.assertEqual(probe_nested_fixed_response_parameter(parent, structure, child_index, dop, b'\0'*20, kernel_sha256=PROFILE)['reason'], 'nested-probe-child-index-invalid')

    def test_pdu_bounds_are_checked_before_slice_and_aggregate_position_is_bounded(self):
        parent, structure, dop = nested_fixture()
        self.assertEqual(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*6, kernel_sha256=PROFILE)['reason'], 'nested-probe-truncated-parent-position')
        for pdu in (b'\0'*4096, bytearray(20)):
            self.assertEqual(probe_nested_fixed_response_parameter(parent, structure, 0, dop, pdu, kernel_sha256=PROFILE)['reason'], 'nested-probe-pdu-invalid')
        parent['serializedBytePosition'] = 4093
        self.assertEqual(probe_nested_fixed_response_parameter(parent, structure, 0, dop, b'\0'*4095, kernel_sha256=PROFILE)['reason'], 'nested-probe-combined-position-invalid')

    def test_synthetic_nested_values_include_exact_distinct_prefixes_and_truncation(self):
        parent, structure, dop = nested_fixture();dop['layout'] = enum_dop()
        probes = synthetic_nested_response_parameter_probes(parent, structure, 0, dop, kernel_sha256=PROFILE)
        self.assertEqual([p['result']['candidateValue'] for p in probes if p['result']['ok']], ['关', '开', '条件未满足'])
        for p in probes:
            raw = bytes.fromhex(p['pduHex']);self.assertEqual(raw[:10], b'\x5a'*7+b'\xa7'*3)
            self.assertFalse(p['actualVehicleResponse'])
        self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')

    def test_writer_preserves_three_sources_and_keeps_blocked_parent_evidence(self):
        parent, structure, dop = nested_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);response = {**{k: dop[k] for k in ('source', 'record', 'sourceOffset', 'compressedBytes', 'recordSha256')},
                'parameters': {'parsedParameters': [parent]}}
            (root/'responses.jsonl').write_text(json.dumps(response)+'\n', encoding='utf8')
            (root/'layouts.jsonl').write_text('\n'.join(json.dumps(r) for r in (structure, dop))+'\n', encoding='utf8')
            counts = write_nested_response_numeric_probes(root/'responses.jsonl', root/'layouts.jsonl', root/'out.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['supportedChildPaths'], 1)
            result = json.loads((root/'out.jsonl').read_text(encoding='utf8'))
            self.assertEqual(result['structureRecordSha256'], structure['recordSha256'])
            self.assertEqual(result['dopRecordSha256'], dop['recordSha256'])
            self.assertEqual(result['parentParameterIndex'], 0)
            parent['bytePositionAvailable'] = False
            (root/'responses.jsonl').write_text(json.dumps(response)+'\n', encoding='utf8')
            counts = write_nested_response_numeric_probes(root/'responses.jsonl', root/'layouts.jsonl', root/'blocked.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['parentBlocked:response-probe-runtime-position-unqualified'], 1)
            self.assertEqual(counts['outputPaths'], 1)
            result = json.loads((root/'blocked.jsonl').read_text(encoding='utf8'))
            self.assertIsNone(result['childParameter'])
            self.assertNotIn('candidateValue', result['probes'][0]['result'])
