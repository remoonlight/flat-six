import unittest

from scripts.diagnostics.decoder_reference import reference_piwis_fixed_value
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_fixed_values import decode_fixed_value
from scripts.diagnostics.piwis_numeric import probe_dop_numeric, synthetic_dop_probes
from scripts.diagnostics.piwis_recursive_numeric import synthetic_recursive_structure_probes
from scripts.diagnostics.piwis_table_struct import synthetic_table_structure_child_probes
from scripts.diagnostics.tests.test_piwis_recursive_numeric import fixture as recursive_fixture
from scripts.diagnostics.tests.test_piwis_table_struct import fixture as table_fixture


def fixed_dop(width=32, text=True, order=1):
    return {'recordType': 0x2c, 'fieldLayoutComplete': True,
        'codedType': {'present': True, 'recordType': 0x23, 'nativeKind': 2,
            'bitLength': width, 'bitMaskPresent': False,
            'nativeEncodingFieldsRaw': [4, 8, order, 0] if text else [7, 11, order, 0]},
        'physicalType': {'nativeBaseDataType': 4 if text else 5},
        'compuPrefix': {'compuMethod': {'nativeCategory': 0}}}


class PiwisFixedValueTests(unittest.TestCase):
    def test_all_iso88591_bytes_preserved_in_both_orders(self):
        data = b'prefix' + bytes(range(256)) + b'suffix'
        for order in (0, 1):
            layout = fixed_dop(2048, order=order)
            result = decode_fixed_value(layout, data, 6, kernel_sha256=PROFILE)
            self.assertEqual(result['candidateValue'], ''.join(chr(i) for i in range(256)))
            self.assertEqual(result['candidateValue'], reference_piwis_fixed_value(layout['codedType'], data, 6))
            self.assertEqual(result['candidateValueType'], 'unicode-text')
            self.assertEqual(len(result['candidateValue']), 256)
            self.assertEqual(result['rawValue'], bytes(range(256)).hex())

    def test_fixed_text_keeps_embedded_zero_high_bytes_and_whitespace(self):
        data = b' A\x00\x80\xff '
        result = probe_dop_numeric(fixed_dop(48), data, 0, kernel_sha256=PROFILE)
        self.assertTrue(result['ok'], result['reason'])
        self.assertEqual(result['candidateValue'], ' A\x00\x80\xff ')
        for name in ('physicalConversionQualified', 'runtimeStringStorageVerified',
                     'parameterBindingVerified', 'ecuApplicabilityVerified', 'executionEnabled'):
            self.assertFalse(result[name])

    def test_bytefields_keep_wire_order_for_many_widths(self):
        for width in (8, 16, 24, 32, 40, 48, 96, 128, 192, 2048, 32760):
            for order in (0, 1):
                layout = fixed_dop(width, text=False, order=order)
                size = width//8
                field = bytes((37*i+128) % 256 for i in range(size))
                prefix = b'pre' if size < 4092 else b''
                data = prefix+field
                result = decode_fixed_value(layout, data, len(prefix), kernel_sha256=PROFILE)
                self.assertEqual(result['candidateValue'], reference_piwis_fixed_value(layout['codedType'], data, len(prefix)))
                self.assertEqual(result['candidateValue'], field.hex())
                self.assertEqual(result['candidateValueType'], 'bytefield-hex')
                with self.assertRaisesRegex(ValueError, 'truncated-field'):
                    decode_fixed_value(layout, data[:-1], len(prefix), kernel_sha256=PROFILE)

    def test_positions_input_types_and_pdu_guard(self):
        layout = fixed_dop(8)
        for offset in (-1, True, 0.0, 4095):
            with self.assertRaisesRegex(ValueError, 'byte-position-invalid'):
                decode_fixed_value(layout, b'A', offset, kernel_sha256=PROFILE)
        for bit in (True, 0.0, 1, 7):
            with self.assertRaisesRegex(ValueError, 'zero-bit-position-only'):
                decode_fixed_value(layout, b'A', 0, bit, kernel_sha256=PROFILE)
        for data in (bytearray(b'A'), 'A', b'A'*4096):
            with self.assertRaisesRegex(ValueError, 'pdu-invalid'):
                decode_fixed_value(layout, data, 0, kernel_sha256=PROFILE)
        self.assertEqual(decode_fixed_value(layout, b'x'*4094+b'Z', 4094, kernel_sha256=PROFILE)['candidateValue'], 'Z')
        self.assertFalse(probe_dop_numeric(layout, b'A', 0, kernel_sha256='0'*64)['ok'])

    def test_unknown_masks_variable_encodings_and_conversions_rejected(self):
        for kind in (0, 1, True):
            layout = fixed_dop(); layout['codedType']['nativeKind'] = kind
            self.assertFalse(probe_dop_numeric(layout, b'A'*4, 0, kernel_sha256=PROFILE)['ok'])
        for width in (0, 1, 7, 9, 32768, True):
            self.assertFalse(probe_dop_numeric(fixed_dop(width), b'A'*4, 0, kernel_sha256=PROFILE)['ok'])
        changes = ({'bitMaskPresent': True, 'bitMaskRawHex': 'ffffffff'},
            {'bitMaskPresent': False, 'bitMaskRawHex': 0},
            {'bitMaskPresent': False, 'bitMaskRawHex': '00'},
            {'nativeEncodingFieldsRaw': [4, 8, True, 0]},
            {'nativeEncodingFieldsRaw': [4, 8, 2, 0]},
            {'nativeEncodingFieldsRaw': [4, 8, 1, 1]},
            {'nativeEncodingFieldsRaw': [5, 5, 1, 0]},
            {'nativeEncodingFieldsRaw': [6, 6, 1, 0]},
            {'nativeEncodingFieldsRaw': [4, 8, 1]})
        for change in changes:
            layout = fixed_dop(); layout['codedType'].update(change)
            self.assertFalse(probe_dop_numeric(layout, b'A'*4, 0, kernel_sha256=PROFILE)['ok'], change)
        for text in (True, False):
            for target in (1, 3, True):
                layout = fixed_dop(text=text); layout['physicalType']['nativeBaseDataType'] = target
                self.assertFalse(probe_dop_numeric(layout, b'A'*4, 0, kernel_sha256=PROFILE)['ok'])
            for category in (1, 3, False):
                layout = fixed_dop(text=text); layout['compuPrefix']['compuMethod']['nativeCategory'] = category
                self.assertFalse(probe_dop_numeric(layout, b'A'*4, 0, kernel_sha256=PROFILE)['ok'])

    def test_synthetic_fields_are_bounded_and_truncated(self):
        for text in (True, False):
            rows = synthetic_dop_probes(fixed_dop(128, text), kernel_sha256=PROFILE)
            self.assertEqual(len(rows), 5)
            self.assertEqual(sum(row['result']['ok'] for row in rows), 4)
            self.assertEqual(rows[-1]['result']['reason'], 'fixed-value-probe-truncated-field')
            self.assertTrue(all(not row['actualVehicleResponse'] for row in rows))

    def test_malformed_conversion_containers_keep_explicit_rejections(self):
        for field in ('physicalType', 'compuPrefix'):
            layout = fixed_dop(); layout[field] = ['bad']
            self.assertEqual(probe_dop_numeric(layout, b'A'*4, 0, kernel_sha256=PROFILE)['reason'],
                             'fixed-value-probe-conversion-layout-invalid')
        layout = fixed_dop(); layout['compuPrefix']['compuMethod'] = 'bad'
        self.assertFalse(probe_dop_numeric(layout, b'A'*4, 0, kernel_sha256=PROFILE)['ok'])

    def test_recursive_byte_text_uses_same_source_and_position_chain(self):
        for width in (24, 128):
            for text in (True, False):
                values = recursive_fixture(); values[3]['layout'] = fixed_dop(width, text)
                rows = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE)
                for row in rows:
                    result = row['result']; data = bytes.fromhex(row['pduHex'])
                    if row['case'] == 'truncated':
                        self.assertFalse(result['ok'])
                    else:
                        self.assertTrue(result['ok'], result['reason'])
                        self.assertEqual(result['candidateValue'], reference_piwis_fixed_value(values[3]['layout']['codedType'], data, 12))
                        self.assertEqual(result['structureChildPositions'], [2, 3])
                        self.assertFalse(result['rowValueDecoded'])

    def test_table_key_rejections_precede_byte_text_decoding(self):
        response, table, row, key_dop, structure, dop, pool = table_fixture()
        dop['layout'] = fixed_dop(40)
        rows = synthetic_table_structure_child_probes(response, 1, table, row, key_dop, structure, 0, dop,
            ascii_strings=pool, kernel_sha256=PROFILE)
        for row in rows:
            result = row['result']
            if row['case'] in ('different-key', 'truncated-key', 'truncated'):
                self.assertFalse(result['ok'])
            else:
                self.assertTrue(result['ok'], result['reason'])
                self.assertEqual(result['candidateValueType'], 'unicode-text')


if __name__ == '__main__':
    unittest.main()
