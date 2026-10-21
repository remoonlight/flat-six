from copy import deepcopy
import unittest

from scripts.diagnostics.x431_can_initialization import InitializationReference, GUARDS


class InitializationReferenceTests(unittest.TestCase):
    def fixture(self, kind=0x10, subtype=3):
        # Explicit small callback fixture; not a fake source-file qualification.
        ref = object.__new__(InitializationReference)
        ref.enter_template = bytes.fromhex('55aa0d610108') + bytes(24)
        ref.link_template = bytes.fromhex('02027e0000ffffff00017e0000ffff0000000000')
        comm = dict(protocolKind=kind, token={'TOKEN+0x641': {'value': 3}},
                    pairs=[dict(storedTx=0xe2a0, storedRx=0xefe0, mode5Extra=0x55)])
        ref.records = {(0xd04, 0): comm}
        case = dict(family=13, systemKey=0xd04, linkIndex=0, comm=deepcopy(comm), enterSubtype=subtype,
                    carType=0, pin1Return=0, pin2Return=0, parameterReturn=0,
                    enterFrameReturn=0, linkReturn=1, foundationReturn=0,
                    priorFilter=0x12345678, differentSourcePointer=False)
        return ref, case

    def test_missing_or_changed_original_cannot_qualify_reference(self):
        for value in (None, bytearray(b'changed'), b'changed'):
            with self.assertRaisesRegex(ValueError, 'comm-source-unreviewed'):
                InitializationReference(value, b'anything')

    def test_source_record_changes_are_rejected(self):
        ref, case = self.fixture()
        for field, value in [('family', True), ('systemKey', True), ('linkIndex', 1), ('enterSubtype', True),
                             ('enterSubtype', 7), ('differentSourcePointer', 1), ('priorFilter', True)]:
            changed = deepcopy(case); changed[field] = value
            with self.assertRaises(ValueError):
                ref.predict(changed)
        case['comm']['pairs'][0]['storedRx'] ^= 1
        with self.assertRaisesRegex(ValueError, 'source-record-mismatch'):
            ref.predict(case)
        case['comm']['pairs'][0]['storedRx'] = float(ref.records[(0xd04, 0)]['pairs'][0]['storedRx'])
        with self.assertRaisesRegex(ValueError, 'source-record-mismatch'):
            ref.predict(case)

    def test_initial_pin_failure_stops_before_every_other_callback(self):
        ref, case = self.fixture(); case['pin1Return'] = -1
        row = ref.predict(case)
        self.assertEqual(row['nativeReturnWord'], 0xffffffff)
        self.assertEqual(len(row['callbackEvents']), 1)
        self.assertEqual(row['globalFilterAfter'], case['priorFilter'])

    def test_explicit_channel_reaches_both_pin_calls_and_parameter_callback(self):
        for kind, subtype in [(0x10, 3), (0x30, 1), (0x50, 5)]:
            ref, case = self.fixture(kind=kind, subtype=subtype)
            for channel in range(256):
                with self.subTest(kind=kind, channel=channel):
                    case['channelByte'] = channel
                    row = ref.predict(case)
                    self.assertEqual(row['channelSource'], 'explicit-observation')
                    self.assertEqual(row['channelByte'], channel)
                    pins = [e['args'][0] for e in row['callbackEvents'] if e['name'] == 'SelectPIN']
                    self.assertEqual(pins, [channel, channel])
                    parameter = next(e for e in row['callbackEvents'] if e['name'] == 'CanSetParameterNormal')
                    self.assertEqual(parameter['channel'], channel)
                    for key, value in GUARDS.items():
                        self.assertEqual(row[key], value)

    def test_legacy_channel_fixture_is_identified_and_not_retained_between_calls(self):
        ref, case = self.fixture()
        legacy = ref.predict(case)
        self.assertEqual(legacy['channelSource'], 'legacy-fixture-one')
        case['channelByte'] = 0
        self.assertEqual(ref.predict(case)['callbackEvents'][0]['args'][0], 0)
        case['channelByte'] = 1
        explicit = ref.predict(case)
        self.assertEqual(explicit['callbackEvents'], legacy['callbackEvents'])
        self.assertEqual(explicit['channelSource'], 'explicit-observation')
        del case['channelByte']
        self.assertEqual(ref.predict(case), legacy)

    def test_channel_is_an_explicit_byte_not_bool_coerced_or_wrapped(self):
        ref, case = self.fixture()
        for channel in [None, False, True, -1, 256, 65536, 1.0, '0']:
            with self.subTest(channel=channel), self.assertRaisesRegex(ValueError, 'channel-byte-invalid'):
                ref.predict(dict(case, channelByte=channel))

    def test_full_word_parameter_failure_differs_from_low_word_frame_failure(self):
        ref, case = self.fixture(); case['parameterReturn'] = 65536
        row = ref.predict(case)
        self.assertEqual(row['nativeReturnWord'], 0xfffffffd)
        self.assertNotIn('SetEnterFrame', [e['name'] for e in row['callbackEvents']])
        case['parameterReturn'] = 0; case['enterFrameReturn'] = 65536
        self.assertEqual(ref.predict(case)['nativeReturnWord'], 1)
        case['enterFrameReturn'] = 1
        self.assertEqual(ref.predict(case)['nativeReturnWord'], 0xfffffffc)

    def test_low_sixteen_car_type_controls_pin_branch_and_query_count(self):
        ref, case = self.fixture()
        for value, count in [(65538, 1), (65546, 2)]:
            case['carType'] = value
            events = ref.predict(case)['callbackEvents']
            self.assertEqual(sum(e['name'] == 'GetDBSCarType' for e in events), count)
            self.assertEqual([e['args'] for e in events if e['name'] == 'SelectPIN'][1], [1, 33, 0, 6, 14])

    def test_foundation_and_link_negative_returns_do_not_become_hardware_success(self):
        ref, case = self.fixture(); case['foundationReturn'] = case['linkReturn'] = -1
        row = ref.predict(case)
        self.assertEqual(row['nativeReturnWord'], 1)
        self.assertEqual(row['callbackEvents'][-1]['returnedWord'], 0xffffffff)
        self.assertFalse(row['initializerChecksLinkFilterReturn'])
        for key, value in GUARDS.items():
            self.assertEqual(row[key], value)

    def test_mode30_preserves_prior_global_filter_and_skips_enter_frame(self):
        ref, case = self.fixture(kind=0x30, subtype=1)
        row = ref.predict(case)
        self.assertEqual(row['globalFilterAfter'], 0x12345678)
        self.assertEqual(next(e['parameterHex'] for e in row['callbackEvents'] if e['name'] == 'CanSetParameterNormal'), '000000000000000003ff0000')
        self.assertNotIn('SetEnterFrame', [e['name'] for e in row['callbackEvents']])

    def test_frames_match_retained_golden_boundaries_not_ecu_pdus(self):
        for subtype, expected in [(3, '55aa0d610108e2a0301005aaaaaaaaaaa8'),
                                  (5, '55aa0d610108ea8055301005aaaaaaaa7f'),
                                  (6, '55aa0f610188e2a0efe0300001aaaaaaaaaa31')]:
            ref, case = self.fixture(subtype=subtype)
            if subtype == 5:
                # Preserve the actual retained source's address for this golden frame.
                pair = dict(storedTx=0xea80, storedRx=0xf7c0, mode5Extra=0x55)
                case['comm']['pairs'] = [pair]
                ref.records[(0xd04, 0)] = deepcopy(case['comm'])
            row = ref.predict(case)
            self.assertEqual(next(e['frameHex'] for e in row['callbackEvents'] if e['name'] == 'SetEnterFrame'), expected)
            self.assertEqual(row['callbackEvents'][-1]['frameHex'], '55aa18601900020000027e0000ffffff000000017e0000ffff00009f')

    def test_changed_filter_order_return_or_raw_frame_rejects_observation(self):
        ref, case = self.fixture()
        original = dict(case=case, **ref.predict(case))
        for field in ['globalFilterAfter', 'nativeReturnWord', 'callbackEvents']:
            bad = deepcopy(original)
            if field == 'callbackEvents':
                bad[field][-1]['frameHex'] = '00'
            else:
                bad[field] ^= 1
            with self.assertRaisesRegex(ValueError, 'reference-mismatch:' + field):
                ref.compare(bad)
        bad = deepcopy(original); bad['callbackEvents'][0:2] = reversed(bad['callbackEvents'][0:2])
        with self.assertRaisesRegex(ValueError, 'callbackEvents'):
            ref.compare(bad)


if __name__ == '__main__':
    unittest.main()
