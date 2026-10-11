"""Offline source audits of version-map copies and subtype-four reassembly.

Supplied observations are internal layout candidates. No vendor code, transport,
vehicle version selection, coding backup or ECU payload qualification occurs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

from .x431_response_contract import (
    audit_porsche_mode30_wrapper, audit_internal_response, GUARDS, PORSCHE_COMM_SHA256,
    SOURCE_SHA256 as ABSTRACT_SHA256)

SYSP_SHA256 = 'a1e81fe9ade6f9a016d997988568253d92f778e9b0a43822da8e98b1af6082ca'
FILE_SHA256 = '80a4e5692adefa90a6bbf210bd6b9d5715eeb6410f3ee34f3bc25994e148bbab'
SYS_DATA_LOGICAL_SHA256 = '18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'
PROEX_WRAPPERS=('mode10-subtype3-proex-0x11678','mode10-subtype3-proex-sequence-0x11678')
MODE50_WRAPPERS=('mode50-subtype5-0x11320','mode50-subtype5-sequence-0x11320')
# Producer call sites verified against retained PorscheEnterSys source. Naming a
# site is explicit offline context; it is not proof that a vehicle used that site.
PRODUCER_COUNT_KINDS = {0x19d94:'communication-return', 0x19fa2:'communication-return',
                        0x1a084:'communication-return',
                        0x1a136:'forced-one-after-nonnegative-return',
                        0x1a352:'communication-return'}
VERSION_GUARDS = {**GUARDS, 'ecuIdentityQualified': False, 'ecuVersionQualified': False,
                  'freshReadProven': False, 'completeVehicleCodingScopeQualified': False,
                  'selectedVehicleVariant': None}
COMPLETION_FIELDS = ({'aggregateReceive','completionCallbackReturn'},
                     {'aggregateReceive','completionReceive'},
                     {'aggregateReceive','completionCallbackReturn','completionReceive'})


def _hex(value, minimum, maximum):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-fA-F]*',value) or len(value)%2:
        raise ValueError('version-source-hex-invalid')
    if not minimum*2 <= len(value) <= maximum*2:
        raise ValueError('version-source-byte-length-invalid')
    return bytes.fromhex(value)


def native_request_map_key(request: bytes) -> int:
    """Retained 2/3/4-byte arithmetic, not a request construction API."""
    if not isinstance(request,bytes) or len(request) not in (2,3,4):
        raise ValueError('version-source-request-size-unqualified')
    if len(request)==2:
        key=request[0]+request[1]
    elif len(request)==3:
        key=request[0]+(request[1]<<8)+request[2]
    else:
        key=int.from_bytes(request[:2],'big')+int.from_bytes(request[2:],'big')
    return key & 65535


def audit_communication_mode_context(observation: dict, wrapper: str | None=None) -> dict:
    """Match an explicit source context to an implemented response layout.

    Initializer selection and communication mode occupy different fields. No
    initializer selection, actual definition origin or runtime binding is inferred.
    A full supplied configuration must cover the retained initializer copy extent.
    """
    if not isinstance(observation,dict):
        raise ValueError('communication-mode-context-required')
    if set(observation)=={'communicationRecordHex','pairIndex'}:
        return audit_communication_pair_context(observation['communicationRecordHex'],observation['pairIndex'],wrapper)
    configuration=None
    if set(observation)=={'configurationHex'}:
        configuration=_hex(observation['configurationHex'],0x2358,0x2358)
        mode,subtype=configuration[1],configuration[0x119]
    elif set(observation)=={'modeByte','subtypeByte'}:
        mode,subtype=observation['modeByte'],observation['subtypeByte']
        if type(mode) is not int or type(subtype) is not int or not 0<=mode<=255 or not 0<=subtype<=255:
            raise ValueError('communication-mode-and-subtype-bytes-required')
    else:
        raise ValueError('communication-mode-context-fields-invalid')
    entry=(0x10da4 if subtype==4 else 0x11208) if mode==0x30 else (0xf8d4 if (mode,subtype)==(0x10,3) else None)
    wrappers=(('subtype4-0x10da4','subtype4-read-chain-0x10da4') if subtype==4 else
              ('mode30-non-subtype4-0x11208',)) if mode==0x30 else (
              ('mode10-subtype3-short-0xf8d4','mode10-subtype3-0xf8d4',
               'mode10-subtype3-sequence-0xf8d4')+PROEX_WRAPPERS if (mode,subtype)==(0x10,3) else ())
    entries=[entry] if entry is not None else []
    if (mode,subtype)==(0x10,3):
        entries=[0xf8d4,0x11678]
        entry=None if wrapper is None else 0x11678 if wrapper in PROEX_WRAPPERS else 0xf8d4
    if (mode,subtype)==(0x50,5):
        entry=0x11320;entries=[entry];wrappers=MODE50_WRAPPERS
    if wrapper is not None and wrapper not in wrappers:
        raise ValueError('communication-mode-wrapper-mismatch')
    return {'kind':'x431-offline-communication-mode-context-audit',
        'commSourceSha256':PORSCHE_COMM_SHA256,'modeByte':mode,'subtypeByte':subtype,
        'sourceModeOffset':1,'sourceSubtypeOffset':0x119,
        'initializerCopyBytes':0x2358,'initializerDestinationBaseOffset':0x63c,
        'suppliedConfigurationBytes':len(configuration) if configuration is not None else None,
        'suppliedConfigurationSha256':hashlib.sha256(configuration).hexdigest() if configuration is not None else None,
        'nativeResponseEntryCandidate':entry,'nativeResponseEntryCandidates':entries,
        'compatibleOfflineWrappers':list(wrappers),
        'offlineResponseLayoutSupported':bool(wrappers),
        'offlineResponseLayoutScope':'source-qualified-request-length-branches; explicit single or retry-sequence observations' if (mode,subtype)==(0x10,3) else None,
        'status':'explicit-mode-layout-candidate' if wrappers else 'mode-layout-not-implemented-by-this-auditor',
        'modeContextSource':'explicit-full-initialization-config' if configuration is not None else 'explicit-field-observation',
        'actualModeSelected':False,'actualInitializationConfigurationOriginProven':False,
        'initializerSelectionEqualsCommunicationModeProven':False,**VERSION_GUARDS}


def audit_communication_pair_context(record_hex: str, pair_index: int, wrapper: str | None=None) -> dict:
    """Trace two fields through a complete, explicit common communication record.

    A pair index is required even for a single pair. No selected vehicle pair,
    CAN identifier conversion or full initialization configuration is inferred.
    """
    from scripts.x431_re.sysdata import parse_comm
    if type(pair_index) is not int or not 0<=pair_index<8:
        raise ValueError('communication-pair-index-required')
    blob=_hex(record_hex,1,65536)
    record=parse_comm(blob,0,len(blob))
    if record['class']!='static-source-confirmed':
        raise ValueError('communication-record-layout-unreviewed')
    if record['consumedEnd']!=len(blob):
        raise ValueError('communication-record-trailing-bytes-unqualified')
    if pair_index>=record['pairCount']:
        raise ValueError('communication-pair-index-out-of-range')
    # Common source copies record mode to TOKEN+0x63d, pair mode to
    # TOKEN+0x64f+index; EnterSys copies that byte to TOKEN+0x755.
    cursor=3
    for pair in record['pairs'][:pair_index]:
        cursor+=10+(4 if pair['mode']==5 else 0)+4*pair['paramCount']
    pair=record['pairs'][pair_index]
    out=audit_communication_mode_context({'modeByte':record['protocolKind'],'subtypeByte':pair['mode']},wrapper)
    out.update(modeContextSource='explicit-packed-communication-record-and-pair',
        fileSourceSha256=FILE_SHA256,syspSourceSha256=SYSP_SHA256,
        communicationRecordSha256=hashlib.sha256(blob).hexdigest(),communicationRecordBytes=len(blob),
        communicationPairCount=record['pairCount'],explicitPairIndex=pair_index,
        recordModeSourceOffset=0,recordSubtypeSourceOffset=cursor+8,
        tokenModeOffset=0x63d,tokenPairModeOffset=0x64f+pair_index,tokenSelectedSubtypeOffset=0x755,
        staticEntryTokenAssignmentVerified=True,staticPairSubtypeCopyVerified=True,
        actualTokenPointersAliasProven=False,actualCommunicationPairSelected=False,
        fullInitializationConfigurationReconstructed=False,definitionIdentityQualified=False,
        sourceCommunicationTrailerWords=list(record['trailingThree']),
        sourceCommunicationTrailerOffset=len(blob)-12,
        sourceTrailerTokenOffsets=[0x750,0x644,0x648],
        tokenDispatchWordCandidate=record['trailingThree'][1],
        tokenDispatchWordOffset=0x644,tokenDispatchWordEqualsDsnFormulaIdProven=False,
        sourceSelectedPairCommandIds=list(pair['params']))
    return out


def audit_source_communication_context(logical_blob: bytes, selection: dict) -> dict:
    """Bind explicit 9X1 source selectors to an exact retained packed record."""
    from scripts.x431_re.sysdata import parse_family_index, parse_systems
    if not isinstance(logical_blob,bytes) or hashlib.sha256(logical_blob).hexdigest()!=SYS_DATA_LOGICAL_SHA256:
        raise ValueError('communication-logical-source-unreviewed')
    if not isinstance(selection,dict) or set(selection)!={'familyId','systemKey','linkIndex','pairIndex'}:
        raise ValueError('communication-source-selection-fields-invalid')
    if any(type(value) is not int for value in selection.values()):
        raise ValueError('communication-source-selection-integers-required')
    if selection['familyId']!=13 or selection['systemKey']>>8!=13:
        raise ValueError('communication-source-family-unreviewed')
    family=next(row for row in parse_family_index(logical_blob) if row['familyId']==13)
    systems=parse_systems(logical_blob,family['off'])
    matches=[row for row in systems if row['systemKey']==selection['systemKey']]
    if len(matches)!=1:
        raise ValueError('communication-source-system-not-unique')
    system=matches[0];index=selection['linkIndex']
    if not 0<=index<len(system['links']):
        raise ValueError('communication-source-link-index-out-of-range')
    link=system['links'][index];start,end=link['commOff'],link['serviceOff']
    context={'communicationRecordHex':logical_blob[start:end].hex(),'pairIndex':selection['pairIndex']}
    out=audit_communication_mode_context(context)
    out.update(kind='x431-offline-source-communication-context-audit',
        logicalSourceSha256=SYS_DATA_LOGICAL_SHA256,explicitSourceSelection=dict(selection),
        communicationRecordSourceOffset=start,communicationRecordSourceEnd=end,
        sourcePairModeOffset=start+out['recordSubtypeSourceOffset'],
        modeContextCandidate=context,sourceRecordBindingVerified=True,
        actualDsnVersionRouteBindingProven=False,actualVehicleGenerationQualified=False)
    return out


# Exact retained context words and source-command domains. These tables describe
# legacy output bytes only; they never select a live ECU or DSN version.
_TOKEN_FIELD_SPECS = {
    0x0e010001: ('04000000', {2097158: [('raw', 1, 1, 4)]}),
    0x0e010002: ('06000000', {2097153: [('raw', 1, 2, 6)]}),
    0x0e020001: ('04000000', {2097158: [('hex', 1, 1, 2)]}),
    0x0e040001: ('06070202', {2097153: [('raw', 1, 2, 6)], 2097154: [('raw', 2, 2, 7)], 2097155: [('flag', 3, 2, 1, 4, 'ja', '**'), ('flag', 4, 2, 1, 64, 'ja', '**')]}),
    0x0e050001: ('06000000', {2097153: [('raw', 1, 2, 6)]}),
    0x0e070001: ('ff060400', {2097153: [('raw', 2, 2, 6)], 2097154: [('raw', 1, 2, 255)], 2097155: [('flag', 3, 2, 1, 4, '****', 'nein')]}),
    0x0e080001: ('06ff0202', {2097153: [('raw', 1, 2, 6)], 2097154: [('raw', 2, 2, 255)], 2097155: [('flag', 3, 2, 1, 4, 'ja', '**'), ('flag', 4, 2, 1, 64, 'ja', '**')]}),
    0x0e090001: ('ff060202', {2097153: [('raw', 2, 2, 6)], 2097154: [('raw', 1, 2, 255)], 2097155: [('flag', 3, 2, 1, 4, 'ja', '**'), ('flag', 4, 2, 1, 64, 'ja', '**')]}),
    0x0e120001: ('06020200', {2097153: [('raw', 1, 2, 6)], 2097155: [('flag', 2, 2, 1, 4, 'ja', '**'), ('flag', 3, 2, 1, 64, 'ja', '**')]}),
    0x0e130001: ('ff060202', {2097153: [('raw', 2, 2, 6)], 2097154: [('raw', 1, 2, 255)], 2097155: [('flag', 3, 2, 1, 4, 'ja', '**'), ('flag', 4, 2, 1, 64, 'ja', '**')]}),
    0x0e1d0001: ('0604ff00', {2097153: [('raw', 1, 2, 6)], 2097154: [('raw', 3, 2, 255)], 2097160: [('raw', 2, 2, 4)]}),
    0x0e1e0003: ('06020000', {6770: [('raw', 1, 2, 6)], 6772: [('flag', 2, 2, 1, 4, 'ja', '**')]}),
    0x0e1f0003: ('06020000', {6770: [('raw', 1, 2, 6)], 6772: [('flag', 2, 2, 1, 4, 'ja', '**')]}),
    0x0e200001: ('06000000', {2097153: [('raw', 1, 2, 6)]}),
    0x0e230001: ('06000000', {2097153: [('raw', 1, 2, 6)]}),
    0x0e270001: ('06ff0202', {2097153: [('normalize001', 1, 2, 6)], 2097154: [('raw', 2, 2, 255)], 2097155: [('flag', 3, 2, 1, 4, 'ja', '**'), ('flag', 4, 2, 1, 64, 'ja', '**')]}),
    0x0e2f0001: ('06020200', {2097153: [('raw', 1, 2, 6)], 2097155: [('flag', 2, 2, 1, 4, 'ja', '**'), ('flag', 3, 2, 1, 64, 'ja', '**')]}),
    0x0e300001: ('06020200', {2097153: [('raw', 1, 2, 6)], 2097155: [('flag', 2, 2, 1, 4, 'ja', '**'), ('flag', 3, 2, 1, 64, 'ja', '**')]}),
    0x0e320001: ('06020200', {2097153: [('raw', 1, 2, 6)], 2097155: [('flag', 2, 2, 1, 4, 'ja', '**'), ('flag', 3, 2, 1, 64, 'ja', '**')]}),
    0x0e360001: ('06020200', {2097153: [('raw', 1, 2, 6)], 2097155: [('flag', 2, 2, 1, 4, 'ja', '**'), ('flag', 3, 2, 1, 64, 'ja', '**')]}),
    0x0e370001: ('06020000', {2097153: [('raw', 1, 2, 6)], 2097155: [('flag', 2, 2, 1, 4, 'ja', '**')]}),
    0x0e400001: ('05060404', {2097153: [('raw', 2, 2, 6)], 2097154: [('raw', 1, 2, 5)], 2097155: [('flag', 3, 2, 1, 4, 'ja  ', 'nein'), ('flag', 4, 2, 1, 64, 'ja  ', '****')]}),
    0x0e410001: ('04000000', {2097158: [('hex', 1, 1, 2)]}),
    0x0e410002: ('03ff0000', {2097153: [('raw', 1, 2, 3)], 2097154: [('raw', 2, 2, 255)]}),
    0x0e440001: ('04000000', {2097158: [('hex', 1, 1, 2)]}),
    0x0e450001: ('05060400', {2097153: [('raw', 2, 2, 6)], 2097154: [('raw', 1, 2, 5)], 2097155: [('flag', 3, 2, 1, 4, 'ja  ', 'nein')]}),
    0x0e460001: ('04000000', {2097158: [('hex', 1, 1, 2)]}),
    0x0e460002: ('03ff0000', {2097153: [('raw', 1, 2, 3)], 2097154: [('raw', 2, 2, 255)]}),
    0x0e4a0001: ('0404ff03', {2097153: [('raw', 4, 2, 3)], 2097154: [('raw', 3, 2, 255)], 2097155: [('flag', 1, 2, 1, 4, '****', 'nein')], 2097160: [('raw', 2, 2, 4)]}),
    0x0e4b0001: ('04000000', {2097158: [('hex', 1, 1, 2)]}),
    0x0e4b0002: ('03ff0000', {2097153: [('raw', 1, 2, 3)], 2097154: [('raw', 2, 2, 255)]}),
    0x0e4c0001: ('06040200', {6770: [('raw', 1, 2, 6)], 6771: [('raw', 2, 2, 4)], 6772: [('flag', 3, 2, 1, 4, 'ja', '**')]}),
    0x0e4e0001: ('06040200', {6770: [('raw', 1, 2, 6)], 6771: [('raw', 2, 2, 4)], 6772: [('flag', 3, 2, 1, 4, 'ja', '**')]}),
    0x0e510001: ('ff030000', {2097153: [('raw', 2, 2, 3)], 2097154: [('raw', 1, 2, 255)]}),
    0x0e570001: ('03ff0000', {2097153: [('raw', 1, 2, 3)], 2097154: [('raw', 2, 2, 255)]}),
    0x0e580001: ('ff030000', {2097153: [('raw', 2, 2, 3)], 2097154: [('raw', 1, 2, 255)]}),
    0x0ea50001: ('03ff0000', {2097153: [('raw', 1, 2, 3)], 2097154: [('raw', 2, 2, 255)]}),
}
_TOKEN_REVIEWED_COMMANDS = {
    0x0e010001: frozenset((2097158, 2097159)),
    0x0e010002: frozenset((1049511, 2097153, 2097154, 2097162)),
    0x0e020001: frozenset((2097158, 2097159)),
    0x0e040001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e050001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e070001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e080001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e090001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e120001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e130001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e1d0001: frozenset((1049511, 2097153, 2097154, 2097155, 2097160, 2097162)),
    0x0e1e0003: frozenset((6769, 6770, 6772, 6777, 3146164)),
    0x0e1f0003: frozenset((6769, 6770, 6772, 6777, 3146165)),
    0x0e200001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e230001: frozenset((1049511, 2097153, 2097162)),
    0x0e270001: frozenset((6185, 1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e2f0001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e300001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e320001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e360001: frozenset((1049511, 2097153, 2097155, 2097162)),
    0x0e370001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e400001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e410001: frozenset((2097158, 2097159)),
    0x0e410002: frozenset((1049511, 2097153, 2097154, 2097162)),
    0x0e440001: frozenset((2097158, 2097159)),
    0x0e450001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e460001: frozenset((2097158, 2097159)),
    0x0e460002: frozenset((1049511, 2097153, 2097154, 2097162)),
    0x0e4a0001: frozenset((1049511, 2097153, 2097154, 2097155, 2097160, 2097162)),
    0x0e4b0001: frozenset((2097158, 2097159)),
    0x0e4b0002: frozenset((1049511, 2097153, 2097154, 2097162)),
    0x0e4c0001: frozenset((6770, 6771, 6772, 6777, 6786, 3146162)),
    0x0e4e0001: frozenset((6770, 6771, 6772, 6777, 6786, 3146163)),
    0x0e510001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0e570001: frozenset((1049511, 2097153, 2097154, 2097162)),
    0x0e580001: frozenset((1049511, 2097153, 2097154, 2097155, 2097162)),
    0x0ea50001: frozenset((1049511, 2097153, 2097154, 2097162)),
}

def audit_token_identity_response(observation: dict, logical_blob: bytes | None=None) -> dict:
    """Audit explicit source-written TOKEN bytes for retained modern contexts.

    These legacy output slots are distinct from DSN version-map vectors. Their
    native parser can read beyond a zero count or a short written extent; this
    audit never pads missing bytes or turns an unchanged slot into a new result.
    All other contexts remain visible as unreviewed, rather than guessed.
    """
    fields={'commandId','producerCount','sourceWrittenResponseHex'}
    context_fields=({'communicationRecordHex','pairIndex'} if logical_blob is None else {'sourceSelection'})
    if not isinstance(observation,dict) or set(observation)!=fields|context_fields:
        raise ValueError('token-identity-response-observation-fields-invalid')
    command,count=observation['commandId'],observation['producerCount']
    if type(command) is not int or not 0<=command<=0xffffffff:
        raise ValueError('token-identity-command-id-required')
    if type(count) is not int or not 0<=count<=4097:
        raise ValueError('token-identity-producer-count-required')
    response=_hex(observation['sourceWrittenResponseHex'],0,4097)
    context=(audit_communication_pair_context(observation['communicationRecordHex'],observation['pairIndex'])
             if logical_blob is None else audit_source_communication_context(logical_blob,observation['sourceSelection']))
    if command not in context['sourceSelectedPairCommandIds']:
        raise ValueError('token-identity-command-not-in-selected-source-pair')
    word=context['tokenDispatchWordCandidate']
    reviewed=word in _TOKEN_FIELD_SPECS and command in _TOKEN_REVIEWED_COMMANDS[word]
    out={'kind':'x431-offline-token-identity-response-audit','syspSourceSha256':SYSP_SHA256,
         'fileSourceSha256':FILE_SHA256,'communicationSourceAudit':context,
         'tokenDispatchWordCandidate':word,'commandId':command,'producerCount':count,
         'sourceWrittenResponseHex':response.hex(),'sourceWrittenResponseBytes':len(response),
         'tokenTailEntryVa':0x1b654,'reviewedPostPrefixStartVa':0x1b7c6,
         'reviewedUnwindRegionSha256':'9d43937c3536a8d0206ee717b8cadf0740ff6eb7540e73972c64fbf9190415b1',
         'exportedCompleteFunctionExtentClaimed':False,
         'sourceContextParserReviewed':reviewed,'status':'context-parser-not-reviewed',
         'zeroDispatchWord':word==0,'fields':[],
         'tokenFieldOffset':0x1f8,'tokenDeclaredFieldBytesCandidate':None,
         'tokenLayoutWordCandidate':None,'tokenFlagF8Candidate':None,
         'slAtNormalReturnGateCandidate':None,'nativeFieldWriteCandidate':None,
         'fieldSourceOffset':None,'fieldSourceBytes':None,'requiredSourceEndExclusive':None,
         'sourceWrittenRangeCovered':False,'nativeReadExceedsProducerCountCandidate':None,
         'fieldHexCandidate':None,'fieldTextCandidate':None,
         'fieldStoredBytesCandidate':None,'fieldIsDsnVersionVector':False,
         'sourceBytesRuntimeOriginQualified':False,'nativeReturnProvesSuccess':False,**VERSION_GUARDS}
    if not reviewed:
        if word in _TOKEN_FIELD_SPECS:out['status']='source-command-parser-not-reviewed'
        return out
    layout_hex,commands=_TOKEN_FIELD_SPECS[word]
    layout=bytes.fromhex(layout_hex);actions=commands.get(command,[])
    width=layout[0]
    out.update(tokenDeclaredFieldBytesCandidate=width,tokenLayoutWordCandidate=width,
               tokenFlagF8Candidate=1,slAtNormalReturnGateCandidate=-1)
    out['tokenLayoutWordCandidate']=int.from_bytes(layout,'little')
    by_slot={a[1]:a for a in actions}
    for slot in range(1,5):
        action=by_slot.get(slot)
        field={'slot':slot,'tokenFieldOffset':0xf8+0x100*slot,
               'tokenDeclaredFieldBytesCandidate':layout[slot-1],
               'status':'command-leaves-previous-field-untouched','nativeFieldWriteCandidate':action is not None,
               'fieldSourceOffset':None,'fieldSourceBytes':None,'requiredSourceEndExclusive':None,
               'sourceWrittenRangeCovered':False,'nativeReadExceedsProducerCountCandidate':None,
               'fieldHexCandidate':None,'fieldTextCandidate':None,'fieldStoredBytesCandidate':None,
               'sourceNormalizationAppliedCandidate':False}
        if action is not None:
            kind,_,offset,size,*extra=action;end=offset+size
            field.update(fieldSourceOffset=offset,fieldSourceBytes=size,requiredSourceEndExclusive=end,
                         sourceWrittenRangeCovered=len(response)>=end,nativeReadExceedsProducerCountCandidate=end>count)
            if len(response)<end:
                field['status']='source-written-response-too-short'
            elif count==0:
                field['status']='zero-count-field-not-qualified'
            else:
                raw=response[offset:end];text=None
                if kind=='hex':text=raw.hex().upper()
                elif kind=='flag':
                    mask,yes,no=extra;text=yes if raw[0]&mask else no
                elif kind=='normalize001' and raw[:3]==b'001':
                    raw=b'001000';field['sourceNormalizationAppliedCandidate']=True
                stored=text.encode('ascii')+b'\0' if text is not None else raw
                field.update(status='complete-explicit-bytes-field-candidate',fieldHexCandidate=stored.hex(),
                             fieldTextCandidate=text,fieldStoredBytesCandidate=len(stored))
        out['fields'].append(field)
    # Preserve first-slot fields for existing callers, while exposing every slot
    # separately. Whole-command status covers all writes; an untouched first
    # slot never inherits a result from another slot.
    first=out['fields'][0]
    for key in ('fieldSourceOffset','fieldSourceBytes','fieldHexCandidate','fieldTextCandidate','fieldStoredBytesCandidate'):
        out[key]=first[key]
    changed=[f for f in out['fields'] if f['nativeFieldWriteCandidate']]
    out['nativeFieldWriteCandidate']=bool(changed)
    if not changed:
        out['status']='command-leaves-previous-field-untouched'
    else:
        out['requiredSourceEndExclusive']=max(f['requiredSourceEndExclusive'] for f in changed)
        out['sourceWrittenRangeCovered']=all(f['sourceWrittenRangeCovered'] for f in changed)
        out['nativeReadExceedsProducerCountCandidate']=any(f['nativeReadExceedsProducerCountCandidate'] for f in changed)
        out['status']=('source-written-response-too-short' if not out['sourceWrittenRangeCovered'] else
                       'zero-count-field-not-qualified' if count==0 else 'complete-explicit-bytes-field-candidate')
    return out


def audit_mode10_request_packing(request_bytes: int, extended_request_byte: int=0) -> dict:
    """Source length arithmetic only; no packet bytes, sending or configuration."""
    if any(type(value) is not int or not 0<=value<=255 for value in (request_bytes,extended_request_byte)):
        raise ValueError('mode10-request-length-bytes-required')
    short=request_bytes<=7 and extended_request_byte==0
    high=15 if extended_request_byte==16 else extended_request_byte
    extended_used=1<=high<=15
    length=request_bytes+(high<<8) if extended_used else request_bytes
    chunks=0 if short else (length+7)//7
    large=chunks>=256
    command=17 if short else (9+8*chunks if large else 3+14*chunks)
    mutations=[]
    if not short and extended_request_byte==16:
        mutations.append({'callSite':0x105ce,'value':15})
    if not short and extended_used:
        mutations.append({'callSite':0x10a6c,'value':0})
    # Copy starts at the low length byte. An eight-byte temporary copy can
    # occur at a seven-byte remainder; the next fragment overlaps that byte.
    remaining=length;largest=0;max_extent=0;copied_sum=0
    for index in range(chunks):
        copied=7 if remaining>7 else max(0,remaining+1)
        largest=max(largest,copied);max_extent=max(max_extent,7*index+copied)
        copied_sum+=copied;remaining-=7
    return {'kind':'x431-offline-mode10-request-length-audit','commSourceSha256':PORSCHE_COMM_SHA256,
        'requestLengthOffset':0x31c,'extendedRequestLengthOffset':0x232d,
        'suppliedLengthByte':request_bytes,'suppliedExtendedLengthByte':extended_request_byte,
        'shortRequestPath':short,'effectiveRequestBytesCandidate':length,
        'extendedLengthByteUsed':extended_used,'extendedLengthLayoutQualified':extended_request_byte<=16,
        'sourceFragmentCountCandidate':chunks,'sourceFragmentCounterLowByteCandidate':chunks&255 if not short else None,
        'sourceFragmentCounterHighByteWrittenCandidate':chunks>>8 if large else None,
        'sourcePackingForm':'fixed-short' if short else ('first14-following8' if large else 'all14'),
        'providerExportCandidate':'CommSubFunc0001' if short else 'CommSubFunc0005',
        'providerEntryCandidate':0x388e4 if short else 0x38d14,
        'providerCommandBytesCandidate':command,'providerCapacityCandidate':0x1014,
        'providerCallSiteCandidate':0x10bd0 if large else 0x10c0a,
        'sourceTemplateCopyBytes':0x1264,'packedCommandWithinTemplateCopyCandidate':command<=0x1264,
        'sourceLargestTemporaryPayloadCopyCandidate':largest if not short else length+1,
        'sourcePayloadCopySumCandidate':copied_sum if not short else length+1,
        'sourceUniquePayloadReadExtentCandidate':max_extent if not short else length+1,
        'sourcePayloadExtentMatchesLengthCandidate':max_extent==length+1 if not short else True,
        'requestLengthByteMutationCandidates':mutations,'actualRequestLengthMutationProven':False,
        'sourceFrameControlCallsExpected':large,'sourceFrameControlCallSites':[0x10bc0,0x10be0] if large else [],
        'sourceFrameControlReturnChecked':False,'frameControlSideEffectsQualified':False,
        'status':'source-length-candidate' if extended_request_byte<=16 else 'extended-byte-ignored-by-this-source-branch',
        **VERSION_GUARDS}


def audit_mode10_proex_request_packing(request_bytes: int, extended_request_byte: int=0) -> dict:
    """Shared source arithmetic, retaining ProEx's own consumer/mutation sites."""
    packing=audit_mode10_request_packing(request_bytes,extended_request_byte)
    return {**packing,'sourceRequestEntryCandidate':0x11678,
        'providerCallSiteCandidate':0x1198a if packing['sourceFrameControlCallsExpected'] else 0x119c4,
        'sourceFrameControlCallSites':[0x1197a,0x1199a] if packing['sourceFrameControlCallsExpected'] else [],
        'requestLengthByteMutationCandidates':[
            {**mutation,'callSite':0x1178a if mutation['callSite']==0x105ce else 0x11878}
            for mutation in packing['requestLengthByteMutationCandidates']],
        'responseRetryAuditImplemented':True,'sourceSingleSendGlobalUsedByEntry':False,
        'sourceNrc33ImmediateParseBypass':False}


