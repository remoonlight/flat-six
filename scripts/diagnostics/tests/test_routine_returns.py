import unittest

from scripts.diagnostics.routine_returns import routine_return_rule_candidates, audit_routine_helper_response
from scripts.diagnostics.routine_dispatch import routine_dispatch_candidates


class RoutineReturnRulesTests(unittest.TestCase):
    def test_shared_profile_keeps_different_native_input_lengths(self):
        result = routine_return_rule_candidates(['SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmd2201XX'])
        self.assertEqual(list(result['profiles']), ['generic'])
        self.assertEqual(result['helpers']['SendUdsRoutineSidPidCmd']['inputLengthAdjustment'], 0)
        self.assertEqual(result['helpers']['SendUdsRoutineSidPidCmd2201XX']['inputLengthAdjustment'], 1)

    def test_generic_31_depends_on_internal_byte_one(self):
        rules = routine_return_rule_candidates(['SendUdsRoutineSidPidCmd'])['profiles']['generic']
        split = [rule for rule in rules if rule['selectors'] == [0x31]]
        self.assertEqual(len(split), 2)
        self.assertEqual(split[0]['inputByte1Equals'], 2)
        self.assertEqual((split[0]['minimumCount'], split[0]['stripBytes'], split[0]['belowMinimumReturn']), (2, 1, 0))
        self.assertEqual(split[1]['inputByte1NotEquals'], 2)
        self.assertEqual((split[1]['minimumCount'], split[1]['stripBytes']), (4, 3))

    def test_mode_one_zero_length_copy_is_not_output_written(self):
        result = routine_return_rule_candidates(['SendUdsRoutineSidPidCmdMode1'])
        rule = result['profiles']['mode1'][0]
        self.assertEqual(rule['selectors'], [0x30, 0x31])
        self.assertEqual((rule['minimumCount'], rule['stripBytes']), (1, 1))
        self.assertEqual(result['copyCountAndReturn'], 'count-minus-strip-bytes')
        self.assertFalse(result['zeroLengthCopyWritesOutput'])

    def test_positive_short_results_differ_between_modes(self):
        result = routine_return_rule_candidates(['SendUdsRoutineSidPidCmdMode1', 'SendUdsRoutineSidPidCmdMode2'])
        one = next(r for r in result['profiles']['mode1'] if 0x32 in r['selectors'])
        two = next(r for r in result['profiles']['mode2'] if 0x32 in r['selectors'])
        self.assertEqual(one['belowMinimumReturn'], 0)
        self.assertEqual(two['belowMinimumReturn'], 1)
        self.assertFalse(result['belowMinimumWritesOutput'])

    def test_no_dependency_has_no_rule_profile(self):
        result = routine_return_rule_candidates([])
        self.assertEqual(result['helpers'], {})
        self.assertEqual(result['profiles'], {})
        self.assertEqual(result['unresolvedHelpers'], [])

    def test_unrecognized_helper_does_not_borrow_rule(self):
        result = routine_return_rule_candidates(['other', 'SendUdsRoutineSidPidCmdMode2'])
        self.assertEqual(result['unresolvedHelpers'], ['other'])
        self.assertNotIn('other', result['helpers'])
        self.assertEqual(list(result['profiles']), ['mode2'])

    def test_export_mutation_does_not_change_retained_rules(self):
        first = routine_return_rule_candidates(['SendUdsRoutineSidPidCmd'])
        first['profiles']['generic'][0]['selectors'].append(0xFF)
        first['profiles']['generic'][0]['stripBytes'] = 0
        second = routine_return_rule_candidates(['SendUdsRoutineSidPidCmd'])
        self.assertEqual(second['profiles']['generic'][0]['selectors'], [0x2F])
        self.assertEqual(second['profiles']['generic'][0]['stripBytes'], 3)

    def test_output_content_and_execution_remain_unqualified(self):
        result = routine_return_rule_candidates(['SendUdsRoutineSidPidCmdMode2'])
        self.assertEqual(result['internalReturnBufferBytes'], 2048)
        self.assertEqual(result['zeroInputLengthOrSignedCountBelowOneReturn'], -1)
        for key in ('positiveReturnProvesOutputWritten', 'responseContentValidated', 'actualHelperSelected',
                    'callerOutputUseQualified', 'executionEnabled', 'liveVerified', 'otherSelectorWritesOutput'):
            self.assertFalse(result[key])

    def test_dispatch_export_uses_only_source_dependencies(self):
        row = {'tag': 0x12, 'sourceFieldA': None, 'sourceFieldB': None, 'fieldCount': 0}
        result = routine_dispatch_candidates([row, {**row, 'tag': 1}, {**row, 'tag': 3}])
        rules = result['sourceReturnRuleCandidates']
        self.assertEqual(set(rules['helpers']), {'SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmdMode2'})
        self.assertEqual(set(rules['profiles']), {'generic', 'mode2'})
        self.assertFalse(result['executionEnabled'])


def observed_helper(body=b'\x51\x42\x01', *, helper='SendUdsRoutineSidPidCmd', selector=0x30,
                    second=4, declared=None, provider_count=None):
    raw = b'\x00\x01' + bytes(6) + bytes([len(body)+1 if declared is None else declared, 0x62]) + body
    return {'helper': helper, 'commandHex': bytes([6, second, selector, 0, 0, 0, 0]).hex(),
            'communication': {'requestBytes': 7 if helper.endswith('2201XX') else 6,
                              'extendedRequestByte': 0,
                              'internal': {'path': 'direct', 'capacity': 0x1014,
                                           'transportReturn': len(raw)+1 if provider_count is None else provider_count+1,
                                           'frameHex': (b'\x00'+raw).hex()}}}


