import copy
import itertools
import math
import struct
import unittest

from scripts.diagnostics.decoder_reference import (reference_piwis_scale_linear,
    reference_piwis_unsigned, reference_piwis_signed)
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_numeric import probe_dop_numeric, synthetic_dop_probes
from scripts.diagnostics.tests.test_piwis_numeric import coefficients
from scripts.diagnostics.tests.test_piwis_scale_limits import limited


def layout_for(ranges=((0, 76, -664, 8), (77, 177, -127, 1), (178, 255, -1364, 8)), *, signed=False, target=3):
    layout, first = limited(signed=signed)
    layout['codedType']['bitLength'] = 8
    layout['physicalType']['nativeBaseDataType'] = target
    method = layout['compuPrefix']['compuMethod'];method['nativeCategory'] = 2
    base = method['nativeField28'];base['recordType'] = 5
    base['default'] = {'nativeType': 255, 'nativeValueDefined': False, 'rawHex': ''}
    rows = []
    for lower, upper, offset, factor in ranges:
        _, scale = limited(lower, upper, signed=signed)
        scale.update(recordType=0x19, nativeField20=coefficients([offset, factor], []))
        rows.append(scale)
    base['scales'] = {'declaredScales': len(rows), 'scales': rows}
    return layout, base


def set_default(base, kind, value):
    raw = (struct.pack('<f' if kind == 4 else '<d', value) if kind in (4, 5)
           else value.to_bytes(2 if kind in (6, 10) else 4, 'little', signed=kind in (6, 7)))
    base['default'] = {'nativeType': kind, 'nativeValueDefined': True, 'rawHex': raw.hex()}