def audit_mode10_subtype3_short_response(observation: dict) -> dict:
    """Retained short-only interface, using the shared source receive audit."""
    return _audit_mode10_response(observation,short_only=True)


def audit_mode10_subtype3_response(observation: dict) -> dict:
    """One source-qualified short or long receive; retries remain unobserved."""
    return _audit_mode10_response(observation,short_only=False)


def audit_mode10_retry_context(observation: dict) -> dict:
    """Source attempt limits from explicit fields; never reads runtime globals."""
    return _audit_mode10_retry_context(observation,pro_ex=False)


def audit_mode10_proex_retry_context(observation: dict) -> dict:
    """ProEx has no local single-send-global check; do not import Pro's limit."""
    return _audit_mode10_retry_context(observation,pro_ex=True)


def _audit_mode10_retry_context(observation: dict, *, pro_ex: bool) -> dict:
    fields={'filterIdWord','sendOnlyAndOne','requestFlagByte','initialCommunicationStatusByte'}
    if pro_ex:fields.remove('sendOnlyAndOne')
    record_fields=(fields-{'filterIdWord'})|{'communicationRecordHex','pairIndex'}
    source_pair=None
    if isinstance(observation,dict) and set(observation)==record_fields:
        from scripts.x431_re.sysdata import parse_comm
        source_pair=audit_communication_pair_context(observation['communicationRecordHex'],
            observation['pairIndex'],PROEX_WRAPPERS[1] if pro_ex else 'mode10-subtype3-sequence-0xf8d4')
        raw=bytes.fromhex(observation['communicationRecordHex'])
        pair=parse_comm(raw,0,len(raw))['pairs'][observation['pairIndex']]
        # GetCommData combines a full stored Tx word with the low16 Rx word;
        # TOKEN+0x65c then goes to UpdateCanFilterId before the selected requests.
        word=((pair['storedTx']<<16)|(pair['storedRx']&65535))&0xffffffff
        observation={key:observation[key] for key in fields if key!='filterIdWord'}|{'filterIdWord':word}
    if not isinstance(observation,dict) or set(observation)!=fields:
        raise ValueError('mode10-retry-context-fields-required')
    for key in fields:
        maximum=0xffffffff if key in ('filterIdWord','sendOnlyAndOne') else 255
        if type(observation[key]) is not int or not 0<=observation[key]<=maximum:
            raise ValueError('mode10-retry-context-unsigned-values-required')
    word=observation['filterIdWord'];first=word>>24;second=(word>>16)&255
    limit=1 if first==0xe0 and second==0 else 2
    if not pro_ex and observation['sendOnlyAndOne']==1:limit=1
    return {'kind':'x431-offline-mode10-proex-retry-context-audit' if pro_ex else 'x431-offline-mode10-retry-context-audit','commSourceSha256':PORSCHE_COMM_SHA256,
        **observation,'filterFirstByte':first,'filterSecondByte':second,
        'sourceFilterGlobal':'iFilterId','sourceSendOnlyGlobal':None if pro_ex else 'g_SendOnlyAndOne',
        'sourceSingleSendGlobalUsedByEntry':not pro_ex,
        'sourceRequestEntryCandidate':0x11678 if pro_ex else 0xf8d4,
        'sourceRequestFlagOffset':0x31b,'sourceStatusOffset':0x2325,
        'sourceStatusInitializedByEntry':False,'outerAttemptLimitCandidate':limit,
        'busyReceiveLimitPerOuterAttempt':5,'maximumReceiveObservationsCandidate':limit*5,
        'sourceBusyDelayMicroseconds':1000000,'sourceBusyDelayImport':'usleep',
        'filterWordSource':'source-common-record-selected-pair' if source_pair is not None else 'explicit-word-observation',
        'communicationPairSourceAudit':source_pair,'sourceTokenFilterOffset':0x65c,
        'sourceFilterSetterExport':'UpdateCanFilterId','sourceFilterSetterEntry':0x12238,
        'staticFilterSetterAssignmentVerified':True,'actualFilterSetterCallProven':False,
        'runtimeGlobalsQualified':False,**VERSION_GUARDS}


