import copy
import struct
import unittest

from scripts.diagnostics.routine_buffers import routine_buffer_candidates
from scripts.diagnostics.routine_body import routine_body_layout


def record(tag, a=0x3103, b=0xFEDC):
    return {'tag': tag, 'sourceFieldA': a, 'sourceFieldB': b}


def string_record(tag, text):
    return {**record(tag, None, None), 'prefixString': {'rawHex': (text+b'\0').hex()}}


class RoutineBufferTests(unittest.TestCase):
    def test_mode_one_preserves_internal_prefix_and_low_word_byte_order(self):
        cases = routine_buffer_candidates([record(0x10, 0xABCD3103, 0x1234FEDC), record(0x20, 0x2201, 0x0102), record(0x30, 0x3102, 0xC301)])['cases']
        self.assertEqual([b['internalBufferHex'] for b in cases[0]['buffers']], ['06043103fedc', '060422010102', '06043102c301'])
        self.assertEqual([b['argumentRegister'] for b in cases[0]['buffers']], ['r1', 'r2', 'r3'])

    def test_mode_two_normalizes_signed_positive_word_before_low_byte_packing(self):
        source = [record(0x10), record(0x20, 0x7FFF300A, 0x1122), record(0x30, 0xFFFF310B, 0x3344)]
        case = routine_buffer_candidates(source)['cases'][1]
        self.assertEqual([b['internalBufferHex'] for b in case['buffers']], ['040233dc', '04023a22', '04020b44'])
        self.assertEqual([m['candidateSourceFieldA'] for m in case['sourceFieldAMutationCandidates']], [0x33, 0x3A])
        self.assertEqual(source[0]['sourceFieldA'], 0x3103)

    def test_last_matching_record_overwrites_buffer_and_index(self):
        case = routine_buffer_candidates([record(0x10, 0x2101, 0x1234), record(0x10, 0x3102, 0x5678)])['cases'][0]
        self.assertEqual(case['buffers'][0]['internalBufferHex'], '060431025678')
        self.assertEqual(case['buffers'][0]['sourceRecordIndex'], 1)
        self.assertEqual(case['recordIndexSlots'][0]['sourceRecordIndex'], 1)

    def test_different_index_only_tags_never_fill_a_missing_buffer(self):
        source = [record(0x40, None, None), record(0x50), record(0x40, None, None)]
        cases = routine_buffer_candidates(source)['cases']
        self.assertEqual(cases[0]['recordIndexSlots'][-1], {'recordTag': 0x40, 'sourceRecordIndex': 2})
        self.assertEqual(cases[1]['recordIndexSlots'][-1], {'recordTag': 0x50, 'sourceRecordIndex': 1})
        self.assertTrue(all(b['internalBufferHex'] is None for c in cases for b in c['buffers']))

    def test_unknown_native_words_and_unwritten_buffers_stay_distinct(self):
        parsed = routine_buffer_candidates([record(0x10, None), record(0x11, None, None)])
        self.assertEqual(parsed['undeclaredNativeWordSourceIndices'], [0, 1])
        for case in parsed['cases'][:2]:
            self.assertEqual(case['buffers'][0]['status'], 'native-record-word-unresolved')
            self.assertEqual(case['buffers'][1]['status'], 'native-buffer-not-written-by-builder')
            self.assertTrue(all(b['internalBufferHex'] is None for b in case['buffers']))

    def test_act_word_conditions_have_distinct_packing_orders(self):
        source = [record(3, 0x2F01, 0xAABB), record(2, 0x3102, 0xCCDD), record(1, 0x22, 0xEEFF),
                  record(4, 0x2003, 0x1122), record(6, 0xABCD3204, 0x3344)]
        case = routine_buffer_candidates(source)['cases'][2]
        self.assertEqual([b['internalBufferHex'] for b in case['buffers']],
                         ['06042faabb01', '06043102ccdd', '050322eeff', '0503202203', '060432043344'])

    def test_act_aliases_share_last_output_and_index_only_tags(self):
        case = routine_buffer_candidates([record(1), record(0x10, 0x2102, 3), record(5, None),
                                          record(7, None), record(4), record(8, 0x2204, 5)])['cases'][2]
        self.assertEqual(case['buffers'][2]['sourceRecordIndex'], 1)
        self.assertEqual(case['buffers'][3]['sourceRecordIndex'], 5)
        self.assertEqual(case['recordIndexSlots'][-1]['sourceRecordIndex'], 3)
        self.assertEqual(case['buffers'][0]['status'], 'native-buffer-not-written-by-builder')

    def test_all_act_output_unknown_words_stay_unresolved(self):
        case = routine_buffer_candidates([record(tag, None) for tag in (3, 2, 1, 4, 6)])['cases'][2]
        self.assertTrue(all(b['internalBufferHex'] is None and b['status'] == 'native-record-word-unresolved'
                            for b in case['buffers']))

    def test_string_builders_dispatch_different_tags_and_preserve_bytes(self):
        source = [string_record(tag, text) for tag, text in ((0x60,b'0xAABB'),(0x70,b'0x0011'),
                 (0x80,b'0x2233'),(0x11,b'0x4455'),(0x12,b'0x6677'),(0x13,b'0x8899'),(0x14,b'0xAABC'))]
        before = copy.deepcopy(source); cases = routine_buffer_candidates(source)['cases']
        self.assertEqual([b['internalBufferHex'] for b in cases[3]['buffers']], ['0402aabb','04020011','04022233'])
        self.assertEqual([b['internalBufferHex'] for b in cases[4]['buffers']], ['04024455','0402aabc','04026677','04028899'])
        self.assertEqual(source, before)

    def test_short_string_reuses_previous_length_even_from_other_tags(self):
        source = [string_record(0x60,b'x'), string_record(0x11,b'0x001122334455'), string_record(0x70,b'x')]
        case = routine_buffer_candidates(source)['cases'][3]
        self.assertEqual(case['buffers'][0]['internalBufferHex'], '00000000')
        self.assertEqual(case['buffers'][1]['internalBufferHex'], '00'*8)
        self.assertTrue(case['shortStringLengthDependsOnEarlierRecords'])

    def test_unknown_native_string_blocks_short_length_but_later_long_string_resets(self):
        case = routine_buffer_candidates([record(1), string_record(0x60,b'x'),
                   string_record(0x70,b'0x0011')])['cases'][3]
        self.assertEqual(case['undeclaredNativeStringSourceIndices'], [0])
        self.assertEqual(case['buffers'][0]['status'], 'native-prior-string-length-unresolved')
        self.assertEqual(case['buffers'][1]['internalBufferHex'], '04020011')

    def test_native_string_skips_prefix_and_odd_trailing_character(self):
        case = routine_buffer_candidates([string_record(0x60,b'zzAABB'),
                                          string_record(0x70,b'0xABC')])['cases'][3]
        self.assertEqual(case['buffers'][0]['internalBufferHex'], '0402aabb')
        self.assertEqual(case['buffers'][1]['internalBufferHex'], '0402ab00')
        self.assertFalse(case['nativeStringPrefixValidated'])

    def test_string_scratch_boundary_and_non_hex_pairs_refuse_guesses(self):
        case = routine_buffer_candidates([string_record(0x60,b'0x'+b'AA'*254),
                   string_record(0x70,b'0x'+b'AA'*255), string_record(0x80,b'0x+ABB')])['cases'][3]
        self.assertEqual(case['buffers'][0]['internalBufferHex'], '00fe'+'aa'*254)
        self.assertEqual(case['buffers'][1]['status'], 'native-string-scratch-layout-unresolved')
        self.assertEqual(case['buffers'][2]['status'], 'native-strtol-pair-unresolved')

    def test_string_overwrite_keeps_last_unresolved_result(self):
        case = routine_buffer_candidates([string_record(0x11,b'0x0011'),record(0x11)])['cases'][4]
        self.assertIsNone(case['buffers'][0]['internalBufferHex'])
        self.assertEqual(case['buffers'][0]['sourceRecordIndex'], 1)

    def test_normalization_checks_all_declared_words_without_mutating_source(self):
        records = [record(0x01), record(0x40), record(0x02, 0xFF), record(0x03, 0x4003)]
        before = copy.deepcopy(records); parsed = routine_buffer_candidates(records)
        self.assertEqual(records, before)
        self.assertEqual([m['sourceRecordIndex'] for m in parsed['cases'][1]['sourceFieldAMutationCandidates']], [0, 1])
        self.assertEqual(parsed['cases'][0]['sourceFieldAMutationCandidates'], [])

    def test_no_caller_transport_or_execution_qualification_is_published(self):
        for records in ([], [record(0x10)]):
            parsed = routine_buffer_candidates(records)
            for key in ('callerPostprocessingApplied', 'actualCallerSelected', 'completeNativeInputQualified',
                        'internalHeaderSemanticsQualified', 'wirePayloadQualified', 'recipeQualified', 'executionEnabled', 'liveVerified'):
                self.assertFalse(parsed[key])
            self.assertIsNone(parsed['requestHex']); self.assertIsNone(parsed['writePayload'])

    def test_incomplete_body_never_publishes_buffer_candidates(self):
        body = b'\x01\x10' + struct.pack('<IIB', 0x3103, 0xFEDC, 0) + b'\x81'
        parsed = routine_body_layout(body)
        self.assertEqual(parsed['nativeBufferCandidates']['cases'][0]['buffers'][0]['internalBufferHex'], '06043103fedc')
        self.assertEqual(parsed['records'][0]['sourceFieldA'], 0x3103)
        for size in range(len(body)):
            self.assertIsNone(routine_body_layout(body[:size])['nativeBufferCandidates'])

    def test_mode_three_keeps_distinct_arguments_and_fallback_lengths(self):
        source = [record(1, 0x2F01, 0xAABB), record(2, 0x22, 0xCCDD), record(3, 0x3102, 0xEEFF),
                  record(5, 0x3203, 0x1122), record(6, 0x2004, 0x3344)]
        case = routine_buffer_candidates(source)['cases'][5]
        self.assertEqual([b['internalBufferHex'] for b in case['buffers']],
                         ['06042faabb01', '050322ccdd', '040202ff', '04020322', '04020444'])
        self.assertEqual([b['argumentRegister'] for b in case['buffers']],
                         ['r1', 'r2', 'r3', 'stack-argument-4', 'stack-argument-5'])

    def test_mode_three_index_alias_does_not_overwrite_output(self):
        case = routine_buffer_candidates([record(5, 0x3101), record(7, None), record(4, None)])['cases'][5]
        self.assertEqual(case['buffers'][3]['sourceRecordIndex'], 0)
        self.assertEqual(case['buffers'][3]['internalBufferHex'], '040201dc')
        self.assertEqual(case['recordIndexSlots'][-1]['sourceRecordIndex'], 1)
        self.assertEqual(case['recordIndexSlots'][3]['sourceRecordIndex'], 2)

    def test_mode_seven_aliases_and_fallback_are_distinct_from_act_one(self):
        case = routine_buffer_candidates([record(1), record(0x27, 0x3102), record(2, 0x3203),
                   record(3, 0x22), record(4, 0x2F04), record(0x26, 0x2005), record(7)])['cases'][7]
        self.assertEqual([b['internalBufferHex'] for b in case['buffers']],
                         ['050331dc02', '050332dc03', '050322fedc', '06042ffedc04', '050320dc05'])
        self.assertEqual(case['recordIndexSlots'][-1]['sourceRecordIndex'], 6)
        self.assertEqual(case['buffers'][0]['sourceRecordIndex'], 1)

    def test_routine_five_third_output_is_tag_forty_and_thirty_is_index_only(self):
        source = [record(0x10), record(0x20, 0x7FFF300A, 0x1122), record(0x30), record(0x40, None, 0x3344)]
        before = copy.deepcopy(source); case = routine_buffer_candidates(source)['cases'][8]
        self.assertEqual(source, before)
        self.assertEqual([b['recordTag'] for b in case['buffers']], [0x10, 0x20, 0x40])
        self.assertEqual([b['internalBufferHex'] for b in case['buffers']], ['040233dc', '04023a22', None])
        self.assertEqual(case['buffers'][2]['status'], 'native-record-word-unresolved')
        self.assertEqual([s['sourceRecordIndex'] for s in case['recordIndexSlots']], [0, 1, 2, 3])
        self.assertEqual([m['sourceRecordIndex'] for m in case['sourceFieldAMutationCandidates']], [0, 1, 2])

    def test_routine_five_signed_negative_word_is_never_normalized(self):
        case = routine_buffer_candidates([record(0x40, 0xFFFF310B, 0x3344)])['cases'][8]
        self.assertEqual(case['buffers'][2]['internalBufferHex'], '04020b44')
        self.assertEqual(case['sourceFieldAMutationCandidates'], [])

    def test_act_five_exact_copy_length_is_not_derived_from_first_header_byte(self):
        for a, b, expected, count in ((0x3101, None, '03023101', 2),
                                      (0x3102, None, '03023102', 2),
                                      (0x3103, None, '03023103', 2),
                                      (0x3128, 0xABCD, '040328cd00', 3),
                                      (0x3185, 0xABCD, '070585cdffffff', 5)):
            case = routine_buffer_candidates([record(0x23, a, b)])['cases'][6]
            first = case['buffers'][0]
            self.assertEqual(first['internalBufferHex'], expected)
            self.assertEqual(first['copiedBytes'], count + 2)
            self.assertEqual(first['nativeDataCountCandidate'], count)
            self.assertFalse(case['internalHeaderLengthEqualityQualified'])
            self.assertTrue(all(b['status'] == 'native-buffer-not-written-by-builder' for b in case['buffers'][1:]))

    def test_act_five_unrecognized_selector_uses_prior_count_and_cleared_scratch(self):
        for previous, expected in ((None, '0000'), (0x3101, '00007700'),
                                    (0x28, '0000770000'), (0x85, '00007700000000')):
            source = [] if previous is None else [record(0x10, previous)]
            source.append(record(0x25, 0x77, None))
            case = routine_buffer_candidates(source)['cases'][6]
            self.assertEqual(case['buffers'][0]['internalBufferHex'], expected)
            self.assertEqual(case['buffers'][0]['copiedBytes'], len(expected)//2)

    def test_act_five_unknown_word_blocks_prior_count_until_recognized_selector(self):
        case = routine_buffer_candidates([record(0x10, None), record(0x24, 0x77)])['cases'][6]
        self.assertEqual(case['buffers'][0]['status'], 'native-prior-data-count-unresolved')
        self.assertIsNone(case['buffers'][0]['copiedBytes'])
        case = routine_buffer_candidates([record(0x10, None), record(0x24, 0x3102, None)])['cases'][6]
        self.assertEqual(case['buffers'][0]['internalBufferHex'], '03023102')

    def test_act_five_recognized_selector_sets_count_even_when_b_is_unknown(self):
        case = routine_buffer_candidates([record(0x10, 0x85, None), record(0x24, 0x77)])['cases'][6]
        self.assertEqual(case['buffers'][0]['internalBufferHex'], '00007700000000')
        case = routine_buffer_candidates([record(0x25, 0x28, None)])['cases'][6]
        self.assertEqual(case['buffers'][0]['status'], 'native-record-word-unresolved')
        self.assertEqual(case['buffers'][0]['copiedBytes'], 5)

    def test_act_five_aliases_only_write_first_buffer_and_last_unknown_wins(self):
        source = [record(0x23, 0x85), record(0x24, 0x3102), record(0x25, None)]
        before = copy.deepcopy(source); case = routine_buffer_candidates(source)['cases'][6]
        self.assertEqual(source, before)
        self.assertEqual(case['buffers'][0]['sourceRecordIndex'], 2)
        self.assertIsNone(case['buffers'][0]['internalBufferHex'])
        self.assertEqual([s['sourceRecordIndex'] for s in case['recordIndexSlots']], [2, None, None, None, None, None])

    def test_nine_builder_order_and_original_word_absence_remain_explicit(self):
        parsed = routine_buffer_candidates([record(tag, None) for tag in (1, 2, 3, 5, 6)])
        self.assertEqual([c['builder'] for c in parsed['cases']],
                         ['GetRoutCmdMode1', 'GetRoutCmdMode2_9x1', 'GetActCmdMode1', 'GetRoutCmdMode3',
                          'GetRoutCmdMode4', 'GetActCmdMode3', 'GetActCmdMode5', 'GetActCmdMode7', 'GetRoutCmdMode5'])
        for case in (parsed['cases'][5], parsed['cases'][7]):
            self.assertTrue(all(b['internalBufferHex'] is None for b in case['buffers']))


if __name__ == '__main__':
    unittest.main()
