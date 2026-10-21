import copy
import itertools
import struct
import unittest

from scripts.diagnostics.decoder_reference import (reference_piwis_integer_scale_contains,
    reference_piwis_rational, reference_piwis_signed, reference_piwis_unsigned)
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_numeric import probe_dop_numeric, synthetic_dop_probes
from scripts.diagnostics.piwis_scale_limits import (integer_scale_range,
    integer_range_contains, synthetic_integer_range_values)
from scripts.diagnostics.tests.test_piwis_numeric import dop


def bound(value=None, *, signed=False, closed=True):
    return {'recordType': 0x37, 'nativeInterval': 0x6d03 if value is None else 0x6d02 if closed else 0x6d01,
        'value': {'nativeType': 255 if value is None else 7 if signed else 11,
            'nativeValueDefined': value is not None,
            'rawHex': '' if value is None else value.to_bytes(4, 'little', signed=signed).hex()}}


def limited(lower=10, upper=20, *, signed=False, closed=(True, True)):
    layout = dop()
    if signed:
        layout['codedType']['nativeEncodingFieldsRaw'] = [0, 3, 1, 0]
    scale = layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]
    scale.update(lowerLimitCoded=bound(lower, signed=signed, closed=closed[0]),
                 upperLimitCoded=bound(upper, signed=signed, closed=closed[1]))
    return layout, scale