class RoutineHelperResponseTests(unittest.TestCase):
    def test_fresh_internal_body_is_stripped_without_vehicle_qualification(self):
        result = audit_routine_helper_response(observed_helper())
        self.assertEqual(result['sourceDataHexCandidate'], '01')
        self.assertEqual((result['nativeHelperReturnCandidate'], result['sourceCopiedBytesCandidate']), (1, 1))
        self.assertTrue(result['sourceDataComplete'])
        for key in ('positiveEcuResponseQualified', 'vehicleResultQualified', 'executionEnabled',
                    'liveVerified', 'deviceIO', 'vehicleIO', 'callerOutputCapacityQualified',
                    'actualHelperSelected', 'returnAloneProvesValueOrVehicleSuccess'):
            self.assertFalse(result[key])
        self.assertIsNone(result['requestHex']); self.assertIsNone(result['writePayload'])

    def test_mode_specific_copy_extents_and_2201_length_adjustment(self):
        one = audit_routine_helper_response(observed_helper(helper='SendUdsRoutineSidPidCmdMode1'))
        two = audit_routine_helper_response(observed_helper(helper='SendUdsRoutineSidPidCmdMode2'))
        adjusted = audit_routine_helper_response(observed_helper(helper='SendUdsRoutineSidPidCmd2201XX'))
        self.assertEqual(one['sourceDataHexCandidate'], '4201')
        self.assertEqual(two['sourceDataHexCandidate'], '4201')
        self.assertEqual(adjusted['sourceDataHexCandidate'], '01')

    def test_empty_body_can_force_positive_return_without_fresh_output(self):
        result = audit_routine_helper_response(observed_helper(b''))
        self.assertTrue(result['communicationAudit']['sourceOutputComplete'])
        self.assertEqual(result['communicationReturnCandidate'], 1)
        self.assertEqual(result['nativeHelperReturnCandidate'], 1)
        self.assertFalse(result['sourceDataComplete'])
        self.assertIsNone(result['sourceDataHexCandidate'])
        self.assertEqual(result['sourceCopiedBytesCandidate'], 0)

    def test_zero_byte_copy_and_unsupported_selector_never_supply_data(self):
        zero = audit_routine_helper_response(observed_helper(b'Q', helper='SendUdsRoutineSidPidCmdMode1'))
        other = audit_routine_helper_response(observed_helper(selector=0x99))
        self.assertEqual(zero['status'], 'routine-helper-zero-byte-copy')
        self.assertEqual(zero['nativeHelperReturnCandidate'], 0)
        self.assertEqual(other['nativeHelperReturnCandidate'], 3)
        self.assertEqual(other['status'], 'routine-helper-selector-does-not-copy')
        for result in (zero, other):
            self.assertFalse(result['sourceDataComplete']); self.assertIsNone(result['sourceDataHexCandidate'])

    def test_truncated_and_inconsistent_reported_extent_stop_before_value_derivation(self):
        for observation in (observed_helper(b'', declared=4), observed_helper(b'Q', declared=4),
                            observed_helper(provider_count=1)):
            result = audit_routine_helper_response(observation)
            self.assertFalse(result['sourceDataComplete'])
            self.assertIsNone(result['nativeHelperReturnCandidate'])
            self.assertIsNone(result['sourceDataHexCandidate'])

    def test_missing_header_and_failed_receive_have_no_value_candidate(self):
        for count, frame in ((-1, ''), (0, '00'), (10, '00000100000000000004')):
            observation = observed_helper()
            observation['communication']['internal'].update(transportReturn=count, frameHex=frame)
            result = audit_routine_helper_response(observation)
            self.assertFalse(result['sourceDataComplete']); self.assertIsNone(result['sourceDataHexCandidate'])

    def test_no_command_rejects_fabricated_receive_and_short_source_buffer(self):
        empty = {'helper': 'SendUdsRoutineSidPidCmd', 'commandHex': '00', 'communication': None}
        self.assertEqual(audit_routine_helper_response(empty)['nativeHelperReturnCandidate'], -1)
        with self.assertRaises(ValueError): audit_routine_helper_response({**empty, 'communication': {}})
        with self.assertRaises(ValueError): audit_routine_helper_response({**observed_helper(), 'commandHex': '0630'})

    def test_inconsistent_or_unknown_route_does_not_borrow_a_contract(self):
        for change in ({'helper': 'unknown'}, {'helper': []}, {'commandHex': '0'},
                       {'commandHex': 'gg'}, {'extra': True}):
            with self.assertRaises(ValueError): audit_routine_helper_response({**observed_helper(), **change})
        for field, value in (('requestBytes', 5), ('extendedRequestByte', 1), ('extendedRequestByte', False)):
            observation = observed_helper(); observation['communication'][field] = value
            with self.assertRaises(ValueError): audit_routine_helper_response(observation)

    def test_oversized_internal_copy_and_extended_packing_are_not_guessed(self):
        observation = observed_helper()
        raw = b'\x00\x02' + bytes(6) + b'\x18\x02\x62' + b'A'*2049
        observation['communication']['internal'].update(transportReturn=len(raw)+1, frameHex=(b'\x00'+raw).hex())
        result = audit_routine_helper_response(observation)
        self.assertEqual(result['status'], 'routine-helper-internal-copy-extent-unqualified')
        self.assertFalse(result['sourceDataComplete'])
        observation = observed_helper(helper='SendUdsRoutineSidPidCmd2201XX')
        observation['commandHex'] = (bytes([255, 4, 0x30])+bytes(253)).hex()
        observation['communication']['requestBytes'] = 256
        result = audit_routine_helper_response(observation)
        self.assertEqual(result['status'], 'routine-helper-extended-packing-unqualified')
        self.assertFalse(result['sourceDataComplete'])


if __name__ == '__main__':
    unittest.main()
