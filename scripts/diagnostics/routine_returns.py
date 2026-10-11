"""Retained native return-helper rules; source contracts, never response validity.

Counts refer to the helper's signed transport-wrapper result, not a CAN length.
The selectors are internal command bytes; they are not qualified vehicle SIDs.
"""
from copy import deepcopy

_HELPERS = {
    'SendUdsRoutineSidPidCmd': ('generic', 0),
    'SendUdsRoutineSidPidCmd2201XX': ('generic', 1),
    'SendUdsRoutineSidPidCmdMode1': ('mode1', 0),
    'SendUdsRoutineSidPidCmdMode2': ('mode2', 0),
}
_PROFILES = {
    'generic': [
        {'selectors': [0x2F], 'minimumCount': 4, 'stripBytes': 3, 'belowMinimumReturn': 'original-count'},
        {'selectors': [0x30, 0x22], 'minimumCount': 3, 'stripBytes': 2, 'belowMinimumReturn': 'original-count'},
        {'selectors': [0x31], 'inputByte1Equals': 2, 'minimumCount': 2, 'stripBytes': 1, 'belowMinimumReturn': 0},
        {'selectors': [0x31], 'inputByte1NotEquals': 2, 'minimumCount': 4, 'stripBytes': 3, 'belowMinimumReturn': 'original-count'},
        {'selectors': [0x32, 0x33], 'minimumCount': 2, 'stripBytes': 1, 'belowMinimumReturn': 1},
    ],
    'mode1': [
        {'selectors': [0x30, 0x31], 'minimumCount': 1, 'stripBytes': 1, 'belowMinimumReturn': 'early-failure'},
        {'selectors': [0x32, 0x33], 'minimumCount': 2, 'stripBytes': 1, 'belowMinimumReturn': 0},
    ],
    'mode2': [
        {'selectors': [0x2F], 'minimumCount': 4, 'stripBytes': 3, 'belowMinimumReturn': 'original-count'},
        {'selectors': [0x30, 0x32, 0x33], 'minimumCount': 2, 'stripBytes': 1, 'belowMinimumReturn': 1},
        {'selectors': [0x31, 0x22], 'minimumCount': 3, 'stripBytes': 2, 'belowMinimumReturn': 'original-count'},
    ],
}


def routine_return_rule_candidates(helpers):
    """Export only exact retained dependencies, sharing equal source profiles.

    No helper is chosen. No actual bytes, count, output or request are supplied.
    An unrecognized dependency has no contract; it cannot borrow another rule.
    """
    names = sorted(set(helpers))
    known = {name: {'profile': _HELPERS[name][0], 'inputLengthAdjustment': _HELPERS[name][1]}
             for name in names if name in _HELPERS}
    profiles = sorted({item['profile'] for item in known.values()})
    return {'status': 'source-return-rule-candidates', 'helpers': known,
            'unresolvedHelpers': [name for name in names if name not in _HELPERS],
            'profiles': {profile: deepcopy(_PROFILES[profile]) for profile in profiles},
            'inputLengthByteOffset': 0, 'selectorByteOffset': 2,
            'zeroInputLengthOrSignedCountBelowOneReturn': -1,
            'earlyFailureWritesOutput': False,
            'otherSelectorReturn': 'original-positive-count', 'otherSelectorWritesOutput': False,
            'belowMinimumWritesOutput': False,
            'copyCountAndReturn': 'count-minus-strip-bytes',
            'internalReturnBufferBytes': 2048, 'zeroLengthCopyWritesOutput': False,
            'positiveReturnProvesOutputWritten': False, 'responseContentValidated': False,
            'actualHelperSelected': False, 'callerOutputUseQualified': False,
            'executionEnabled': False, 'liveVerified': False}
