from copy import deepcopy
from fractions import Fraction
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.coding_numeric import numeric_domain, numeric_physical_value, preview_numeric_candidate, rational, physical_target, audit_variants, GUARDS
from scripts.diagnostics.decoder_reference import reference_patch, reference_value
from scripts.diagnostics.tests.test_realtime_preparation import field


def linear(width=16, shift=0, order=1, lower=0, upper=65535, xa='0', xb='1', xc='1', signed=False, mask='0', reserved=''):
    record = field(offset=2)
    datatype = 'A_INT32' if signed else 'A_UINT32'
    record.update(bitOffset=shift, unit='ms', at=100,
        formula={'text': f'LINEAR:Lower={lower},Upper={upper},BaseDataType={datatype},DataType=A_FLOAT64,Xa={xa},Xb={xb},Xc={xc},BitLength={width},HighLow={order},BitMask={mask},Encoding=Undefined,Precision=2;' + reserved})
    return record


class NumericCodingTests(unittest.TestCase):
    def test_single_declared_scale_segment_uses_its_own_bounds_and_coefficients(self):
        record = linear(width=8, upper=255)
        record['formula']['text'] = ('SCALE-LINEAR:Lower=10,Upper=20,Xa=1,Xb=3,Xc=2;'
            'BaseDataType=A_UINT32,BitLength=8,HighLow=1,BitMask=0,Encoding=Undefined,DataType=A_FLOAT64,Precision=2;')
        domain=numeric_domain(record)
        self.assertTrue(domain['ok'],domain)
        self.assertEqual(domain['physicalEnvelope'],[rational(Fraction(19,3)),rational(13)])
        self.assertEqual(numeric_physical_value(record,11),Fraction(7))
        result=preview_numeric_candidate(record,bytes.fromhex('A5FF0FA5'),rational(7))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['codedValue'],11)
        self.assertEqual(result['afterHex'],'A5FF0BA5')
        self.assertEqual(preview_numeric_candidate(record,bytes(4),rational(Fraction(-1,3)))['reason'],'numeric-target-out-of-source-bounds')
        self.assertEqual(preview_numeric_candidate(record,bytes(4),rational(Fraction(13,2)))['reason'],'numeric-target-not-representable')
        with self.assertRaisesRegex(ValueError,'numeric-coded-value-invalid'):numeric_physical_value(record,True)

    def test_scale_segment_does_not_merge_multiple_segments_or_allow_reserved_targets(self):
        record=linear(width=8,upper=255)
        tail='BaseDataType=A_UINT32,BitLength=8,HighLow=1,BitMask=0,Encoding=Undefined,DataType=A_FLOAT64;'
        record['formula']['text']='SCALE-LINEAR:Lower=0,Upper=100,Xa=0,Xb=1,Xc=1;'+tail+'Ic:[0xFF]->0xF001;[0x32]->0xF002;'
        self.assertEqual(preview_numeric_candidate(record,bytes(4),rational(50))['reason'],'numeric-target-reserved')
        self.assertTrue(preview_numeric_candidate(record,bytes(4),rational(49))['ok'])
        record['formula']['text']='SCALE-LINEAR:Lower=0,Upper=10,Xa=0,Xb=1,Xc=1;Lower=20,Upper=30,Xa=0,Xb=1,Xc=1;'+tail
        self.assertEqual(numeric_domain(record)['reason'],'numeric-single-source-segment-required')

    def test_decimal_and_fraction_targets_remain_exact(self):
        self.assertEqual(physical_target('0.1'), rational(Fraction(1, 10)))
        self.assertEqual(physical_target(' 5/3 '), rational(Fraction(5, 3)))
        self.assertEqual(physical_target('-1.25'), rational(Fraction(-5, 4)))
        for text in ('1e3', '1/0', 'NaN', '1,5', True, '9' * 129):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'numeric-target-invalid'):
                physical_target(text)
    def test_full_16bit_domain_not_truncated_to_enum_budget(self):
        record = linear(); domain = numeric_domain(record)
        self.assertTrue(domain['ok']); self.assertEqual((domain['codedLower'], domain['codedUpper']), (0, 65535))
        before = bytes.fromhex('FAA55555FFFF')
        result = preview_numeric_candidate(record, before, rational(60000))
        self.assertTrue(result['ok']); self.assertEqual(bytes.fromhex(result['afterHex']), reference_patch(record, before, 60000))
        self.assertEqual(result['decodedCandidate']['value'], 60000)
        self.assertEqual(result['targetPhysical'], rational(60000))
        for key, value in GUARDS.items(): self.assertEqual(result[key], value)
        self.assertFalse(domain['allEnvelopeValuesAllowed'])

    def test_exact_rational_inverse_without_rounding_or_nearest_value(self):
        record = linear(width=8, upper=255, xa='1', xb='3', xc='2')
        result = preview_numeric_candidate(record, bytes(6), rational(Fraction(5, 3)))
        self.assertTrue(result['ok']); self.assertEqual(result['codedValue'], 3)
        self.assertEqual(preview_numeric_candidate(record, bytes(6), rational(1))['codedValue'], 2)
        rejected = preview_numeric_candidate(record, bytes(6), rational(Fraction(1, 2)))
        self.assertEqual(rejected['reason'], 'numeric-target-not-representable')

    def test_negative_slope_envelope_and_source_bounds(self):
        record = linear(width=8, lower=10, upper=20, xa='5', xb='2', xc='-1')
        domain = numeric_domain(record)
        self.assertEqual(domain['physicalEnvelope'], [rational(Fraction(-25, 2)), rational(Fraction(-15, 2))])
        self.assertEqual(preview_numeric_candidate(record, bytes(6), rational(Fraction(-15, 2)))['codedValue'], 10)
        self.assertEqual(preview_numeric_candidate(record, bytes(6), rational(-13))['reason'], 'numeric-target-out-of-source-bounds')

    def test_signed_cross_byte_both_orders_preserve_all_other_bits(self):
        for order in (0, 1):
            record = linear(width=8, shift=7, order=order, lower=-128, upper=127, signed=True)
            before = bytes.fromhex('FAA56DB5FFFF')
            result = preview_numeric_candidate(record, before, rational(-123))
            self.assertTrue(result['ok'], result)
            after = bytes.fromhex(result['afterHex'])
            self.assertEqual(after, reference_patch(record, before, 133))
            self.assertEqual(reference_value(record, after)['value'], -123)
            self.assertEqual(after[:2], before[:2]); self.assertEqual(after[4:], before[4:])

    def test_reserved_values_and_mask_holes_remain_illegal(self):
        record = linear(width=8, upper=255, mask='0F', reserved='Ic:[0x2,0x3]->0xF001;')
        before = bytes.fromhex('0000FAFFFF')
        self.assertEqual(preview_numeric_candidate(record, before, rational(2))['reason'], 'numeric-target-reserved')
        self.assertEqual(preview_numeric_candidate(record, before, rational(16))['reason'], 'numeric-target-mask-unrepresentable')
        result = preview_numeric_candidate(record, before, rational(4))
        self.assertTrue(result['ok']); self.assertEqual(result['afterHex'], '0000F4FFFF')

    def test_missing_bounds_name_request_and_inverse_keep_explicit_gaps(self):
        for change, reason in (({'formula': linear(lower='null')['formula']}, 'numeric-complete-source-bounds-required'),
                ({'pid':0xF101}, 'numeric-read-request-unresolved'),
                ({'name':''}, 'numeric-field-name-unresolved'),
                ({'formula':linear(xc='0')['formula']}, 'numeric-inverse-not-unique'),
                ({'formula':linear(xb='0')['formula']}, 'numeric-inverse-not-unique'),
                ({'formula':linear(width=8)['formula']}, 'numeric-source-bounds-unrepresentable'),
                ({'byteOffset':None}, 'numeric-field-layout-unresolved')):
            record = linear(); record.update(change)
            with self.subTest(change=change):self.assertEqual(numeric_domain(record)['reason'], reason)

    def test_target_format_and_short_buffer_never_produce_changed_bytes(self):
        record = linear()
        for target in (None, {'numerator':True,'denominator':'1'}, {'numerator':'1','denominator':'0'},
                {'numerator':'nan','denominator':'1'}, {'numerator':'1','denominator':'-1'},
                {'numerator':'1','denominator':'1','rawValue':1}, {'numerator':'1'*129,'denominator':'1'}):
            result = preview_numeric_candidate(record, bytes(6), target)
            self.assertEqual(result['reason'], 'numeric-target-invalid'); self.assertNotIn('afterHex', result)
        self.assertEqual(preview_numeric_candidate(record, b'\0', rational(10))['reason'], 'truncated')
        self.assertEqual(preview_numeric_candidate(record, bytearray(6), rational(10))['reason'], 'numeric-original-buffer-invalid')

    def test_source_audit_checks_acceptance_rejection_and_bits_independently(self):
        record = linear(width=8, upper=10); missing = deepcopy(record); missing['formula'] = linear(width=8, upper='null')['formula']
        variant = {'profile_id':'synthetic-only', 'on_target_menu':True,'generation':'981',
            'pool_records':{'coding':{'records':[record,missing]}}}
        with tempfile.TemporaryDirectory() as temp:
            result = audit_variants(io.StringIO(json.dumps(variant)+'\n'), Path(temp)/'audit')
            with self.assertRaisesRegex(ValueError, 'fresh-output'):audit_variants(io.StringIO(''), Path(temp)/'audit')
        self.assertEqual(result['counts']['reference:matched'], 1)
        self.assertEqual(result['counts']['blocked:numeric-complete-source-bounds-required'], 1)
        self.assertEqual(result['uniqueSyntheticCases'], 15)
        self.assertEqual(result['uniqueRejectedCases']['numeric-target-out-of-source-bounds'], 6)
        self.assertFalse(result['nativeEditingBehaviorVerified']); self.assertFalse(result['editorEnabled'])


if __name__ == '__main__': unittest.main()
