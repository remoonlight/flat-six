"""Native layout boundaries; synthetic inputs are not vehicle qualification."""
import copy
import struct
import unittest

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256, RECORD_LIMIT, digest
from scripts.diagnostics.piwis_ecu_config import data_record_core_prefix, data_record_body_prefix
from .test_piwis_ecu_config import DATA_POOLS


POOLS = copy.deepcopy(DATA_POOLS)
for token, text in ((10, '*.bin'), (11, '*.Session')):
    POOLS['AStringData'][token] = {'text': text, 'encoding': 'ascii',
        'units': len(text), 'offset': token * 10}


def record(*, byte_data=None, data_id=b'\xff', coding_flag=1, coding_presence=1,
           coding_type=0x187, file_name=10, late_bound=1, data_format=0x6b01,
           audience=0, tail=b'unparsed-parent-tail'):
    common = struct.pack('<H6I', 0x18a, 1, 1, 1, 0, 0, 3)
    bytefield = b'\0' if byte_data is None else b'\1' + struct.pack('<I', len(byte_data)) + byte_data
    core = bytes([audience]) + bytefield + struct.pack('<H', data_format) + data_id
    core += bytes([coding_flag, coding_presence])
    if coding_presence:
        core += struct.pack('<H7I', coding_type, 5, 0, 0, 1, 1, 1, file_name) + bytes([late_bound])
    return common + core + tail


def parse(raw):
    return data_record_core_prefix(raw, POOLS, kernel_sha256=REQUEST_KERNEL_SHA256)