class PiwisScaleLinearTests(unittest.TestCase):
    def compare(self, layout, data, offset=0, bit=0):
        c = layout['codedType']
        raw = (reference_piwis_signed if c['nativeEncodingFieldsRaw'][:2] == [0, 3]
               else reference_piwis_unsigned)(c, data, offset, bit)
        result = probe_dop_numeric(layout, data, offset, bit, kernel_sha256=PROFILE)
        expected = reference_piwis_scale_linear(layout, raw)
        self.assertTrue(result['ok'], result)
        number = -0.0 if expected['negativeZero'] else float(expected['value'])
        if layout['physicalType']['nativeBaseDataType'] == 2:
            number = struct.unpack('<f', struct.pack('<f', number))[0]
        self.assertEqual(result['rawValue'], raw)
        self.assertAlmostEqual(result['candidateValue'], number)
        if number == 0 and expected['selection']['kind'] == 'explicit-forward-default':
            self.assertEqual(math.copysign(1, result['candidateValue']), math.copysign(1, number))
        self.assertEqual(result['scaleSelection'], expected['selection'])
        self.assertEqual(result['probeKind'], 'scale-linear-float')
        for flag in ('executionEnabled', 'physicalConversionQualified', 'ecuApplicabilityVerified',
                     'parameterBindingVerified', 'unitSemanticsVerified', 'constraintReferencesVerified',
                     'runtimeFloatingEnvironmentVerified'):
            self.assertFalse(result[flag])
        return result

    def test_retained_three_ranges_all_256_values_and_junctions(self):
        layout, base = layout_for()
        for value in range(256):
            result = self.compare(layout, bytes([value]))
            index = 0 if value <= 76 else 1 if value <= 177 else 2
            self.assertEqual(result['scaleIndex'], index)
            expected = value*8-664 if index == 0 else value-127 if index == 1 else value*8-1364
            self.assertEqual(result['candidateValue'], expected)

    def test_overlap_uses_serialized_first_match_without_second_formula(self):
        layout, base = layout_for(((0, 100, 1000, 1), (50, 255, -1000, 2)))
        for value in (0, 49, 50, 99, 100, 101, 255):self.compare(layout, bytes([value]))
        self.assertEqual(self.compare(layout, b'\x32')['candidateValue'], 1050)
        base['scales']['scales'].reverse()
        self.assertEqual(self.compare(layout, b'\x32')['candidateValue'], -900)
        base['scales']['scales'][1]['nativeField20'] = coefficients([1], [0])
        self.assertEqual(self.compare(layout, b'\x32')['candidateValue'], -900)
        self.assertEqual(probe_dop_numeric(layout, b'\x00', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-zero-denominator')

    def test_gap_empty_interval_and_undefined_default_fail_without_guessing(self):
        layout, base = layout_for(((10, 20, 0, 1), (50, 40, 999, 1), (40, 50, 100, 1)))
        for value in (10, 20, 40, 50):self.compare(layout, bytes([value]))
        for value in (0, 9, 21, 30, 39, 51, 255):
            result = probe_dop_numeric(layout, bytes([value]), 0, kernel_sha256=PROFILE)
            self.assertEqual(result['reason'], 'numeric-probe-scale-linear-no-match')
            self.assertNotIn('candidateValue', result)
        for kind in (0, 255):
            base['default']['nativeType'] = kind
            self.assertEqual(probe_dop_numeric(layout, b'\0', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-scale-linear-no-match')

    def test_explicit_default_numeric_types_and_float_rounding_preserve_zero_sign(self):
        for target, kind, value in itertools.product((2, 3), (4, 5, 6, 7, 10, 11), (0, 3)):
            layout, base = layout_for(((10, 20, 0, 1),), target=target)
            set_default(base, kind, value)
            self.compare(layout, b'\0')
            self.compare(layout, b'\x0f')
        for kind, value in ((4, -0.0), (5, -0.0), (4, 1.25), (5, 2**24+1), (6, -32768),
                            (7, -(2**31)), (10, 65535), (11, 2**32-1)):
            for target in (2, 3):
                layout, base = layout_for(((10, 20, 0, 1),), target=target)
                set_default(base, kind, value);self.compare(layout, b'\0')

    def test_default_only_empty_collection_matches_native_zero_count_branch(self):
        layout, base = layout_for(())
        set_default(base, 5, 12.5)
        for value in (0, 127, 255):self.compare(layout, bytes([value]))
        probes = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
        self.assertTrue(probes[0]['result']['ok'])
        base['default'] = {'nativeType': 255, 'nativeValueDefined': False, 'rawHex': ''}
        self.assertEqual(probe_dop_numeric(layout, b'\0', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-scale-linear-no-match')

    def test_open_signed_masked_fields_with_both_orders_and_cross_byte_positions(self):
        for signed, order, bit in itertools.product((False, True), (0, 1), (0, 3, 7)):
            layout, base = layout_for(((-10 if signed else 10, 20, 5, 2), (25, 80, -5, 1)), signed=signed)
            c = layout['codedType'];c['nativeEncodingFieldsRaw'][2] = order
            c.update(bitMaskPresent=True, bitMaskRawHex='f7')
            base['scales']['scales'][0]['lowerLimitCoded']['nativeInterval'] = 0x6d01
            base['scales']['scales'][0]['upperLimitCoded']['nativeInterval'] = 0x6d01
            set_default(base, 5, -0.5)
            size = (8+bit+7)//8
            for value in (-128, -10, -9, -1, 0, 20, 25, 80, 127) if signed else (0, 10, 11, 20, 25, 80, 255):
                data = b'prefix'+((value & 255) << bit).to_bytes(size, 'big' if order else 'little')
                self.compare(layout, data, 6, bit)
                self.assertEqual(probe_dop_numeric(layout, data[:-1], 6, bit, kernel_sha256=PROFILE)['reason'], 'numeric-probe-truncated-field')

    def test_nonfinite_malformed_unsupported_default_blocks_only_when_default_needed(self):
        layout, base = layout_for(((10, 20, 0, 1),))
        for kind, value in ((4, math.inf), (5, math.nan), (5, -math.inf)):
            set_default(base, kind, value)
            self.compare(layout, b'\x0f')
            result = probe_dop_numeric(layout, b'\0', 0, kernel_sha256=PROFILE)
            self.assertEqual(result['reason'], 'numeric-probe-scale-linear-default-not-finite')
        for value in ({'nativeType': 14, 'nativeValueDefined': True, 'string': {'text': '1'}},
                      {'nativeType': 5, 'nativeValueDefined': True, 'rawHex': '00'},
                      {'nativeType': 7, 'nativeValueDefined': True, 'rawHex': 'gg000000'}):
            base['default'] = value
            self.compare(layout, b'\x0f')
            self.assertEqual(probe_dop_numeric(layout, b'\0', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-scale-linear-default-unqualified')
        set_default(base, 5, 1e100);layout['physicalType']['nativeBaseDataType'] = 2
        self.assertEqual(probe_dop_numeric(layout, b'\0', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-float32-overflow')

    def test_wrong_kernel_missing_coded_base_counts_limits_and_coefficients_remain_rejected(self):
        layout, base = layout_for()
        self.assertEqual(probe_dop_numeric(layout, b'\0', 0, kernel_sha256='0'*64)['reason'], 'numeric-probe-kernel-profile-mismatch')
        for count in (-1, True, 2, 4097):
            bad = copy.deepcopy(layout);bad['compuPrefix']['compuMethod']['nativeField28']['scales']['declaredScales'] = count
            self.assertFalse(probe_dop_numeric(bad, b'\0', 0, kernel_sha256=PROFILE)['ok'])
        bad = copy.deepcopy(layout);bad['compuPrefix']['compuMethod']['nativeField28'] = None
        self.assertEqual(probe_dop_numeric(bad, b'\0', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-scale-linear-coded-base-missing')
        for field, value in (('lowerLimitCoded', None), ('nativeField20', None), ('recordType', 0x18)):
            bad = copy.deepcopy(layout);bad['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0][field] = value
            self.assertFalse(probe_dop_numeric(bad, b'\0', 0, kernel_sha256=PROFILE)['ok'])
        for value in (None, {'nativeType': True, 'nativeValueDefined': True},
                      {'nativeType': 255, 'nativeValueDefined': True, 'rawHex': ''},
                      {'nativeType': 5, 'nativeValueDefined': False, 'rawHex': ''}):
            base['default'] = value
            self.assertFalse(probe_dop_numeric(layout, b'\0', 0, kernel_sha256=PROFILE)['ok'])

    def test_synthetic_fields_cover_each_range_junction_and_no_match(self):
        layout, base = layout_for(((10, 20, 0, 1), (30, 40, 1, 2)))
        probes = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
        self.assertTrue(probes[0]['result']['ok'])
        values = {int(p['fieldHex'], 16) for p in probes if p['case'] != 'truncated'}
        self.assertTrue({0, 9, 10, 11, 19, 20, 21, 29, 30, 31, 39, 40, 41, 255} <= values)
        self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')
        self.assertTrue(any(p['result']['reason'] == 'numeric-probe-scale-linear-no-match' for p in probes))


if __name__ == '__main__':
    unittest.main()
