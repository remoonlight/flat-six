"""Pinned 0x11320 request packing and receives, using explicit offline bytes.

Internal provider packets are source models, never CAN frames or executable
requests. No callbacks, vendor execution or vehicle qualification occurs.
"""
from __future__ import annotations

from .x431_response_contract import audit_internal_response, PORSCHE_COMM_SHA256
from .x431_version_response import VERSION_GUARDS, MODE50_WRAPPERS

WRAPPERS = MODE50_WRAPPERS
REQUEST_REGIONS = (
    (0x11320, 0x114da, 'b516fd9ab43ddbf961508eb5daa3eab08f8db98a9a1c96b9990cfbf4a1579631'),
    (0x114da, 0x114fa, '871b12756b3082187f655495b53ec83c16434271ef2c497a2729f5d3208badc7'),
)
TEMPLATE_SHA256 = '55cf24becbc6a1222782c8b85d192ee92089e512de9b38032ba6c87a1d219d40'
TEMPLATE = bytes.fromhex('01ff030d610108') + bytes(93)
RECEIVE_CLEAR_START = 0xc0 - 0x58
RECEIVE_CLEAR_END = RECEIVE_CLEAR_START + 2048


def audit_mode50_request_source(blob):
    """Pin the exact request, clear-before-provider and constant source bytes."""
    import hashlib
    if type(blob) is not bytes or len(blob) != 210456 or hashlib.sha256(blob).hexdigest() != PORSCHE_COMM_SHA256:
        raise ValueError('mode50-request-source-unreviewed')
    from .x431_native_binding import _Elf
    elf = _Elf(blob)
    for start, end, digest in REQUEST_REGIONS:
        if hashlib.sha256(elf.raw(start, end-start)).hexdigest() != digest:
            raise ValueError('mode50-request-region-unreviewed')
    if elf.raw(0x2f6e8, 100) != TEMPLATE or hashlib.sha256(TEMPLATE).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('mode50-request-template-unreviewed')
    if elf.relocations[0x331a0]['symbol']['name'] != 'iFilterId':
        raise ValueError('mode50-request-filter-object-unreviewed')
    return dict(commSourceSha256=PORSCHE_COMM_SHA256, requestRegions=[
        dict(startVa=a,endVa=z,sha256=h) for a,z,h in REQUEST_REGIONS],
        templateVa=0x2f6e8, templateBytes=100, templateSha256=TEMPLATE_SHA256,
        sourceAudited=True, nativeOriginalInstructionsExecuted=False, **VERSION_GUARDS)


def audit_mode50_request_packing(request_bytes, extended_request_byte=0):
    if any(type(v) is not int or not 0 <= v <= 255 for v in (request_bytes, extended_request_byte)):
        raise ValueError('mode50-request-length-bytes-required')
    effective = request_bytes | (extended_request_byte << 8)
    chunks = (effective+6)//6 if effective > 7 else 0
    return {'kind': 'x431-offline-mode50-request-length-audit', 'commSourceSha256': PORSCHE_COMM_SHA256,
        'requestLengthOffset': 0x31c, 'extendedRequestLengthOffset': 0x232d,
        'effectiveRequestBytesCandidate': effective, 'sourceChunkCountCandidate': chunks,
        'sourceChunkDataBytes': 6, 'sourceChunkEnvelopeBytes': 14,
        'sourceChunkCountByteCandidate': chunks & 255 if chunks else None,
        'providerCommandBytesCandidate': (chunks & 255)*14+3 if chunks else 17,
        'sourceChunkCounterWrapped': chunks > 255,
        'requestPacketSourceModelImplemented': True,
        'sourceReceiveClearRelativeOffset': RECEIVE_CLEAR_START,
        'sourceReceiveClearBytes': 2048,
        'sourceSevenByteRequestProviderDropsLastByte': True,
        'providerExportCandidate': 'CommSubFunc0001', 'providerObjectSymbol': 'CommSubFunc_00000001',
        'providerObjectVa': 0x346a0, 'providerCallSite': 0x114fa, 'providerCapacity': 2048,
        'providerPicLoadVa': 0x114ce, 'providerPicAddVa': 0x114d0, 'providerGotVa': 0x331e0,
        'sourceReceiveRegionStartVa': 0x114da, 'sourceReceiveRegionEndVa': 0x11606,
        'sourceReceiveRegionSha256': '20a89fd29f958de2bdfb20992f9ac0ea1b094d636eb5731ecba025e37d26bd47',
        'providerRuntimeBindingQualified': False, 'nativeRequestScratchBoundsQualified': False,
        'requestPacketBytesConstructed': False, **VERSION_GUARDS}