class DataRecordCoreTests(unittest.TestCase):
    def test_file_patterns_and_late_bound_are_definitions_only(self):
        for token, pattern in ((10, '*.bin'), (11, '*.Session')):
            with self.subTest(pattern=pattern):
                layout = parse(record(file_name=token))
                self.assertTrue(layout['prefixLayoutComplete'])
                self.assertFalse(layout['fieldLayoutComplete'])
                self.assertEqual(layout['dataFormat']['nativeU16'], 0x6b01)
                self.assertFalse(layout['dataFormat']['meaningVerified'])
                self.assertFalse(layout['dataId']['nativeValueDefined'])
                coding = layout['codingData']
                self.assertEqual(coding['fields']['fileName']['text'], pattern)
                self.assertTrue(coding['isLateBound'])
                self.assertTrue(coding['embeddedBodyLayoutComplete'])
                self.assertTrue(coding['fileNameMeaningVerified'])
                self.assertTrue(coding['lateBoundFlagMeaningVerified'])
                self.assertFalse(coding['lateBoundResolutionVerified'])
                for name in ('actualOemFileQualified', 'actualVehicleOriginalCoding',
                        'ecuApplicabilityVerified', 'runtimeStreamByteOrderVerified',
                        'runtimeReferenceResolutionVerified', 'runtimeMappingSelectionVerified',
                        'executionEnabled', 'recordEnvelopeVerified', 'diagnosticDidVerified'):
                    self.assertIs(layout[name], False)

    def test_vector_bytefield_uses_u32_length_and_preserves_every_byte(self):
        data = bytes(range(256)) * 256 + b'\xff'
        layout = parse(record(byte_data=data, data_id=b'\4\x12\x34\x56\x78'))
        self.assertTrue(layout['prefixLayoutComplete'])
        self.assertEqual(layout['byteField']['declaredBytes'], 65537)
        self.assertEqual(layout['byteField']['rawHex'], data.hex())
        self.assertEqual(layout['dataId']['nativeType'], 4)
        self.assertEqual(layout['dataId']['rawHex'], '12345678')
        self.assertEqual(layout['dataFormat']['nativeU16'], 0x6b01)
        self.assertFalse(layout['byteField']['actualVehicleOriginalCoding'])
        self.assertFalse(layout['diagnosticDidVerified'])

    def test_typed_value_bytefield_keeps_its_separate_u16_layout(self):
        layout = parse(record(byte_data=b'\0\xff', data_id=b'\2\1\3\0ABC'))
        self.assertTrue(layout['prefixLayoutComplete'])
        self.assertEqual(layout['byteField']['rawHex'], '00ff')
        self.assertEqual(layout['dataId']['rawHex'], '414243')
        self.assertTrue(layout['dataId']['present'])

    def test_every_prefix_truncation_keeps_the_unread_bytes(self):
        raw = record(byte_data=b'\0AB', tail=b'')
        for cut in range(2, len(raw)):
            with self.subTest(cut=cut):
                layout = parse(raw[:cut])
                self.assertFalse(layout['prefixLayoutComplete'])
                self.assertIsNotNone(layout['unsupportedReason'])
                self.assertEqual(layout['remainingRawHex'], raw[layout['endOffset']:cut].hex())
                self.assertEqual(layout['remainingSha256'], digest(raw[layout['endOffset']:cut]))

    def test_presence_and_declaration_are_independent_and_absence_consumes_no_body(self):
        for declared, present in ((0, 1), (1, 0), (0, 0)):
            layout = parse(record(coding_flag=declared, coding_presence=present))
            self.assertTrue(layout['prefixLayoutComplete'])
            self.assertEqual(layout['hasCodingDataFlag'], declared)
            self.assertEqual(layout['codingDataPresence'], present)
            self.assertEqual('codingData' in layout, bool(present))
            self.assertFalse(layout['itemDeclarationSemanticsVerified'])
            self.assertEqual(bytes.fromhex(layout['remainingRawHex']), b'unparsed-parent-tail')

    def test_unknown_audience_type_value_and_boolean_stop_the_prefix(self):
        for raw, reason in (
                (record(audience=1), 'ecu-config-audience-body-unqualified'),
                (record(coding_type=0x188), 'ecu-config-coding-data-type-unqualified-0x188'),
                (record(data_id=b'\xfe'), 'unsupported-compu-value-type-254'),
                (record(coding_flag=2), 'unsupported-object-presence-2'),
                (record(coding_presence=2), 'unsupported-object-presence-2'),
                (record(late_bound=2), 'unsupported-object-presence-2')):
            with self.subTest(reason=reason):
                layout = parse(raw)
                self.assertFalse(layout['prefixLayoutComplete'])
                self.assertEqual(layout['unsupportedReason'], reason)
                self.assertEqual(layout['remainingRawHex'], raw[layout['endOffset']:].hex())

    def test_oversized_bytefield_is_rejected_before_read_or_allocation(self):
        raw = record(byte_data=b'')
        raw = raw[:28] + struct.pack('<I', RECORD_LIMIT + 1) + raw[32:]
        layout = parse(raw)
        self.assertFalse(layout['prefixLayoutComplete'])
        self.assertEqual(layout['unsupportedReason'], 'ecu-config-bytefield-size-limit')
        self.assertNotIn('rawHex', layout['byteField'])
        self.assertEqual(layout['inspectedOffset'], 32)

    def test_kernel_type_and_record_size_guards(self):
        self.assertIsNone(parse(b''))
        self.assertIsNone(parse(b'\x84\x01'))
        layout = data_record_core_prefix(record(), POOLS, kernel_sha256='wrong')
        self.assertEqual(layout['unsupportedReason'], 'ecu-config-kernel-profile-mismatch')
        self.assertEqual(layout['endOffset'], 2)
        layout = parse(record(tail=b'#' * RECORD_LIMIT))
        self.assertEqual(layout['unsupportedReason'], 'ecu-config-record-size-limit')

    def test_exact_consumed_boundary_and_unknown_format_stay_explicit(self):
        raw = record(data_format=0xffff, late_bound=0)
        layout = parse(raw); boundary = len(raw) - len(b'unparsed-parent-tail')
        self.assertTrue(layout['prefixLayoutComplete'])
        self.assertEqual(layout['prefixEndOffset'], boundary)
        self.assertEqual(layout['endOffset'], boundary)
        self.assertEqual(layout['layoutSha256'], digest(raw[:boundary]))
        self.assertEqual(layout['dataFormat']['nativeU16'], 0xffff)
        self.assertFalse(layout['dataFormat']['meaningVerified'])
        self.assertFalse(layout['codingData']['isLateBound'])
        self.assertEqual(layout['remainingRawHex'], b'unparsed-parent-tail'.hex())

    def test_null_and_unresolved_file_names_do_not_qualify_oem_files(self):
        for token in (0, 999999):
            layout = parse(record(file_name=token))
            self.assertTrue(layout['prefixLayoutComplete'])
            self.assertEqual(layout['codingData']['fields']['fileName']['token'], token)
            self.assertFalse(layout['actualOemFileQualified'])
            self.assertFalse(layout['codingData']['actualOemFileQualified'])
            self.assertFalse(layout['executionEnabled'])