def audit_mode10_receive_sequence(observation: dict) -> dict:
    return _audit_mode10_receive_sequence(observation,pro_ex=False)


def audit_mode10_proex_receive_sequence(observation: dict) -> dict:
    return _audit_mode10_receive_sequence(observation,pro_ex=True)


def audit_mode10_proex_response(observation: dict) -> dict:
    """One explicit ProEx receive; every recognized NRC requires retry context."""
    return _audit_mode10_response(observation,short_only=False,pro_ex=True)


def _audit_mode10_receive_sequence(observation: dict, *, pro_ex: bool) -> dict:
    """Audit an explicit ordered tape, without receiving, retrying or sleeping.

    Final native counts and freshly written bodies are independent. Long pending
    or failure exits may retain a positive packed length without copying a body.
    Missing observations never establish a final status or version-map update.
    """
    fields={'requestBytes','extendedRequestByte','retryContext','receives'}
    if not isinstance(observation,dict) or set(observation)!=fields:
        raise ValueError('mode10-receive-sequence-fields-required')
    packing=(audit_mode10_proex_request_packing if pro_ex else audit_mode10_request_packing)(observation['requestBytes'],observation['extendedRequestByte'])
    if not packing['extendedLengthLayoutQualified']:
        raise ValueError('mode10-extended-length-layout-unqualified')
    context=_audit_mode10_retry_context(observation['retryContext'],pro_ex=pro_ex);receives=observation['receives']
    if not isinstance(receives,list) or len(receives)>context['maximumReceiveObservationsCandidate']:
        raise ValueError('mode10-receive-sequence-list-or-limit-invalid')
    # Validate the whole tape even if a previous unqualified observation prevents
    # following its source flow. Later bytes cannot repair a missing header.
    audits=[_audit_mode10_response({'requestBytes':observation['requestBytes'],
        'extendedRequestByte':observation['extendedRequestByte'],'internal':item},short_only=False,pro_ex=pro_ex) for item in receives]
    fp=0 if packing['shortRequestPath'] else 3+14*packing['sourceFragmentCountCandidate']
    out={'kind':'x431-offline-mode10-proex-receive-sequence-audit' if pro_ex else 'x431-offline-mode10-receive-sequence-audit','commSourceSha256':PORSCHE_COMM_SHA256,
        'abstractSourceSha256':ABSTRACT_SHA256,'wrapperContext':PROEX_WRAPPERS[1] if pro_ex else 'mode10-subtype3-sequence-0xf8d4',
        'requestPackingAudit':packing,'retryContextAudit':context,'sourceInitialReturnCandidate':fp,
        'nativeReturnCandidate':None,'communicationStatusByteCandidate':None,
        'sourceWrittenPrefixHex':None,'sourceOutputComplete':False,'sourceReceiveSequenceComplete':False,
        'additionalObservationRequired':True,'status':'mode10-receive-sequence-incomplete',
        'receivedObservationsSupplied':len(receives),'receivedObservationsConsumed':0,
        'observations':[],'sourceBusyDelayCallsCandidate':0,'providerRuntimeBindingQualified':False,
        'actualReceiveSequenceQualified':False,'actualReceiveCleanupProven':False,**VERSION_GUARDS}
    outer=0;busy=0;status=context['initialCommunicationStatusByte']
    for index,audit in enumerate(audits):
        lower_return=audit['lowerResponse']['nativeReturnCandidate']
        event={'observationIndex':index,'outerAttemptOrdinal':outer+1,
               'busyReceivesBefore':busy,'receiveAudit':audit}
        out['observations'].append(event);out['receivedObservationsConsumed']=index+1
        terminal=False;returned=None
        if lower_return is not None and lower_return<0:
            if context['requestFlagByte']==0:
                returned=0;terminal=True;event['sourceAction']='failure-zero-flag-terminal-zero'
            else:
                outer+=1;busy=0;event['sourceAction']='failure-advance-outer-attempt'
                if outer>=context['outerAttemptLimitCandidate']:
                    returned=-1 if outer==2 else fp;terminal=True
        elif audit['receiveAttemptsRequired']:
            status=audit['communicationStatusByteCandidate'];code=audit['negativeResponseCodeCandidate']
            if code in (0x21,0x23):
                busy+=1;out['sourceBusyDelayCallsCandidate']+=1
                event['sourceAction']='busy-repeat-same-outer-attempt'
                if busy<5:continue
            outer+=1;busy=0;event['sourceAction']='negative-advance-outer-attempt'
            if outer>=context['outerAttemptLimitCandidate']:
                returned=fp;terminal=True
        elif audit['sourceOutputComplete']:
            status=audit['communicationStatusByteCandidate'];returned=audit['nativeReturnCandidate'];terminal=True
            out.update(sourceWrittenPrefixHex=audit['sourceWrittenPrefixHex'],sourceOutputComplete=True)
            event['sourceAction']='parse-body-and-terminal-return'
        else:
            event['sourceAction']='unqualified-observation-stops-source-audit'
            if audit['communicationStatusByteCandidate'] is not None:
                status=audit['communicationStatusByteCandidate']
            out.update(status='mode10-receive-sequence-observation-unqualified',
                       additionalObservationRequired=False,unqualifiedObservationIndex=index)
            break
        if terminal:
            if index+1!=len(audits):
                raise ValueError('mode10-observations-after-source-terminal-return')
            returned=0 if status==0x7f else returned
            out.update(nativeReturnCandidate=returned,communicationStatusByteCandidate=status,
                sourceReceiveSequenceComplete=True,additionalObservationRequired=False,
                status='mode10-sequence-body-candidate' if out['sourceOutputComplete'] else 'mode10-sequence-terminal-without-written-body')
            break
    out.update(lastObservedCommunicationStatusByteCandidate=status,
        outerAttemptsAdvancedCandidate=outer,busyReceivesInCurrentOuterAttemptCandidate=busy,
        unprocessedObservations=len(audits)-out['receivedObservationsConsumed'])
    return out


