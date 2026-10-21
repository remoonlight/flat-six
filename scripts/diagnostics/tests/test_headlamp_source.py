"""Synthetic positive/negative PDUs; these do not claim device acceptance."""
import copy
import unittest

from scripts.diagnostics.headlamp_source import decode_identity


PREFIX = '9x1:SCHEINWERFER_LED:'
CASES = (
    ('Scheinwerfer_LED_DK2F_001', 'EV_LEDModRightMELCOVW26X'),
    ('Scheinwerfer_LED_DK2F_001', 'EV_LEDModLeftMELCOVW26X'),
    ('Scheinwerfer_LED_DK3_001', 'EV_LEDModLeftMELCOAU736'),
    ('Scheinwerfer_LED_DK3_001', 'EV_LEDModRightMELCOAU736'),
    ('Scheinwerfer_LED_Links_001', 'EV_LEDModLeftHELLA'),
    ('Scheinwerfer_LED_Links_001', 'EV_LEDModLeftHELLAAU37X'),
    ('Scheinwerfer_LED_Rechts_001', 'EV_LEDModRightHELLA'),
    ('Scheinwerfer_LED_Rechts_001', 'EV_LEDModRightHELLAAU37X'),
)


def replies(family=CASES[0][1], status=0):
    values = {
        '22F19E': family.encode('iso-8859-1') + b'\0',
        '22F1A2': b'001002',
        '22F1DF': bytes([status]),
        '22F187': b'12345678901',
        '22F189': b'0123',
        '22F191': b'10987654321',
    }
    return {request: ('62' + request[2:] + value.hex()).upper() for request, value in values.items()}


class HeadlampIdentityTests(unittest.TestCase):
    def test_all_literal_version_rules_and_complete_identity(self):
        for version, family in CASES:
            with self.subTest(version=version, family=family):
                data = replies(family)
                before = copy.deepcopy(data)
                actual = decode_identity(PREFIX + version, data)
                self.assertEqual(actual['asamIdentifier'], family)
                self.assertEqual(actual['diagnosticMajorVersion'], '001')
                self.assertEqual(actual['diagnosticMinorVersion'], '002')
                self.assertEqual(actual['hardwareNumber'], '10987654321')
                self.assertEqual(actual['softwareVersion'], '0123')
                self.assertEqual(actual['responses'], data)
                self.assertEqual(data, before)
                self.assertNotIn('freshRead', actual)

    def test_mismatched_source_families_never_cross_match(self):
        for version, _ in CASES:
            for other, family in CASES:
                if version == other:
                    continue
                with self.subTest(version=version, other=family):
                    with self.assertRaisesRegex(ValueError, 'identity-mismatch'):
                        decode_identity(PREFIX + version, replies(family))

    def test_real_terminator_is_required_and_no_normalization_is_invented(self):
        data = replies()
        for body in (CASES[0][1].encode(), b'EV_LED_ModRightMELCOVW26X\0',
                     CASES[0][1].encode() + b'\0\0', b'\0' + CASES[0][1].encode()):
            with self.subTest(body=body):
                data['22F19E'] = '62F19E' + body.hex()
                with self.assertRaises(ValueError):
                    decode_identity(PREFIX + CASES[0][0], data)

    def test_major_only_reply_is_incomplete_and_all_fixed_spans_are_exact(self):
        for request in ('22F1A2', '22F1DF', '22F187', '22F189', '22F191'):
            for extra in (False, True):
                data = replies()
                data[request] = data[request] + '00' if extra else data[request][:-2]
                with self.subTest(request=request, extra=extra):
                    with self.assertRaises(ValueError):
                        decode_identity(PREFIX + CASES[0][0], data)
        data = replies()
        data['22F1A2'] = '62F1A2' + b'001'.hex()
        with self.assertRaisesRegex(ValueError, 'length-mismatch'):
            decode_identity(PREFIX + CASES[0][0], data)

    def test_zero_programming_status_is_valid_and_flags_are_independent(self):
        for status in (0, 0x40, 0x04, 0x44):
            actual = decode_identity(PREFIX + CASES[0][0], replies(status=status))
            self.assertEqual(actual['softwareInconsistent'], bool(status & 0x04))
            self.assertEqual(actual['programmable'], bool(status & 0x40))
        for version, family in CASES[4:]:
            self.assertFalse(decode_identity(PREFIX + version, replies(family, 0x40))['softwareInconsistent'])
            with self.assertRaisesRegex(ValueError, 'identity-mismatch'):
                decode_identity(PREFIX + version, replies(family, 0x04))

    def test_wrong_echo_negative_reply_and_malformed_data_are_rejected(self):
        for request in replies():
            for text in ('7F2231', '7F2278', '62F190' + '41' * 17, 'zz', '', None, 0, '62F1A2001'):
                data = replies()
                data[request] = text
                with self.subTest(request=request, text=text):
                    with self.assertRaises(ValueError):
                        decode_identity(PREFIX + CASES[0][0], data)

    def test_missing_any_identity_field_and_blank_fingerprints_are_rejected(self):
        for request in replies():
            data = replies()
            del data[request]
            with self.assertRaisesRegex(ValueError, 'identity-incomplete'):
                decode_identity(PREFIX + CASES[0][0], data)
        for request, width in (('22F187', 11), ('22F189', 4), ('22F191', 11)):
            for fill in ('00', '20', 'FF'):
                data = replies()
                data[request] = '62' + request[2:] + fill * width
                with self.assertRaisesRegex(ValueError, 'identity-blank'):
                    decode_identity(PREFIX + CASES[0][0], data)

    def test_changed_minor_version_and_hardware_are_preserved_for_caller_comparison(self):
        a = replies()
        b = replies()
        b['22F1A2'] = '62F1A2' + b'001003'.hex()
        b['22F191'] = '62F191' + b'20987654321'.hex()
        one = decode_identity(PREFIX + CASES[0][0], a)
        two = decode_identity(PREFIX + CASES[0][0], b)
        self.assertNotEqual(one['responses'], two['responses'])
        self.assertEqual(two['diagnosticMinorVersion'], '003')
        self.assertEqual(two['hardwareNumber'], '20987654321')

    def test_unknown_profile_and_incomplete_containers_are_rejected(self):
        for profile in (None, [], {}, '9x1:SWSG:SWSG_Master_mit_AFS_ab_1310', PREFIX + 'SCHEINWERFER_LED_BaseSys'):
            with self.assertRaisesRegex(ValueError, 'profile-unqualified'):
                decode_identity(profile, replies())
        for data in (None, [], '62F19E'):
            with self.assertRaises(ValueError):
                decode_identity(PREFIX + CASES[0][0], data)


if __name__ == '__main__':
    unittest.main()
