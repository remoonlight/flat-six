"""Conditional retained 9x1 dispatcher source paths, never runtime selection.

Source indices describe native record copies, including repeated indices. Neutral
word/tag patches refer only to hypothetical dispatcher inputs, not source mutation.
"""
from .routine_buffers import NATIVE_LINK_SHA256
from .routine_returns import routine_return_rule_candidates

_KNOWN_BUILDERS = {
    'ActRoutineMode1_9x1': 'GetRoutCmdMode1', 'ActRoutineMode2_9x1': 'GetRoutCmdMode2_9x1',
    'ActActMode1_9x1': 'GetActCmdMode1', 'ActActMode2_9x1': 'GetActCmdMode1',
    'ActRoutineMode3_9x1': 'GetRoutCmdMode3', 'ActRoutineMode4_9x1': 'GetRoutCmdMode4',
    'ActRoutineMode5_9x1': 'GetRoutCmdMode4',
    'ActActMode4_9x1': 'GetActCmdMode3', 'ActActMode6_9x1': 'GetActCmdMode1',
    'ActActMode7_9x1': 'GetActCmdMode5', 'ActActMode8_9x1': 'GetActCmdMode1',
    'ActActMode10_9x1': 'GetActCmdMode1', 'ActActMode11_9x1': 'GetActCmdMode7',
    'ActActMode12_9x1': 'GetActCmdMode7', 'ActRoutineMode7_9x1': 'GetRoutCmdMode4',
    'ActRoutineMode8_9x1': 'GetRoutCmdMode4', 'ActRoutineMode9_9x1': 'GetRoutCmdMode5',
    'ActRoutineMode15_9x1': 'GetRoutCmdMode1', 'ActRoutineMode17_9x1': 'GetRoutCmdMode1',
    'ActRoutineMode18_9x1': 'GetRoutCmdMode2_9x1',
}
# Dependencies recovered from normal-entry branches, including code after
# literal pools. These are not the selected helper or a runtime call sequence.
_RETURN_HELPERS = {callee: ('SendUdsRoutineSidPidCmd',) for callee in _KNOWN_BUILDERS}
for _callee in ('ActRoutineMode2_9x1', 'ActRoutineMode9_9x1', 'ActRoutineMode18_9x1'):
    _RETURN_HELPERS[_callee] = ('SendUdsRoutineSidPidCmdMode1',)
for _callee in ('ActRoutineMode1_9x1', 'ActActMode6_9x1', 'ActActMode7_9x1', 'ActActMode8_9x1', 'ActActMode10_9x1'):
    _RETURN_HELPERS[_callee] = ('SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmd2201XX')
_RETURN_HELPERS['ActActMode4_9x1'] = ('SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmdMode1')
_RETURN_HELPERS['ActRoutineMode8_9x1'] = ('SendUdsRoutineSidPidCmd', 'SendUdsRoutineSidPidCmdMode2')