def audit_mode50_request_packet(observation):
    """Model only explicit source memory and globals, including native data loss.

    requestAreaHex starts at communication-config+0x31c (main TOKEN+0x958
    along PorscheEnterSys): one low length byte, then the complete declared
    payload. The extended byte and two source words are mandatory;
    none are inferred from an ECU, CAN identifier or source communication pair.
    """
    from .x431_version_response import _hex
    fields = {'requestAreaHex', 'extendedRequestByte', 'filterWord', 'channelWord'}
    if not isinstance(observation, dict) or set(observation) != fields:
        raise ValueError('mode50-request-packet-fields-invalid')
    extension = observation['extendedRequestByte']
    words = [observation[key] for key in ('filterWord','channelWord')]
    if type(extension) is not int or not 0 <= extension <= 255 or any(
            type(value) is not int or not 0 <= value <= 0xffffffff for value in words):
        raise ValueError('mode50-request-source-integers-invalid')
    area = _hex(observation['requestAreaHex'], 1, 65536)
    packing = audit_mode50_request_packing(area[0], extension)
    length = packing['effectiveRequestBytesCandidate']
    if len(area) < length+1:
        raise ValueError('mode50-request-source-extent-incomplete')
    count = packing['sourceChunkCountByteCandidate']
    source_word, channel = words
    prefix = bytes.fromhex('0d610108') + bytes((source_word >> 24, (source_word >> 16) & 255, channel & 255))
    positions = []
    if length <= 7:
        scratch = bytearray(TEMPLATE)
        scratch[9:99] = b'\xaa'*90
        scratch[7:10] = prefix[4:]
        scratch[10:11+length] = area[:length+1]
        before = bytes(scratch[:17])
        positions = [10+i for i in range(1,length+1) if 10+i < 17]
    else:
        scratch = bytearray((count,0xff,3))
        for index in range(count):
            start = index*6
            copied = min(6,length-start+1)
            data = area[start:start+copied] + bytes(6-copied)
            sequence = ((0x10+extension) if index == 0 else 0x20+index) & 255
            positions.extend(len(scratch)+8+i for i in range(copied) if 0 < start+i <= length)
            scratch.extend(prefix + bytes((sequence,)) + data)
        before = bytes(scratch)
    assert len(before) == packing['providerCommandBytesCandidate']
    after = bytearray(before)
    cleared = max(0,min(len(after),RECEIVE_CLEAR_END)-RECEIVE_CLEAR_START)
    if cleared:
        after[RECEIVE_CLEAR_START:RECEIVE_CLEAR_START+cleared] = bytes(cleared)
    guard_overwritten = len(before) > RECEIVE_CLEAR_END
    preserved = sum(not RECEIVE_CLEAR_START <= p < RECEIVE_CLEAR_END for p in positions)
    complete = preserved == length and not guard_overwritten and not packing['sourceChunkCounterWrapped']
    status = ('source-command-overwrites-stack-guard' if guard_overwritten else
              'source-chunk-counter-wraps' if packing['sourceChunkCounterWrapped'] else
              'source-receive-clear-overwrites-command' if cleared else
              'source-provider-length-truncates-payload' if not complete else 'source-request-packet-candidate')
    return dict(kind='x431-offline-mode50-request-packet-audit', requestPackingAudit=packing,
        commSourceSha256=PORSCHE_COMM_SHA256, sourceTemplateSha256=TEMPLATE_SHA256,
        sourceFilterWord=source_word, sourceChannelWord=channel,
        filterWordOriginQualified=False, channelWordOriginQualified=False,
        sourcePayloadBytes=length, suppliedSourceBytes=len(area), sourceTrailingBytesIgnored=len(area)-length-1,
        sourcePayloadBytesRepresentedBeforeClear=len(positions),
        sourcePayloadBytesPreservedAfterClear=preserved if not guard_overwritten else None,
        sourceReceiveClearOverwritesCommandBytes=cleared, sourceCommandOverwritesStackGuard=guard_overwritten,
        sourceCommandBeforeReceiveClearHex=before.hex().upper() if not guard_overwritten else None,
        sourceCommandAfterReceiveClearHex=after.hex().upper() if not guard_overwritten else None,
        sourcePayloadFullyRepresented=complete,
        providerCommandHexCandidate=after.hex().upper() if complete else None,
        requestPacketBytesConstructed=complete, status=status,
        providerRuntimeBindingQualified=False, nativeRequestScratchBoundsQualified=False,
        canFrameLayoutQualified=False, nativeOriginalInstructionsExecuted=False, **VERSION_GUARDS)