def _audit_mode10_response(observation: dict, *, short_only: bool, pro_ex: bool=False) -> dict:
    """Audit one explicitly supplied short-request receive, without retrying.

    A negative response needing another attempt cannot establish a final return.
    Declared copies must be covered by received bytes, never zero-filled capacity.
    """
    if not isinstance(observation,dict) or set(observation)!={'requestBytes','extendedRequestByte','internal'}:
        raise ValueError('mode10-short-response-observation-invalid')
    length,extended=observation['requestBytes'],observation['extendedRequestByte']
    packing=(audit_mode10_proex_request_packing if pro_ex else audit_mode10_request_packing)(length,extended)
    if short_only and not packing['shortRequestPath']:
        raise ValueError('mode10-long-request-provider-not-qualified')
    if not packing['extendedLengthLayoutQualified']:
        raise ValueError('mode10-extended-length-layout-unqualified')
    internal=observation['internal']
    if not isinstance(internal,dict) or type(internal.get('capacity')) is not int or internal['capacity']!=0x1014:
        raise ValueError('mode10-short-provider-capacity-mismatch')
    lower=audit_internal_response(internal)
    out={'kind':'x431-offline-mode10-proex-response-audit' if pro_ex else 'x431-offline-mode10-subtype3-short-response-audit' if short_only else 'x431-offline-mode10-subtype3-response-audit',
        'commSourceSha256':PORSCHE_COMM_SHA256,'abstractSourceSha256':ABSTRACT_SHA256,
        'wrapperContext':PROEX_WRAPPERS[0] if pro_ex else 'mode10-subtype3-short-0xf8d4' if short_only else 'mode10-subtype3-0xf8d4',
        'requestBytes':length,'extendedRequestByte':extended,'requestPackingAudit':packing,
        'providerExportCandidate':packing['providerExportCandidate'],'providerEntryCandidate':packing['providerEntryCandidate'],
        'providerCallSite':packing['providerCallSiteCandidate'],'providerCommandBytes':packing['providerCommandBytesCandidate'],
        'providerCapacity':0x1014,
        'lowerResponse':lower,'nativeReturnCandidate':None,'communicationStatusByteCandidate':None,
        'sourceWrittenPrefixHex':None,'sourceOutputComplete':False,
        'receiveAttemptsRequired':False,'status':'mode10-internal-receive-unqualified',
        'providerRuntimeBindingQualified':False,'actualReceiveCleanupProven':False,**VERSION_GUARDS}
    if not lower['internalBufferComplete']:
        return out
    frame=bytes.fromhex(lower['internalBufferHexCandidate'])
    if len(frame)<10:
        out['status']='mode10-response-header-incomplete';return out
    count=int.from_bytes(frame[:2],'big');length_byte,service=frame[8:10]
    out.update(frameCountCandidate=count,firstLengthByte=length_byte,firstServiceByteCandidate=service)
    status=0x7e
    if service==0x7f and length_byte<10:
        if len(frame)<12:
            out['status']='mode10-negative-response-code-incomplete';return out
        code=frame[11];status=0x7e if code==0x78 else 0x7f
        out.update(negativeResponseCodeCandidate=code,communicationStatusByteCandidate=status)
        if pro_ex or code!=0x33:
            out.update(status='mode10-additional-receive-attempts-unqualified',receiveAttemptsRequired=True)
            return out
    out['communicationStatusByteCandidate']=status
    if count==0:
        out['status']='mode10-zero-frame-layout-unqualified';return out
    if count==1:
        declared=length_byte;start=10
    else:
        declared=((length_byte<<8)&0xf00)|service;start=11
    copied=max(0,declared-1)
    out.update(declaredServiceAndBodyBytes=declared,sourceBodyOffset=start,
        sourceCopiedBodyBytesCandidate=copied,removedServiceBytesCandidate=1)
    if copied and start+copied>len(frame):
        out['status']='mode10-declared-body-incomplete';return out
    body=frame[start:start+copied] if copied else b''
    out.update(sourceWrittenPrefixHex=body.hex(),sourceOutputComplete=True,
        nativeReturnCandidate=0 if status==0x7f else (copied if count==1 else declared-1),
        status='mode10-source-written-body-candidate' if copied else 'mode10-zero-body-count')
    if count==1 and declared<=1:
        out['sourceNoDataFlagCandidate']=1
    return out


