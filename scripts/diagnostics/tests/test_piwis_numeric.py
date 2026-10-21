import copy
from fractions import Fraction
import math
import struct
import unittest

from scripts.diagnostics.decoder_reference import reference_piwis_unsigned, reference_piwis_signed, reference_piwis_rational
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_numeric import decode_unsigned_field, decode_signed_field, floating_rational, probe_dop_numeric, synthetic_dop_probes


def coded(width=16, high_low=1):
    return {'present': True, 'recordType': 0x23, 'nativeKind': 2, 'bitLength': width,
        'bitMaskPresent': False, 'nativeEncodingFieldsRaw': [1, 11, high_low, 0]}


def coefficients(numerator, denominator):
    return {'recordType': 0x14, 'numerator': [{'rawHex': struct.pack('<d', x).hex()} for x in numerator],
        'denominator': [{'rawHex': struct.pack('<d', x).hex()} for x in denominator]}


def dop(category=1, target=3):
    unlimited = {'nativeInterval': 0x6d03, 'value': {'nativeValueDefined': False, 'nativeType': 255}}
    scale = {'nativeField10': coefficients([999, 50], []),
        'nativeField20': coefficients([-40, 0.1], [1]),
        'lowerLimitCoded': copy.deepcopy(unlimited), 'upperLimitCoded': copy.deepcopy(unlimited)}
    return {'recordType': 0x2c, 'fieldLayoutComplete': True, 'codedType': coded(),
        'physicalType': {'nativeBaseDataType': target},
        'compuPrefix': {'compuMethod': {'nativeCategory': category, 'nativeField28': {
            'scales': {'declaredScales': 1, 'scales': [scale]}}}}}


