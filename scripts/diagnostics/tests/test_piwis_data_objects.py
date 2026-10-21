import hashlib
import math
import struct
import unittest

from scripts.diagnostics.piwis_data_objects import (definition_value, dop_compu_prefix,
    dop_definition_layout, rational_coefficients, unit_definition_layout, constraint_definition_layout)
from scripts.diagnostics.piwis_parameters import Reader
from scripts.diagnostics.piwis_tables import definition_layout as table_structure_layout, layout_references

POOLS = {'AStringData': {1: {'text': 'source-name', 'encoding': 'ascii', 'units': 11, 'offset': 0}},
         'UStringData': {1: {'text': '枚举值', 'encoding': 'utf-16-le', 'units': 3, 'offset': 0}}}
HEADER = b'\x2c\0\1\0\0\0'
EMPTY_BASE = b'\1\5\0\0\0\0\0\0'
TAIL = b'UNREAD-DOP-BODY'
COMMON = struct.pack('<6I', 1, 1, 1, 0, 0, 0)
UNIT_HEADER = b'\2\1' + COMMON + struct.pack('<I', 1) + struct.pack('<dd', 0.1, -40)


class PiwisDataObjectsTests(unittest.TestCase):
    def test_identity_compu_prefix_keeps_entire_unread_dop_body(self):
        raw = HEADER + b'\1\x0a\0\0\0\0' + TAIL
        parsed = dop_compu_prefix(raw, POOLS)
        self.assertTrue(parsed['compuPrefixLayoutComplete'])
        self.assertEqual(parsed['compuMethod']['nativeCategory'], 0)
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())
        self.assertEqual(parsed['endOffset'], len(raw) - len(TAIL))
        self.assertEqual(parsed['prefixSha256'], hashlib.sha256(raw[:-len(TAIL)]).hexdigest())
        for field in ('dopBodyVerified', 'physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled'):
            self.assertFalse(parsed[field])
        self.assertFalse(parsed['compuMethod']['categorySemanticsVerified'])
        self.assertFalse(parsed['compuMethod']['directionSemanticsVerified'])
        self.assertEqual(parsed['compuMethod']['directionFields'],
                         {'physicalToCoded': 'nativeField18', 'codedToPhysical': 'nativeField28'})
        self.assertTrue(parsed['compuMethod']['directionFieldNamesVerified'])
        self.assertTrue(parsed['compuMethod']['strategyNameVerified'])
        self.assertEqual(parsed['compuMethod']['conversionStrategy'], 'IdenticalConversion')

    def test_eight_registered_strategies_are_named_without_claiming_conversion(self):
        expected = ('IdenticalConversion', 'LinearConversion', 'ScaleLinearConversion',
            'TexttableConversion', 'CompuCodeConversion', 'TabInterpolatedConversion',
            'RationalFunctionConversion', 'ScaleRationalFunctionConversion')
        for category, name in enumerate(expected):
            parsed = dop_compu_prefix(HEADER + b'\1\x0a\0' + bytes((category,)) + b'\0\0' + TAIL, POOLS)
            method = parsed['compuMethod']
            self.assertTrue(parsed['compuPrefixLayoutComplete'])
            self.assertEqual(method['conversionStrategy'], name)
            self.assertFalse(method['categorySemanticsVerified'])
            self.assertFalse(parsed['physicalConversionQualified'])

    def test_optional_absent_method_and_both_base_prefixes(self):
        parsed = dop_compu_prefix(HEADER + b'\0' + TAIL, POOLS)
        self.assertIsNone(parsed['compuMethod'])
        self.assertEqual(parsed['endOffset'], 7)
        raw = HEADER + b'\1\x0a\0\1' + EMPTY_BASE + EMPTY_BASE + TAIL
        parsed = dop_compu_prefix(raw, POOLS)
        self.assertTrue(parsed['compuPrefixLayoutComplete'])
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())
        self.assertIsNone(parsed['compuMethod']['nativeField18']['scales'])

    def test_rational_coefficients_preserve_double_bits_and_negative_values(self):
        raw = b'\2' + struct.pack('<dd', -40, 0.1) + b'\1' + struct.pack('<d', 2)
        r = Reader(raw, 0, POOLS)
        parsed = rational_coefficients(r)
        self.assertEqual(r.pos, len(raw))
        self.assertEqual([x['value'] for x in parsed['numerator']], [-40, 0.1])
        self.assertEqual(parsed['denominator'][0]['value'], 2)
        self.assertFalse(parsed['physicalConversionQualified'])

    def test_nonfinite_coefficients_retained_without_json_nan_or_conversion(self):
        raw = b'\2' + struct.pack('<dd', math.nan, math.inf) + b'\0'
        parsed = rational_coefficients(Reader(raw, 0, POOLS))
        self.assertTrue(all(x['value'] is None and not x['finite'] for x in parsed['numerator']))
        self.assertEqual(parsed['numerator'][1]['rawHex'], struct.pack('<d', math.inf).hex())

    def test_linear_scale_consumes_coefficients_limits_values_and_coded_limits(self):
        coefficient = b'\1\x14\0\2' + struct.pack('<dd', -40, 1) + b'\1' + struct.pack('<d', 1)
        lower = b'\1\x37\0\x0b' + struct.pack('<I', 0) + b'\2'
        upper = b'\1\x37\0\x0b' + struct.pack('<I', 255) + b'\1'
        scale = b'\1\x19\0' + struct.pack('<I', 1) + coefficient + b'\0' + lower + upper + b'\xff\0\xff\0\0'
        scales = b'\1\x1e\0' + struct.pack('<I', 1) + scale
        base = b'\1\5\0' + scales + b'\xff\0\0\xff'
        raw = HEADER + b'\1\x0a\0\1\0' + base + TAIL
        parsed = dop_compu_prefix(raw, POOLS)
        self.assertTrue(parsed['compuPrefixLayoutComplete'], parsed['blockedReason'])
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())
        scale = parsed['compuMethod']['nativeField28']['scales']['scales'][0]
        self.assertEqual(scale['nativeField10']['numerator'][0]['value'], -40)
        self.assertEqual(scale['lowerLimit']['nativeInterval'], 0x6d02)
        self.assertEqual(scale['upperLimit']['value']['rawHex'], 'ff000000')

    def test_category_three_reads_conditional_extra_ids_only_for_defined_values(self):
        # First inverse and second default are the two native branch conditions.
        first = b'\1\5\0\0\0\0\0\x0b' + struct.pack('<I', 0)
        second = b'\1\5\0\0\x0e' + struct.pack('<I', 1) + b'\0\0\0'
        raw = HEADER + b'\1\x0a\0\3' + first + second + struct.pack('<II', 1, 1) + TAIL
        parsed = dop_compu_prefix(raw, POOLS)
        self.assertTrue(parsed['compuPrefixLayoutComplete'], parsed['blockedReason'])
        self.assertEqual(parsed['compuMethod']['nativeField38']['id'], 1)
        self.assertEqual(parsed['compuMethod']['nativeField3c']['id'], 1)
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())
        absent = HEADER + b'\1\x0a\0\3' + EMPTY_BASE + EMPTY_BASE + TAIL
        parsed = dop_compu_prefix(absent, POOLS)
        self.assertTrue(parsed['compuPrefixLayoutComplete'])
        self.assertNotIn('nativeField38', parsed['compuMethod'])
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())

    def test_native_value_widths_bytes_and_string_namespaces(self):
        for t, width in ((4, 4), (5, 8), (6, 2), (7, 4), (10, 2), (11, 4)):
            raw = bytes((t,)) + b'\xff' * width + b'TAIL'
            r = Reader(raw, 0, POOLS)
            parsed = definition_value(r)
            self.assertEqual(parsed['rawHex'], 'ff' * width)
            self.assertEqual(r.pos, 1 + width)
        for t, text in ((1, 'source-name'), (14, '枚举值')):
            r = Reader(bytes((t,)) + struct.pack('<I', 1), 0, POOLS)
            self.assertEqual(definition_value(r)['string']['text'], text)
        for t in (2, 3):
            r = Reader(bytes((t,)) + b'\1\3\0abcTAIL', 0, POOLS)
            self.assertEqual(definition_value(r)['rawHex'], '616263')
            self.assertEqual(r.pos, 7)
        for t in (0, 255):
            self.assertFalse(definition_value(Reader(bytes((t,)), 0, POOLS))['nativeValueDefined'])

    def test_unknown_branches_stop_at_last_complete_prefix_and_keep_raw_method(self):
        cases = ((b'\1\x0b\0', 'unsupported-compu-object'),
                 (b'\1\x0a\0\10', 'unsupported-compu-category'),
                 (b'\1\x0a\0\4\1\5\0\0\0\0\1\x42\0', 'unsupported-compu-object'),
                 (b'\1\x0a\0\1\1\5\0\0\x0d', 'unsupported-compu-value-type'),
                 (b'\2', 'unsupported-object-presence'))
        for method, reason in cases:
            with self.subTest(reason=reason):
                parsed = dop_compu_prefix(HEADER + method + TAIL, POOLS)
                self.assertFalse(parsed['compuPrefixLayoutComplete'])
                self.assertEqual(parsed['endOffset'], 6)
                self.assertIn(reason, parsed['blockedReason'])
                self.assertEqual(parsed['remainingRawHex'], (method + TAIL).hex())

    def test_truncated_values_and_excessive_scale_count_never_resynchronize(self):
        method = b'\1\x0a\0\1\1\5\0\1\x1e\0' + struct.pack('<I', 4097)
        parsed = dop_compu_prefix(HEADER + method + TAIL, POOLS)
        self.assertEqual(parsed['blockedReason'], 'compu-scale-count-limit')
        for raw in (HEADER[:5], HEADER + b'\1\x0a\0\1\1\5\0\0\x0e\1', HEADER + b'\1\x0a\0\1\1\5\0\0\x05\0'):
            parsed = dop_compu_prefix(raw, POOLS)
            self.assertFalse(parsed['compuPrefixLayoutComplete'])
        self.assertIsNone(dop_compu_prefix(b'\xab\0', POOLS))
        with self.assertRaises(ValueError):
            dop_compu_prefix(b'\x2c', POOLS)

    def test_dop_layout_keeps_map_directions_references_and_unknown_tail_separate(self):
        coded = b'\1\x23\0\1' + struct.pack('<II', 20, 2) + b'\1\1\3\1\0'
        physical = b'\1\x3c\0\4\1\3\0\2'
        maps = b'\1\0' + struct.pack('<IH', 99, 4) + b'\1\0' + struct.pack('<IH', 7, 5)
        reference = b'\1' + struct.pack('<II', 1, 1)
        raw = HEADER + b'\0' + coded + physical + maps + reference + b'\0' + reference + b'\x23\x3e\0'
        parsed = dop_definition_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        self.assertEqual(parsed['physicalToCodedIndexMap'], [{'key': 99, 'value': 4}])
        self.assertEqual(parsed['codedToPhysicalIndexMap'], [{'key': 7, 'value': 5}])
        self.assertEqual(parsed['physicalType']['precision'], 3)
        self.assertEqual(parsed['unitReference']['nativeField0c']['text'], 'source-name')
        self.assertIsNone(parsed['internalConstraintReference'])
        self.assertFalse(parsed['physicalConstraintReference']['bindingVerified'])
        self.assertEqual(parsed['remainingRawHex'], '233e00')
        for key in ('dopBodyVerified', 'recordEnvelopeVerified', 'physicalConversionQualified', 'executionEnabled'):
            self.assertFalse(parsed[key])

    def test_dop_partial_layout_stops_before_incomplete_section_and_never_skips(self):
        start = HEADER + b'\0\0\0'
        bad = b'\2\0' + struct.pack('<IH', 7, 1) + struct.pack('<IH', 7, 2)
        parsed = dop_definition_layout(start + bad + TAIL, POOLS)
        self.assertEqual(parsed['blockedReason'], 'duplicate-dop-index-map-key')
        self.assertEqual(parsed['endOffset'], len(start))
        self.assertEqual(parsed['remainingRawHex'], (bad + TAIL).hex())
        parsed = dop_definition_layout(start + struct.pack('<H', 4097) + TAIL, POOLS)
        self.assertEqual(parsed['blockedReason'], 'dop-index-map-count-limit')
        raw = start + b'\0\0\0\0\0\0\0'
        self.assertTrue(dop_definition_layout(raw, POOLS)['fieldLayoutComplete'])
        for end in range(2, len(raw)):
            self.assertFalse(dop_definition_layout(raw[:end], POOLS)['fieldLayoutComplete'])
        unknown = dop_definition_layout(HEADER + b'\1\x0a\0\10' + TAIL, POOLS)
        self.assertEqual(unknown['endOffset'], 6)
        self.assertFalse(unknown['fieldLayoutComplete'])

    def test_unit_layout_with_dimension_and_groups_preserves_exact_pools_and_bits(self):
        dimension = b'\1\x0c\1' + COMMON + struct.pack('<7i', 1, 0, -2, 0, 0, 0, 0)
        group = b'\1\1\0' + struct.pack('<III', 1, 1, 1)
        raw = UNIT_HEADER + dimension + group + b'\x23\x3e\0'
        parsed = unit_definition_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        self.assertEqual(parsed['nativeField40']['text'], '枚举值')
        self.assertEqual(parsed['nativeField48RawHex'], struct.pack('<d', 0.1).hex())
        self.assertEqual(parsed['nativeField50RawHex'], struct.pack('<d', -40).hex())
        self.assertEqual(parsed['physicalDimension']['nativeExponentFieldsRaw'][2], 'feffffff')
        self.assertEqual(parsed['physicalDimension']['exponents'], {'length': 1, 'mass': 0,
            'time': -2, 'current': 0, 'temperature': 0, 'molarAmount': 0, 'luminousIntensity': 0})
        self.assertTrue(parsed['physicalDimension']['exponentFieldNamesVerified'])
        self.assertEqual(parsed['factorSItoUnit']['value'], 0.1)
        self.assertEqual(parsed['offsetSItoUnit']['value'], -40)
        self.assertEqual(parsed['displayName']['text'], '枚举值')
        self.assertEqual(parsed['unitGroups']['declaredReferences'], 1)
        self.assertEqual(parsed['remainingRawHex'], '233e00')
        for key in ('unitSemanticsVerified', 'recordEnvelopeVerified', 'physicalConversionQualified', 'executionEnabled'):
            self.assertFalse(parsed[key])

    def test_unit_absent_dimension_and_groups_nonfinite_bits_and_every_truncation(self):
        raw = UNIT_HEADER[:-16] + struct.pack('<dd', math.inf, math.nan) + b'\0\0'
        parsed = unit_definition_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'])
        self.assertIsNone(parsed['physicalDimension'])
        self.assertIsNone(parsed['unitGroups'])
        self.assertEqual(parsed['nativeField48RawHex'], struct.pack('<d', math.inf).hex())
        self.assertIsNone(parsed['factorSItoUnit']['value'])
        self.assertFalse(parsed['offsetSItoUnit']['finite'])
        for end in range(2, len(raw)):
            parsed = unit_definition_layout(raw[:end], POOLS)
            self.assertFalse(parsed['fieldLayoutComplete'])
            self.assertLessEqual(parsed['endOffset'], parsed['inspectedOffset'])
            self.assertEqual(parsed['remainingRawHex'], raw[parsed['endOffset']:end].hex())

    def test_unknown_unit_dimension_excessive_group_count_and_null_name_stop(self):
        cases = ((b'\1\xff\1', 'unsupported-compu-object'),
                 (b'\0\1' + struct.pack('<H', 4097), 'unit-group-reference-count-limit'),
                 (b'\0\1\1\0' + struct.pack('<I', 0), 'null-unit-group-name'),
                 (b'\2', 'unsupported-object-presence'))
        for tail, reason in cases:
            parsed = unit_definition_layout(UNIT_HEADER + tail + TAIL, POOLS)
            self.assertFalse(parsed['fieldLayoutComplete'])
            self.assertIn(reason, parsed['blockedReason'])
            self.assertFalse(parsed['executionEnabled'])
        self.assertIsNone(unit_definition_layout(b'\xab\0', POOLS))

    def test_code_information_is_metadata_only_with_separate_library_references(self):
        info = b'\1\x41\0' + struct.pack('<5I', 1, 0, 1, 1, 1) + b'\1\0' + struct.pack('<4I', 1, 1, 1, 1)
        base = b'\1\5\0\0\xff\xff' + info + b'\xff'
        raw = HEADER + b'\1\x0a\0\4' + base + b'\0' + TAIL
        parsed = dop_compu_prefix(raw, POOLS)
        self.assertTrue(parsed['compuPrefixLayoutComplete'], parsed['blockedReason'])
        info = parsed['compuMethod']['nativeField18']['codeInformation']
        self.assertEqual(info['fields']['codeFile']['pool'], 'UStringData')
        self.assertEqual(info['declaredLibraries'], 1)
        self.assertEqual(info['libraries'][0]['name']['pool'], 'AStringData')
        self.assertEqual(info['libraries'][0]['reference']['nativeField10']['raw'], 1)
        self.assertFalse(info['libraries'][0]['reference']['nativeField10']['configurationModeVerified'])
        self.assertTrue(info['fields']['nativeField24']['nullReference'])
        for key in ('codeContentVerified', 'targetExecuted', 'executionEnabled'):
            self.assertFalse(info[key])
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())

    def test_code_metadata_null_required_fields_count_and_truncations_are_blocked(self):
        start = HEADER + b'\1\x0a\0\4\1\5\0\0\xff\xff'
        info = b'\1\x41\0' + struct.pack('<5I', 1, 0, 1, 1, 1) + b'\0\0'
        raw = start + info + b'\xff\0'
        self.assertTrue(dop_compu_prefix(raw, POOLS)['compuPrefixLayoutComplete'])
        for end in range(len(start), len(raw)):
            parsed = dop_compu_prefix(raw[:end], POOLS)
            self.assertFalse(parsed['compuPrefixLayoutComplete'])
            self.assertEqual(parsed['endOffset'], 6)
            self.assertEqual(parsed['remainingRawHex'], raw[6:end].hex())
        for fields in ((0, 0, 1, 1, 1), (1, 0, 0, 1, 1), (1, 0, 1, 0, 1)):
            parsed = dop_compu_prefix(start + b'\1\x41\0' + struct.pack('<5I', *fields), POOLS)
            self.assertIn('null-code-information', parsed['blockedReason'])
        parsed = dop_compu_prefix(start + info[:-2] + struct.pack('<H', 4097), POOLS)
        self.assertEqual(parsed['blockedReason'], 'code-library-reference-count-limit')

    def test_constraint_interval_keeps_raw_values_and_native_flag_priority(self):
        for low, low_type in ((0, 0x6d01), (1, 0x6d02), (2, 0x6d03), (3, 0x6d03)):
            for high, high_type in ((0, 0x6d01), (0x10, 0x6d02), (0x20, 0x6d03), (0x30, 0x6d03)):
                flags = low | high | 0x80  # Unused bits remain available in the evidence.
                interval = b'\1\xfb\0\x0b' + struct.pack('<I', 0) + b'\x0b' + struct.pack('<I', 255) + bytes((flags,))
                raw = b'\3\2' + interval + b'\0\0\x23\x3e\0'
                parsed = constraint_definition_layout(raw, POOLS)
                self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
                self.assertEqual(parsed['interval']['nativeLowerIntervalType'], low_type)
                self.assertEqual(parsed['interval']['nativeUpperIntervalType'], high_type)
                self.assertEqual(parsed['interval']['nativeLimitFlags'], flags)
                self.assertEqual(parsed['interval']['upperLimit']['rawHex'], 'ff000000')
                self.assertEqual(parsed['remainingRawHex'], '233e00')
                self.assertFalse(parsed['constraintSemanticsVerified'])
                self.assertFalse(parsed['recordEnvelopeVerified'])
                self.assertFalse(parsed['executionEnabled'])

    def test_constraint_scales_keep_unicode_ascii_namespaces_and_opaque_byte_values(self):
        interval = b'\1\xfb\0\3\1\2\0\xaa\x55\xff\2'
        scale = b'\1\2\2' + interval + b'\5' + struct.pack('<4I', 1, 1, 0, 1) + b'\1'
        raw = b'\3\2\0\1\1\2' + struct.pack('<I', 2) + b'\0' + scale + b'\1TAIL'
        parsed = constraint_definition_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        scales = parsed['scaleConstraints']['scales']
        self.assertIsNone(scales[0])
        self.assertEqual(scales[1]['nativeRangeInfo'], 0x605)
        self.assertEqual([f['text'] for f in scales[1]['fields']], ['枚举值', 'source-name', None, 'source-name'])
        self.assertEqual(scales[1]['interval']['lowerLimit']['rawHex'], 'aa55')
        self.assertEqual(parsed['nativeComputedRaw'], 1)
        self.assertEqual(parsed['remainingRawHex'], b'TAIL'.hex())
        self.assertFalse(parsed['physicalConversionQualified'])

    def test_constraint_truncations_counts_unknown_values_and_tags_keep_section_boundary(self):
        interval = b'\1\xfb\0\x0b' + struct.pack('<I', 0) + b'\x0b' + struct.pack('<I', 255) + b'\x11'
        scale = b'\1\2\2' + interval + b'\3' + struct.pack('<4I', 1, 1, 0, 0) + b'\0'
        raw = b'\3\2' + interval + b'\1\1\2' + struct.pack('<I', 1) + scale + b'\0'
        self.assertTrue(constraint_definition_layout(raw, POOLS)['fieldLayoutComplete'])
        for end in range(2, len(raw)):
            parsed = constraint_definition_layout(raw[:end], POOLS)
            self.assertFalse(parsed['fieldLayoutComplete'])
            self.assertEqual(parsed['remainingRawHex'], raw[parsed['endOffset']:end].hex())
        for tail, reason in ((b'\1\xfa\0', 'unsupported-compu-object'),
                (b'\1\xfb\0\x0d', 'unsupported-compu-value-type-13'),
                (b'\0\1\1\2' + struct.pack('<I', 4097), 'constraint-scale-count-limit')):
            parsed = constraint_definition_layout(b'\3\2' + tail + TAIL, POOLS)
            self.assertFalse(parsed['fieldLayoutComplete'])
            self.assertIn(reason, parsed['blockedReason'])
            self.assertFalse(parsed['physicalConversionQualified'])