def audit_subtype4_request_packing(request_bytes: int, reread_request_bytes: int | None=None) -> dict:
    """Audit source length arithmetic without constructing or sending a request.

    Long requests call an unbound provider before rereading the length byte.
    Their final length must be supplied separately, never assumed unchanged.
    The explicit wrapper context does not establish an actual mode or binding.
    """
    if type(request_bytes) is not int or not 0<=request_bytes<=255:
        raise ValueError('subtype4-request-length-byte-required')
    chunks=(request_bytes+1)//7
    if reread_request_bytes is not None:
        if type(reread_request_bytes) is not int or not 0<=reread_request_bytes<=255:
            raise ValueError('subtype4-reread-length-byte-required')
        if chunks==0:
            raise ValueError('subtype4-short-path-has-no-length-reread')
    final_length=reread_request_bytes if chunks else request_bytes
    out={'kind':'x431-offline-subtype4-request-length-audit',
         'commSourceSha256':PORSCHE_COMM_SHA256,'wrapperContext':'mode30-subtype4-0x10da4',
         'requestLengthOffset':0x31c,'requestBytesOffset':0x31d,
         'initialRequestBytes':request_bytes,'fullChunkCountCandidate':chunks,
         'fullChunkPayloadBytes':7,'lengthRereadAfterChunkCallbacks':chunks>0,
         'rereadRequestBytes':reread_request_bytes,'finalRequestBytesCandidate':final_length,
         'finalCommandBytesCandidate':None,'finalPayloadBytesCandidate':None,
         'finalRemainderCandidate':None,'sourceCopiedExtentBytesCandidate':None,
         'expectedLengthAndRequestExtentBytesCandidate':None,
         'sourceLengthArithmeticComplete':False,'sourceRequestExtentConsistent':False,
         'status':'length-reread-observation-required',
         'actualModeSelected':False,'preAggregateChunkReturnsQualified':False,
         'requestLengthStableAcrossCallbacksProven':False,**VERSION_GUARDS}
    if final_length is None:
        return out
    remainder=(final_length+1)%7
    copied=7*chunks+remainder+1
    # The copied range starts one byte before the length byte. Its first byte
    # is explicitly zeroed; no raw packet or ECU request is produced here.
    consistent=copied==final_length+2
    out.update(finalCommandBytesCandidate=remainder+11,
               finalPayloadBytesCandidate=remainder+1,finalRemainderCandidate=remainder,
               sourceCopiedExtentBytesCandidate=copied,
               expectedLengthAndRequestExtentBytesCandidate=final_length+2,
               sourceLengthArithmeticComplete=True,sourceRequestExtentConsistent=consistent,
               status='source-request-length-consistent' if consistent else 'source-request-extent-mismatch')
    return out


def audit_mode30_filter_context(observation: dict) -> dict:
    """Trace a complete explicit source record through the common filter setter.

    PorscheEnterSys calls the setter after a nonnegative initializer return for
    modes other than 0x60. This is source order, never a runtime observation.
    Stored source words are not hardware CAN identifiers.
    """
    if not isinstance(observation,dict) or set(observation)!={'communicationRecordHex','pairIndex'}:
        raise ValueError('mode30-filter-context-fields-invalid')
    context=audit_communication_pair_context(observation['communicationRecordHex'],observation['pairIndex'])
    if context['modeByte']!=0x30:
        raise ValueError('mode30-filter-record-mode-mismatch')
    from scripts.x431_re.sysdata import parse_comm
    blob=bytes.fromhex(observation['communicationRecordHex'])
    pair=parse_comm(blob,0,len(blob))['pairs'][observation['pairIndex']]
    word=((pair['storedTx']<<16)|(pair['storedRx']&65535))&0xffffffff
    return dict(kind='x431-offline-mode30-filter-context-audit',
        commSourceSha256=PORSCHE_COMM_SHA256,fileSourceSha256=FILE_SHA256,syspSourceSha256=SYSP_SHA256,
        communicationPairSourceAudit=context,sourceFilterWordCandidate=word,
        sourceRequestFilterPrefixHexCandidate=bytes((word>>24,(word>>16)&255)).hex().upper(),
        sourceTokenFilterOffset=0x65c+4*observation['pairIndex'],
        sourceFilterSetterEntry=0x12238,sourceFilterSetterCallSite=0x19eec,
        sourceFilterSetterExport='UpdateCanFilterId',sourceFilterObjectSymbol='iFilterId',
        sourceInitializerCallSite=0x19ec0,sourceCallerInitializerReturnGate='signed-32-bits-nonnegative',
        sourceSetterSkippedMode=0x60,sourceInitializerMode30BranchWritesFilter=False,
        staticFilterSetterAssignmentVerified=True,actualFilterSetterCallProven=False,
        filterAssignmentBeforeSelectedRequestSourceVerified=True,
        actualInitializationReturnQualified=False,actualTokenPointersAliasProven=False,
        runtimeFilterStableAcrossCallbacksProven=False,hardwareAddressConversionQualified=False,
        actualInitializationConfigurationOriginProven=False,**VERSION_GUARDS)


