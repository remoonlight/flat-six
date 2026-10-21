import hashlib
import struct
import unittest

from scripts.diagnostics.piwis_parameters import PARAMETER_LIMIT, request_parameters, response_parameters


def collection(*parameters):
    return b'\x01\x79\x00' + struct.pack('<H', len(parameters)) + b''.join(parameters)


def base_parameter(tag=0xa5, flags=0x20, default=b'', kind=3, extra=b''):
    return (b'\x01' + struct.pack('<H6I', tag, 22, 19, 19, 0, 0, 0)
            + b'\x02' + struct.pack('<I', 7) + bytes([flags]) + default
            + struct.pack('<I', 4) + (struct.pack('<I', 22) if flags & 2 else b'')
            + struct.pack('<I', 0) + bytes([kind, 11]) + extra)


class PiwisParametersTests(unittest.TestCase):
    def setUp(self):
        self.tables = {'AStringData': {19: {'text': 'RawName'}, 22: {'text': 'SERVICE-ID'}},
                       'UStringData': {19: {'text': '名称'}, 22: {'text': '说明'}}}

    def parse(self, raw, start=0):
        return request_parameters(raw, start, self.tables)

    def test_known_prefix_preserves_exact_pools_positions_and_trailing_bytes(self):
        param = base_parameter()
        raw = collection(param) + b'\x00\x23\x3e\x00'
        result = self.parse(raw)
        row = result['parsedParameters'][0]
        self.assertEqual([f['text'] for f in row['fields']], ['说明', '名称', 'RawName', None, None, None])
        self.assertEqual((row['recordOffset'], row['endOffset']), (5, 5 + len(param)))
        self.assertEqual((row['bitPosition'], row['serializedBytePosition'], row['displayLevel']), (2, 7, 4))
        self.assertEqual(row['parameterTypeName'], 'CODED_CONST')
        self.assertEqual(row['serializedSha256'], hashlib.sha256(param).hexdigest())
        self.assertTrue(row['bytePositionAvailable'])
        self.assertTrue(result['parameterCollectionLayoutComplete'])
        self.assertEqual(result['remainingRawHex'], '00233e00')
        self.assertFalse(result['requestBodyVerified'])
        self.assertFalse(result['executionEnabled'])
        self.assertFalse(row['ecuApplicabilityVerified'])

    def test_unavailable_byte_position_and_protocol_flag_are_independent(self):
        row = self.parse(collection(base_parameter(flags=0x80)))['parsedParameters'][0]
        self.assertEqual(row['serializedBytePosition'], 7)
        self.assertFalse(row['bytePositionAvailable'])
        self.assertTrue(row['isProtocolParameter'])

    def test_odx_link_keeps_id_document_layer_flag_and_parameter_boundary(self):
        for layer_flag in (0, 1):
            link = b'\1\x50\2' + struct.pack('<II', 19, 22) + bytes([layer_flag])
            first = base_parameter(flags=0x30, kind=1, extra=link)
            result = self.parse(collection(first, base_parameter()))
            self.assertTrue(result['parameterCollectionLayoutComplete'])
            self.assertEqual(len(result['parsedParameters']), 2)
            row = result['parsedParameters'][0]; value = row['odxLink']
            self.assertEqual(value['recordType'], 0x250)
            self.assertEqual(value['nativeClass'], 'DbOdxLink')
            self.assertEqual(value['idRef']['text'], 'RawName')
            self.assertEqual(value['docRef']['text'], 'SERVICE-ID')
            self.assertEqual(value['isLayerDocType'], bool(layer_flag))
            self.assertEqual(value['endOffset'], row['endOffset'])
            self.assertEqual(row['endOffset'], 5 + len(first))
            for name in ('runtimeOdxResolutionVerified', 'referenceIdentityQualified',
                    'ecuApplicabilityVerified', 'executionEnabled'):
                self.assertIs(value[name], False)
            self.assertNotIn('objectReference', row)

    def test_absent_odx_link_consumes_only_presence_and_keeps_the_next_parameter(self):
        first = base_parameter(flags=0x30, extra=b'\0')
        result = self.parse(collection(first, base_parameter()))
        self.assertTrue(result['parameterCollectionLayoutComplete'])
        value = result['parsedParameters'][0]['odxLink']
        self.assertFalse(value['present'])
        self.assertNotIn('recordType', value)
        self.assertEqual(value['endOffset'] - value['recordOffset'], 1)
        self.assertEqual(result['parsedParameters'][1]['recordOffset'], 5 + len(first))

    def test_each_odx_link_truncation_never_publishes_a_partial_parameter(self):
        prefix = base_parameter(flags=0x30)
        link = b'\1\x50\2' + struct.pack('<II', 19, 22) + b'\0'
        for cut in range(len(link)):
            with self.subTest(cut=cut):
                raw = collection(prefix + link[:cut]); result = self.parse(raw)
                self.assertFalse(result['parameterCollectionLayoutComplete'])
                self.assertEqual(result['parsedParameters'], [])
                self.assertEqual(result['remainingSha256'], hashlib.sha256(raw[5:]).hexdigest())

    def test_unknown_odx_types_and_flags_do_not_resynchronize(self):
        for link in (b'\2', b'\1\x51\2', b'\1\x50\2' + struct.pack('<II', 19, 22) + b'\2'):
            raw = collection(base_parameter(flags=0x30, extra=link), base_parameter())
            result = self.parse(raw)
            self.assertFalse(result['parameterCollectionLayoutComplete'])
            self.assertEqual(result['parsedParameters'], [])
            self.assertEqual(result['remainingBytes'], len(raw) - 5)

    def test_odx_unresolved_null_and_inline_strings_keep_raw_source_identity(self):
        for id_ref in (struct.pack('<I', 0), struct.pack('<I', 999),
                struct.pack('<I', 0x80000003) + b'ABC'):
            link = b'\1\x50\2' + id_ref + struct.pack('<I', 22) + b'\0'
            result = self.parse(collection(base_parameter(flags=0x30, extra=link)))
            self.assertTrue(result['parameterCollectionLayoutComplete'])
            value = result['parsedParameters'][0]['odxLink']
            self.assertFalse(value['referenceIdentityQualified'])
            self.assertFalse(value['runtimeOdxResolutionVerified'])
            self.assertEqual(value['idRef']['pool'], 'AStringData')

    def test_uint32_default_has_no_signed_conversion_or_pdu_output(self):
        row = self.parse(collection(base_parameter(flags=0x23, default=b'\x0b\xff\xff\xff\xff')))['parsedParameters'][0]
        self.assertEqual(row['defaultValue']['value'], 4294967295)
        self.assertEqual(row['defaultValue']['type'], 'eA_UINT32')
        self.assertEqual(row['semantic']['text'], 'SERVICE-ID')
        self.assertNotIn('requestHex', row)

    def test_unicode_default_inline_and_unresolved_ids_keep_selected_pool(self):
        for token, expected in ((struct.pack('<I', 19), '名称'),
                                (struct.pack('<I', 0x80000002) + '😀'.encode('utf-16-le'), '😀'),
                                (struct.pack('<I', 100), None)):
            row = self.parse(collection(base_parameter(flags=0x21, default=b'\x0e' + token)))['parsedParameters'][0]
            self.assertEqual(row['defaultValue']['string']['text'], expected)
            self.assertEqual(row['defaultValue']['string']['pool'], 'UStringData')

    def test_structure_default_value_branches_keep_raw_bits_and_bounds(self):
        for kind, payload in ((0, b''), (255, b''), (1, struct.pack('<I', 19)),
                (2, b'\1\2\0\xaa\xff'), (3, b'\0'), (4, b'\xff' * 4),
                (5, b'\xff' * 8), (6, b'\xff' * 2), (7, b'\xff' * 4), (10, b'\xff' * 2)):
            param = base_parameter(flags=0x21, default=bytes((kind,)) + payload)
            result = self.parse(collection(param))
            self.assertTrue(result['parameterCollectionLayoutComplete'], result['blockedReason'])
            value = result['parsedParameters'][0]['defaultValue']
            self.assertEqual(value['nativeDataType'], kind)
            self.assertNotIn('requestHex', result)
            if kind in (4, 5, 6, 7, 10):
                self.assertEqual(value['rawHex'], payload.hex())
            for end in range(3, len(param)):
                partial = self.parse(collection(param[:end]))
                self.assertFalse(partial['parameterCollectionLayoutComplete'])
                self.assertEqual(partial['parsedParameters'], [])

    def test_coded_type_and_mask_keep_encoding_semantics_unverified(self):
        coded = b'\x01\x23\x00\x02' + struct.pack('<I', 8) + b'\x01' + struct.pack('<I', 2) + b'\xff\x03' + b'\x01\x0b\x01\x00'
        row = self.parse(collection(base_parameter(flags=0x24, extra=coded)))['parsedParameters'][0]
        self.assertEqual(row['codedType']['bitLength'], 8)
        self.assertEqual(row['codedType']['bitMaskRawHex'], 'ff03')
        self.assertEqual(row['codedType']['nativeEncodingFieldsRaw'], [1, 11, 1, 0])
        self.assertEqual(row['codedType']['nativeEncodingFieldsNamed'],
                         {'baseDataType': 1, 'encoding': 11, 'highLowByteOrder': 1, 'condensedBitMask': 0})
        self.assertTrue(row['codedType']['encodingFieldNamesVerified'])
        self.assertFalse(row['codedType']['encodingSemanticsVerified'])
        no_coded = self.parse(collection(base_parameter(flags=0x24, extra=b'\x00')))
        self.assertTrue(no_coded['parameterCollectionLayoutComplete'])
        self.assertFalse(no_coded['parsedParameters'][0]['codedType']['present'])

    def test_registered_encoding_names_never_qualify_value_decoding(self):
        names = ('PackedBCDEncoding', 'UnpackedBCDEncoding', 'OneComplementEncoding',
            'TwoComplementEncoding', 'SignMagnitudeEncoding', 'UTF8Encoding', 'UCS2Encoding',
            'IEEE754Encoding', 'ISO88591Encoding', 'ISO88592Encoding', 'Windows1252Encoding',
            'NoneEncoding')
        for encoding in (*range(12), 12, 255):
            coded = b'\1\x23\0\0' + struct.pack('<I', 8) + bytes((1, encoding, 1, 0))
            result = self.parse(collection(base_parameter(flags=0x24, extra=coded)))
            self.assertTrue(result['parameterCollectionLayoutComplete'])
            row = result['parsedParameters'][0]['codedType']
            self.assertEqual(row['encodingStrategy'], names[encoding] if encoding < 12 else None)
            self.assertEqual(row['encodingStrategyNameVerified'], encoding < 12)
            self.assertFalse(row['encodingSemanticsVerified'])
            self.assertFalse(result['executionEnabled'])
            self.assertNotIn('physicalValue', row)

    def test_table_reference_attributes_and_plain_reference_do_not_bind_objects(self):
        plain = struct.pack('<II', 19, 22)
        attributed = b'\x00' + struct.pack('<II', 22, 19) + b'\x02' + struct.pack('<II', 19, 22) + b'\x01' + struct.pack('<I', 22)
        result = self.parse(collection(base_parameter(tag=0xb2, flags=0x28, kind=9, extra=plain + attributed)))
        row = result['parsedParameters'][0]
        self.assertEqual(row['parameterTypeName'], 'TABLE_KEY')
        self.assertEqual(row['objectReference']['nativeField08']['text'], 'RawName')
        self.assertEqual([a['text'] for a in row['tableReference']['attributes']], ['RawName', 'SERVICE-ID'])
        self.assertFalse(row['tableReference']['bindingVerified'])
        self.assertEqual(row['nativeTableFlag'], 1)
        self.assertEqual(row['nativeTableString']['text'], 'SERVICE-ID')

    def test_unknown_parameter_stops_at_boundary_without_resync(self):
        good, unknown = base_parameter(), b'\x01\xbe\x00' + base_parameter()
        raw = collection(good, unknown, good)
        result = self.parse(raw)
        self.assertEqual(len(result['parsedParameters']), 1)
        self.assertEqual(result['declaredParameters'], 3)
        self.assertEqual(result['stopOffset'], 5 + len(good))
        self.assertEqual(result['inspectedOffset'], result['stopOffset'] + 3)
        self.assertEqual(result['remainingRawHex'], (unknown + good).hex())
        self.assertEqual(result['remainingSha256'], hashlib.sha256(unknown + good).hexdigest())
        self.assertEqual(result['blockedReason'], 'unsupported-parameter-type-0xbe')

    def test_unknown_extensions_and_value_types_never_publish_partial_parameter(self):
        cases = ((base_parameter(flags=0x30, extra=b'\x01\x51\x02'), 'odx-link'),
                 (base_parameter(flags=0x60), 'special-data-group'),
                 (base_parameter(kind=19, extra=b'\x01\xff\x00'), 'nrc-values-object-0xff'),
                 (base_parameter(tag=0xb2, extra=b'\x01'), 'inline-table-key'),
                 (base_parameter(flags=0x21, default=b'\x08'), 'default-value-type-8'),
                 (base_parameter(flags=0x24, extra=b'\x01\x23\x00\x03'), 'coded-type-kind-3'),
                 (base_parameter(flags=0x24, extra=b'\x01\xff\x00'), 'coded-type-tag-0xff'))
        for value, reason in cases:
            with self.subTest(reason=reason):
                result = self.parse(collection(value))
                self.assertIn(reason, result['blockedReason'])
                self.assertEqual(result['parsedParameters'], [])
                self.assertEqual(result['stopOffset'], 5)
                self.assertEqual(result['remainingRawHex'], value.hex())

    def test_every_truncation_is_blocked_and_retains_unread_parameter(self):
        value = base_parameter(tag=0xb2, flags=0x21, default=b'\x0b\x01\x00\x00\x00',
                               extra=b'\x00' + struct.pack('<II', 19, 22) + b'\x01' + struct.pack('<I', 22) + b'\x00' + struct.pack('<I', 19))
        raw = collection(value)
        for end in range(len(raw)):
            with self.subTest(end=end):
                result = self.parse(raw[:end])
                self.assertFalse(result['parameterCollectionLayoutComplete'])
                self.assertEqual(result['parsedParameters'], [])
                self.assertEqual(result['remainingRawHex'], raw[result['stopOffset']:end].hex())
        self.assertTrue(self.parse(raw)['parameterCollectionLayoutComplete'])

    def test_count_presence_tag_and_large_mask_limits_do_not_skip_bytes(self):
        for raw, reason in ((b'\x02', 'presence-2'), (b'\x01\x91\x00', 'collection-type-0x91'),
                            (b'\x01\x79\x00' + struct.pack('<H', PARAMETER_LIMIT + 1), 'count-limit'),
                            (collection(b'\x00'), 'null-parameter-object'),
                            (collection(base_parameter(flags=0x24, extra=b'\x01\x23\x00\x02' + struct.pack('<I', 8) + b'\x01\xff\xff\xff\xff')), 'truncated')):
            with self.subTest(reason=reason):
                result = self.parse(raw)
                self.assertIn(reason, result['blockedReason'])
                self.assertFalse(result['parameterCollectionLayoutComplete'])
        self.assertEqual(self.parse(b'\x00')['declaredParameters'], 0)
        self.assertTrue(self.parse(collection())['parameterCollectionLayoutComplete'])
        for start in (-1, 2):
            with self.assertRaises(ValueError):
                self.parse(b'\x00', start)

    def test_invalid_unicode_is_blocked_without_losing_original_bytes(self):
        value = base_parameter(flags=0x21, default=b'\x0e\x01\x00\x00\x80\x00\xd8')
        result = self.parse(collection(value))
        self.assertFalse(result['parameterCollectionLayoutComplete'])
        self.assertEqual(result['remainingRawHex'], value.hex())
        self.assertEqual(result['stopOffset'], 5)

    def test_table_struct_native_string_is_variable_width_and_reference_is_unbound(self):
        extra = struct.pack('<I', 0x80000003) + b'Key' + struct.pack('<II', 19, 22) + b'\x01' + struct.pack('<I', 22)
        row = self.parse(collection(base_parameter(tag=0xb0, kind=10, extra=extra)))['parsedParameters'][0]
        self.assertEqual(row['recordType'], 0xb0)
        self.assertEqual(row['parameterTypeName'], 'TABLE_STRUCT')
        self.assertEqual(row['nativeField130']['text'], 'Key')
        self.assertEqual(row['tableReference']['nativeField08']['text'], 'RawName')
        self.assertFalse(row['tableReference']['bindingVerified'])

    def test_table_struct_every_truncation_preserves_boundary(self):
        value = base_parameter(tag=0xb0, kind=10, extra=struct.pack('<III', 19, 22, 19) + b'\x01' + struct.pack('<I', 22))
        raw = collection(value)
        for end in range(5, len(raw)):
            with self.subTest(end=end):
                result = self.parse(raw[:end])
                self.assertFalse(result['parameterCollectionLayoutComplete'])
                self.assertEqual(result['parsedParameters'], [])
                self.assertEqual(result['stopOffset'], 5)
                self.assertEqual(result['remainingRawHex'], raw[5:end].hex())
        self.assertTrue(self.parse(raw)['parameterCollectionLayoutComplete'])

    def test_nrc_values_support_absent_empty_multiple_and_unicode_without_names_inference(self):
        for extra, expected in ((b'\x00', None), (b'\x01\xfa\x00\x00\x00', []),
                                (b'\x01\xfa\x00\x02\x00\x0b\xf0\x00\x00\x00\x0e' + struct.pack('<I', 19), [240, '名称'])):
            with self.subTest(expected=expected):
                result = self.parse(collection(base_parameter(kind=19, extra=extra)))
                self.assertTrue(result['parameterCollectionLayoutComplete'])
                nrc = result['parsedParameters'][0]['nrcValues']
                self.assertEqual(nrc['present'], expected is not None)
                if expected is not None:
                    self.assertEqual([v.get('value', v.get('string', {}).get('text')) for v in nrc['values']], expected)

    def test_nrc_unknown_value_count_and_every_truncation_keep_entire_parameter(self):
        good = b'\x01\xfa\x00\x02\x00\x0b\x01\x00\x00\x00\x0b\x02\x00\x00\x00'
        value = base_parameter(kind=19, extra=good)
        for end in range(len(value)):
            result = self.parse(collection(value[:end]))
            self.assertFalse(result['parameterCollectionLayoutComplete'])
            self.assertEqual(result['remainingRawHex'], value[:end].hex())
        for extra, reason in ((b'\x01\xfa\x00\x01\x00\x08', 'nrc-value-type-8'),
                               (b'\x01\xfa\x00' + struct.pack('<H', PARAMETER_LIMIT + 1), 'nrc-value-count-limit'),
                               (b'\x02', 'presence-2')):
            result = self.parse(collection(base_parameter(kind=19, extra=extra)))
            self.assertIn(reason, result['blockedReason'])
            self.assertEqual(result['parsedParameters'], [])

    def test_response_collection_is_separate_and_never_claims_physical_decoding(self):
        param = base_parameter(kind=19, extra=b'\x01\xfa\x00\x01\x00\x0b\x31\x00\x00\x00')
        raw = b'\x01\x92\x00\x01\x00' + param + b'\x02\x6f\x00\x23\x3e\x00'
        response = response_parameters(raw, 0, self.tables)
        self.assertTrue(response['parameterCollectionLayoutComplete'])
        self.assertFalse(response['responseBodyVerified'])
        self.assertNotIn('requestBodyVerified', response)
        self.assertEqual(response['remainingRawHex'], '026f00233e00')
        self.assertEqual(self.parse(raw)['blockedReason'], 'unsupported-request-collection-type-0x92')
        wrong = response_parameters(collection(param), 0, self.tables)
        self.assertEqual(wrong['blockedReason'], 'unsupported-response-collection-type-0x79')
        self.assertEqual(wrong['stopOffset'], 0)
        self.assertEqual(wrong['remainingRawHex'], collection(param).hex())

    def test_matching_request_parameter_preserves_position_length_and_truncation(self):
        value = base_parameter(tag=0xa7, kind=6, extra=struct.pack('<II', 1, 0xffffffff))
        parsed = self.parse(collection(value) + b'TAIL')
        row = parsed['parsedParameters'][0]
        self.assertTrue(parsed['parameterCollectionLayoutComplete'])
        self.assertEqual((row['requestBytePosition'], row['byteLength']), (1, 0xffffffff))
        self.assertEqual(row['parameterTypeName'], 'MATCHING_REQUEST_PARAM')
        self.assertEqual(parsed['remainingRawHex'], b'TAIL'.hex())
        for end in range(1, len(value)):
            parsed = self.parse(collection(value[:end]))
            self.assertFalse(parsed['parameterCollectionLayoutComplete'])
            self.assertEqual(parsed['parsedParameters'], [])
            self.assertEqual(parsed['remainingRawHex'], value[:end].hex())

    def test_variable_coded_type_reads_max_before_min_without_bit_length_guess(self):
        coded = b'\1\x23\0\1' + struct.pack('<II', 1024, 1) + b'\2\1\3\1\0'
        parsed = self.parse(collection(base_parameter(flags=0x24, extra=coded)))
        row = parsed['parsedParameters'][0]['codedType']
        self.assertEqual((row['maxLength'], row['minLength'], row['nativeTerminationRaw']), (1024, 1, 2))
        self.assertNotIn('bitLength', row)
        self.assertEqual(row['nativeEncodingFieldsRaw'], [1, 3, 1, 0])
        self.assertFalse(row['encodingSemanticsVerified'])
        for end in range(1, len(coded)):
            parsed = self.parse(collection(base_parameter(flags=0x24, extra=coded[:end])))
            self.assertFalse(parsed['parameterCollectionLayoutComplete'])
            self.assertEqual(parsed['parsedParameters'], [])


if __name__ == '__main__':
    unittest.main()