class PiwisNumericTests(unittest.TestCase):
    def test_ordinary_mask_preserves_width_and_applies_after_order_and_bit_extraction(self):
        for width in range(1, 33):
            for bit in (0, 3, 7):
                size = (width+bit+7)//8
                for order in (0, 1):
                    for mask in (0, (1 << width)-1, ((1 << width)-1) & 0x55555555):
                        c = {**coded(width, order), 'bitMaskPresent': True,
                             'bitMaskRawHex': mask.to_bytes((width+7)//8, 'big').hex()}
                        for pattern in (0, (1 << (size*8))-1, 0x123456789a & ((1 << (size*8))-1)):
                            data = b'prefix'+pattern.to_bytes(size, 'big' if order else 'little')
                            self.assertEqual(decode_unsigned_field(data, c, 6, bit, kernel_sha256=PROFILE),
                                reference_piwis_unsigned(c, data, 6, bit))
        c = {**coded(16, 0), 'bitMaskPresent': True, 'bitMaskRawHex': '00ff'}
        self.assertEqual(decode_unsigned_field(b'\x34\x12', c, 0, kernel_sha256=PROFILE), 0x34)

    def test_signed_mask_keeps_declared_sign_bit_instead_of_mask_popcount(self):
        for mask, expected in (('1fff', -1), ('00ff', 255), ('1000', -4096), ('0000', 0)):
            c = {**coded(13), 'nativeEncodingFieldsRaw': [0, 3, 1, 0],
                 'bitMaskPresent': True, 'bitMaskRawHex': mask}
            value = decode_signed_field(b'\xff\xff', c, 0, kernel_sha256=PROFILE)
            self.assertEqual(value, expected)
            self.assertEqual(value, reference_piwis_signed(c, b'\xff\xff', 0))

    def test_malformed_and_condensed_masks_remain_rejected(self):
        for raw in ('', 'ff', 'ffffff', 'ff gg', 'FF FF', None):
            with self.assertRaisesRegex(ValueError, 'mask-unqualified'):
                decode_unsigned_field(b'\xff\xff', {**coded(), 'bitMaskPresent': True, 'bitMaskRawHex': raw}, 0, kernel_sha256=PROFILE)
        with self.assertRaisesRegex(ValueError, 'condensed-mask-unqualified'):
            decode_unsigned_field(b'\xff\xff', {**coded(), 'bitMaskPresent': True, 'bitMaskRawHex': '0fff',
                'nativeEncodingFieldsRaw': [1, 11, 1, 1]}, 0, kernel_sha256=PROFILE)

    def test_signed_all_widths_positions_orders_and_boundaries(self):
        for width in range(1, 33):
            for bit in range(8):
                size = (width + bit + 7) // 8
                values = (0, -1, -(1 << (width-1)), (1 << (width-1))-1)
                for order in (0, 1):
                    c = {**coded(width, order), 'nativeEncodingFieldsRaw': [0, 3, order, 0]}
                    for value in values:
                        # Set every unused bit to one: only the declared sign bit matters.
                        outside = ((1 << (size*8))-1) ^ (((1 << width)-1) << bit)
                        raw = outside | ((value & ((1 << width)-1)) << bit)
                        data = b'prefix' + raw.to_bytes(size, 'big' if order else 'little')
                        actual = decode_signed_field(data, c, 6, bit, kernel_sha256=PROFILE)
                        self.assertEqual(actual, value)
                        self.assertEqual(actual, reference_piwis_signed(c, data, 6, bit))
                    with self.assertRaisesRegex(ValueError, 'truncated-field'):
                        decode_signed_field(data[:-1], c, 6, bit, kernel_sha256=PROFILE)

    def test_signed_identity_and_floating_negative_values(self):
        for category, target in ((0, 0), (1, 2), (1, 3)):
            layout = dop(category, target)
            layout['codedType']['nativeEncodingFieldsRaw'] = [0, 3, 1, 0]
            for value in (-32768, -1, 0, 32767):
                result = probe_dop_numeric(layout, (value & 65535).to_bytes(2, 'big'), 0, kernel_sha256=PROFILE)
                self.assertTrue(result['ok'], result['reason'])
                self.assertEqual(result['rawValue'], value)
                expected = value if category == 0 else float(reference_piwis_rational(
                    layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]['nativeField20'], value))
                if target == 2:
                    expected = struct.unpack('<f', struct.pack('<f', expected))[0]
                self.assertTrue(math.isclose(result['candidateValue'], expected, rel_tol=2e-15, abs_tol=1e-12))
                self.assertFalse(result['executionEnabled'])
                self.assertFalse(result['physicalConversionQualified'])
            cases = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
            self.assertEqual({x['case'] for x in cases}, {'zero', 'positive-maximum', 'negative-minimum',
                'minus-one', 'ascending-bytes', 'truncated'})
            self.assertFalse(cases[-1]['result']['ok'])

    def test_signed_does_not_qualify_other_encodings_masks_or_physical_types(self):
        c = {**coded(), 'nativeEncodingFieldsRaw': [0, 3, 1, 0]}
        for fields in ([0, 2, 1, 0], [0, 4, 1, 0], [0, 11, 1, 0], [1, 3, 1, 0],
                       [False, 3, 1, 0], [0, 3, 1, 1], [0, 3, 2, 0], None):
            with self.assertRaises(ValueError):
                decode_signed_field(b'\xff\xff', {**c, 'nativeEncodingFieldsRaw': fields}, 0, kernel_sha256=PROFILE)
        for change in ({'bitMaskPresent': True}, {'bitMaskRawHex': 'ff'}, {'nativeKind': 1}):
            with self.assertRaises(ValueError):
                decode_signed_field(b'\xff\xff', {**c, **change}, 0, kernel_sha256=PROFILE)
        with self.assertRaises(ValueError):
            decode_unsigned_field(b'\xff\xff', c, 0, kernel_sha256=PROFILE)
        for category, target, signed in ((0, 1, True), (0, 0, False), (3, 4, True), (1, 4, True)):
            layout = dop(category, target)
            if signed: layout['codedType'] = c
            self.assertFalse(probe_dop_numeric(layout, b'\xff\xff', 0, kernel_sha256=PROFILE)['ok'])

    def test_byte_orders_widths_offsets_match_independent_bit_sum(self):
        for width in (8, 16, 24, 32):
            for order in (0, 1):
                c = coded(width, order)
                for payload in (b'\0\0\0\0', b'\xff'*4, b'\x12\x34\x56\x78', b'\x80\0\0\1'):
                    data = b'HEADER' + payload + b'TAIL'
                    actual = decode_unsigned_field(data, c, 6, kernel_sha256=PROFILE)
                    self.assertEqual(actual, reference_piwis_unsigned(c, data, 6))
        self.assertEqual(decode_unsigned_field(b'\x12\x34', coded(), 0, kernel_sha256=PROFILE), 0x1234)
        self.assertEqual(decode_unsigned_field(b'\x12\x34', coded(high_low=0), 0, kernel_sha256=PROFILE), 0x3412)

    def test_kernel_unknown_encoding_mask_and_bit_layouts_are_rejected(self):
        changes = ({'nativeKind': 0}, {'bitLength': 0}, {'bitLength': 33}, {'bitMaskPresent': True},
            {'nativeEncodingFieldsRaw': [1, 0, 1, 0]}, {'nativeEncodingFieldsRaw': [1, 11, 2, 0]},
            {'nativeEncodingFieldsRaw': [1, 11, 1, 1]}, {'nativeEncodingFieldsRaw': [True, 11, 1, 0]})
        for change in changes:
            with self.assertRaises(ValueError):
                decode_unsigned_field(b'\0'*4, {**coded(), **change}, 0, kernel_sha256=PROFILE)
        for sha, bit in (('0'*64, 0), (PROFILE, 8), (PROFILE, -1), (PROFILE, False)):
            with self.assertRaises(ValueError):
                decode_unsigned_field(b'\0'*4, coded(), 0, bit, kernel_sha256=sha)

    def test_truncation_invalid_positions_and_payload_limits(self):
        for data, offset in ((b'\0', 0), (b'\0'*4, -1), (b'\0'*4, True),
                             (b'\0'*4096, 0), (bytearray(b'\0\0'), 0)):
            with self.assertRaises(ValueError):
                decode_unsigned_field(data, coded(), offset, kernel_sha256=PROFILE)

    def test_all_widths_and_bit_positions_match_independent_byte_bit_indexing(self):
        patterns = (b'\0'*5, b'\xff'*5, b'\x12\x34\x56\x78\x9a', b'\x80\0\0\0\1')
        for width in range(1, 33):
            for bit in range(8):
                for order in (0, 1):
                    c = coded(width, order)
                    for pattern in patterns:
                        pdu = b'prefix' + pattern + b'tail'
                        actual = decode_unsigned_field(pdu, c, 6, bit, kernel_sha256=PROFILE)
                        self.assertEqual(actual, reference_piwis_unsigned(c, pdu, 6, bit))
        self.assertEqual(decode_unsigned_field(b'\xaa\xf0', coded(9), 0, 3, kernel_sha256=PROFILE), 350)

    def test_unused_bits_do_not_contribute_and_cross_byte_truncation_is_rejected(self):
        for width, bit in ((1, 0), (1, 7), (7, 2), (12, 4), (31, 7), (32, 7)):
            size = (width + bit + 7) // 8
            outside = ((1 << (size * 8)) - 1) ^ (((1 << width) - 1) << bit)
            for order in (0, 1):
                data = outside.to_bytes(size, 'big' if order else 'little')
                self.assertEqual(decode_unsigned_field(data, coded(width, order), 0, bit, kernel_sha256=PROFILE), 0)
                with self.assertRaisesRegex(ValueError, 'truncated-field'):
                    decode_unsigned_field(data[:-1], coded(width, order), 0, bit, kernel_sha256=PROFILE)

    def test_float_horner_matches_exact_binary_coefficient_power_sum(self):
        for nums, dens in (([-40, 0.1], [1]), ([5, 1], [0.00015]),
                ([0, 1], [36000]), ([-5, 0.00015], []), ([3, -2, 0.25], [2, 0.5]), ([], [])):
            c = coefficients(nums, dens)
            for value in (0, 1, 127, 65535):
                actual = floating_rational(c, value)
                expected = float(reference_piwis_rational(c, value))
                self.assertTrue(math.isclose(actual, expected, rel_tol=2e-15, abs_tol=1e-12))

    def test_coefficient_raw_bytes_are_authoritative_and_nonfinite_rejected(self):
        c = coefficients([2], [])
        c['numerator'][0]['value'] = 999
        self.assertEqual(floating_rational(c, 10), 2)
        for bad in (coefficients([math.inf], []), coefficients([1], [0]),
                    {'numerator': [{'rawHex': '00'}], 'denominator': []},
                    coefficients([0, 1e308], []), coefficients([], [0, 1e308])):
            with self.assertRaises(ValueError):
                floating_rational(bad, 100)

    def test_false_direction_uses_second_coefficient_and_keeps_qualification_gaps(self):
        result = probe_dop_numeric(dop(), b'prefix\x01\xf4tail', 6, kernel_sha256=PROFILE)
        self.assertTrue(result['ok'], result['reason'])
        self.assertEqual(result['rawValue'], 500)
        self.assertAlmostEqual(result['candidateValue'], 10)
        for key in ('physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled',
                    'parameterBindingVerified', 'constraintReferencesVerified', 'unitSemanticsVerified',
                    'runtimeFloatingEnvironmentVerified'):
            self.assertFalse(result[key])

    def test_identity_and_float32_destination_are_distinct(self):
        identity = probe_dop_numeric(dop(0, 1), b'\xff\xff', 0, kernel_sha256=PROFILE)
        self.assertEqual(identity['candidateValue'], 65535)
        self.assertIs(type(identity['candidateValue']), int)
        layout = dop(1, 2)
        layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]['nativeField20'] = coefficients([0, 1], [10])
        single = probe_dop_numeric(layout, b'\0\1', 0, kernel_sha256=PROFILE)
        self.assertEqual(single['candidateValue'], 0.10000000149011612)

    def test_unknown_conversion_limits_scales_and_missing_inverse_do_not_emit_value(self):
        rows = [dop(3, 4), dop(4, 1), {**dop(), 'fieldLayoutComplete': False}, None]
        bad_limit = dop();bad_limit['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]['lowerLimitCoded']['value']['nativeValueDefined'] = True
        rows.append(bad_limit)
        no_inverse = dop();del no_inverse['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]['nativeField20']
        rows.append(no_inverse)
        extra = dop();extra['compuPrefix']['compuMethod']['nativeField28']['scales']['declaredScales'] = 2
        rows.append(extra)
        for layout in rows:
            result = probe_dop_numeric(layout, b'\0\1', 0, kernel_sha256=PROFILE)
            self.assertFalse(result['ok'])
            self.assertNotIn('candidateValue', result)
            self.assertFalse(result['executionEnabled'])

    def test_reference_reads_ieee_bits_without_production_coefficient_decoder(self):
        c = {'numerator': [{'rawHex': '000000000000e03f'}], 'denominator': []}
        self.assertEqual(reference_piwis_rational(c, 99), Fraction(1, 2))
        c['numerator'][0]['rawHex'] = '0100000000000000'
        self.assertEqual(reference_piwis_rational(c, 0), Fraction(1, 2**1074))