def audit_mode50_request_source_context(observation):
    """Trace explicit common-record fields to the source packet model only.

    GetCommData overwrites one shared +0x2964 slot for every mode-5 pair;
    selection of an earlier pair does not select that pair's channel word.
    Neither the selected vehicle pair nor intervening callback state is proven.
    """
    from .x431_version_response import _hex, audit_communication_pair_context, FILE_SHA256, SYSP_SHA256
    from scripts.x431_re.sysdata import parse_comm
    fields = {'requestAreaHex', 'extendedRequestByte', 'communicationRecordHex', 'pairIndex'}
    if not isinstance(observation, dict) or set(observation) != fields:
        raise ValueError('mode50-request-source-context-fields-invalid')
    context = audit_communication_pair_context(observation['communicationRecordHex'],
        observation['pairIndex'], WRAPPERS[0])
    raw = _hex(observation['communicationRecordHex'], 1, 65536)
    record = parse_comm(raw, 0, len(raw)); selected = record['pairs'][observation['pairIndex']]
    writers = [(index,pair) for index,pair in enumerate(record['pairs']) if pair['mode'] == 5]
    channel_index, channel_pair = writers[-1]
    filter_word = ((selected['storedTx'] << 16) | (selected['storedRx'] & 65535)) & 0xffffffff
    channel_word = channel_pair['mode5Extra']
    packet = audit_mode50_request_packet(dict(requestAreaHex=observation['requestAreaHex'],
        extendedRequestByte=observation['extendedRequestByte'], filterWord=filter_word, channelWord=channel_word))
    return dict(kind='x431-offline-mode50-request-source-context-audit',
        fileSourceSha256=FILE_SHA256, syspSourceSha256=SYSP_SHA256, commSourceSha256=PORSCHE_COMM_SHA256,
        communicationPairSourceAudit=context, requestPacketAudit=packet,
        sourceFilterWordCandidate=filter_word, sourceChannelWordCandidate=channel_word,
        sourceFilterWordRule='((selected.storedTx << 16) | (selected.storedRx & 0xffff)) & 0xffffffff',
        sourceFilterTokenOffset=0x65c+4*observation['pairIndex'],
        sourceFilterSetterEntry=0x12238, sourceFilterSetterCallSite=0x19eec,
        sourceChannelWordRule='last mode-5 pair overwrites the shared token slot',
        sourceChannelWriterPairIndices=[index for index,pair in writers],
        sourceChannelWriterPairIndex=channel_index, sourceChannelSharedAcrossPairs=True,
        communicationConfigurationBaseTokenOffset=0x63c,
        sourceRequestLengthConfigOffset=0x31c, sourceRequestLengthTokenOffset=0x958,
        sourceExtendedLengthConfigOffset=0x232d, sourceExtendedLengthTokenOffset=0x2969,
        sourceChannelConfigOffset=0x2328, sourceChannelTokenOffset=0x2964,
        sourceChannelStoreCallSite=0x15a10, sourceChannelStoreHasPairIndex=False,
        sourceCommunicationConfigArgumentSite=0x19f48, sourceCommunicationCallSite=0x19f4e,
        staticSourceFieldAssignmentsVerified=True, actualFilterSetterCallProven=False,
        actualTokenPointersAliasProven=False, runtimeSourceWordsUnchangedProven=False,
        fullInitializationConfigurationReconstructed=False, initializerSideEffectsQualified=False,
        canFrameLayoutQualified=False, providerRuntimeBindingQualified=False,
        nativeOriginalInstructionsExecuted=False, **VERSION_GUARDS)


