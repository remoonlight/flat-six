from fractions import Fraction
import unittest

from scripts.diagnostics.decoder_reference import reference_value, reference_cases, compare_reference
from scripts.diagnostics.response_values import decode_application_response


def record(kind='IDENTICAL', **fields):
    defaults = dict(BaseDataType='A_UINT32', BitLength=16, HighLow=1, BitMask=0, Encoding='Undefined')
    defaults.update(fields)
    return {'byteOffset': 1, 'bitOffset': 0, 'unit': 'V',
            'formula': kind + ':' + ','.join(f'{k}={v}' for k, v in defaults.items()) + ';'}


class IndependentReferenceTests(unittest.TestCase):
    def check(self, rec, hx, **known):
        data = bytes.fromhex(hx)
        expected = reference_value(rec, data)
        for key, value in known.items():
            self.assertEqual(expected.get(key), value)
        actual = decode_application_response(rec, data, 'data')
        self.assertEqual(compare_reference(expected, actual), [], (expected, actual))
        return expected, actual

    def test_hand_calculated_endian_signed_cross_byte_and_mask(self):
        self.check(record(), 'FF1234', raw=4660, value=4660)
        self.check(record(HighLow=0), 'FF1234', raw=13330, value=13330)
        self.check(record(BaseDataType='A_INT32', Encoding='2C'), 'FFFFFE', raw=65534, value=-2)
        rec = record(BitLength=12); rec['bitOffset'] = 4
        self.check(rec, 'FFABCD', raw=2748, value=2748)
        self.check(record(BitMask='0FFF'), 'FFABCD', raw=3021, value=3021)
        self.check(record(), 'FF12', ok=False, reason='truncated')

    def test_hand_calculated_rational_scaling_and_sign(self):
        self.check(record('LINEAR', Xa=40, Xb=2, Xc='0.5'), 'FF0064', value=100, phys=Fraction(5))
        self.check(record('LINEAR', BaseDataType='A_INT32', Encoding='2C', Xa=0, Xb=10, Xc=1),
                   'FFFFF6', value=-10, phys=Fraction(-1))
        self.check(record('LINEAR', Xa=0, Xb=3, Xc=1), 'FF0001', phys=Fraction(1, 3))

    def test_enum_reserved_unmapped_and_bounds(self):
        rec = record('TEXTTABLE', BitLength=8)
        rec['formula'] += '[0x00]->0x1234;[0x01,0x02]->0x5678;Ic:[0xFE,0xFF]->0x9999'
        rec['enumText'] = {'1234': '关闭', '5678': '开启'}
        self.check(rec, 'FF01', text='开启', textId='5678', numeric=False)
        self.check(rec, 'FFFE', ok=False, reason='invalid_reserved')
        self.check(rec, 'FF03', ok=False, reason='texttable_unmapped')
        self.check(record(Lower=1, Upper=5), 'FF0006', ok=False, reason='out_of_bounds')

    def test_fixed_string_and_bytefield(self):
        self.check(record(BaseDataType='A_ASCIISTRING', Encoding='ISO-8859-2', BitLength=32),
                   'FF41A1005A', raw='41A1005A', value='AĄ', numeric=False)
        self.check(record(BaseDataType='A_BYTEFIELD', BitLength=16), 'FF1234', raw='1234', value='1234')

    def test_mutated_result_is_detected(self):
        expected, actual = self.check(record(), 'FF1234', value=4660)
        actual['value'] += 1
        self.assertEqual(compare_reference(expected, actual), ['value'])
        rec = record('LINEAR', Xa=0, Xb=2, Xc=1)
        expected, actual = self.check(rec, 'FF0004', phys=Fraction(2))
        actual['phys'] = 'NaN'
        self.assertEqual(compare_reference(expected, actual), ['phys'])

    def test_generated_cases_include_signed_boundary_and_are_deterministic(self):
        rec = record(BaseDataType='A_INT32', Encoding='2C')
        cases = reference_cases(rec)
        values = {reference_value(rec, data)['value'] for data in cases}
        self.assertTrue({0, 1, -1, -32768, 32767}.issubset(values))
        self.assertEqual(cases, reference_cases(rec))
        for data in cases:
            self.assertEqual(compare_reference(reference_value(rec, data), decode_application_response(rec, data, 'data')), [])


if __name__ == '__main__': unittest.main()