def _dispatch_path(rows):
    order = [i for i, _ in rows]
    patches = []

    def result(callee=None, reason=None, token_case=None):
        return {'nativeCalleeCandidate': callee, 'retainedBuilderCandidate': _KNOWN_BUILDERS.get(callee),
                'sourceReturnHelperDependencies': list(_RETURN_HELPERS.get(callee, ())),
                'reason': reason, 'callerTokenWordCase': token_case,
                'recordSourceOrder': order, 'dispatcherRecordPatchCandidates': patches}

    if not rows:
        return [result(reason='native-first-record-prerequisite-missing')]
    tag = rows[0][1]['tag']
    if tag == 0x11:
        return [result('ActRoutineMode4_9x1', token_case={'byteOffset': 36, 'bytes': 2, 'unsignedRelation': '>=', 'value': 2816}),
                result('ActRoutineMode7_9x1', token_case={'byteOffset': 36, 'bytes': 2, 'unsignedRelation': '<', 'value': 2816})]
    direct = {0x60: 'ActRoutineMode3_9x1', 0x25: 'ActActMode7_9x1',
              0x27: 'ActActMode12_9x1', 0x26: 'ActActMode11_9x1'}
    if tag in direct:
        return [result(direct[tag])]
    swaps = {0x13: (0, 2), 0x12: (0, 2), 2: (0, 1), 8: (1, 2)}
    reorder_calls = {0x13: 'ActRoutineMode5_9x1', 0x12: 'ActRoutineMode8_9x1', 8: 'ActRoutineMode17_9x1'}
    if tag in swaps:
        a, b = swaps[tag]
        if len(rows) <= b:
            return [result(reason='native-record-reorder-prerequisite-missing')]
        order[a], order[b] = order[b], order[a]
        if tag in reorder_calls:
            return [result(reorder_calls[tag])]
    if tag == 0x20:
        if len(rows) < 3:
            return [result(reason='native-record-reorder-prerequisite-missing')]
        # Native final copy reads snapshot record 1, not original record 0.
        order[:3] = [order[1], order[2], order[1]]
        return [result('ActRoutineMode9_9x1')]
    if tag in (0x30, 4, 3):
        if len(rows) > 1:
            b = 2 if tag == 3 else 1
            if len(rows) <= b:
                return [result(reason='native-record-reorder-prerequisite-missing')]
            order[0], order[b] = order[b], order[0]
        else:
            original = rows[0][1]
            a, b = original['sourceFieldA'], original['sourceFieldB']
            target_tag = 0x10 if tag == 0x30 else 1
            patches.append({'sourceRecordIndex': order[0], 'field': 'tag', 'original': tag, 'candidate': target_tag})
            if b is None or (b == 0 and a is None):
                return [result(reason='native-record-word-unresolved')]
            if b == 0:
                patches.extend(({'sourceRecordIndex': order[0], 'field': 'sourceFieldA', 'original': a, 'candidate': a >> 16},
                                {'sourceRecordIndex': order[0], 'field': 'sourceFieldB', 'original': b, 'candidate': a & 65535}))
        if tag == 4:
            return [result('ActActMode10_9x1')]
        if tag == 3:
            return [result('ActActMode6_9x1')]
        chosen = next(r for i, r in rows if i == order[0])
        a = next((p['candidate'] for p in patches if p['field'] == 'sourceFieldA'), chosen['sourceFieldA'])
        if a is None:
            return [result(reason='native-record-word-unresolved')]
        return [result('ActRoutineMode18_9x1' if a & 0xFF00 == 0 and a & 255 == 0x31 else 'ActRoutineMode15_9x1')]
    if tag not in (0x10, 1, 2):
        return [result(reason='native-first-tag-without-callee-branch')]
    chosen = next(r for i, r in rows if i == order[0])
    a = chosen['sourceFieldA']
    if a is None:
        return [result(reason='native-record-word-unresolved')]
    if tag == 2:
        return [result('ActActMode8_9x1' if a & 0xFF00 == 0x2F00 else 'ActActMode4_9x1')]
    if tag == 0x10:
        return [result('ActRoutineMode2_9x1' if a & 0xFF00 == 0 and a & 255 == 0x31 else 'ActRoutineMode1_9x1')]
    callee = {0x2F: 'ActActMode1_9x1', 0x30: 'ActActMode2_9x1'}.get((a >> 8) & 255)
    return [result(callee, reason=None if callee else 'native-first-word-without-callee-branch')]


def routine_dispatch_candidates(records):
    kept = [(i, r) for i, r in enumerate(records) if r['tag'] != 0 and not (r['tag'] == 4 and r['fieldCount'] == 0)]
    patched = []
    patches = []
    for i, row in kept:
        a = row['sourceFieldA']
        if a is not None and a & 0xF000 == 0x3000:
            target = (a & 15) | 0x30
            patches.append({'sourceRecordIndex': i, 'originalSourceFieldA': a, 'candidateSourceFieldA': target})
            row = {**row, 'sourceFieldA': target}
        patched.append((i, row))
    cases = []
    for assumption, rows, changes in (('native-string-argument-does-not-match-PCM31_A3_2', kept, []),
                                       ('native-string-argument-exactly-matches-PCM31_A3_2', patched, patches)):
        cases.append({'postprocessAssumption': assumption, 'keptSourceRecordIndices': [i for i, _ in rows],
                      'postprocessWordPatchCandidates': changes, 'branches': _dispatch_path(rows)})
    helpers = [helper for case in cases for branch in case['branches']
               for helper in branch['sourceReturnHelperDependencies']]
    return {'status': 'source-native-dispatch-candidates', 'nativeLibrarySha256': NATIVE_LINK_SHA256,
            'dispatcher': 'PorscheActRoutineFor9x1', 'readerTokenByte2EqualsAny': [13, 14],
            'inputBasis': 'conditional-nine-x-one-reader-and-string-postprocessing', 'cases': cases,
            'sourceReturnRuleCandidates': routine_return_rule_candidates(helpers),
            'nativeEntryNormalReturnValue': 1, 'normalReturnProvesCalleeOrVehicleSuccess': False,
            'completeNativeRecordStateKnown': False, 'actualCallerSelected': False, 'callerTokenWordQualified': False,
            'postprocessArgumentOriginQualified': False, 'completeCalleeFlowsQualified': False,
            'dependencyBasis': 'retained-native-normal-entry-branches',
            'actualNativeCallSequenceQualified': False, 'exceptionFlowQualified': False,
            'indirectCallBindingsQualified': False,
            'wirePayloadQualified': False, 'recipeQualified': False, 'executionEnabled': False,
            'liveVerified': False, 'requestHex': None, 'writePayload': None}
