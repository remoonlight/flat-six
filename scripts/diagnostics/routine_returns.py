"""Retained native return-helper rules; source contracts, never response validity.

Counts refer to the helper's signed transport-wrapper result, not a CAN length.
The selectors are internal command bytes; they are not qualified vehicle SIDs.
"""
from copy import deepcopy
import re
from .routine_buffers import NATIVE_LINK_SHA256

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


def audit_routine_helper_response(observation):
    """Audit one supplied mode10/subtype3 reply through a retained helper.

    Complete internal bytes and the helper's copy extent are separate from its
    return value. This never identifies an ECU response or a vehicle result.
    """
    if not isinstance(observation, dict) or set(observation) != {'helper', 'commandHex', 'communication'}:
        raise ValueError('routine-helper-observation-fields-required')
    helper = observation['helper']
    if not isinstance(helper, str) or helper not in _HELPERS:
        raise ValueError('routine-helper-source-unqualified')
    hx = observation['commandHex']
    if not isinstance(hx, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,256}', hx):
        raise ValueError('routine-helper-command-hex-invalid')
    command = bytes.fromhex(hx)
    out = {'status': 'routine-helper-response-unqualified', 'helper': helper,
           'nativeLibrarySha256': NATIVE_LINK_SHA256,
           'sourceKind': 'explicit-offline-internal-observation',
           'communicationAudit': None, 'communicationReturnCandidate': None,
           'nativeHelperReturnCandidate': None, 'sourceCopiedBytesCandidate': 0,
           'sourceDataHexCandidate': None, 'sourceDataComplete': False,
           'returnAloneProvesValueOrVehicleSuccess': False,
           'sourceReturnRules': routine_return_rule_candidates([helper]),
           'actualHelperSelected': False, 'callerOutputCapacityQualified': False,
           'positiveEcuResponseQualified': False, 'vehicleResultQualified': False,
           'offlineOnly': True, 'executionEnabled': False, 'liveVerified': False,
           'deviceIO': False, 'vehicleIO': False, 'requestHex': None, 'writePayload': None}
    if command[0] == 0:
        if observation['communication'] is not None:
            raise ValueError('routine-helper-empty-command-has-no-communication')
        return {**out, 'status': 'routine-helper-no-command', 'nativeHelperReturnCandidate': -1}
    profile, adjustment = _HELPERS[helper]
    forwarded = command[0] + adjustment
    if len(command) < max(3, forwarded):
        raise ValueError('routine-helper-command-extent-incomplete')
    communication = observation['communication']
    if (not isinstance(communication, dict) or communication.get('requestBytes') != forwarded
            or type(communication.get('requestBytes')) is not int
            or communication.get('extendedRequestByte') != 0
            or type(communication.get('extendedRequestByte')) is not int):
        raise ValueError('routine-helper-communication-length-mismatch')
    # The 256-byte forwarded case requires a separately established extended
    # packing contract; do not truncate it to a one-byte length here.
    if forwarded > 255:
        return {**out, 'status': 'routine-helper-extended-packing-unqualified'}
    from .x431_version_response import audit_mode10_subtype3_response
    lower = audit_mode10_subtype3_response(communication)
    out['communicationAudit'] = lower
    if not lower['sourceOutputComplete']:
        return out
    prefix = bytes.fromhex(lower['sourceWrittenPrefixHex'])
    count = lower['nativeReturnCandidate']
    if len(prefix) > 2048 or type(count) is not int or not 0 <= count <= 2048:
        return {**out, 'status': 'routine-helper-internal-copy-extent-unqualified'}
    # PorscheSendCommandByIDEX can force return 1 without copying a byte.
    if lower.get('sourceNoDataFlagCandidate') == 1:
        count = 1
    if lower['communicationStatusByteCandidate'] == 0x7f:
        count = 0
    out['communicationReturnCandidate'] = count
    if count < 1:
        return {**out, 'status': 'routine-helper-no-value', 'nativeHelperReturnCandidate': -1}
    rule = next((r for r in _PROFILES[profile] if command[2] in r['selectors']
                 and ('inputByte1Equals' not in r or command[1] == r['inputByte1Equals'])
                 and ('inputByte1NotEquals' not in r or command[1] != r['inputByte1NotEquals'])), None)
    if rule is None:
        return {**out, 'status': 'routine-helper-selector-does-not-copy', 'nativeHelperReturnCandidate': count}
    if count < rule['minimumCount']:
        returned = rule['belowMinimumReturn']
        returned = count if returned == 'original-count' else -1 if returned == 'early-failure' else returned
        return {**out, 'status': 'routine-helper-below-copy-minimum', 'nativeHelperReturnCandidate': returned}
    copied = count - rule['stripBytes']
    out.update(nativeHelperReturnCandidate=copied, sourceCopiedBytesCandidate=copied)
    if copied == 0:
        return {**out, 'status': 'routine-helper-zero-byte-copy'}
    end = rule['stripBytes'] + copied
    if end > len(prefix):
        return {**out, 'status': 'routine-helper-copy-not-backed-by-fresh-bytes'}
    return {**out, 'status': 'routine-helper-source-data-candidate',
            'sourceDataHexCandidate': prefix[rule['stripBytes']:end].hex(), 'sourceDataComplete': True}
