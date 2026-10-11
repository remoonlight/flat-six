import struct
import unittest

from scripts.diagnostics.routine_body import routine_body_layout, MAX_SOURCE_FIELDS, MAX_SOURCE_STRING_BYTES
from scripts.diagnostics.routine_definitions import routine_definition


def source_string(value=b'0xF001A261\0'):
    return struct.pack('<H', len(value)) + value


def field(direct=False):
    return source_string() + (b'' if direct else struct.pack('<I', 0xF00001A3)) + struct.pack('<BHBI', 3, 0x1234, 5, 0xF0002B77)


def row(tag=2, count=1):
    return bytes([tag]) + (b'' if tag in (5, 7, 0x40) else struct.pack('<I', 0x2F00)) + struct.pack('<IB', 0x301, count) + field() * count


class RoutineBodyTests(unittest.TestCase):
    def assert_unqualified(self, parsed):
        for key in ('firstBodyByteSemanticsQualified', 'recordFieldSemanticsQualified', 'nativeCallerVersionQualified',
                    'recipeQualified', 'executionEnabled', 'liveVerified'):
            self.assertFalse(parsed[key])
        self.assertIsNone(parsed['requestHex']); self.assertIsNone(parsed['writePayload'])

    def test_words_fields_and_offsets_preserve_original_storage(self):
        body = b'\x01' + row() + b'\x81'
        parsed = routine_body_layout(body)
        self.assertEqual(parsed['status'], 'source-body-layout-candidate')
        self.assertEqual(parsed['recordCount'], 1); self.assertEqual(parsed['fieldCount'], 1)
        item = parsed['records'][0]; parameter = item['fields'][0]
        self.assertEqual(item['offset'], 1); self.assertEqual(item['tag'], 2)
        self.assertEqual(item['sourceFieldA'], 0x2F00); self.assertEqual(item['sourceFieldB'], 0x301)
        self.assertEqual(parameter['offset'], 11); self.assertEqual(parameter['sourceString']['offset'], 11)
        self.assertEqual(parameter['sourceString']['rawHex'], b'0xF001A261\0'.hex())
        self.assertEqual(parameter['sourceFieldC'], 0xF00001A3)
        self.assertEqual((parameter['sourceByte'], parameter['sourceWord'], parameter['sourceByte2']), (3, 0x1234, 5))
        self.assertEqual(parameter['sourceFieldD'], 0xF0002B77)
        self.assertEqual(parameter['end'], len(body)-1); self.assertEqual(item['end'], len(body)-1)
        self.assertEqual(parsed['consumedBytes'], len(body)); self.assertEqual(parsed['terminalTag'], 0x81)
        self.assert_unqualified(parsed)

    def test_first_body_byte_is_not_used_as_record_count(self):
        for first in (0, 1, 255):
            parsed = routine_body_layout(bytes([first]) + row(count=0) * 2 + b'\x81')
            self.assertEqual(parsed['recordCount'], 2); self.assertEqual(parsed['firstBodyByte'], first)
            self.assert_unqualified(parsed)

    def test_source_string_tags_and_conditional_field_id(self):
        for tag in (0x11, 0x12, 0x13, 0x14, 0x60, 0x70, 0x80):
            count = 1 if tag in (0x12, 0x80) else 0
            raw = bytes([tag]) + source_string(b'2F0000\0') + (b'\x01' + field(True) if count else b'')
            parsed = routine_body_layout(b'\x01' + raw + b'\x81')
            self.assertEqual(parsed['status'], 'source-body-layout-candidate')
            item = parsed['records'][0]
            self.assertEqual(item['fieldCount'], count); self.assertEqual(item['sourceFieldA'], None)
            self.assertEqual(item['prefixString']['rawHex'], b'2F0000\0'.hex())
            if count: self.assertIsNone(item['fields'][0]['sourceFieldC'])
            self.assert_unqualified(parsed)

    def test_native_no_first_word_tags_still_have_second_word(self):
        for tag in (5, 7, 0x40):
            parsed = routine_body_layout(b'\x01' + row(tag) + b'\x81')
            self.assertEqual(parsed['status'], 'source-body-layout-candidate')
            item = parsed['records'][0]
            self.assertIsNone(item['sourceFieldA']); self.assertEqual(item['sourceFieldB'], 0x301)
            self.assertEqual(item['fields'][0]['sourceFieldC'], 0xF00001A3)

    def test_unknown_tag_uses_only_native_storage_default_not_a_command(self):
        parsed = routine_body_layout(b'\x01' + row(0xFE, 0) + b'\x81')
        self.assertEqual(parsed['records'][0]['tag'], 0xFE)
        self.assert_unqualified(parsed)

    def test_all_truncations_refuse_complete_layout(self):
        body = b'\x01' + row() + b'\x81'
        for size in range(len(body)):
            with self.subTest(size=size):
                parsed = routine_body_layout(body[:size])
                self.assertEqual(parsed['status'], 'unresolved'); self.assertEqual(parsed['records'], [])
                self.assert_unqualified(parsed)

    def test_empty_records_are_source_layout_not_an_executable_function(self):
        parsed = routine_body_layout(b'\x00\x81')
        self.assertEqual(parsed['status'], 'source-body-layout-candidate'); self.assertEqual(parsed['recordCount'], 0)
        self.assert_unqualified(parsed)

    def test_native_seven_record_boundary_does_not_silently_drop_tail(self):
        complete = routine_body_layout(b'\x01' + row(count=0) * 6 + b'\x81')
        self.assertEqual(complete['recordCount'], 6)
        parsed = routine_body_layout(b'\x01' + row(count=0) * 7 + b'\x81')
        self.assertEqual(parsed['reason'], 'routine-body-native-record-limit'); self.assertEqual(parsed['records'], [])

    def test_terminator_must_be_last_byte(self):
        parsed = routine_body_layout(b'\x01\x81\x00')
        self.assertEqual(parsed['reason'], 'routine-body-unconsumed-tail'); self.assertEqual(parsed['records'], [])

    def test_local_field_and_string_resource_limits(self):
        too_many = b'\x01\x02' + struct.pack('<IIB', 0, 0, MAX_SOURCE_FIELDS + 1)
        self.assertEqual(routine_body_layout(too_many)['reason'], 'routine-body-field-count-protection')
        for size in (0, MAX_SOURCE_STRING_BYTES + 1):
            parsed = routine_body_layout(b'\x01\x11' + struct.pack('<H', size) + b'\x00' * size)
            self.assertEqual(parsed['reason'], 'routine-body-string-length-unqualified')
        self.assertEqual(routine_body_layout(b'\x01\x11' + source_string(b'not terminated'))['reason'], 'routine-body-string-framing-unqualified')

    def test_invalid_input_and_oversized_body(self):
        for body in ('0181', None, 1, b'\0' * 65537):
            parsed = routine_body_layout(body)
            self.assertEqual(parsed['reason'], 'routine-body-input-invalid'); self.assert_unqualified(parsed)

    def test_source_header_and_body_qualification_remain_separate(self):
        record = {'at': 1, 'payload_kind': 'u32count_u32leafptrs_id_leaves',
            'raw_hex': (b'\x02' + source_string(b'0x40005A15\0') + b'\x01' + row() + b'\x81').hex(),
            'labels': [{'id_hex': '40005A15', 'namespace': 'DSTREAM_CN.GAG', 'text': '来源功能'}]}
        parsed = routine_definition(record)
        self.assertEqual(parsed['name'], '来源功能'); self.assertEqual(parsed['bodyLayout']['recordCount'], 1)
        self.assertFalse(parsed['recipeQualified']); self.assertFalse(parsed['nativeHeaderSemanticsQualified'])
        malformed = routine_definition({**record, 'raw_hex': record['raw_hex'][:-2]})
        self.assertEqual(malformed['name'], '来源功能'); self.assertEqual(malformed['bodyLayout']['status'], 'unresolved')

    def test_native_reader_filter_uses_field_count_not_first_word(self):
        zero = b'\x04' + struct.pack('<IIB', 0x3103, 9, 0)
        nonzero = b'\x04' + struct.pack('<IIB', 0, 9, 1) + field()
        body = b'\x01' + row(0, 1) + zero + nonzero + row(2, 0) + b'\x81'
        parsed = routine_body_layout(body)
        case = parsed['nativeContextCandidates']['nineXOneReaderCase']
        self.assertEqual(case['tokenByte2EqualsAny'], [13, 14])
        self.assertEqual(case['keptSourceRecordIndices'], [2, 3])
        self.assertEqual(case['omittedSourceRecordIndices'], [0, 1])
        self.assertEqual(parsed['recordCount'], 4)  # Original source is never filtered or overwritten.
        self.assertEqual(parsed['records'][1]['sourceFieldA'], 0x3103)
        self.assertEqual(parsed['records'][2]['sourceFieldA'], 0)

    def test_native_postprocess_bfi_preserves_low_nibble_and_is_conditional(self):
        words = [0x3103, 0xFFFF300A, 0x4003, 0x30]
        body = b'\x01' + b''.join(b'\x02' + struct.pack('<IIB', word, 0, 0) for word in words) + b'\x81'
        parsed = routine_body_layout(body); candidates = parsed['nativeContextCandidates']
        post = candidates['postprocessCase']
        self.assertEqual(post['nativeStringArgumentEquals'], 'PCM31_A3_2')
        self.assertFalse(post['argumentOriginQualified']); self.assertFalse(candidates['actualCallerSelected'])
        self.assertEqual(post['sourceFieldAPatches'], [
            {'sourceRecordIndex': 0, 'originalSourceFieldA': 0x3103, 'candidateSourceFieldA': 0x33},
            {'sourceRecordIndex': 1, 'originalSourceFieldA': 0xFFFF300A, 'candidateSourceFieldA': 0x3A}])
        self.assertEqual([r['sourceFieldA'] for r in parsed['records']], words)
        for key in ('nativeCallerVersionQualified', 'recipeQualified', 'executionEnabled', 'liveVerified'):
            self.assertFalse(candidates[key])
        self.assertIsNone(candidates['requestHex']); self.assertIsNone(candidates['writePayload'])
        self.assert_unqualified(parsed)

    def test_native_postprocess_case_respects_filter_and_missing_words(self):
        body = b'\x01\x04' + struct.pack('<IIB', 0x3103, 0, 0) + row(5, 0) + b'\x11' + source_string(b'3103\0') + b'\x81'
        parsed = routine_body_layout(body); candidates = parsed['nativeContextCandidates']
        self.assertEqual(candidates['nineXOneReaderCase']['keptSourceRecordIndices'], [1, 2])
        self.assertEqual(candidates['postprocessCase']['sourceFieldAPatches'], [])
        self.assertEqual(candidates['postprocessCase']['undeclaredNativeWordSourceIndices'], [1, 2])
        self.assertFalse(candidates['postprocessCase']['completeNativeRecordStateKnown'])
        empty = routine_body_layout(b'\x00\x81')['nativeContextCandidates']
        self.assertEqual(empty['nineXOneReaderCase']['keptSourceRecordIndices'], [])
        self.assertEqual(empty['postprocessCase']['sourceFieldAPatches'], [])

    def test_unresolved_source_never_publishes_context_candidates(self):
        body = b'\x01' + row() + b'\x81'
        for size in range(len(body)):
            self.assertIsNone(routine_body_layout(body[:size])['nativeContextCandidates'])
        case = routine_body_layout(body)['nativeContextCandidates']['readerSelection']
        self.assertEqual(case, {'tokenByteOffset': 2, 'modernReaderMinimum': 11,
                               'modernReader': 'GetActChileCmdDop', 'olderReader': 'GetActChileCmdDopold'})


if __name__ == '__main__':
    unittest.main()
