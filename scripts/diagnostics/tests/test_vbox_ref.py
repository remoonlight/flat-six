import unittest
from decimal import Decimal
from scripts.diagnostics.vbox_ref import candidate_layout, decode_candidate


def row(**changes):
    return {'sourceId': 'vbox-ref-boxster-981', 'signed': False, 'startBit': 56,
            'length': 8, 'dlc': 8, 'byteOrder': 'motorola-vendor-label',
            'factor': '1', 'offset': '0', **changes}


class VboxReferenceTests(unittest.TestCase):
    def test_manual_first_byte_example(self):
        # VBOXTools p.19: Motorola start56/length8 highlights byte1.
        result = decode_candidate(row(), bytes.fromhex('12 34 56 78 9A BC DE F0'))
        self.assertEqual(result['raw'], 0x12)
        self.assertEqual(result['layout']['vectorDbcStartBit'], 7)
        self.assertFalse(result['layout']['runtimeDecodeEnabled'])
        self.assertFalse(result['layout']['columnBindingVerified'])

    def test_throttle_numbering_has_same_physical_byte_as_intel48(self):
        result = decode_candidate(row(startBit=8, factor='0.4'), bytes.fromhex('00 00 00 00 00 00 7D 00'))
        self.assertEqual(result['raw'], 125)
        self.assertEqual(result['value'], 50)
        self.assertEqual(result['layout']['vectorDbcStartBit'], 55)
        self.assertEqual(result['layout']['valueBitPositions'], [(6, bit) for bit in range(8)])

    def test_intel_rpm_and_cross_byte_wheel(self):
        rpm = decode_candidate(row(startBit=16, length=16, byteOrder='intel-vendor-label', factor='0.25'),
                               bytes.fromhex('00 00 E0 2E 00 00 00 00'))
        self.assertEqual(rpm['value'], 3000)
        wheel = decode_candidate(row(startBit=52, length=12, byteOrder='intel-vendor-label', factor='0.1'),
                                 bytes.fromhex('00 00 00 00 00 00 40 1F'))
        self.assertEqual(wheel['raw'], 500)
        self.assertEqual(wheel['value'], 50)

    def test_motorola_cross_byte_negative_scale(self):
        result = decode_candidate(row(startBit=8, length=10, factor='-0.8', offset='817.6'),
                                  bytes.fromhex('00 00 00 00 00 02 01 00'))
        self.assertEqual(result['raw'], 513)
        self.assertEqual(result['value'], Decimal('407.2'))
        self.assertEqual(result['layout']['vectorDbcStartBit'], 41)

    def test_unknown_source_encoding_and_invalid_frame_are_rejected(self):
        for changes in ({'sourceId': 'planetkris-718'}, {'signed': None}, {'multiplexing': 'm1'},
                        {'byteOrder': 'motorola'}, {'dlc': 7}, {'length': 65}, {'startBit': -1},
                        {'startBit': 63, 'length': 2}, {'factor': 'NaN'}, {'offset': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                candidate_layout(row(**changes))
        with self.assertRaisesRegex(ValueError, 'frame-length'):
            decode_candidate(row(), bytes(7))


if __name__ == '__main__': unittest.main()