def audit_mode30_non4_request_packet(observation: dict) -> dict:
    """Model the 0x11208 internal provider argument; never sends an ECU request.

    Refuse a packet candidate when the source command overlaps its receive
    scratch, or byte-sized length/checksum arithmetic wraps. A source-consistent
    candidate still has no runtime, native memory or hardware qualification.
    """
    fields={'communicationRecordHex','pairIndex','requestAreaHex'}
    if not isinstance(observation,dict) or set(observation)!=fields:
        raise ValueError('mode30-request-packet-fields-invalid')
    context=audit_mode30_filter_context({key:observation[key] for key in fields if key!='requestAreaHex'})
    if context['communicationPairSourceAudit']['subtypeByte']==4:
        raise ValueError('mode30-non4-request-subtype-mismatch')
    area=_hex(observation['requestAreaHex'],1,256)
    length=area[0]
    if len(area)<length+1:
        raise ValueError('mode30-request-source-extent-incomplete')
    length_byte=(length+5)&255
    xor_count=(length+6)&255
    frame_bytes=length_byte+4
    copied_end=8+length
    checksum_offset=length_byte+3
    overlap=max(copied_end,checksum_offset+1)>64
    wrapped=length+6>255 or length+5>255
    complete=not overlap and not wrapped
    frame=None
    if complete:
        frame=bytearray(bytes((0x55,0xaa,length_byte,0x61,1,length)))
        frame.extend(bytes.fromhex(context['sourceRequestFilterPrefixHexCandidate']))
        frame.extend(area[1:length+1])
        check=0
        for byte in frame[2:2+xor_count]:check^=byte
        frame.append(check)
        assert len(frame)==frame_bytes and len(frame)==length+9
    return dict(kind='x431-offline-mode30-non4-request-packet-audit',
        commSourceSha256=PORSCHE_COMM_SHA256,filterContextAudit=context,
        sourceRequestEntry=0x11208,sourceRequestRegionSha256='87b27bb29c67a2818a2dab187403933810a2157ea492f7cceb18f0dd4312e5ec',
        sourceRequestLengthOffset=0x31c,sourceRequestPayloadOffset=0x31d,
        sourcePayloadBytes=length,suppliedSourceBytes=len(area),sourceTrailingBytesIgnored=len(area)-length-1,
        sourceFrameLengthByteCandidate=length_byte,sourceChecksumCountByteCandidate=xor_count,
        sourceChecksumOffsetCandidate=checksum_offset,providerCommandBytesCandidate=frame_bytes,
        sourceReceiveScratchRelativeOffset=64,sourceCommandOverlapsReceiveScratch=overlap,
        sourceLengthArithmeticWrapped=wrapped,sourcePayloadFullyRepresented=complete,
        providerCommandHexCandidate=frame.hex().upper() if frame is not None else None,
        providerObjectSymbol='CommSubFunc_00000002',providerCallSite=0x112bc,providerCapacity=1024,
        status=('source-length-arithmetic-wraps' if wrapped else 'source-command-overlaps-receive-scratch'
                if overlap else 'source-internal-provider-packet-candidate'),
        requestPacketBytesConstructed=frame is not None,canFrameLayoutQualified=False,
        providerRuntimeBindingQualified=False,nativeRequestScratchBoundsQualified=False,
        nativeOriginalInstructionsExecuted=False,**VERSION_GUARDS)


def audit_version_map_origin(observation: dict) -> dict:
    """Copy only the prefix supported by the explicit communication layout.

    The actual producer copies from the beginning of the communication output,
    using its caller's count. A short declared body does not shorten that count.
    Copied initialization bytes are reported as a gap, not fresh vehicle bytes.
    Subtype-four vectors use source-checked flattening, still without provider,
    runtime or vehicle qualification.
    """
    if not isinstance(observation,dict) or set(observation)!={'requestHex','producerCallSite','communication','communicationStatusByte'}:
        raise ValueError('version-map-origin-observation-invalid')
    request=_hex(observation['requestHex'],2,4)
    site=observation['producerCallSite']
    if type(site) is not int or site not in PRODUCER_COUNT_KINDS:
        raise ValueError('version-map-producer-site-unqualified')
    communication_status=observation['communicationStatusByte']
    if type(communication_status) is not int or not 0<=communication_status<=255:
        raise ValueError('version-map-communication-status-required')
    communication=observation['communication']
    if isinstance(communication,dict) and communication.get('wrapper') in PROEX_WRAPPERS:
        raise ValueError('version-map-proex-producer-binding-unqualified')
    mode_audit=None
    if isinstance(communication,dict) and 'modeContext' in communication:
        mode_audit=audit_communication_mode_context(communication['modeContext'],communication.get('wrapper'))
        communication={key:value for key,value in communication.items() if key!='modeContext'}
    if isinstance(communication,dict) and communication.get('wrapper') in (
            'subtype4-0x10da4','subtype4-read-chain-0x10da4'):
        if communication['wrapper']=='subtype4-read-chain-0x10da4':
            if set(communication)-{'wrapper'} not in COMPLETION_FIELDS:
                raise ValueError('subtype4-read-chain-observation-invalid')
            wrapper=audit_subtype4_receive_chain({key:value for key,value in communication.items() if key!='wrapper'})
            packing=audit_subtype4_request_packing(len(request))
            if wrapper['commandBytes']!=packing['finalCommandBytesCandidate']:
                raise ValueError('subtype4-request-command-length-mismatch')
            wrapper['requestPackingAudit']=packing
        else:
            if set(communication)!={'wrapper','aggregateHex'}:
                raise ValueError('subtype4-communication-observation-invalid')
            wrapper=audit_subtype4_reassembly(communication['aggregateHex'])
        wrapper_return=wrapper['nativeReturnCandidate']
        derived_status=wrapper['communicationStatusByteCandidate']
        if derived_status is not None and communication_status!=derived_status:
            raise ValueError('subtype4-communication-status-mismatch')
        output=(bytes.fromhex(wrapper['sourceWrittenPrefixHex'])
                if wrapper['sourceWrittenPrefixHex'] is not None else None)
        output_complete=wrapper['sourceOutputComplete'] and derived_status is not None
    elif isinstance(communication,dict) and communication.get('wrapper') in MODE50_WRAPPERS:
        from .x431_mode50_response import audit_mode50_response,audit_mode50_receive_sequence
        if site!=0x19fa2:raise ValueError('mode50-version-producer-site-mismatch')
        audit_mode50=audit_mode50_receive_sequence if communication['wrapper']==MODE50_WRAPPERS[1] else audit_mode50_response
        wrapper=audit_mode50({key:value for key,value in communication.items() if key!='wrapper'})
        if wrapper['requestPackingAudit']['effectiveRequestBytesCandidate']!=len(request):
            raise ValueError('mode50-version-request-length-mismatch')
        wrapper_return=wrapper['nativeReturnCandidate']
        derived_status=wrapper['communicationStatusByteCandidate']
        if derived_status is not None and communication_status!=derived_status:
            raise ValueError('mode50-communication-status-mismatch')
        output_complete=wrapper['sourceOutputComplete']
        output=bytes.fromhex(wrapper['sourceWrittenPrefixHex']) if output_complete else None
    elif isinstance(communication,dict) and communication.get('wrapper') in (
            'mode10-subtype3-short-0xf8d4','mode10-subtype3-0xf8d4','mode10-subtype3-sequence-0xf8d4'):
        if site!=0x19fa2:
            raise ValueError('mode10-version-producer-site-mismatch')
        audit_mode10={'mode10-subtype3-short-0xf8d4':audit_mode10_subtype3_short_response,
                      'mode10-subtype3-0xf8d4':audit_mode10_subtype3_response,
                      'mode10-subtype3-sequence-0xf8d4':audit_mode10_receive_sequence}[communication['wrapper']]
        wrapper=audit_mode10({key:value for key,value in communication.items() if key!='wrapper'})
        if wrapper['requestPackingAudit']['effectiveRequestBytesCandidate']!=len(request):
            raise ValueError('mode10-version-request-length-mismatch')
        wrapper_return=wrapper['nativeReturnCandidate']
        derived_status=wrapper['communicationStatusByteCandidate']
        if derived_status is not None and communication_status!=derived_status:
            raise ValueError('mode10-communication-status-mismatch')
        output_complete=wrapper['sourceOutputComplete']
        output=bytes.fromhex(wrapper['sourceWrittenPrefixHex']) if output_complete else None
    else:
        wrapper=audit_porsche_mode30_wrapper(communication)
        wrapper_return=wrapper['lowerResponse']['nativeReturnCandidate']
        output_complete=wrapper['declaredBodyComplete']
        output=(bytes([wrapper['wrapperStatusByte'],wrapper['declaredBodyBytes']])+
                bytes.fromhex(wrapper['declaredBodyHexCandidate'])) if output_complete else None
    if mode_audit is not None:
        wrapper['modeContextAudit']=mode_audit
        retry_pair=(wrapper.get('retryContextAudit') or {}).get('communicationPairSourceAudit')
        if retry_pair is not None and mode_audit.get('communicationRecordSha256') is not None:
            if any(retry_pair[key]!=mode_audit[key] for key in ('communicationRecordSha256','explicitPairIndex')):
                raise ValueError('mode10-retry-filter-and-mode-source-pair-mismatch')
    # PorscheCommunicationPro's common return gate suppresses the count to zero
    # when its separate status byte is 0x7f. It is not wrapper buffer[0].
    incomplete_mode10=(wrapper.get('sourceReceiveSequenceComplete') is False or
                       wrapper.get('receiveAttemptsRequired',False))
    returned=None if incomplete_mode10 else (0 if communication_status==0x7f else wrapper_return)
    kind=PRODUCER_COUNT_KINDS[site]
    count=(1 if returned is not None and returned>=0 else None) if kind.startswith('forced-one') else returned
    out={'kind':'x431-offline-version-map-origin-audit','syspSourceSha256':SYSP_SHA256,
         'commSourceSha256':PORSCHE_COMM_SHA256,'producerCallSite':site,
         'producerCountKind':kind,'communicationReturnCandidate':returned,'producerCountCandidate':count,
         'wrapperReturnCandidate':wrapper_return,'communicationStatusByte':communication_status,
         'requestHex':request.hex().upper(),'nativeResponseMapKey':native_request_map_key(request),
         'wrapperResponse':wrapper,'modeContextAudit':mode_audit,'status':'communication-output-unqualified',
         'nativeMapVectorHexCandidate':None,'mapUpdateCandidate':None,
         'producerInputOffset':0x1961,'callerSiteRuntimeQualified':False,**VERSION_GUARDS}
    if count==0:
        out.update(status='zero-count-leaves-previous-map-entry',mapUpdateCandidate=False)
        return out
    if count is None or count<0 or not output_complete:
        return out
    out.update(sourceWrittenBytes=len(output),sourceWrittenPrefixHex=output.hex(),mapUpdateCandidate=True)
    if count>len(output):
        out.update(status='producer-count-exceeds-source-written-extent',
                   initializationBytesCopiedCandidate=count-len(output))
        return out
    out.update(status='source-written-prefix-map-candidate',nativeMapVectorHexCandidate=output[:count].hex())
    return out