def audit_mode50_initialization(observation):
    """Pinned CAN initializer's mode-50/subtype-5 branch, no driver execution.

    A complete explicit configuration and ordered raw ABI return observations
    are required. Unchecked calls remain unchecked source behavior, not success.
    Even source return 1 does not qualify any callback, head or vehicle state.
    """
    import hashlib
    from .x431_version_response import _hex
    if not isinstance(observation,dict) or set(observation)!={'configurationHex','mainTokenFields','calls'}:
        raise ValueError('mode50-initialization-fields-invalid')
    config=_hex(observation['configurationHex'],0x2358,0x2358)
    if (config[1],config[0x119])!=(0x50,5):
        raise ValueError('mode50-initialization-branch-unreviewed')
    main=observation['mainTokenFields']
    limits={'systemModeWord':0xffffffff,'protocolKindByte':255,'ecuSelectorHalfword':65535}
    if not isinstance(main,dict) or set(main)!=set(limits) or any(
            type(main[k]) is not int or not 0<=main[k]<=maximum for k,maximum in limits.items()):
        raise ValueError('mode50-initialization-main-fields-required')
    calls=observation['calls']
    if not isinstance(calls,list) or len(calls)>16 or any(not isinstance(c,dict)
            or set(c)!={'callSite','returnWord'} or any(type(c[k]) is not int for k in c)
            or not 0<=c['returnWord']<=0xffffffff for c in calls):
        raise ValueError('mode50-initialization-call-observations-invalid')
    filter_offset=0x1c+4*config[0x12]
    filter_word=int.from_bytes(config[filter_offset:filter_offset+4],'little')
    channel_word=int.from_bytes(config[0x2328:0x232c],'little')
    enter=bytearray(bytes.fromhex('55aa0d610108')+bytes(11))
    enter[6:12]=bytes((filter_word>>24,(filter_word>>16)&255,channel_word&255,0x30,0x10,5))
    enter[12:16]=b'\xaa'*4
    for value in enter[2:16]:enter[16]^=value
    link_source=bytes.fromhex('02027e0000ffffff00017e0000ffff0000000000')
    link=bytearray(bytes.fromhex('55aa1860190002')+bytes(21))
    for index in range(2):link[9+10*index:17+10*index]=link_source[1+8*index:9+8*index]
    for value in link[2:27]:link[27]^=value
    normal=bytes(8)+bytes((config[5],1,(filter_word>>8)&255,filter_word&255))
    result=dict(kind='x431-offline-mode50-can-initialization-audit',commSourceSha256=PORSCHE_COMM_SHA256,
        initializerEntry=0x11c30,initializerSha256='d3d92a2becffb770545d510b02fba9f8397118c8d5e501eb3c218744e6c7f341',
        configurationBytes=len(config),configurationSha256=hashlib.sha256(config).hexdigest(),
        sourceConfigurationCopyBytes=0x2358,sourceConfigurationDestinationTokenOffset=0x63c,
        sourceDestinationPointerSlot=0x34018,actualDestinationPointerAliasProven=False,
        mainTokenFields=dict(main),mainTokenFieldOffsets=dict(systemModeWord=0x4089d0,protocolKindByte=0x63d,ecuSelectorHalfword=0x24),
        mainTokenAndConfigurationAliasProven=False,fullMainTokenMemoryQualified=False,runtimeSourceWordsUnchangedProven=False,
        sourceFilterConfigOffset=filter_offset,sourceFilterIndexByte=config[0x12],sourceInitialFilterWordCandidate=filter_word,
        sourceFilterIndexIsSelectedPairProven=False,sourceChannelWordCandidate=channel_word,
        sourceNormalParameterWrittenPrefixHexCandidate=normal.hex().upper(),sourceNormalParameterFullArgumentExtentQualified=False,
        sourceEnterFrameHexCandidate=enter.hex().upper(),sourceLinkFilterCommandHexCandidate=link.hex().upper(),
        sourceLinkFilterTemplateVa=0x2a1a8,sourceLinkFilterProviderCallSite=0x1220a,
        sourceLinkFilterReturnCheckedByInitializer=False,sourceAllDriverReturnsChecked=False,
        sourceCallObservationScope='initializer direct calls and the nested link-filter provider; loader internals not expanded',
        sourceSuccessImpliesCompleteDriverConfiguration=False,sourceFilterAssignedBeforeNormalParameterReturn=False,
        sourceLinkFilterReturnCandidate=None,sourceLinkFilterFailureSuppressed=False,
        sourceInitializationReturnCandidate=None,sourceCallSequenceComplete=False,calls=[],nextExpectedCallSite=None,
        nativeOriginalInstructionsExecuted=False,actualInitializationConfigurationOriginProven=False,
        runtimeCallbacksQualified=False,headConfigurationQualified=False,canFrameLayoutQualified=False,**VERSION_GUARDS)
    class NeedObservation(Exception):pass
    class SourceReturn(Exception):
        def __init__(self,value):self.value=value
    position=0
    def call(site,name,args,check=None,command=None):
        nonlocal position
        result['nextExpectedCallSite']=site
        if position==len(calls):raise NeedObservation()
        row=calls[position]
        if row['callSite']!=site:raise ValueError('mode50-initialization-call-order-mismatch')
        position+=1;word=row['returnWord']
        result['calls'].append(dict(callSite=site,symbol=name,
            nonPointerArgumentWordCandidates={str(k):v for k,v in (args.items() if isinstance(args,dict) else enumerate(args))},
            actualArgumentValuesQualified=False,returnWord=word,
            sourceReturnCheck=check,sourceReturnChecked=check is not None,sourceArgumentDataHexCandidate=command,
            sourceArgumentDataExtentQualified=command is not None and site!=0x11e8a,
            callSuccessInterpretationQualified=False))
        return word
    try:
        call(0x11c76,'OpenAbstractCommDll',[])
        if call(0x11c92,'SelectPIN',[config[0],14,0,0,0],'all-32-bits-zero')!=0:raise SourceReturn(-1)
        first=call(0x11ca6,'GetDBSCarType',[],'low-16-bits-equal-2')&65535
        special=first==2
        if not special:special=(call(0x11cb8,'GetDBSCarType',[],'low-16-bits-equal-10')&65535)==10
        five=config[5]==5
        if call(0x11d12,'SelectPIN',[config[0],0x21 if special else 3,0,3 if five else 6,11 if five else 14],
                'all-32-bits-zero')!=0:raise SourceReturn(-2)
        call(0x11d26,'BeginCombination',[])
        call(0x11d36,'SetCommunicationMaximumWaitTime',[0x4650])
        call(0x11d46,'SetEcuCommunicationVoltageLogic',[0,255])
        call(0x11d58,'SetEcuBaudRate',[0x47610000])
        call(0x11d6c,'SetCommunicationParameter',[1,8,1,0])
        interval=0x32
        if main['systemModeWord']==7 and main['protocolKindByte']==0x20:
            interval=15 if main['ecuSelectorHalfword']&0xff00==0x300 else 5
        call(0x11dbe,'SetEcuCommTimeInterval',[0,interval,0x640])
        call(0x11dc8,'EndCombination',[])
        result['sourceFilterAssignedBeforeNormalParameterReturn']=True
        if call(0x11e8a,'CanSetParameterNormal',[config[0]],'all-32-bits-zero',normal.hex().upper())!=0:
            raise SourceReturn(-3)
        if call(0x11fda,'SetEnterFrame',{1:17},'low-16-bits-zero',enter.hex().upper())&65535:
            raise SourceReturn(-5)
        # This is the nested helper's actual lower driver call, not a made-up
        # initializer return observation. CMP/IT GT yields the helper boolean.
        word=call(0x1220a,'OneToOne_SendDataToEcuGetAnswer',{0:255,2:80,3:1,4:28,6:0,7:2,8:4},
            'signed-32-bits-positive-produces-helper-one',link.hex().upper())
        helper=int(0<word<0x80000000)
        result.update(sourceLinkFilterReturnCandidate=helper,sourceLinkFilterFailureSuppressed=helper==0)
        raise SourceReturn(1)
    except NeedObservation:
        result['status']='additional-initialization-call-observations-required'
    except SourceReturn as terminal:
        if position!=len(calls):raise ValueError('mode50-initialization-call-after-terminal')
        result.update(sourceInitializationReturnCandidate=terminal.value,sourceCallSequenceComplete=True,
            nextExpectedCallSite=None,status='source-initializer-return-candidate')
    return result


