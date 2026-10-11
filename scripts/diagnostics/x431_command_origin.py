"""Pinned X431 command-source and explicit buffer audits, with no transport.

Raw COMMAND records and postprocessed native descriptors are distinct. Heap
termination, current mode, extended length and ECU identity are never invented.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from scripts.x431_re.gag_codec import parse_text_gag,decode_record,encrypt_number_strict
from scripts.x431_re.ggp_index import parse_header
from scripts.x431_re.sysdata import parse_comm
from .x431_variant_selection import REVIEWED_LANGUAGE_SHA256
from .x431_response_contract import PORSCHE_COMM_SHA256
from .x431_version_response import (
    FILE_SHA256,SYSP_SHA256,SYS_DATA_LOGICAL_SHA256,VERSION_GUARDS,audit_source_communication_context,
    audit_communication_mode_context,audit_mode10_retry_context,audit_version_map_origin)
from .x431_version_response import audit_mode10_request_packing,audit_mode10_proex_request_packing

STD_SHA256='f92baf83d6b83aed87c0066a2b114c4c3864ec24d27aeb17b9c8896e1a840c20'
COMMAND_GAG_SHA256='300c899617d1aa43b9f860dc2ebfbe0eb820a27b27f2e1b7362eb696ec29d4bc'
INDIRECT_MARKER=b'SubIndexNum='
STAGING_ENTRIES={
    'PorscheSendCommandByIDEX':(0x13240,'calculated'),
    'PorscheSendCommandByIDEX_COMM_INFO':(0x13308,'retained'),
    'PorscheSendCommandByIDEXSendOnlyAndOne':(0x133ac,'retained'),
    'PorscheSendCommandByIDEX1':(0x13468,'explicit'),
    'PorscheSendCommandByIDEX2':(0x134ec,'explicit'),
}


def audit_enter_sys_command_caller(mode_byte: int, subtype_byte: int) -> dict:
    """Pinned EnterSys lookup calls, separate from lower response wrappers.

    Thumb PIC/GOT relocations bind both lookup slots to FromCmdIdToCmdBuff
    (object 0x48e5c4) and both communication slots to PorscheCommunicationPro
    (object 0x48e7bc). Mode 30 branches at 0x19bf6; modes 10/50 share the
    lookup at 0x19e20. Only retained 9X1 mode/subtype tuples are included.
    Scratch slots and ignored return values do not establish safe native
    memory bounds, postprocessing, or an actual selected vehicle context.
    """
    if any(type(v) is not int or not 0 <= v <= 255 for v in (mode_byte, subtype_byte)):
        raise ValueError('source-command-mode-bytes-required')
    if (mode_byte, subtype_byte) not in ((0x10, 3), (0x30, 1), (0x30, 4), (0x50, 5)):
        raise ValueError('source-command-mode-unreviewed')
    thirty = mode_byte == 0x30
    return {'kind': 'x431-offline-enter-sys-command-caller-audit', 'syspSourceSha256': SYSP_SHA256,
        'callerEntry': 0x19920, 'modeByte': mode_byte, 'subtypeByte': subtype_byte,
        'lookupCallSite': 0x19c40 if thirty else 0x19e20,
        'lookupObjectSymbol': 'FromCmdIdToCmdBuff', 'lookupObjectVa': 0x48e5c4,
        'lookupGotVa': 0x64af4, 'lookupStackOffset': 0xb8 if thirty else 0xc4,
        'lookupPicLoadVa': 0x19b98 if thirty else 0x19ae4,
        'lookupPicAddVa': 0x19b9c if thirty else 0x19ae8,
        'communicationCallSite': 0x19d70 if thirty else 0x19f4e,
        'communicationObjectSymbol': 'PorscheCommunicationPro', 'communicationObjectVa': 0x48e7bc,
        'communicationGotVa': 0x64b00, 'communicationStackOffset': 0x8c if thirty else 0xb0,
        'communicationPicLoadVa': 0x19b7a if thirty else 0x19ab2,
        'communicationPicAddVa': 0x19b7e if thirty else 0x19ab6,
        'lookupArgumentCapacity': 512, 'lookupDestinationTokenOffset': 0x957,
        'commandIdTokenBaseOffset': 0x670, 'commandIdPairStride': 40, 'commandIdRequestStride': 4,
        'scratchDescriptorPairStride': 400, 'scratchDescriptorRequestStride': 20,
        'lookupReturnCheckedBeforeDescriptorCopy': False, 'nativeScratchBoundsQualified': False,
        'staticLookupCalleeBindingReviewed': True, 'staticCommunicationCalleeBindingReviewed': True,
        'callerRegionSha256': '8d48664151b1c86d82ef12547df20b14d94448d531be2be3b7eef475e02171a9' if thirty else
            'cc88096e703893584e9e2df691a2bde3e291bdb9a137d63ddb4a5941767042cc',
        'callerRegionStartVa': 0x19bca if thirty else 0x19dd6,
        'callerRegionEndVa': 0x19c44 if thirty else 0x19e24,
        'versionMapProducerCallSite': 0x19d94 if thirty else 0x19fa2,
        'commandResponseAssociationAuditImplemented': True,
        'actualLookupCallQualified': False, 'actualCommunicationCallQualified': False, **VERSION_GUARDS}


def _bytes(hx,maximum):
    if not isinstance(hx,str) or len(hx)>maximum*2 or len(hx)%2 or not re.fullmatch('[0-9a-fA-F]*',hx):
        raise ValueError('command-source-hex-invalid')
    return bytes.fromhex(hx)


def audit_command_postprocessing(observation: dict) -> dict:
    """Audit IdSearch's plain-body tail; no recursive lookup or string reading.

    Only known bytes may establish strlen termination. An observed scratch
    prefix must match the clamped decoded body, and fit capacity+2 allocation.
    """
    from .command_lookup_buffers import lookup_buffer_source_metadata
    fields={'decodedPayloadHex','lookupCapacity'}
    if not isinstance(observation,dict) or set(observation) not in (fields,fields|{'searchBufferHex'}):
        raise ValueError('command-postprocessing-fields-invalid')
    capacity=observation['lookupCapacity']
    if type(capacity) is not int or not 1<=capacity<=32765:
        raise ValueError('command-lookup-capacity-invalid')
    body=_bytes(observation['decodedPayloadHex'],65533);copied=min(len(body),capacity)
    prefix=body[:copied];known=prefix
    if 'searchBufferHex' in observation:
        known=_bytes(observation['searchBufferHex'],capacity+2)
        if len(known)<copied or known[:copied]!=prefix:
            raise ValueError('command-search-buffer-prefix-mismatch')
    nul=known.find(b'\0')
    marker=(known.startswith(INDIRECT_MARKER) if len(known)>=len(INDIRECT_MARKER) else
            False if any(a!=b for a,b in zip(known,INDIRECT_MARKER)) else None)
    out={'kind':'x431-offline-command-postprocessing-audit','stdSourceSha256':STD_SHA256,
        'lookupBufferSourceAudit':lookup_buffer_source_metadata(),
        'sourceEntry':0x25614,'decodedPayloadBytes':len(body),'lookupCapacity':capacity,
        'sourceLookupCopiedBytesCandidate':copied,'sourceCallerCopyBytes':capacity,
        'sourceAllocatedScratchBytes':capacity+2,'sourceCallerIgnoresLookupReturn':True,
        'sourcePostprocessorCalledCandidate':copied>0,'sourcePostprocessorMarker':'SubIndexNum=',
        'sourceMarkerPrefixMatchesCandidate':marker,'knownBufferBytes':len(known),
        'knownNullTerminatorOffset':nul if nul>=0 else None,
        'sourceStringTerminationQualified':nul>=0,'sourceStringLengthCandidate':nul if nul>=0 else None,
        'sourceStringLengthGateMinimum':22,'sourceStringLengthGateMaximum':544,
        'sourcePostprocessingComplete':False,'sourceRecursiveLookupRequiredCandidate':False,
        'nativeLookupReturnCandidate':None,'sourceWrittenPrefixHex':None,
        'callerUnqualifiedTailBytes':capacity-copied,
        'bufferObservationSource':'explicit-offline-buffer' if 'searchBufferHex' in observation else 'decoded-record-only',
        'actualHeapBufferQualified':False,'status':'command-string-termination-unqualified',**VERSION_GUARDS}
    if copied==0:
        out.update(sourcePostprocessingComplete=True,nativeLookupReturnCandidate=0,
                   sourceWrittenPrefixHex='',status='empty-record-skips-postprocessing')
    elif nul>=0 and (nul<22 or nul>544 or marker is False):
        out.update(sourcePostprocessingComplete=True,nativeLookupReturnCandidate=copied,
                   sourceWrittenPrefixHex=prefix.hex(),status='source-postprocessing-bypass-candidate')
    elif nul>=0:
        out.update(sourceRecursiveLookupRequiredCandidate=True,status='command-indirect-text-lookup-unqualified')
    return out


def audit_command_staging(observation: dict) -> dict:
    """Review a named wrapper's explicit offline descriptor and extension source.

    This does not build a packet or infer a current mode/global value. The
    descriptor includes its flag and low length byte; unknown retained state
    must be supplied explicitly (after initialization for COMM_INFO). ProEx
    shares packing arithmetic but its response/retry audit is separate. The reviewer caps the copied prefix
    at the observed 4106-byte region; this is not a native bounds check.
    """
    if not isinstance(observation,dict) or not isinstance(observation.get('entry'),str):
        raise ValueError('command-staging-entry-required')
    entry=observation['entry']
    if entry not in STAGING_ENTRIES:raise ValueError('command-staging-entry-unreviewed')
    va,origin=STAGING_ENTRIES[entry];fields={'entry','descriptorHex'}
    extra={'retainedExtendedByte'} if origin=='retained' else {'extendedArgument'} if origin=='explicit' else set()
    if set(observation)!=fields|extra:raise ValueError('command-staging-fields-invalid')
    descriptor=_bytes(observation['descriptorHex'],0x100a)
    if len(descriptor)<2:raise ValueError('command-staging-descriptor-header-required')
    count=len(descriptor);flag,low=descriptor[:2]
    if origin=='calculated':
        extension=0 if count<258 else ((count-2)//255)&255
    else:
        key=next(iter(extra));value=observation[key]
        maximum=255 if origin=='retained' else 0xffffffff
        if type(value) is not int or not 0<=value<=maximum:
            raise ValueError('command-staging-extended-value-invalid')
        extension=value&255
    clears=entry not in ('PorscheSendCommandByIDEX1','PorscheSendCommandByIDEX2')
    pro_ex=entry=='PorscheSendCommandByIDEX2'
    packing=(audit_mode10_proex_request_packing if pro_ex else audit_mode10_request_packing)(low,extension)
    required=packing['effectiveRequestBytesCandidate']
    copied_body=count-2
    return {'kind':'x431-offline-command-staging-audit','sourceEntryName':entry,'sourceEntry':va,
        'commSourceSha256':PORSCHE_COMM_SHA256,
        'sourceDescriptorTokenOffset':0x957,'sourceCommunicationTokenOffset':0x63c,
        'sourceExtendedTokenOffset':0x2969,'sourceExtendedCommunicationOffset':0x232d,
        'suppliedDescriptorBytes':count,'suppliedDescriptorSha256':hashlib.sha256(descriptor).hexdigest(),
        'requestFlagByteCandidate':flag,'requestLengthByteCandidate':low,
        'sourceCopyBytes':count,'sourceTailClearBytesCandidate':0x100a-count if clears else 0,
        'sourceClearedTailIsOriginalCommandData':False,
        'sourceExtendedByteOrigin':origin,'sourceExtendedByteCandidate':extension,
        'sourceExtensionCalculationDivisor':255 if origin=='calculated' else None,
        'sourceExtensionAfterNormalReturnCandidate':0 if origin=='explicit' else None,
        'sourceSendOnlyAndOneSetCandidate':1 if entry=='PorscheSendCommandByIDEXSendOnlyAndOne' else None,
        'sourceSendOnlyAndOneAfterNormalReturnCandidate':0 if entry=='PorscheSendCommandByIDEXSendOnlyAndOne' else None,
        'sourceCallerRunsCanInitialization':entry=='PorscheSendCommandByIDEX_COMM_INFO',
        'callee':'PorscheCommunicationProEx' if pro_ex else 'PorscheCommunicationPro',
        'mode10RequestPackingSourceReviewedCandidate':True,'mode10PackingCandidate':packing,
        'mode10ResponseAuditImplemented':True,
        'mode10ResponseWrapperCandidate':'mode10-subtype3-proex-0x11678' if pro_ex else 'mode10-subtype3-0xf8d4',
        'versionMapProducerSourceQualifiedForCallee':not pro_ex,
        'sourceRetryPolicySameAsProCandidate':not pro_ex,
        'suppliedBodyBytes':copied_body,'sourceMode10BodyBytesCandidate':required,
        'copiedBodyCoversMode10LengthCandidate':copied_body>=required,
        'copiedBodyExactlyMatchesMode10LengthCandidate':copied_body==required,
        'mode10ActuallySelectedQualified':False,'normalCleanupRuntimeQualified':False,
        'actualTokenBindingQualified':False,'actualRetainedExtendedByteQualified':False,
        'requestPacketBytesConstructed':False,**VERSION_GUARDS}


def audit_gag_name_selection(entries: list, requested_name: str) -> dict:
    """Separate the native first-prefix result from an exact source binding.

    The native name table is prepared through an imported uppercase table.
    Explicit ASCII rows model that prepared table; they never qualify its
    runtime allocation, name mapping or active GGP contents.
    """
    if not isinstance(entries,list) or len(entries)>255:
        raise ValueError('gag-directory-byte-count-required')
    if not isinstance(requested_name,str) or not requested_name.isascii() or '\0' in requested_name:
        raise ValueError('gag-request-name-ascii-required')
    for index,row in enumerate(entries):
        if (not isinstance(row,dict) or set(row)!={'i','name_ascii','offset','size'}
                or type(row['i']) is not int or row['i']!=index
                or any(type(row[key]) is not int or not 0<=row[key]<=0xffffffff for key in ('offset','size'))
                or not isinstance(row['name_ascii'],str) or not row['name_ascii']
                or not row['name_ascii'].isascii() or '\0' in row['name_ascii'] or len(row['name_ascii'])>64):
            raise ValueError('gag-directory-explicit-row-invalid')
    allowed=len(requested_name)<=64
    matches=[row['i'] for row in entries if allowed and row['name_ascii'].startswith(requested_name)]
    exact=[row['i'] for row in entries if row['name_ascii']==requested_name]
    first=entries[matches[0]] if matches else None
    compared=entries[:matches[0]+1] if matches else entries
    # GetAllGGpInfo allocates strlen(name)+1. A longer memcmp may read beyond
    # those known bytes; an early logical mismatch does not qualify that read.
    unknown=[row['i'] for row in compared if allowed and len(row['name_ascii'])+1<len(requested_name)]
    ranges=not unknown
    qualified=bool(requested_name) and allowed and ranges and len(matches)==len(exact)==1 and matches==exact
    return {'kind':'x431-offline-gag-name-selection-audit','stdSourceSha256':STD_SHA256,
        'sourceOffsetGetterEntry':0x25c54,'sourceSizeGetterEntry':0x25e88,
        'requestedName':requested_name,'sourceNameLengthAllowedCandidate':allowed,
        'sourceLogicalPrefixMatchIndexes':matches,'exactNameMatchIndexes':exact,
        'sourceKnownNameComparisonRangesCompleteCandidate':ranges,'sourceUnknownComparisonRowIndexes':unknown,
        'sourceFirstPrefixIndexCandidate':matches[0] if matches and ranges else None,
        'sourceNativeOffsetCandidate':(first['offset'] if first else 0) if ranges else None,
        'sourceNativeSizeCandidate':(first['size'] if first else 0) if ranges else None,
        'sourceExactBindingCandidate':qualified,'qualifiedSourceEntryCandidate':first if qualified else None,
        'nativePrefixOrEmptyNamePromotedToExactBinding':False,
        'actualNameMappingTableQualified':False,'actualGgpCacheContextQualified':False,**VERSION_GUARDS}


def audit_gag_cache_reuse(observation: dict) -> dict:
    """Model only the observed pathname/header-pointer branch, never freshness."""
    fields={'requestedGgpPath','cachedGgpPath','cachedHeaderPointerPresent'}
    if not isinstance(observation,dict) or set(observation)!=fields or type(observation['cachedHeaderPointerPresent']) is not bool:
        raise ValueError('gag-cache-explicit-observation-required')
    for key in ('requestedGgpPath','cachedGgpPath'):
        value=observation[key]
        if value is None and key=='cachedGgpPath':continue
        # This bounds the review input to the source's256-byte pathname area;
        # the native strlen+1 copy itself has no corresponding bounds check.
        if not isinstance(value,str) or not value or not value.isascii() or '\0' in value or len(value)>255:
            raise ValueError('gag-cache-path-review-bound-required')
    present=observation['cachedHeaderPointerPresent'];cached=observation['cachedGgpPath']
    reuse=None if present and cached is None else present and cached==observation['requestedGgpPath']
    return {'kind':'x431-offline-gag-cache-reuse-audit','stdSourceSha256':STD_SHA256,
        'sourceLoaderEntryCandidate':0x25cc0,'sourceLoaderExtentQualified':False,
        'sourceHeaderObject':'fpggp','sourceHeaderPointerOffset':0x118,
        'sourceCacheReuseCandidate':reuse,'sourceReloadCandidate':None if reuse is None else not reuse,
        'sourcePathComparison':'exact-case-sensitive-strcmp','sourcePathAreaBytes':256,
        'reviewInputPathBoundIsNativeBoundsCheck':False,
        'samePathProvesSameContent':False,'actualFileIdentityQualified':False,
        'actualGgpCacheContextQualified':False,'freshReadProven':False,**VERSION_GUARDS}


def _parse_command_catalog(language_blob: bytes):
    if not isinstance(language_blob,bytes) or hashlib.sha256(language_blob).hexdigest()!=REVIEWED_LANGUAGE_SHA256:
        raise ValueError('command-language-source-unreviewed')
    directory=parse_header(language_blob)['entries']
    source_selection=audit_gag_name_selection(directory,'COMMAND.GAG')
    entries=[e for e in directory if e['name_ascii']=='COMMAND.GAG']
    if len(entries)!=1:raise ValueError('command-gag-not-unique')
    entry=entries[0];raw=language_blob[entry['offset']:entry['offset']+entry['size']]
    if not source_selection['sourceExactBindingCandidate'] or source_selection['qualifiedSourceEntryCandidate']!=entry:
        raise ValueError('command-gag-native-name-selection-unqualified')
    if hashlib.sha256(raw).hexdigest()!=COMMAND_GAG_SHA256:raise ValueError('command-gag-source-unreviewed')
    st=parse_text_gag(raw)
    if (st['keylen'],st['count'])!=(4,3139):raise ValueError('command-gag-index-layout-unreviewed')
    keys=[raw[st['index_off']+5+i*8:st['index_off']+9+i*8] for i in range(st['count'])]
    if keys!=sorted(keys) or len(set(keys))!=len(keys):raise ValueError('command-index-order-or-duplicate')
    return entry,raw,st,source_selection,keys


def _lookup_command(language_blob: bytes, command_id: int) -> dict:
    if type(command_id) is not int or not 0<=command_id<=0xffffffff:
        raise ValueError('command-id-invalid')
    entry,raw,st,source_selection,keys=_parse_command_catalog(language_blob)
    needle=bytearray(command_id.to_bytes(4,'big'));encrypt_number_strict(needle,st['ctx'])
    lo=0;hi=len(keys)-1;found=None
    while lo<=hi:
        mid=(lo+hi)//2
        if needle<keys[mid]:hi=mid-1
        elif needle>keys[mid]:lo=mid+1
        else:found=mid;break
    out={'commandId':command_id,'commandIdHex':f'{command_id:08X}',
        'languageSourceSha256':REVIEWED_LANGUAGE_SHA256,'commandGagSha256':COMMAND_GAG_SHA256,
        'sourceFileSelectionAudit':source_selection,
        'commandGagSourceOffset':entry['offset'],'storedKeyHex':bytes(needle).hex(),
        'commandRecordIndex':found,'decodedPayloadHex':None,'recordFound':found is not None,
        'actualCommandLookupRuntimeQualified':False}
    if found is not None:
        key,body=decode_record(st,found)
        if key!=bytes(needle):raise ValueError('command-decoded-key-mismatch')
        out.update(decodedPayloadHex=body.hex(),decodedPayloadBytes=len(body),
                   decodedPayloadSha256=hashlib.sha256(body).hexdigest())
    return out


def _decode_command_records(language_blob: bytes):
    """Shared full-index decode for catalog and existing selector inspection."""
    entry, raw, state, selection, stored_keys = _parse_command_catalog(language_blob)
    if len(stored_keys) != state['count']:
        raise ValueError('command-catalog-index-count-mismatch')
    rows = []; seen = set()
    for index in range(len(stored_keys)):
        record = decode_record(state, index)
        if record is None:
            raise ValueError('command-catalog-record-missing')
        stored, body = record
        if stored != stored_keys[index]:
            raise ValueError('command-catalog-index-key-mismatch')
        key = bytearray(stored); encrypt_number_strict(key, state['ctx'])
        roundtrip = bytearray(key); encrypt_number_strict(roundtrip, state['ctx'])
        if bytes(roundtrip) != stored:
            raise ValueError('command-catalog-id-roundtrip-mismatch')
        command_id = int.from_bytes(key, 'big')
        if command_id in seen:
            raise ValueError('command-catalog-duplicate-id')
        seen.add(command_id)
        rows.append((index, stored, command_id, body))
    return {'languageSourceSha256': REVIEWED_LANGUAGE_SHA256, 'commandGagSha256': COMMAND_GAG_SHA256,
        'commandGagSourceOffset': entry['offset'], 'commandGagBytes': len(raw),
        'sourceFileSelectionAudit': selection}, rows


def decode_command_catalog(language_blob: bytes) -> dict:
    """Decode every retained descriptor by its own length, without native strlen.

    This is the project's source decoder, not an observation of IdSearch's
    heap or postprocessor. All commands, including writes and vendor-specific
    services, remain source data; neither flag nor SID grants execution.
    """
    source, decoded = _decode_command_records(language_blob); rows = []
    for index, stored, command_id, body in decoded:
        if body.startswith(INDIRECT_MARKER):
            raise ValueError('command-catalog-indirect-record-unresolved')
        if len(body) < 2 or len(body) != body[1]+2:
            raise ValueError('command-catalog-descriptor-length-mismatch')
        rows.append({'commandId': command_id, 'commandIdHex': f'{command_id:08X}',
            'recordIndex': index, 'storedKeyHex': stored.hex().upper(),
            'sourceDescriptorHex': body.hex().upper(),
            'sourceDescriptorSha256': hashlib.sha256(body).hexdigest(),
            'sourceFlagByte': body[0], 'sourceLengthByte': body[1],
            'sourceRequestHex': body[2:].hex().upper()})
    return {'kind': 'x431-project-command-catalog', **source, 'recordCount': len(rows),
        'flagCounts': dict(sorted(Counter(r['sourceFlagByte'] for r in rows).items())),
        'projectDescriptorDecoderComplete': True, 'decodingUsesExplicitRecordLength': True,
        'nativeStringTerminationAssumed': False, 'actualCommandLookupRuntimeQualified': False,
        'requestPacketBytesConstructed': False, 'commandExecutionAuthorized': False,
        'vehicleVerified': False, 'liveVerified': False, 'writePayload': None,
        'records': rows, **VERSION_GUARDS}


def audit_command_route_catalog(logical_blob: bytes, language_blob: bytes, family_ids: list) -> dict:
    """Join complete source command IDs to explicit SYS_DATA family selections.

    Preserve table order, duplicate requests, all pairs, unknown layouts and
    missing IDs. A family ID is a source selector, not a vehicle generation or
    a fitted ECU claim. No first matching pair or service sequence is chosen.
    """
    from scripts.x431_re.sysdata import parse_family_index, parse_payload
    if (not isinstance(logical_blob, bytes)
            or hashlib.sha256(logical_blob).hexdigest() != SYS_DATA_LOGICAL_SHA256):
        raise ValueError('communication-logical-source-unreviewed')
    if (type(family_ids) is not list or not family_ids or len(family_ids) > 255
            or any(type(v) is not int or not 0 <= v <= 255 for v in family_ids)
            or len(set(family_ids)) != len(family_ids)):
        raise ValueError('command-route-explicit-family-ids-required')
    present = [r['familyId'] for r in parse_family_index(logical_blob)]
    if any(v not in present for v in family_ids):
        raise ValueError('command-route-family-absent')
    catalog = decode_command_catalog(language_blob)
    by_id = {r['commandId']: r for r in catalog['records']}
    families = []; counts = Counter({key: 0 for key in ('systems', 'links', 'pairs',
        'commandReferences', 'resolvedCommandReferences', 'missingCommandReferences',
        'unresolvedFamilies', 'unresolvedLinks')}); referenced = set(); missing = set()
    for family_id in family_ids:
        try:
            parsed = parse_payload(logical_blob, family_id=family_id)
        except ValueError as error:
            families.append({'familyId': family_id, 'status': 'source-family-layout-unresolved',
                'reason': str(error), 'systems': []})
            counts['unresolvedFamilies'] += 1
            continue
        if not parsed.get('ok') or parsed.get('familyIdQueried') != family_id:
            raise ValueError('command-route-parser-family-mismatch')
        systems = []
        for system in parsed['systems']:
            if system['familyId'] != family_id:
                raise ValueError('command-route-system-family-mismatch')
            links = []; counts['systems'] += 1
            for link in system['links']:
                comm = link['comm']; counts['links'] += 1; pairs = []
                layout_complete = (comm.get('class') == 'static-source-confirmed'
                    and comm.get('consumedEnd') == link['serviceOff'])
                if not layout_complete:
                    counts['unresolvedLinks'] += 1
                # Unknown layouts must not contribute guessed parameter IDs.
                for pair_index, pair in enumerate(comm.get('pairs', []) if layout_complete else []):
                    counts['pairs'] += 1; commands = []
                    for request_index, command_id in enumerate(pair['params']):
                        counts['commandReferences'] += 1; record = by_id.get(command_id)
                        found = record is not None
                        counts['resolvedCommandReferences' if found else 'missingCommandReferences'] += 1
                        (referenced if found else missing).add(command_id)
                        commands.append({'requestIndex': request_index, 'commandId': command_id,
                            'commandIdHex': f'{command_id:08X}', 'recordFound': found,
                            'recordIndex': record['recordIndex'] if found else None,
                            'sourceDescriptorSha256': record['sourceDescriptorSha256'] if found else None,
                            'sourceFlagByte': record['sourceFlagByte'] if found else None,
                            'sourceRequestHex': record['sourceRequestHex'] if found else None})
                    pairs.append({'pairIndex': pair_index, 'storedTx': pair['storedTx'],
                        'storedRx': pair['storedRx'], 'subtypeByte': pair['mode'], 'commands': commands})
                links.append({'linkIndex': link['index'], 'communicationSourceOffset': link['commOff'],
                    'serviceSourceOffset': link['serviceOff'], 'modeByte': comm.get('protocolKind'),
                    'status': 'source-layout-decoded' if layout_complete else 'source-link-layout-unresolved',
                    'reason': None if layout_complete else comm.get('reason') or 'communication-trailing-bytes-unresolved',
                    'sourceVariantNames': [v['name'] for v in link['service']['variants']], 'pairs': pairs})
            systems.append({'systemKey': system['systemKey'], 'ecuId': system['ecuId'], 'links': links})
        families.append({'familyId': family_id, 'status': 'source-family-decoded', 'systems': systems})
    complete = not any(counts[k] for k in ('unresolvedFamilies', 'unresolvedLinks', 'missingCommandReferences'))
    return {'kind': 'x431-project-command-route-catalog', 'sysDataSourceSha256': SYS_DATA_LOGICAL_SHA256,
        'languageSourceSha256': REVIEWED_LANGUAGE_SHA256, 'commandGagSha256': COMMAND_GAG_SHA256,
        'commandCatalogRecords': catalog['recordCount'], 'selectedSourceFamilyIds': family_ids.copy(),
        'otherSourceFamilyIdsNotQueried': [v for v in present if v not in family_ids],
        'counts': dict(counts), 'uniqueResolvedCommandIds': len(referenced),
        'missingCommandIds': [f'{v:08X}' for v in sorted(missing)],
        'sourceReferenceJoinComplete': complete, 'families': families,
        'familyIdsProveVehicleGeneration': False, 'runtimePairOrCommandSequenceSelected': False,
        'nativeStringTerminationAssumed': False, 'actualCommandLookupRuntimeQualified': False,
        'requestPacketBytesConstructed': False, 'commandExecutionAuthorized': False,
        'vehicleVerified': False, 'liveVerified': False, 'writePayload': None, **VERSION_GUARDS}


def audit_source_command_context(logical_blob: bytes,language_blob: bytes,observation: dict) -> dict:
    from .command_lookup_buffers import audit_command_lookup_buffers
    fields={'familyId','systemKey','linkIndex','pairIndex','requestIndex'}
    if not isinstance(observation,dict) or set(observation) not in (fields,fields|{'searchBufferHex'},fields|{'lookupBufferObservation'}):
        raise ValueError('source-command-selection-fields-invalid')
    selection={key:observation[key] for key in fields}
    if any(type(value) is not int for value in selection.values()):raise ValueError('source-command-selection-integers-required')
    mode=audit_source_communication_context(logical_blob,{key:value for key,value in selection.items() if key!='requestIndex'})
    caller=audit_enter_sys_command_caller(mode['modeByte'],mode['subtypeByte'])
    context=mode['modeContextCandidate'];raw=bytes.fromhex(context['communicationRecordHex'])
    pair=parse_comm(raw,0,len(raw))['pairs'][selection['pairIndex']]
    index=selection['requestIndex']
    if not 0<=index<len(pair['params']):raise ValueError('source-command-request-index-out-of-range')
    lookup=_lookup_command(language_blob,pair['params'][index])
    out={'kind':'x431-offline-source-command-context-audit','fileSourceSha256':FILE_SHA256,
        'stdSourceSha256':STD_SHA256,'sourceLookupCallerEntry':0x17748,
        'enterSysCallerSourceAudit':caller,
        'sourceLookupCapacity':512,'sourceRequestFlagTokenOffset':0x957,
        'sourceRequestLengthTokenOffset':0x958,'sourceRequestBodyTokenOffset':0x959,
        'explicitSourceSelection':selection,'communicationSourceAudit':mode,'commandSourceAudit':lookup,
        'filterIdWordCandidate':((pair['storedTx']<<16)|(pair['storedRx']&65535))&0xffffffff,
        'postprocessingAudit':None,'nativeLookupBufferAudit':None,'requestFlagByteCandidate':None,'requestBytesCandidate':None,
        'requestHexCandidate':None,'rawDescriptorComplete':False,'nativeDescriptorCompleteCandidate':False,
        'extendedRequestLengthOriginQualified':False,'requestIndexActualSelectionQualified':False,
        'status':'source-command-id-not-found',**VERSION_GUARDS}
    buffer_audit=None
    if 'lookupBufferObservation' in observation:
        supplied=observation['lookupBufferObservation']
        allowed={'payloadReadReturnedBytes','idSearchReturnValue','searchBufferHex','scratchAtLookupReturnHex','callerAfterCopyHex',
                 'loaderSequenceObservation'}
        if not isinstance(supplied,dict) or 'payloadReadReturnedBytes' not in supplied or set(supplied)-allowed:
            raise ValueError('source-command-lookup-buffer-observation-invalid')
        buffer_audit=audit_command_lookup_buffers({**supplied,'lookupCapacity':512,
            'decodedPayloadHex':lookup.get('decodedPayloadHex') or '', 'recordFound':lookup['recordFound']})
        out['nativeLookupBufferAudit']=buffer_audit
    if not lookup['recordFound']:return out
    body=bytes.fromhex(lookup['decodedPayloadHex'])
    if buffer_audit is not None:
        post=buffer_audit['postprocessingAudit']
    else:
        post=audit_command_postprocessing({'decodedPayloadHex':lookup['decodedPayloadHex'],'lookupCapacity':512,
            **({'searchBufferHex':observation['searchBufferHex']} if 'searchBufferHex' in observation else {})})
    out.update(postprocessingAudit=post,status='source-command-descriptor-incomplete')
    if len(body)<2:return out
    flag,length=body[:2];complete=len(body)>=length+2
    out.update(requestFlagByteCandidate=flag,requestBytesCandidate=length,rawDescriptorComplete=complete,
        requestHexCandidate=body[2:2+length].hex() if complete else None,
        rawRecordExtraBytes=max(0,len(body)-length-2),
        nativeDescriptorCompleteCandidate=complete and post is not None and post['sourcePostprocessingComplete']
            and (buffer_audit is None or buffer_audit['lookupBufferChainCompleteCandidate']),
        status='source-command-descriptor-candidate' if complete else 'source-command-descriptor-incomplete')
    return out


def audit_source_command_response(logical_blob: bytes,language_blob: bytes,observation: dict) -> dict:
    if not isinstance(observation,dict) or set(observation)!={'commandSelection','responseObservation'}:
        raise ValueError('source-command-response-fields-invalid')
    command=audit_source_command_context(logical_blob,language_blob,observation['commandSelection'])
    if not command['enterSysCallerSourceAudit']['commandResponseAssociationAuditImplemented']:
        raise ValueError('source-command-response-mode-unreviewed')
    out={'kind':'x431-offline-source-command-response-audit','commandContextAudit':command,
        'responseOriginAudit':None,'status':'command-native-descriptor-source-incomplete',**VERSION_GUARDS}
    response=observation['responseObservation']
    if not isinstance(response,dict) or set(response)!={'requestHex','producerCallSite','communication','communicationStatusByte'}:
        raise ValueError('source-command-response-observation-required')
    supplied_request=_bytes(response['requestHex'],4)
    if len(supplied_request) not in (2,3,4):raise ValueError('source-command-response-request-size-invalid')
    if not command['rawDescriptorComplete']:return out
    hx=response.get('requestHex')
    if not isinstance(hx,str) or hx.lower()!=command['requestHexCandidate']:
        raise ValueError('source-command-response-request-mismatch')
    communication=response.get('communication')
    source=command['communicationSourceAudit']
    allowed=source['compatibleOfflineWrappers']
    if not isinstance(communication,dict) or communication.get('wrapper') not in allowed or 'proex' in communication['wrapper']:
        raise ValueError('source-command-response-wrapper-mismatch')
    if response['producerCallSite']!=command['enterSysCallerSourceAudit']['versionMapProducerCallSite']:
        raise ValueError('source-command-response-producer-site-mismatch')
    actual_context=command['communicationSourceAudit']['modeContextCandidate']
    if 'retryContext' in communication:
        if source['modeByte']==0x50:
            retry=communication['retryContext']
            if not isinstance(retry,dict) or retry.get('requestFlagByte')!=command['requestFlagByteCandidate']:
                raise ValueError('source-command-retry-context-mismatch')
        else:
            retry=audit_mode10_retry_context(communication['retryContext'])
            if (retry['requestFlagByte'],retry['filterIdWord'])!=(command['requestFlagByteCandidate'],command['filterIdWordCandidate']):
                raise ValueError('source-command-retry-context-mismatch')
            pair=retry['communicationPairSourceAudit']
            if pair is not None and any(pair[k]!=source[k] for k in ('communicationRecordSha256','explicitPairIndex')):
                raise ValueError('source-command-retry-source-pair-mismatch')
    if 'modeContext' in communication:
        mode=audit_communication_mode_context(communication['modeContext'],communication['wrapper'])
        if (mode['modeByte'],mode['subtypeByte'])!=(source['modeByte'],source['subtypeByte']):
            raise ValueError('source-command-mode-context-mismatch')
        if mode.get('communicationRecordSha256') is not None and any(mode[k]!=source[k] for k in ('communicationRecordSha256','explicitPairIndex')):
            raise ValueError('source-command-mode-context-mismatch')
    # Do not let a matching response repair an unobserved strlen/lookup exit.
    if not command['nativeDescriptorCompleteCandidate']:return out
    response={**response,'communication':{**communication,'modeContext':actual_context}}
    out.update(responseOriginAudit=audit_version_map_origin(response),status='source-command-response-candidate')
    return out


def audit_source_command_version_observations(definition: dict,observations: list,
                                             logical_blob: bytes,language_path: Path) -> dict:
    """Preserve command provenance while reusing the reviewed version calculator."""
    from .x431_variant_selection import audit_version_vectors,audit_version_observations
    if not isinstance(observations,list) or len(observations)>256:
        raise ValueError('source-command-version-observations-invalid')
    if not isinstance(logical_blob,bytes) or hashlib.sha256(logical_blob).hexdigest()!=SYS_DATA_LOGICAL_SHA256:
        raise ValueError('communication-logical-source-unreviewed')
    language_blob=language_path.read_bytes()
    if hashlib.sha256(language_blob).hexdigest()!=REVIEWED_LANGUAGE_SHA256:
        raise ValueError('command-language-source-unreviewed')
    allowed={group['requestCandidateHex'] for group in audit_version_vectors(definition,[])['requestGroups']}
    reports=[];qualified=[];seen=set();cohort=None
    for observation in observations:
        report=audit_source_command_response(logical_blob,language_blob,observation)
        hx=observation['responseObservation']['requestHex'].upper()
        if hx in seen:raise ValueError('source-command-version-duplicate-request')
        if hx not in allowed:raise ValueError('source-command-version-outside-definition')
        seen.add(hx);reports.append(report)
        selection=report['commandContextAudit'].get('explicitSourceSelection')
        fields=('familyId','systemKey','linkIndex','pairIndex')
        if (not isinstance(selection,dict) or any(key not in selection for key in fields)
                or any(type(selection[key]) is not int for key in fields)):
            raise ValueError('source-command-version-source-selection-required')
        source={key:selection[key] for key in fields}
        if cohort is None:cohort=source
        elif source!=cohort:raise ValueError('source-command-version-mixed-source-selection')
        if report['responseOriginAudit'] is not None:
            response=observation['responseObservation']
            context=report['commandContextAudit']['communicationSourceAudit']['modeContextCandidate']
            qualified.append({**response,'communication':{**response['communication'],'modeContext':context}})
    result=audit_version_observations(definition,qualified,language_path)
    result.update(kind='x431-offline-source-command-version-audit',commandOriginAudits=reports,
        inputSource='pinned-command-table-and-explicit-internal-response-observations',
        sourceSelectionCohort=cohort,sourceSelectionCohortConsistentCandidate=cohort is not None,
        actualTransportContinuityQualified=False,actualCommandLookupRuntimeQualified=False)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('postprocessing','lookup-buffers','request-staging','source-command','command-response',
        'command-catalog','route-catalog'),required=True)
    parser.add_argument('--observation-json',type=Path,required=True)
    parser.add_argument('--logical-sys-data',type=Path)
    parser.add_argument('--language-ggp',type=Path)
    args=parser.parse_args(argv)
    try:
        observation=json.loads(args.observation_json.read_text(encoding='utf-8-sig'))
        if args.kind in ('command-catalog', 'route-catalog'):
            fields = set() if args.kind == 'command-catalog' else {'familyIds'}
            if type(observation) is not dict or set(observation) != fields:
                raise ValueError('command-catalog-explicit-selection-required')
            if args.language_ggp is None or (args.kind == 'route-catalog' and args.logical_sys_data is None):
                raise ValueError('command-pinned-source-paths-required')
            if args.kind == 'command-catalog':
                if args.logical_sys_data is not None:
                    raise ValueError('command-catalog-logical-source-not-used')
                report = decode_command_catalog(args.language_ggp.read_bytes())
            else:
                report = audit_command_route_catalog(args.logical_sys_data.read_bytes(),
                    args.language_ggp.read_bytes(), observation['familyIds'])
        elif args.kind in ('postprocessing','lookup-buffers','request-staging'):
            if args.logical_sys_data is not None or args.language_ggp is not None:
                raise ValueError('command-postprocessing-source-paths-not-used')
            from .command_lookup_buffers import audit_command_lookup_buffers
            report={'postprocessing':audit_command_postprocessing,'lookup-buffers':audit_command_lookup_buffers,
                    'request-staging':audit_command_staging}[args.kind](observation)
        else:
            if args.logical_sys_data is None or args.language_ggp is None:raise ValueError('command-pinned-source-paths-required')
            audit=audit_source_command_context if args.kind=='source-command' else audit_source_command_response
            report=audit(args.logical_sys_data.read_bytes(),args.language_ggp.read_bytes(),observation)
    except (ValueError,OSError) as error:parser.exit(2,f'{error}\n')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