def audit_subtype4_frames(aggregate_hex: str, index: int) -> dict:
    """Bounds-check the retained 0x13e6c layout: count, then length+body rows.

    Native code copies the row's length byte and that many following bytes.
    It returns without changing the destination when index exceeds count.
    This parser requires complete explicitly supplied bytes, not a zero tail.
    """
    raw=_hex(aggregate_hex,1,1024)
    if type(index) is not int or not 1<=index<=255:
        raise ValueError('subtype4-frame-index-invalid')
    count=raw[0]
    out={'kind':'x431-offline-subtype4-frame-audit','commSourceSha256':PORSCHE_COMM_SHA256,
         'selectorEntry':0x13e6c,'declaredFrames':count,'requestedIndex':index,
         'status':None,'selectedFrameHexCandidate':None,'frames':[],
         'sourceDestinationUntouchedCandidate':index>count,**VERSION_GUARDS}
    if index>count:
        out['status']='frame-index-not-declared'
        return out
    cursor=1
    selected=None
    for ordinal in range(1,count+1):
        if cursor>=len(raw):
            out.update(status='aggregate-frame-length-missing',incompleteFrameIndex=ordinal)
            return out
        length=raw[cursor]; stop=cursor+1+length
        if stop>len(raw):
            out.update(status='aggregate-frame-truncated',incompleteFrameIndex=ordinal,
                       declaredFrameBytes=length+1,availableFrameBytes=len(raw)-cursor)
            return out
        out['frames'].append({'index':ordinal,'offset':cursor,'bodyBytes':length,'nativeCopiedBytes':length+1})
        if ordinal==index:
            selected=raw[cursor:stop]
        cursor=stop
    out.update(status='complete-internal-frame-candidate',selectedFrameHexCandidate=selected.hex(),
               zeroLengthTerminator=selected[0]==0,extraSuppliedBytes=len(raw)-cursor)
    return out


def audit_subtype4_reassembly(aggregate_hex: str) -> dict:
    """Audit the retained post-receive algorithm, never the aggregate producer.

    Rows contain one metadata byte followed by fragment bytes. The first row's
    0x80/metadata-bit-4 combination is skipped; zero length ends selection.
    The concatenated stream contains two-byte, low-12-bit-length records. The
    first record loses its first body byte; subsequent bodies are copied whole.
    Explicit bounds are stricter than native unchecked scratch-buffer reads.
    """
    frames=audit_subtype4_frames(aggregate_hex,1)
    out={'kind':'x431-offline-subtype4-reassembly-audit',
         'commSourceSha256':PORSCHE_COMM_SHA256,'reassemblyEntry':0x10da4,
         'frameAudit':frames,'status':'aggregate-unqualified',
         'nativeReturnCandidate':None,'communicationStatusByteCandidate':None,
         'sourceWrittenPrefixHex':None,'sourceOutputComplete':False,
         'concatenatedHexCandidate':None,'fragments':[],'records':[],
         'aggregateProviderQualified':False,'preReceiveGatesObserved':False,
         'nativeAggregateExtentChecked':False,**VERSION_GUARDS}
    if frames['status'] not in ('complete-internal-frame-candidate','frame-index-not-declared'):
        return out
    raw=bytes.fromhex(aggregate_hex)
    stream=bytearray()
    for row in frames['frames']:
        ordinal=row['index']; length=row['bodyBytes']; cursor=row['offset']
        if not length:
            out['zeroLengthTerminatorIndex']=ordinal
            out['ignoredDeclaredFrames']=frames['declaredFrames']-ordinal
            break
        metadata=raw[cursor+1]
        skip=(ordinal==1 and length>=2 and raw[cursor+2]==0x80 and bool(metadata&0x10))
        included=length>=2 and not skip
        fragment=raw[cursor+2:cursor+1+length] if included else b''
        stream.extend(fragment)
        out['fragments'].append({'index':ordinal,'metadataByte':metadata,
                                 'firstRowFilterMatched':skip,'included':included,
                                 'copiedBytes':len(fragment)})
    out['concatenatedHexCandidate']=stream.hex()
    out['concatenatedBytes']=len(stream)
    # This source read is unconditional. For fewer than three written bytes it
    # depends on an unobserved initialized/previous output byte: never infer it.
    if len(stream)>=3:
        out['communicationStatusByteCandidate']=0x7f if stream[2]==0x7f else 0x7e
    if len(stream)<4:
        out.update(status='short-stream-source-special-case',
                   nativeReturnCandidate=1 if len(stream)==3 else -1,
                   sourceWrittenPrefixHex=stream.hex(),sourceOutputComplete=True,
                   recordsParsed=False)
        return out
    cursor=0; flattened=bytearray()
    while cursor<len(stream):
        if len(stream)-cursor<2:
            out.update(status='record-header-truncated',incompleteRecordOffset=cursor)
            return out
        length=((stream[cursor]&0x0f)<<8)|stream[cursor+1]
        stop=cursor+2+length
        if stop>len(stream):
            out.update(status='record-body-truncated',incompleteRecordOffset=cursor,
                       declaredRecordBodyBytes=length,availableRecordBodyBytes=len(stream)-cursor-2)
            return out
        body=stream[cursor+2:stop]
        copied=body[1:] if not out['records'] else body
        out['records'].append({'index':len(out['records'])+1,'offset':cursor,
                               'headerHighNibble':stream[cursor]>>4,
                               'bodyBytes':length,'copiedBytes':len(copied)})
        flattened.extend(copied);cursor=stop
    out.update(status='complete-source-reassembly-candidate',recordsParsed=True,
               nativeReturnCandidate=len(flattened),sourceWrittenPrefixHex=flattened.hex(),
               sourceOutputComplete=True,emptyOutputFlagCandidate=not flattened)
    return out


def audit_subtype4_completion_receive(observation: dict) -> dict:
    """Audit the fixed completion caller's receive, never an ECU acknowledgement.

    CommSubFunc0002 forwards the shared read helper's signed return and frees
    its temporary request state. The upper caller ignores its response bytes.
    These source facts do not establish actual provider/cleanup execution.
    """
    if not isinstance(observation,dict) or set(observation)!={'internal'}:
        raise ValueError('subtype4-completion-receive-observation-invalid')
    internal=observation['internal']
    if not isinstance(internal,dict) or internal.get('capacity')!=256:
        raise ValueError('subtype4-completion-capacity-mismatch')
    lower=audit_internal_response(internal)
    return {'kind':'x431-offline-subtype4-completion-receive-audit',
        'abstractSourceSha256':ABSTRACT_SHA256,'commSourceSha256':PORSCHE_COMM_SHA256,
        'lookupSymbol':'CommSubFunc0002','entry':0x3899c,'upperCallSite':0x11032,
        'sourceCallerCommandBytes':10,'sourceCallerBlockCount':1,
        'sourceCallerCapacity':256,'sourceCallerFilter':255,
        'sourceCallerTailArguments':[0,2,4],
        'lowerResponse':lower,'nativeReturnCandidate':lower['nativeReturnCandidate'],
        'callbackStaticReadAndCleanupVerified':True,'requestPreparationReturnsChecked':False,
        'sourceTemporaryRequestCleanup':'free requestBuffer; reset pointer, length and capacity',
        'sourceOutputUsedForReassembly':False,'sourceCallerClearsResponseBufferAfterNonnegativeReturn':True,
        'callbackRuntimeBindingQualified':False,'actualCleanupExecutedProven':False,
        'completionAcknowledgementQualified':False,**VERSION_GUARDS}