def _base(observation, sequence):
    fields = {'requestBytes', 'extendedRequestByte', 'receives', 'retryContext'} if sequence else {
        'requestBytes', 'extendedRequestByte', 'internal'}
    if not isinstance(observation, dict) or set(observation) != fields:
        raise ValueError('mode50-response-fields-invalid')
    packing = audit_mode50_request_packing(observation['requestBytes'], observation['extendedRequestByte'])
    return {'kind': 'x431-offline-mode50-receive-sequence-audit' if sequence else 'x431-offline-mode50-response-audit',
        'commSourceSha256': PORSCHE_COMM_SHA256, 'wrapperContext': WRAPPERS[1 if sequence else 0],
        'responseEntry': 0x11320, 'requestPackingAudit': packing, 'providerCapacity': 2048,
        'nativeReturnCandidate': None, 'communicationStatusByteCandidate': None,
        'sourceWrittenPrefixHex': None, 'sourceOutputComplete': False,
        'receiveAttemptsRequired': False, 'sourceReceiveSequenceComplete': False,
        'status': 'mode50-internal-receive-unqualified', 'actualReceiveCleanupProven': False,
        'providerRuntimeBindingQualified': False, **VERSION_GUARDS}


def _lower(internal):
    if not isinstance(internal, dict) or type(internal.get('capacity')) is not int or internal['capacity'] != 2048:
        raise ValueError('mode50-provider-capacity-mismatch')
    return audit_internal_response(internal)


