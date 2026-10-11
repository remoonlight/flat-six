import unittest

from scripts.diagnostics.routine_returns import routine_return_rule_candidates
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


if __name__ == '__main__':
    unittest.main()