class PiwisScaleLimitTests(unittest.TestCase):
    def test_open_closed_and_one_sided_ranges_match_independent_raw_reader(self):
        for signed, closed in itertools.product((False, True), itertools.product((False, True), repeat=2)):
            for lower, upper in ((10, 20), (10, 10), (20, 10), (None, 20), (10, None), (None, None)):
                if signed and lower is not None: lower -= 15
                if signed and upper is not None: upper -= 15
                layout, scale = limited(lower, upper, signed=signed, closed=closed)
                profile = integer_scale_range(scale, signed=signed)
                for value in range(-10 if signed else 0, 30):
                    expected = ((lower is None or (value >= lower if closed[0] else value > lower))
                                and (upper is None or (value <= upper if closed[1] else value < upper)))
                    self.assertEqual(integer_range_contains(profile, value), expected)
                    self.assertEqual(reference_piwis_integer_scale_contains(scale, value, signed=signed), expected)
                    payload = (value & 65535).to_bytes(2, 'big')
                    result = probe_dop_numeric(layout, payload, 0, kernel_sha256=PROFILE)
                    self.assertEqual(result['ok'], expected, (lower, upper, value, result))
                    self.assertFalse(result['physicalConversionQualified'])
                    self.assertFalse(result['executionEnabled'])
                    if expected:
                        self.assertAlmostEqual(result['candidateValue'], float(reference_piwis_rational(scale['nativeField20'], value)))
                    else:
                        self.assertEqual(result['reason'], 'numeric-probe-outside-coded-scale-range')
                        self.assertNotIn('candidateValue', result)

    def test_range_compares_extracted_masked_signed_value_at_local_bit_position(self):
        for signed, order, bit in itertools.product((False, True), (0, 1), (0, 3, 7)):
            layout, scale = limited(-4 if signed else 3, 6, signed=signed)
            c = layout['codedType']; c['bitLength'] = 8; c['nativeEncodingFieldsRaw'][2] = order
            c.update(bitMaskPresent=True, bitMaskRawHex='f7')
            for value in (-128, -8, -1, 0, 3, 6, 8, 127) if signed else (0, 3, 6, 8, 127, 255):
                size = (8+bit+7)//8
                payload = b'prefix'+((value & 255) << bit).to_bytes(size, 'big' if order else 'little')
                raw = (reference_piwis_signed if signed else reference_piwis_unsigned)(c, payload, 6, bit)
                result = probe_dop_numeric(layout, payload, 6, bit, kernel_sha256=PROFILE)
                expected = reference_piwis_integer_scale_contains(scale, raw, signed=signed)
                self.assertEqual(result['ok'], expected)
                if expected:
                    self.assertEqual(result['rawValue'], raw)
                    self.assertEqual(result['scaleIndex'], 0)
                    self.assertTrue(result['codedScaleRangeChecked'])
                truncated = probe_dop_numeric(layout, payload[:-1], 6, bit, kernel_sha256=PROFILE)
                self.assertEqual(truncated['reason'], 'numeric-probe-truncated-field')

    def test_unsupported_limit_types_missing_values_and_unknown_intervals_fail_closed(self):
        layout, scale = limited()
        for change in ({'nativeInterval': True}, {'nativeInterval': 0x6d00}, {'recordType': 0x36},
                       {'nativeInterval': 0x6d03}, {'value': None}):
            bad = copy.deepcopy(layout); row = bad['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]
            row['lowerLimitCoded'].update(change)
            self.assertEqual(probe_dop_numeric(bad, b'\0\x0f', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-scale-limit-unqualified')
        for change in ({'nativeType': 7}, {'nativeType': 4}, {'nativeType': 5}, {'nativeType': True},
                       {'nativeValueDefined': False}, {'nativeValueDefined': 1}, {'rawHex': 'ff'},
                       {'rawHex': '00 00 00'}, {'rawHex': 'gg000000'}, {'rawHex': None}):
            bad = copy.deepcopy(layout); row = bad['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]
            row['lowerLimitCoded']['value'].update(change)
            result = probe_dop_numeric(bad, b'\0\x0f', 0, kernel_sha256=PROFILE)
            self.assertFalse(result['ok']);self.assertNotIn('candidateValue', result)
        bad = copy.deepcopy(layout); del bad['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]['upperLimitCoded']
        self.assertFalse(probe_dop_numeric(bad, b'\0\x0f', 0, kernel_sha256=PROFILE)['ok'])

    def test_unlimited_bounds_preserve_prior_outputs_and_reject_contradictions(self):
        layout, scale = limited(None, None)
        result = probe_dop_numeric(layout, b'\0\x0f', 0, kernel_sha256=PROFILE)
        self.assertEqual(result, probe_dop_numeric(dop(), b'\0\x0f', 0, kernel_sha256=PROFILE))
        for native_type in (0, 255):
            scale['lowerLimitCoded']['value']['nativeType'] = native_type
            self.assertTrue(probe_dop_numeric(layout, b'\0\x0f', 0, kernel_sha256=PROFILE)['ok'])
        scale['lowerLimitCoded']['value']['rawHex'] = '00000000'
        self.assertFalse(probe_dop_numeric(layout, b'\0\x0f', 0, kernel_sha256=PROFILE)['ok'])

    def test_synthetic_ranges_start_inside_and_include_both_boundary_neighbors(self):
        for signed in (False, True):
            layout, scale = limited(-10 if signed else 10, 20, signed=signed, closed=(False, True))
            probes = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
            self.assertTrue(probes[0]['result']['ok'])
            values = {r['case']: r['result'] for r in probes}
            self.assertFalse(values['range-lower']['ok'])
            self.assertTrue(values['range-lower-plus-one']['ok'])
            self.assertTrue(values['range-upper']['ok'])
            self.assertFalse(values['range-upper-plus-one']['ok'])
            self.assertEqual(values['truncated']['reason'], 'numeric-probe-truncated-field')

    def test_full_int32_uint32_limits_are_exact_before_float32_rounding(self):
        for signed in (False, True):
            lo, hi = (-(1 << 31), (1 << 31)-1) if signed else (0, (1 << 32)-1)
            layout, scale = limited(lo, hi, signed=signed, closed=(False, False))
            layout['codedType']['bitLength'] = 32
            layout['physicalType']['nativeBaseDataType'] = 2
            scale['nativeField20']['numerator'] = [{'rawHex': struct.pack('<d', 0).hex()},
                                                    {'rawHex': struct.pack('<d', 1).hex()}]
            for value in (lo, lo+1, 2**24+1, hi-1, hi):
                data = (value & 0xffffffff).to_bytes(4, 'big')
                result = probe_dop_numeric(layout, data, 0, kernel_sha256=PROFILE)
                expected_ok = value not in (lo, hi)
                self.assertEqual(result['ok'], expected_ok)
                if expected_ok:
                    self.assertEqual(result['candidateValue'], struct.unpack('<f', struct.pack('<f', value))[0])
                    self.assertEqual(result['rawValue'], value)
                    self.assertFalse(result['runtimeFloatingEnvironmentVerified'])
            self.assertEqual(synthetic_dop_probes(layout, kernel_sha256=PROFILE)[0]['result']['ok'], True)

    def test_masked_fixture_reachability_matches_exhaustive_small_domains(self):
        for width, signed in itertools.product(range(1, 6), (False, True)):
            minimum, maximum = (-(1 << (width-1)), (1 << (width-1))-1) if signed else (0, (1 << width)-1)
            for mask in range(1 << width):
                for lo, hi in ((minimum, maximum), (minimum, minimum), (maximum, maximum), (0, 0), (1, 2), (-3, -1), (3, 1)):
                    profile = ((lo, True), (hi, True))
                    fixtures = synthetic_integer_range_values(profile, width, signed=signed, mask=mask)
                    interior = [v for n, v in fixtures if n == 'linear-range-interior']
                    reachable = [v for v in range(minimum, maximum+1) if lo <= v <= hi and (v & ((1 << width)-1)) & mask == (v & ((1 << width)-1))]
                    self.assertEqual(interior, reachable[:1], (width, signed, mask, profile))
        # Full domain / sparse mask must finish without enumeration.
        self.assertEqual(synthetic_integer_range_values(((0x80000000, True), (0xffffffff, True)), 32, mask=0x80000000)[0], ('linear-range-interior', 0x80000000))

    def test_outside_range_precedes_zero_denominator_and_other_conversion_strategies_stay_blocked(self):
        layout, scale = limited()
        scale['nativeField20']['denominator'] = [{'rawHex': struct.pack('<d', 0).hex()}]
        self.assertEqual(probe_dop_numeric(layout, b'\0\0', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-outside-coded-scale-range')
        self.assertEqual(probe_dop_numeric(layout, b'\0\x0f', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-zero-denominator')
        for category in (5, 6, 7):
            layout['compuPrefix']['compuMethod']['nativeCategory'] = category
            self.assertEqual(probe_dop_numeric(layout, b'\0\x0f', 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-conversion-unqualified')


if __name__ == '__main__':
    unittest.main()