def _frame(lower, out):
    if not lower['internalBufferComplete']:
        return None
    frame = bytes.fromhex(lower['internalBufferHexCandidate'])
    if len(frame) < 11:
        out['status'] = 'mode50-response-header-incomplete';return None
    out.update(frameCountCandidate=int.from_bytes(frame[:2], 'big'), firstLengthByte=frame[9],
               firstServiceByteCandidate=frame[10])
    if frame[10] == 0x7f:
        if len(frame) < 13:
            out['status'] = 'mode50-negative-response-code-incomplete';return None
        out['negativeResponseCodeCandidate'] = frame[12]
    return frame


def _copy(frame, out):
    count = int.from_bytes(frame[:2], 'big')
    if count == 0:
        out['status'] = 'mode50-zero-frame-layout-unqualified';return
    declared = frame[9] if count == 1 else ((frame[9] << 8) & 0xf00) | frame[10]
    copied = max(0, declared-1);start = 11 if count == 1 else 12
    out.update(declaredServiceAndBodyBytes=declared, sourceBodyOffset=start,
               sourceCopiedBodyBytesCandidate=copied, removedServiceBytesCandidate=1)
    if copied and start+copied > len(frame):
        out['status'] = 'mode50-declared-body-incomplete';return
    out.update(sourceWrittenPrefixHex=frame[start:start+copied].hex(), sourceOutputComplete=True,
               nativeReturnCandidate=(copied if count == 1 else declared-1),
               status='mode50-source-written-body-candidate' if copied else 'mode50-zero-body-count')


