import copy
import struct
import unittest
from scripts.diagnostics.routine_dispatch import routine_dispatch_candidates
from scripts.diagnostics.routine_body import routine_body_layout


def record(tag, a=0x2F01, b=1, count=1):
    return {'tag': tag, 'sourceFieldA': a, 'sourceFieldB': b, 'fieldCount': count}


def path(rows, matched=False):
    return routine_dispatch_candidates(rows)['cases'][int(matched)]['branches']


class RoutineDispatchTests(unittest.TestCase):
    def test_word_dispatch_distinguishes_low_byte_and_high_byte_conditions(self):
        for a, callee in ((0x31,'ActRoutineMode2_9x1'),(0xABCD0031,'ActRoutineMode2_9x1'),
                          (0x3101,'ActRoutineMode1_9x1'),(0x131,'ActRoutineMode1_9x1')):
            self.assertEqual(path([record(0x10,a)])[0]['nativeCalleeCandidate'],callee)
        self.assertEqual(path([record(1,0xFFFF2F01)])[0]['nativeCalleeCandidate'],'ActActMode1_9x1')
        self.assertEqual(path([record(1,0x3001)])[0]['nativeCalleeCandidate'],'ActActMode2_9x1')
        self.assertIsNone(path([record(1,0x3101)])[0]['nativeCalleeCandidate'])

    def test_string_first_tag_requires_unsigned_token_word_alternatives(self):
        branches=path([record(0x11,None,None)])
        self.assertEqual([b['nativeCalleeCandidate'] for b in branches],['ActRoutineMode4_9x1','ActRoutineMode7_9x1'])
        self.assertEqual([b['callerTokenWordCase']['unsignedRelation'] for b in branches],['>=','<'])
        self.assertEqual(branches[0]['callerTokenWordCase']['byteOffset'],36)
        self.assertEqual(branches[0]['callerTokenWordCase']['value'],2816)

    def test_direct_and_reordered_targets_retain_unparsed_callees(self):
        for tag,callee in ((0x60,'ActRoutineMode3_9x1'),(0x25,'ActActMode7_9x1'),
                          (0x26,'ActActMode11_9x1'),(0x27,'ActActMode12_9x1')):
            self.assertEqual(path([record(tag)])[0]['nativeCalleeCandidate'],callee)
        for tag,callee,order in ((0x13,'ActRoutineMode5_9x1',[2,1,0]),
                                (0x12,'ActRoutineMode8_9x1',[2,1,0]),(8,'ActRoutineMode17_9x1',[0,2,1])):
            p=path([record(tag),record(7),record(6)])[0]
            self.assertEqual((p['nativeCalleeCandidate'],p['recordSourceOrder']),(callee,order))

    def test_tag_twenty_copy_repeats_snapshot_record_one(self):
        p=path([record(0x20),record(1),record(3),record(4)])[0]
        self.assertEqual(p['nativeCalleeCandidate'],'ActRoutineMode9_9x1')
        self.assertEqual(p['recordSourceOrder'],[1,2,1,3])

    def test_tag_two_selects_from_new_first_record_after_swap(self):
        for a,callee in ((0x2F99,'ActActMode8_9x1'),(0x3101,'ActActMode4_9x1')):
            p=path([record(2,0x3101),record(1,a)])[0]
            self.assertEqual(p['recordSourceOrder'],[1,0])
            self.assertEqual(p['nativeCalleeCandidate'],callee)

    def test_single_record_retag_split_and_multi_record_swap_are_distinct(self):
        p=path([record(0x30,0x00311234,0)])[0]
        self.assertEqual(p['nativeCalleeCandidate'],'ActRoutineMode18_9x1')
        self.assertEqual([r['candidate'] for r in p['dispatcherRecordPatchCandidates']],[0x10,0x31,0x1234])
        p=path([record(0x30,0x31,1),record(1,0x3101)])[0]
        self.assertEqual(p['nativeCalleeCandidate'],'ActRoutineMode15_9x1')
        self.assertEqual(p['recordSourceOrder'],[1,0]); self.assertEqual(p['dispatcherRecordPatchCandidates'],[])
        for tag,callee in ((4,'ActActMode10_9x1'),(3,'ActActMode6_9x1')):
            p=path([record(tag,0x22334455,0)])[0]
            self.assertEqual(p['nativeCalleeCandidate'],callee)
            self.assertEqual([r['candidate'] for r in p['dispatcherRecordPatchCandidates']],[1,0x2233,0x4455])

    def test_missing_records_and_unknown_native_words_do_not_choose_callees(self):
        for rows in ([],[record(2)],[record(0x13)],[record(0x20),record(1)],
                     [record(3),record(1)],[record(0x10,None)],[record(0x30,1,None)]):
            p=path(rows)[0]
            self.assertIsNone(p['nativeCalleeCandidate']); self.assertIsNotNone(p['reason'])
        self.assertEqual(path([record(0x41)])[0]['reason'],'native-first-tag-without-callee-branch')

    def test_reader_filter_and_exact_name_postprocess_precede_dispatch(self):
        rows=[record(0),record(4,count=0),record(1,0x3001)]
        before=copy.deepcopy(rows); result=routine_dispatch_candidates(rows)
        self.assertEqual(result['cases'][0]['keptSourceRecordIndices'],[2])
        self.assertEqual(result['cases'][0]['branches'][0]['nativeCalleeCandidate'],'ActActMode2_9x1')
        self.assertIsNone(result['cases'][1]['branches'][0]['nativeCalleeCandidate'])
        self.assertEqual(rows,before)

    def test_no_runtime_success_transport_or_execution_qualification(self):
        d=routine_dispatch_candidates([record(0x10,0x31)])
        for key in ('normalReturnProvesCalleeOrVehicleSuccess','actualCallerSelected','callerTokenWordQualified',
                    'postprocessArgumentOriginQualified','completeNativeRecordStateKnown','completeCalleeFlowsQualified',
                    'wirePayloadQualified','recipeQualified','executionEnabled','liveVerified'):
            self.assertFalse(d[key])
        self.assertEqual(d['nativeEntryNormalReturnValue'],1)
        self.assertIsNone(d['requestHex']); self.assertIsNone(d['writePayload'])

    def test_body_integration_and_truncation_guard(self):
        body=b'\x01\x10'+struct.pack('<IIB',0x31,1,0)+b'\x81'
        d=routine_body_layout(body)['nativeDispatchCandidates']
        self.assertEqual(d['cases'][0]['branches'][0]['nativeCalleeCandidate'],'ActRoutineMode2_9x1')
        for n in range(len(body)):
            self.assertIsNone(routine_body_layout(body[:n])['nativeDispatchCandidates'])

    def test_recovered_tail_keeps_act_eight_extra_return_dependency(self):
        p = path([record(2, 0x3101), record(1, 0x2F01)])[0]
        self.assertEqual(p['nativeCalleeCandidate'], 'ActActMode8_9x1')
        self.assertEqual(p['retainedBuilderCandidate'], 'GetActCmdMode1')
        self.assertEqual(p['sourceReturnHelperDependencies'],
                         ['SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmd2201XX'])

    def test_newly_retained_act_builders_bind_to_exact_source_targets(self):
        cases = [(0x25, 'GetActCmdMode5'), (0x26, 'GetActCmdMode7'), (0x27, 'GetActCmdMode7')]
        for tag, builder in cases:
            self.assertEqual(path([record(tag)])[0]['retainedBuilderCandidate'], builder)
        p = path([record(2), record(1, 0x3101)])[0]
        self.assertEqual(p['retainedBuilderCandidate'], 'GetActCmdMode3')
        self.assertEqual(p['sourceReturnHelperDependencies'],
                         ['SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmdMode1'])

    def test_routine_dependencies_distinguish_modes_and_helper_variants(self):
        p = path([record(0x20), record(1), record(3)])[0]
        self.assertEqual(p['retainedBuilderCandidate'], 'GetRoutCmdMode5')
        self.assertEqual(p['sourceReturnHelperDependencies'], ['SendUdsRoutineSidPidCmdMode1'])
        p = path([record(0x12), record(1), record(3)])[0]
        self.assertEqual(p['retainedBuilderCandidate'], 'GetRoutCmdMode4')
        self.assertEqual(p['sourceReturnHelperDependencies'],
                         ['SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmdMode2'])
        self.assertTrue(all(p['retainedBuilderCandidate'] == 'GetRoutCmdMode4'
                            for p in path([record(0x11)])))

    def test_missing_target_never_borrows_dependency_from_another_branch(self):
        for records in ([], [record(0x41)], [record(2)], [record(1, None)]):
            for branch in path(records):
                self.assertIsNone(branch['retainedBuilderCandidate'])
                self.assertEqual(branch['sourceReturnHelperDependencies'], [])

    def test_source_dependencies_do_not_qualify_runtime_sequence_or_exceptions(self):
        rows = [record(2), record(1, 0x2F01)]; before = copy.deepcopy(rows)
        d = routine_dispatch_candidates(rows)
        self.assertEqual(rows, before)
        self.assertEqual(d['dependencyBasis'], 'retained-native-normal-entry-branches')
        for key in ('actualNativeCallSequenceQualified', 'exceptionFlowQualified', 'indirectCallBindingsQualified',
                    'completeCalleeFlowsQualified', 'wirePayloadQualified', 'executionEnabled', 'recipeQualified'):
            self.assertFalse(d[key])


if __name__=='__main__':unittest.main()