class DataRecordBodyTests(unittest.TestCase):
    @staticmethod
    def parse(raw):
        return data_record_body_prefix(raw, POOLS, kernel_sha256=REQUEST_KERNEL_SHA256)

    @staticmethod
    def tail(*, sdg=0, key=0, rule=0, size=1023, field_c0=11, f8=0, d8=0):
        return bytes([sdg]) + struct.pack('<4I', key, rule, size, field_c0) + bytes([f8, d8])

    def test_loader_end_preserves_the_unknown_envelope(self):
        raw = record(tail=self.tail() + b'#>\0')
        result = self.parse(raw)
        self.assertTrue(result['corePrefixLayoutComplete'])
        self.assertTrue(result['loaderBodyLayoutComplete'])
        self.assertEqual(result['remainingRawHex'], '233e00')
        self.assertEqual(result['endOffset'], len(raw) - 3)
        self.assertEqual(result['layoutSha256'], digest(raw[:-3]))
        for field in ('fieldLayoutComplete', 'recordEnvelopeVerified', 'nativeFieldC0MeaningVerified',
                'valueSizeUnitVerified', 'additionalAudienceRolesVerified', 'actualVehicleOriginalCoding',
                'ecuApplicabilityVerified', 'executionEnabled'):
            self.assertIs(result[field], False)

    def test_key_rule_size_and_neutral_string_preserve_the_source(self):
        result = self.parse(record(tail=self.tail(key=10, rule=9, size=0xfedcba98, field_c0=11)))
        self.assertTrue(result['loaderBodyLayoutComplete'])
        self.assertEqual(result['key']['text'], '*.bin')
        self.assertEqual(result['rule']['text'], 'attribute')
        self.assertEqual(result['configurationRecordValueSize']['nativeU32'], 0xfedcba98)
        self.assertFalse(result['configurationRecordValueSize']['unitVerified'])
        self.assertEqual(result['nativeFieldC0']['text'], '*.Session')
        self.assertFalse(result['nativeFieldC0MeaningVerified'])

    def test_nonempty_optional_collections_stop_before_their_unknown_bodies(self):
        for kwargs, reason in (({'sdg': 1}, 'ecu-config-special-data-group-body-unqualified'),
                ({'f8': 1}, 'ecu-config-additionalAudienceF8Presence-body-unqualified'),
                ({'d8': 1}, 'ecu-config-additionalAudienceD8Presence-body-unqualified')):
            with self.subTest(kwargs=kwargs):
                raw = record(tail=self.tail(**kwargs) + b'unknown-body')
                result = self.parse(raw)
                self.assertTrue(result['corePrefixLayoutComplete'])
                self.assertFalse(result['prefixLayoutComplete'])
                self.assertFalse(result['loaderBodyLayoutComplete'])
                self.assertEqual(result['unsupportedReason'], reason)
                self.assertNotIn('prefixEndOffset', result)
                self.assertEqual(result['remainingRawHex'], raw[result['endOffset']:].hex())

    def test_each_truncated_tail_retains_complete_core_and_unread_bytes(self):
        base = record(tail=b''); tail = self.tail()
        for cut in range(len(tail)):
            with self.subTest(cut=cut):
                raw = base + tail[:cut]; result = self.parse(raw)
                self.assertTrue(result['corePrefixLayoutComplete'])
                self.assertFalse(result['loaderBodyLayoutComplete'])
                self.assertIsNotNone(result['unsupportedReason'])
                self.assertEqual(result['remainingRawHex'], raw[result['endOffset']:].hex())

    def test_an_unknown_core_never_attempts_to_parse_the_tail(self):
        raw = record(audience=1, tail=self.tail())
        result = self.parse(raw)
        self.assertFalse(result['corePrefixLayoutComplete'])
        self.assertFalse(result['loaderBodyLayoutComplete'])
        self.assertNotIn('specialDataGroupPresence', result)
        self.assertEqual(result['unsupportedReason'], 'ecu-config-audience-body-unqualified')
        self.assertIsNone(self.parse(b''))


if __name__ == '__main__':
    unittest.main()