def audit_mode50_response(observation):
    """One complete positive receive only; negatives require sequence context."""
    out = _base(observation, False);lower = _lower(observation['internal'])
    out['lowerResponse'] = lower;frame = _frame(lower, out)
    if frame is None:return out
    if frame[10] == 0x7f:
        out.update(receiveAttemptsRequired=True, status='mode50-additional-receive-attempts-unqualified')
        return out
    out.update(communicationStatusByteCandidate=0x7e, sourceReceiveSequenceComplete=True)
    _copy(frame, out)
    return out


def audit_mode50_receive_sequence(observation):
    """Two outer attempts, each with up to five 21/23 receives; no replay.

    Exhaustion can return the request-envelope count with no copied response.
    Keep that count and empty fresh extent separate; never pad a version vector.
    """
    out = _base(observation, True);context = observation['retryContext']
    if not isinstance(context, dict) or set(context) != {'requestFlagByte', 'initialCommunicationStatusByte'}:
        raise ValueError('mode50-retry-context-fields-invalid')
    if any(type(v) is not int or not 0 <= v <= 255 for v in context.values()):
        raise ValueError('mode50-retry-context-bytes-required')
    receives = observation['receives']
    if not isinstance(receives, list) or not 1 <= len(receives) <= 10:
        raise ValueError('mode50-receive-count-invalid')
    out.update(retryContextAudit=dict(context), sourceOuterAttemptLimit=2, sourceInnerRetryLimit=5,
               sourceRetryDelayMicroseconds=1000000, receives=[])
    outer = inner = 0;status = context['initialCommunicationStatusByte'];terminal = False
    for index, internal in enumerate(receives):
        if terminal:raise ValueError('mode50-receive-after-terminal')
        lower = _lower(internal)
        step = {'outerAttemptIndex': outer, 'innerAttemptIndex': inner, 'lowerResponse': lower}
        out['receives'].append(step)
        native = lower['nativeReturnCandidate']
        if native is not None and native < 0:
            if context['requestFlagByte'] == 0:
                out.update(nativeReturnCandidate=0, sourceWrittenPrefixHex='', sourceOutputComplete=True,
                           status='mode50-negative-provider-flag-zero-return');terminal = True
                continue
            outer += 1;inner = 0
            if outer == 2:
                out.update(nativeReturnCandidate=-1, sourceWrittenPrefixHex='', sourceOutputComplete=True,
                           status='mode50-negative-provider-exhausted');terminal = True
            continue
        frame = _frame(lower, step)
        if frame is None:
            out['status'] = step.get('status', 'mode50-internal-receive-unqualified');return out
        if frame[10] != 0x7f:
            status = 0x7e;_copy(frame, out);terminal = True
            continue
        code = frame[12];status = 0x7e if code == 0x78 else 0x7f
        if code in (0x21, 0x23):
            inner += 1
            if inner < 5:continue
        outer += 1;inner = 0
        if outer == 2:
            out.update(nativeReturnCandidate=out['requestPackingAudit']['providerCommandBytesCandidate'],
                       sourceWrittenPrefixHex='', sourceOutputComplete=True,
                       status='mode50-negative-response-exhausted-no-body-copy');terminal = True
    out.update(communicationStatusByteCandidate=status, sourceReceiveSequenceComplete=terminal,
               receiveAttemptsRequired=not terminal)
    if not terminal:out['status'] = 'mode50-additional-receive-attempts-unqualified'
    return out
