import copy
import itertools
import unittest

from scripts.diagnostics.decoder_reference import (reference_piwis_integer_rational,
    reference_piwis_unsigned, reference_piwis_signed, reference_piwis_integer_scale_contains)
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_integer_conversion import integer_rational
from scripts.diagnostics.piwis_numeric import probe_dop_numeric, synthetic_dop_probes
from scripts.diagnostics.tests.test_piwis_numeric import coefficients, dop
from scripts.diagnostics.tests.test_piwis_scale_limits import bound


def integer_dop(numerator=(0, 1), denominator=(), *, target=1, signed=False, width=32):
    layout = dop(target=target)
    layout['codedType']['bitLength'] = width
    layout['codedType']['nativeEncodingFieldsRaw'] = [0, 3, 1, 0] if signed else [1, 11, 1, 0]
    scale = layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]
    scale['nativeField20'] = coefficients(numerator, denominator)
    return layout, scale


class PiwisIntegerConversionTests(unittest.TestCase):
    def test_coefficients_truncate_before_polynomial_and_division_toward_zero(self):
        for unsigned in (False, True):
            c = coefficients([-1.75, 1.9, -2.4], [-3.9])
            for value in (-12, -1, 0, 1, 2, 13, 0x7fffffff, 0xffffffff):
                self.assertEqual(integer_rational(c, value, unsigned=unsigned),
                                 reference_piwis_integer_rational(c, value, unsigned=unsigned))
        self.assertEqual(integer_rational(coefficients([0, 1.9], []), 10, unsigned=False), 10)
        self.assertEqual(integer_rational(coefficients([-7], [3]), 0, unsigned=False), -2)
        self.assertEqual(integer_rational(coefficients([7], [-3]), 0, unsigned=False), -2)
        self.assertEqual(integer_rational(coefficients([-7], [-3]), 0, unsigned=False), 2)

    def test_modular_products_additions_and_denominator_wrap_match_power_sum(self):
        vectors = (([], []), ([0xffffffff, 0xffffffff], []), ([0, 2147483647, -3], [1, 2]),
                   ([0, 2], [0, 2]), ([-2147483648], [-1]), ([-1, 0.5, 1.99], [3.8]))
        for unsigned, value, (n, d) in itertools.product((False, True),
                (-2147483648, -1, 0, 1, 2, 65535, 2147483647, 2147483648, 4294967295), vectors):
            c = coefficients(n, d)
            try:
                expected = reference_piwis_integer_rational(c, value, unsigned=unsigned)
            except ValueError:
                with self.assertRaises(ValueError): integer_rational(c, value, unsigned=unsigned)
            else:
                self.assertEqual(integer_rational(c, value, unsigned=unsigned), expected)
        self.assertEqual(integer_rational(coefficients([0, 2], []), 0xffffffff, unsigned=True), 0xfffffffe)
        self.assertEqual(integer_rational(coefficients([0, 2], []), 0x7fffffff, unsigned=False), -2)

    def test_signed_unsigned_and_mixed_declared_types_use_native_routing(self):
        for target, signed in itertools.product((0, 1), (False, True)):
            layout, _ = integer_dop(target=target, signed=signed)
            unsigned = target == 1 and not signed
            result = probe_dop_numeric(layout, b'\xff'*4, 0, kernel_sha256=PROFILE)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['candidateValue'], 0xffffffff if unsigned else -1)
            self.assertEqual(result['nativeResultDataType'], 11 if unsigned else 7)
            self.assertEqual(result['nativeArithmetic'], 'unsigned32' if unsigned else 'signed32')
            self.assertEqual(result['declaredPhysicalBaseDataType'], target)
            for flag in ('physicalConversionQualified', 'executionEnabled', 'ecuApplicabilityVerified',
                         'parameterBindingVerified', 'unitSemanticsVerified', 'runtimeFloatingEnvironmentVerified'):
                self.assertFalse(result[flag])

    def test_empty_numerator_denominator_and_zero_denominator_after_truncation(self):
        for unsigned in (False, True):
            self.assertEqual(integer_rational(coefficients([], []), 12, unsigned=unsigned), 0)
            for n, d in (([7], [0.9]), ([1], [0, 2]), ([0], [0])):
                with self.assertRaisesRegex(ValueError, 'zero-denominator'):
                    integer_rational(coefficients(n, d), 0, unsigned=unsigned)
            with self.assertRaisesRegex(ValueError, 'zero-denominator'):
                integer_rational(coefficients([1], [0, 2]), 0x80000000, unsigned=unsigned)

    def test_integer_cpu_invalid_conversions_division_overflow_are_explicitly_blocked(self):
        for unsigned, c in ((False, coefficients([2**31], [])),
                            (False, coefficients([-2**31-1], [])),
                            (True, coefficients([2**63], [])),
                            (True, coefficients([-2**63, 1], []))):
            with self.assertRaisesRegex(ValueError, 'truncation-unqualified'):
                integer_rational(c, 0, unsigned=unsigned)
        self.assertEqual(integer_rational(coefficients([-2**63], []), 1, unsigned=True), 0)
        self.assertEqual(integer_rational(coefficients([-2**31-0.5], []), 1, unsigned=False), -2**31)
        layout, _ = integer_dop([-2**31], [-1], target=0)
        result = probe_dop_numeric(layout, bytes(4), 0, kernel_sha256=PROFILE)
        self.assertEqual(result['reason'], 'numeric-probe-integer-division-overflow')
        self.assertNotIn('candidateValue', result)

    def test_malformed_and_nonfinite_coefficients_and_input_reject(self):
        for raw in (None, '', 'zz'*8, '00 '*8, '00'*7):
            c = coefficients([1], []); c['numerator'][0]['rawHex'] = raw
            with self.assertRaisesRegex(ValueError, 'bytes-invalid'):
                integer_rational(c, 0, unsigned=True)
        for value in (float('inf'), float('-inf'), float('nan')):
            with self.assertRaisesRegex(ValueError, 'not-finite'):
                integer_rational(coefficients([value], []), 0, unsigned=True)
        for value in (True, 1.0, -2**31-1, 2**32):
            with self.assertRaisesRegex(ValueError, 'input-invalid'):
                integer_rational(coefficients([1], []), value, unsigned=True)
        for name in ('numerator', 'denominator'):
            for bad in (None, {}, [coefficients([1], [])['numerator'][0]]*256):
                c = coefficients([1], []);c[name] = bad
                with self.assertRaisesRegex(ValueError, 'count-invalid'):
                    integer_rational(c, 0, unsigned=True)

    def test_range_applies_before_integer_conversion_and_bad_denominator(self):
        for signed in (False, True):
            layout, scale = integer_dop([0, 1], [0], target=0, signed=signed)
            scale.update(lowerLimitCoded=bound(-4 if signed else 3, signed=signed, closed=False),
                         upperLimitCoded=bound(6, signed=signed))
            for value in range(-8 if signed else 0, 10):
                result = probe_dop_numeric(layout, (value & 0xffffffff).to_bytes(4, 'big'), 0, kernel_sha256=PROFILE)
                inside = reference_piwis_integer_scale_contains(scale, value, signed=signed)
                self.assertEqual(result['reason'], 'numeric-probe-zero-denominator' if inside
                                 else 'numeric-probe-outside-coded-scale-range')
                self.assertNotIn('candidateValue', result)

    def test_all_bit_widths_orders_offsets_masks_match_independent_reference(self):
        for signed, width, order, bit in itertools.product((False, True), (1, 7, 8, 13, 16, 24, 31, 32), (0, 1), (0, 3, 7)):
            layout, scale = integer_dop([-40, 1], target=0, signed=signed, width=width)
            c = layout['codedType']; c['nativeEncodingFieldsRaw'][2] = order
            mask = ((1 << width)-1) & 0xaaaaaaaa
            c.update(bitMaskPresent=True, bitMaskRawHex=mask.to_bytes((width+7)//8, 'big').hex())
            size = (width+bit+7)//8
            data = b'prefix'+(((1 << width)-1) << bit).to_bytes(size, 'big' if order else 'little')
            raw = (reference_piwis_signed if signed else reference_piwis_unsigned)(c, data, 6, bit)
            result = probe_dop_numeric(layout, data, 6, bit, kernel_sha256=PROFILE)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['rawValue'], raw)
            self.assertEqual(result['candidateValue'], reference_piwis_integer_rational(scale['nativeField20'], raw, unsigned=False))
            self.assertEqual(probe_dop_numeric(layout, data[:-1], 6, bit, kernel_sha256=PROFILE)['reason'], 'numeric-probe-truncated-field')

    def test_synthetic_closed_ranges_and_exhaustive_temperature_source_formula(self):
        layout, scale = integer_dop([-40, 1], target=0, width=8)
        for value in range(256):
            result = probe_dop_numeric(layout, bytes([value]), 0, kernel_sha256=PROFILE)
            self.assertEqual(result['candidateValue'], value-40)
        scale.update(lowerLimitCoded=bound(10), upperLimitCoded=bound(20))
        probes = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
        self.assertTrue(probes[0]['result']['ok'])
        self.assertTrue(any(p['result']['reason'] == 'numeric-probe-outside-coded-scale-range' for p in probes))
        self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')

    def test_wrong_source_missing_forward_coefficients_and_other_categories_stay_blocked(self):
        layout, scale = integer_dop()
        self.assertEqual(probe_dop_numeric(layout, bytes(4), 0, kernel_sha256='bad')['reason'], 'numeric-probe-kernel-profile-mismatch')
        del scale['nativeField20']
        self.assertEqual(probe_dop_numeric(layout, bytes(4), 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-coded-to-physical-coefficients-missing')
        for category in (2, 4, 5, 6, 7):
            bad, _ = integer_dop();bad['compuPrefix']['compuMethod']['nativeCategory'] = category
            self.assertEqual(probe_dop_numeric(bad, bytes(4), 0, kernel_sha256=PROFILE)['reason'], 'numeric-probe-conversion-unqualified')


if __name__ == '__main__':
    unittest.main()