def audit_subtype4_receive_chain(observation: dict) -> dict:
    """Compose the supplied internal receive extent with post-receive parsing.

    Command byte count is observed context, never request bytes. The completion
    callback's signed return is separately supplied or audited from its own
    receive; it cannot be inferred from the aggregate receive. Runtime binding
    and vehicle freshness stay unknown. Completion bytes are never merged.
    """
    if not isinstance(observation,dict) or set(observation)-{'modeContext'} not in COMPLETION_FIELDS:
        raise ValueError('subtype4-read-chain-observation-invalid')
    mode_audit=(audit_communication_mode_context(observation['modeContext'],'subtype4-read-chain-0x10da4')
                if 'modeContext' in observation else None)
    receive=observation['aggregateReceive']
    if not isinstance(receive,dict) or set(receive)!={'commandBytes','internal'}:
        raise ValueError('subtype4-aggregate-receive-observation-invalid')
    command_bytes=receive['commandBytes']
    if type(command_bytes) is not int or not 11<=command_bytes<=17:
        raise ValueError('subtype4-command-byte-count-unqualified')
    internal=receive['internal']
    if not isinstance(internal,dict) or internal.get('capacity')!=1024:
        raise ValueError('subtype4-aggregate-capacity-mismatch')
    declared=observation.get('completionCallbackReturn')
    if 'completionCallbackReturn' in observation and (type(declared) is not int or not -32768<=declared<=32767):
        raise ValueError('subtype4-completion-signed16-return-required')
    completion_audit=(audit_subtype4_completion_receive(observation['completionReceive'])
                      if 'completionReceive' in observation else None)
    completion=(completion_audit['nativeReturnCandidate'] if completion_audit is not None else declared)
    if completion_audit is not None and declared is not None and completion is not None and declared!=completion:
        raise ValueError('subtype4-completion-return-observation-mismatch')
    lower=audit_internal_response(internal)
    returned=lower['nativeReturnCandidate']
    out={'kind':'x431-offline-subtype4-receive-chain-audit',
         'abstractSourceSha256':ABSTRACT_SHA256,'commSourceSha256':PORSCHE_COMM_SHA256,
         'providerSymbol':'VWCANSendDataToEcuGetMultiFrameAnswer1','providerEntry':0x31444,
         'providerLoaderLookupSite':0x164f0,'providerLoaderStoreSite':0x164fe,
         'providerStaticLookupStoreVerified':True,'providerNullLookupFailureReturn':-112,
         'completionLookupSymbol':'CommSubFunc0002','completionPointerObject':'CommSubFunc_00000002',
         'completionLoaderLookupSite':0x16fb6,'completionLoaderStoreSite':0x16fc6,
         'completionStaticLookupStoreVerified':True,'completionLoaderNullCheckVerified':False,
         'cachedHandlePathChecksProviderPointer':False,
         'lowerResponse':lower,'providerReturnCandidate':returned,'commandBytes':command_bytes,
         'completionCallbackReturn':completion,'completionGateCandidate':None,
         'declaredCompletionCallbackReturn':declared,'completionReceiveAudit':completion_audit,
         'completionReadContractAudited':completion_audit is not None,
         'completionReturnSource':'audited-internal-response-candidate' if completion_audit is not None else 'supplied-signed-return',
         'modeContextAudit':mode_audit,
         'status':'aggregate-receive-output-unqualified','reassembly':None,
         'nativeReturnCandidate':None,'communicationStatusByteCandidate':None,
         'sourceWrittenPrefixHex':None,'sourceOutputComplete':False,
         'aggregateProviderQualified':False,'providerRuntimeBindingQualified':False,
         'completionCallbackSourceQualified':False,**VERSION_GUARDS}
    if returned is not None and returned<0:
        # The caller replaces any initial provider failure with -1.
        out.update(status='aggregate-provider-negative-return',nativeReturnCandidate=-1)
        return out
    if returned is None:
        return out
    if completion is None:
        out['status']='completion-receive-return-unqualified'
        return out
    out['completionGateCandidate']=completion>=0
    if completion<0:
        out.update(status='subtype4-completion-callback-failed',nativeReturnCandidate=completion)
        return out
    if not lower['internalBufferComplete']:
        return out
    reassembly=audit_subtype4_reassembly(lower['internalBufferHexCandidate'])
    out.update(status=reassembly['status'],reassembly=reassembly,
               nativeReturnCandidate=reassembly['nativeReturnCandidate'],
               communicationStatusByteCandidate=reassembly['communicationStatusByteCandidate'],
               sourceWrittenPrefixHex=reassembly['sourceWrittenPrefixHex'],
               sourceOutputComplete=reassembly['sourceOutputComplete'])
    return out


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation-json',type=Path,required=True)
    parser.add_argument('--kind',choices=('version-map','token-identity-response','subtype4','subtype4-reassembly','subtype4-receive-chain','subtype4-request-packing','subtype4-completion-receive','communication-mode','source-communication','mode30-filter-context','mode30-non4-request-packet','mode10-short-response','mode10-response','mode10-request-packing','mode10-retry-context','mode10-receive-sequence','mode10-proex-response','mode10-proex-request-packing','mode10-proex-retry-context','mode10-proex-receive-sequence','mode50-response','mode50-request-packing','mode50-request-packet','mode50-request-source-context','mode50-initialization','mode50-receive-sequence'),required=True)
    parser.add_argument('--logical-sys-data',type=Path,help='Pinned SYS_DATA source for source-communication or source-selected token-identity-response')
    args=parser.parse_args(argv)
    try:
        observation=json.loads(args.observation_json.read_text(encoding='utf-8-sig'))
        if args.kind=='token-identity-response':
            report=audit_token_identity_response(observation,args.logical_sys_data.read_bytes() if args.logical_sys_data is not None else None)
        elif args.kind=='source-communication':
            if args.logical_sys_data is None:
                raise ValueError('communication-logical-source-required')
            report=audit_source_communication_context(args.logical_sys_data.read_bytes(),observation)
        elif args.logical_sys_data is not None:
            raise ValueError('communication-logical-source-option-requires-source-kind')
        elif args.kind=='mode30-filter-context':
            report=audit_mode30_filter_context(observation)
        elif args.kind=='mode30-non4-request-packet':
            report=audit_mode30_non4_request_packet(observation)
        elif args.kind.startswith('mode50-'):
            from .x431_mode50_response import audit_mode50_response,audit_mode50_receive_sequence,audit_mode50_request_packing,audit_mode50_request_packet,audit_mode50_request_source_context,audit_mode50_initialization
            if args.kind=='mode50-initialization':
                report=audit_mode50_initialization(observation)
            elif args.kind=='mode50-request-source-context':
                report=audit_mode50_request_source_context(observation)
            elif args.kind=='mode50-request-packet':
                report=audit_mode50_request_packet(observation)
            elif args.kind=='mode50-request-packing':
                if not isinstance(observation,dict) or set(observation)!={'requestBytes','extendedRequestByte'}:
                    raise ValueError('mode50-request-length-observation-invalid')
                report=audit_mode50_request_packing(observation['requestBytes'],observation['extendedRequestByte'])
            else:
                report=(audit_mode50_receive_sequence if args.kind=='mode50-receive-sequence' else audit_mode50_response)(observation)
        elif args.kind in ('mode10-request-packing','mode10-proex-request-packing'):
            if not isinstance(observation,dict) or set(observation)!={'requestBytes','extendedRequestByte'}:
                raise ValueError('mode10-request-length-observation-invalid')
            report=(audit_mode10_proex_request_packing if args.kind=='mode10-proex-request-packing' else audit_mode10_request_packing)(observation['requestBytes'],observation['extendedRequestByte'])
        elif args.kind=='mode10-proex-retry-context':
            report=audit_mode10_proex_retry_context(observation)
        elif args.kind=='mode10-proex-receive-sequence':
            report=audit_mode10_proex_receive_sequence(observation)
        elif args.kind=='mode10-proex-response':
            report=audit_mode10_proex_response(observation)
        elif args.kind=='mode10-retry-context':
            report=audit_mode10_retry_context(observation)
        elif args.kind=='mode10-receive-sequence':
            report=audit_mode10_receive_sequence(observation)
        elif args.kind=='mode10-response':
            report=audit_mode10_subtype3_response(observation)
        elif args.kind=='mode10-short-response':
            report=audit_mode10_subtype3_short_response(observation)
        elif args.kind=='communication-mode':
            report=audit_communication_mode_context(observation)
        elif args.kind=='subtype4-completion-receive':
            report=audit_subtype4_completion_receive(observation)
        elif args.kind=='subtype4-request-packing':
            if not isinstance(observation,dict) or set(observation) not in (
                    {'requestBytes'},{'requestBytes','rereadRequestBytes'}):
                raise ValueError('subtype4-request-packing-observation-invalid')
            if 'rereadRequestBytes' in observation and observation['rereadRequestBytes'] is None:
                raise ValueError('subtype4-reread-length-byte-required')
            report=audit_subtype4_request_packing(observation['requestBytes'],observation.get('rereadRequestBytes'))
        elif args.kind=='subtype4':
            if not isinstance(observation,dict) or set(observation)!={'aggregateHex','index'}:
                raise ValueError('subtype4-observation-invalid')
            report=audit_subtype4_frames(observation['aggregateHex'],observation['index'])
        elif args.kind=='subtype4-reassembly':
            if not isinstance(observation,dict) or set(observation)!={'aggregateHex'}:
                raise ValueError('subtype4-reassembly-observation-invalid')
            report=audit_subtype4_reassembly(observation['aggregateHex'])
        elif args.kind=='subtype4-receive-chain':
            report=audit_subtype4_receive_chain(observation)
        else:
            report=audit_version_map_origin(observation)
    except (OSError,ValueError) as error:
        parser.exit(2,f'{error}\n')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