class PiwisTableStructureTests(unittest.TestCase):
    table_header = struct.pack('<H6I', 0xab, 1, 1, 1, 1, 1, 1)
    key_entry = struct.pack('<4I', 1, 1, 1, 1)
    table_suffix = struct.pack('<IH', 0, 0) + b'\1' + struct.pack('<II', 1, 1) + b'\0'
    base = struct.pack('<6I', 1, 1, 1, 0, 0, 0) + b'\0' + struct.pack('<I', 0) + b'\x28' + struct.pack('<II', 0, 0) + b'\1\x0b' + struct.pack('<II', 1, 1)

    def test_table_key_namespace_third_reference_field_and_unread_tail(self):
        raw = self.table_header + struct.pack('<I', 1) + self.key_entry + self.table_suffix + TAIL
        parsed = table_structure_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        key = parsed['keyMap'][0]
        self.assertEqual(key['key']['text'], '枚举值')
        self.assertEqual(key['reference']['nativeField08']['text'], 'source-name')
        self.assertEqual(key['reference']['nativeField10']['raw'], 1)
        self.assertFalse(key['reference']['nativeField10']['configurationModeVerified'])
        self.assertEqual(len(layout_references(parsed)), 2)
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())
        for field in ('recordEnvelopeVerified', 'tableSemanticsVerified', 'physicalConversionQualified', 'executionEnabled'):
            self.assertFalse(parsed[field])
        for end in range(2, len(raw) - len(TAIL)):
            partial = table_structure_layout(raw[:end], POOLS)
            self.assertFalse(partial['fieldLayoutComplete'])
            self.assertEqual(partial['remainingRawHex'], raw[partial['endOffset']:end].hex())

    def test_named_reference_ambiguous_inline_encoding_never_resyncs(self):
        entry = struct.pack('<3I', 2, 1, 1) + struct.pack('<I', 0x80000001) + b'A'
        raw = self.table_header + struct.pack('<I', 2) + self.key_entry + entry + self.table_suffix
        pools = {'AStringData': POOLS['AStringData'], 'UStringData': {**POOLS['UStringData'], 2: {'text': 'different'}}}
        parsed = table_structure_layout(raw, pools)
        self.assertEqual(parsed['blockedReason'], 'ambiguous-named-reference-encoding')
        self.assertEqual(len(parsed['keyMap']), 1)
        self.assertEqual(parsed['remainingRawHex'], (entry + self.table_suffix).hex())
        self.assertFalse(parsed['fieldLayoutComplete'])

    def test_duplicate_text_with_distinct_tokens_missing_key_and_count_limits(self):
        pools = {'AStringData': POOLS['AStringData'], 'UStringData': {**POOLS['UStringData'], 2: POOLS['UStringData'][1]}}
        entry2 = struct.pack('<I', 2) + self.key_entry[4:]
        for tail, reason in ((struct.pack('<I', 2) + self.key_entry + entry2, 'duplicate-table-key'),
                (struct.pack('<I', 1) + struct.pack('<I', 9), 'unresolved-or-duplicate-table-key'),
                (struct.pack('<I', 4097), 'table-key-map-count-limit')):
            parsed = table_structure_layout(self.table_header + tail, pools)
            self.assertFalse(parsed['fieldLayoutComplete'])
            self.assertIn(reason, parsed['blockedReason'])

    def test_diag_com_attributes_and_optional_string_vector_are_separate(self):
        # Empty key map, one named service reference, two attributes, raw enum
        # fields, two vector strings; absent DOP and special data groups.
        ref = struct.pack('<3I', 1, 1, 1) + b'\2' + struct.pack('<2I', 1, 0) + b'\5' + struct.pack('<H', 0xcad) + b'\1\2\0' + struct.pack('<2I', 1, 0)
        raw = self.table_header + struct.pack('<IIH', 0, 0, 1) + ref + b'\0\0' + TAIL
        parsed = table_structure_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        ref = parsed['diagComReferences']['items'][0]['reference']
        self.assertEqual(len(ref['attributes']), 2)
        self.assertEqual(ref['nativeField28Raw'], 0xcad)
        self.assertEqual(len(ref['nativeField30Strings']), 2)
        self.assertFalse(parsed['diagComReferences']['serviceSemanticsVerified'])
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())

    def test_unknown_sdg_and_excessive_service_vector_stop(self):
        prefix = self.table_header + struct.pack('<IIH', 0, 0, 0)
        parsed = table_structure_layout(prefix + b'\0\1' + TAIL, POOLS)
        self.assertEqual(parsed['blockedReason'], 'unsupported-table-special-data-groups')
        ref = struct.pack('<3I', 1, 1, 1) + b'\0\0\0\0\1' + struct.pack('<H', 4097)
        parsed = table_structure_layout(self.table_header + struct.pack('<IIH', 0, 0, 1) + ref, POOLS)
        self.assertEqual(parsed['blockedReason'], 'table-diag-com-string-vector-count-limit')

    def test_structure_uses_6d_parameters_and_keeps_partial_collection(self):
        header = struct.pack('<H6IH', 0xaa, 1, 1, 1, 1, 1, 1, 65535)
        parameter = b'\1\xa5\0' + self.base
        raw = header + b'\1\x6d\0\1\0' + parameter + TAIL
        parsed = table_structure_layout(raw, POOLS)
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        self.assertEqual(parsed['serializedByteLength'], 65535)
        self.assertEqual(len(layout_references(parsed)), 1)
        self.assertEqual(parsed['remainingRawHex'], TAIL.hex())
        bad = table_structure_layout(header + b'\1\x6d\0\2\0' + parameter + b'\1\xbe\0' + TAIL, POOLS)
        self.assertFalse(bad['fieldLayoutComplete'])
        self.assertEqual(len(bad['parameters']['parsedParameters']), 1)
        self.assertEqual(bad['remainingRawHex'], (b'\1\xbe\0' + TAIL).hex())
        for end in range(2, len(raw) - len(TAIL)):
            self.assertFalse(table_structure_layout(raw[:end], POOLS)['fieldLayoutComplete'])
        self.assertEqual(table_structure_layout(header + b'\1\x79\0', POOLS)['blockedReason'], 'unsupported-structure-collection-type-0x79')
        self.assertTrue(table_structure_layout(header + b'\0' + TAIL, POOLS)['fieldLayoutComplete'])

    def test_table_row_reads_known_named_fields_and_rejects_unknown_streams(self):
        prefix = b'\xac\0' + struct.pack('<I', 1) + b'\0\0\0' + self.base
        raw = prefix + b'#>\0A\1#>\1#<\0B\0\0#>\1#<\0'
        parsed = table_structure_layout(raw, POOLS)
        self.assertTrue(parsed['rowPrefixLayoutComplete'])
        self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
        self.assertIsNone(parsed['blockedReason'])
        self.assertEqual(parsed['endOffset'], len(raw))
        self.assertEqual(parsed['remainingRawHex'], '')
        self.assertEqual(parsed['namedFields']['nativeField170Raw'], 1)
        self.assertEqual(parsed['namedFields']['declaredPreconditions'], 0)
        self.assertFalse(parsed['namedFields']['nativeFlagSemanticsVerified'])
        self.assertFalse(parsed['recordEnvelopeVerified'])
        self.assertEqual(len(layout_references(parsed)), 1)
        for end in range(2, len(prefix)):
            partial = table_structure_layout(raw[:end], POOLS)
            self.assertFalse(partial.get('rowPrefixLayoutComplete', False))
        for n, field in enumerate(('audience', 'disabledAudiences', 'enabledAudiences')):
            raw = b'\xac\0' + struct.pack('<I', 1) + b'\0' * n + b'\1'
            partial = table_structure_layout(raw, POOLS)
            self.assertEqual(partial['blockedReason'], 'unsupported-table-row-' + field)
        self.assertIsNone(table_structure_layout(b'\x2c\0', POOLS))

    def test_named_stream_boundaries_and_unknown_conditions_preserve_whole_tail(self):
        prefix = b'\xac\0' + struct.pack('<I', 1) + b'\0\0\0' + self.base
        a, b = b'A\2#>\1#<\0', b'B\0\0#>\1#<\0'
        for tail in (b'#>\0' + a + b, b'#>\0' + b + a, b'#>\0' + a + b + b'\0'):
            parsed = table_structure_layout(prefix + tail, POOLS)
            self.assertTrue(parsed['fieldLayoutComplete'], parsed['blockedReason'])
            self.assertEqual(parsed['namedFields']['nativeField170Raw'], 2)
        cases = ((b'GAP#>\0' + a + b, 'stream-opener'),
            (b'#>\0' + a, 'stream-names'), (b'#>\0' + a + b + a, 'duplicate'),
            (b'#>\0' + a + b + b'\0EXTRA', 'trailing-bytes'),
            (b'#>\0' + a + b'B\1\0#>\1#<\0', 'nonempty-preconditions'),
            (b'#>\0' + a + b'B\1\x10#>\1#<\0', 'precondition-count-limit'),
            (b'#>\0A\1UNKNOWN#<\0' + b, 'nested-streams'),
            (b'#>\0' + a + b[:-1], 'unterminated'),
            (b'#>\0C#>\1#<\0' + a + b, 'stream-names'))
        for tail, reason in cases:
            parsed = table_structure_layout(prefix + tail, POOLS)
            self.assertFalse(parsed['fieldLayoutComplete'])
            self.assertTrue(parsed['rowPrefixLayoutComplete'])
            self.assertIn(reason, parsed['blockedReason'])
            self.assertEqual(parsed['endOffset'], len(prefix))
            self.assertEqual(parsed['remainingRawHex'], tail.hex())

    def test_code_library_third_field_missing_or_ambiguous_is_blocked(self):
        start = HEADER + b'\1\x0a\0\4\1\5\0\0\xff\xff'
        info = b'\1\x41\0' + struct.pack('<5I', 1, 0, 1, 1, 1) + b'\1\0' + struct.pack('<3I', 1, 1, 1)
        self.assertFalse(dop_compu_prefix(start + info, POOLS)['compuPrefixLayoutComplete'])
        parsed = dop_compu_prefix(start + info + struct.pack('<I', 0x80000001) + b'A\xff\0', POOLS)
        self.assertEqual(parsed['blockedReason'], 'ambiguous-named-reference-encoding')
        self.assertFalse(parsed['executionEnabled'])


if __name__ == '__main__':
    unittest.main()
