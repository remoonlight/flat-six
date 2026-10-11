"""Offline source initialization/session sequences versus anchored X431 frames.

Source table order is not a chosen runtime procedure. Keep every host command
as a boundary, including unknown/configuration traffic, to prevent false joins.
No transport, native loader, ECU identity promotion or vehicle write is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .identity_capture import FLAGS, AUDIT_SHA, SUMMARY_SHA, reference_routes, source_profiles
from .offline_match import ANCHORS, verify_frames, verify_source
from .x431_capture import envelope, extract_return
from .x431_command_origin import _lookup_command
from .x431_variant_selection import REVIEWED_LANGUAGE_SHA256

GUARDS = {**FLAGS, 'actualInitializationQualified': False, 'actualSessionQualified': False,
          'actualLinkPairSelected': False, 'diagnosticSequenceQualified': False,
          'nativeCodeExecuted': False}
OBSERVED_SIDS = frozenset({0x10, 0x18, 0x19, 0x1A, 0x21, 0x22, 0x3E})


def sha(data):
    return hashlib.sha256(data).hexdigest()


CHANNEL_DESCRIPTORS = {
    0x1A71: 'd9d82b98796aa6278614214ee1466b1cf3a7707656ea587fda8919f639f91c00',
    0x1A82: 'd9d82b98796aa6278614214ee1466b1cf3a7707656ea587fda8919f639f91c00',
    0x3001B2: '879a25b5d7bceb62ae5f6d2c7a57a83adf79663037c89404f8ffc278771d2206',
    0x3001B3: 'ca329f7da060572def3cbf4c4e88ab1bc1c098eeb1c9dc0d4431c8edbd00ed27',
    0x3001B4: 'f0fd11ca7031b7af87358c92867d0d36f34199407e39769933fe5f89b08d2c16',
    0x3001B5: '41d39c6321f786a5327d1907d587dfdb2f475251dc198e6a0a133af415517066',
}
PROTOCOL_REFERENCE = {
    'url': 'https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/blob/76c4637ede9985ee6c1d14268c5a36b7e6ed9883/vehicle/OVMS.V3/components/poller/src/vehicle_poller_vwtp.cpp',
    'sha256': 'd3bc0a0377e85abd79a18d0bb549ebf3cbb1f4a65b2851a2830670101de20a7f',
    'scope': 'independent protocol-shape reference; no Porsche runtime or adapter qualification',
}


def mode30_entry_source(sysp_blob):
    """Pin the caller algorithm; this neither chooses a route nor runs native code."""
    import struct
    from .x431_native_binding import _Elf
    from .x431_version_response import SYSP_SHA256
    if type(sysp_blob) is not bytes or sha(sysp_blob)!=SYSP_SHA256:
        raise ValueError('session-entry-original-unreviewed')
    elf=_Elf(sysp_blob)
    symbols=[s for s in elf.symbols if s['name']=='PorscheEnterSys' and s['type']==2]
    digest='c541449aa2ee70b3a6303cd2109e19237ff8bcb279ee62410d0be97a8b14e582'
    if (len(symbols)!=1 or symbols[0]['va']!=0x19921 or symbols[0]['bytes']!=3232
            or sha(elf.raw(0x19920,3232))!=digest):
        raise ValueError('session-entry-region-unreviewed')
    bindings=[]
    for name,load,add,slot in [('UpdateCanFilterId',0x19b52,0x19b56,0x28),
            ('PorscheCanInitialize',0x19b66,0x19b6a,4),
            ('PorscheCommunicationPro',0x19b7a,0x19b7e,0x8c),
            ('FromCmdIdToCmdBuff',0x19b98,0x19b9c,0xb8)]:
        first,second=struct.unpack('<HH',elf.raw(load,4))
        if first!=0xf8df or second>>12!=0 or elf.raw(add,2)!=b'\x78\x44':
            raise ValueError('session-entry-wide-pic-instruction-unreviewed')
        literal=((load+4)&~3)+(second&0xfff)
        got=(struct.unpack('<I',elf.raw(literal,4))[0]+add+4)&0xffffffff
        rel=elf.relocations.get(got);symbol=(rel or {}).get('symbol',{})
        if not rel or rel['type']!=21 or symbol.get('name')!=name or symbol.get('type')!=1 or symbol.get('bytes')!=4:
            raise ValueError('session-entry-binding-unreviewed')
        bindings.append(dict(name=name,loadVa=load,addVa=add,gotVa=got,objectVa=symbol['va'],stackSlot=slot))
    return dict(syspSourceSha256=SYSP_SHA256,entry=0x19920,bytes=3232,sha256=digest,bindings=bindings,
        reviewedPhaseEntry=0x19bfc,reviewedPhaseEndExclusive=0x19dd8,
        modeByte=0x30,lookupBeforeAllCommunication=True,lookupReturnChecked=False,
        scratchPairStride=400,scratchCommandStride=20,filterUpdateCallSite=0x19ce8,
        initializationCallSite=0x19d08,initializeOnlyFirstPair=True,initializerZeroContinues=True,
        initializerNegativeExitsPhase=True,
        communicationCallSite=0x19d70,communicationZeroContinues=True,
        negativeCommunicationNextAction='source-next-link-decision-at-0x1a3a0',
        successfulPhaseNextAction='password-and-postprocessing-outside-reviewed-phase',
        mutationBeforePairIndex=1,mutationReadsTokenOffsets=[0x1967,0x1968],
        mutationWritesTokenOffsets=[0x660,0x664],mutationPreservesLow16FromTokenOffset=0x660,
        mutationShift=21,mutationResultBits=32,mutationHasLocalFreshLengthCheck=False,
        responseStatusTokenOffset=0x1961,responseLengthTokenOffset=0x1962,responseBodyTokenOffset=0x1963,
        addressWordBodyOffsets=[4,5],minimumFreshBodyBytesForAddressWord=6,
        minimumFreshInternalBytesForAddressWord=14,minimumExtentIsNotNegotiationQualification=True,
        actualNegotiatedChannelQualified=False,actualCallbackBindingQualified=False,**GUARDS)


def exit_source(comm_blob, sysp_blob, file_blob):
    """Pin exit/keep-stop conditions; native callbacks and close remain unknown."""
    from .x431_native_binding import _Elf
    from .x431_response_contract import PORSCHE_COMM_SHA256
    from .x431_version_response import SYSP_SHA256, FILE_SHA256
    blobs={'comm':comm_blob,'sysp':sysp_blob,'file':file_blob}
    hashes={'comm':PORSCHE_COMM_SHA256,'sysp':SYSP_SHA256,'file':FILE_SHA256}
    for name,blob in blobs.items():
        if type(blob) is not bytes or sha(blob)!=hashes[name]:
            raise ValueError('session-exit-'+name+'-original-unreviewed')
    elves={name:_Elf(blob) for name,blob in blobs.items()}
    regions=[]
    for lib,name,start,size,digest in [
        ('sysp','PorscheExitSys',0x1e810,180,'a6dd12882d842f970f367becbea03b69e6dbbdd5cf1c61c82de54eb3a1508abb'),
        ('sysp','PorscheExitSysPlanB',0x1e8c4,180,'717b2257857dde978f7844b26f3fcebfe1a404dc6a71a3fd95b69ce825560042'),
        ('comm','PorscheSetLinkKeep',0x12324,1200,'9185729200e5cd5eb24ce9ee55fa39bb7c7bedc6abdc99e35fe454e083f54c94'),
        ('file','GetCommData',0x15580,1688,'a7149cf6f9ea466e0989872877b938083bca578c0872e706bca5ad43c9eb7b22')]:
        elf=elves[lib];symbols=[s for s in elf.symbols if s['name']==name and s['type']==2]
        if (len(symbols)!=1 or symbols[0]['va']!=start|1 or symbols[0]['bytes']!=size
                or sha(elf.raw(start,size))!=digest):
            raise ValueError('session-exit-region-unreviewed')
        regions.append(dict(library=lib,name=name,entry=start,bytes=size,sha256=digest))
    sites=[]
    for entry in [0x1e810,0x1e8c4]:
        sites += [('sysp',entry+l,entry+a,name) for l,a,name in [
            (8,10,'token'),(22,34,'FromCmdIdToCmdBuff'),(40,42,'token'),
            (48,50,'PorscheCommunicationPro'),(54,56,'token'),(130,142,'PorscheSetLinkKeep')]]
    sites += [('comm',l,a,name) for l,a,name in [
        (0x12594,0x12596,'SetLinkKeep'),(0x125aa,0x125ac,'SetCanIIBusLinkKeep'),
        (0x125a4,0x125a6,'SetCanIIBusLinkKeep'),(0x12530,0x12532,'SetCANLinkKeep20'),
        (0x12754,0x12756,'SetCanLinkKeep_Mit')]]
    bindings=[]
    for lib,load,add,name in sites:
        elf=elves[lib];got=elf.pic_address(load,add);rel=elf.relocations.get(got);s=(rel or {}).get('symbol',{})
        if not rel or rel['type']!=21 or s.get('name')!=name or s.get('type')!=1 or s.get('bytes')!=4:
            raise ValueError('session-exit-binding-unreviewed')
        bindings.append(dict(library=lib,name=name,loadVa=load,addVa=add,gotVa=got,objectVa=s['va']))
    return dict(sourceSha256=hashes,regions=regions,bindings=bindings,
        trailCountTokenOffset=0x738,trailIdsTokenOffset=0x73c,trailIdStride=4,
        lookupCapacity=512,lookupReturnChecked=False,communicationReturnChecked=False,
        mode30ForcesSubtype=1,nonMode20DescriptorFlagOverride=0,finalDescriptorFlag=255,
        finalTailCall='PorscheSetLinkKeep',keepStopChecksProviderReturn=False,
        keepStopNormalReturn=0,keepStopReturnIsSuccessProof=False,
        unsupportedKeepModeMayReturnZeroWithoutProvider=True,
        interLibraryBindingExecuted=False,actualCallbackBindingQualified=False,
        actualChannelCloseQualified=False,actualDeviceReleaseQualified=False,**GUARDS)


def subsystem_source(sysp_blob, file_blob):
    """Pinned name/default/caller behavior, never a runtime version selector."""
    import struct
    from .x431_native_binding import _Elf
    from .x431_version_response import SYSP_SHA256, FILE_SHA256
    hashes={'sysp':SYSP_SHA256,'file':FILE_SHA256};blobs={'sysp':sysp_blob,'file':file_blob}
    for lib,blob in blobs.items():
        if type(blob) is not bytes or sha(blob)!=hashes[lib]:
            raise ValueError('session-subsystem-'+lib+'-original-unreviewed')
    elves={lib:_Elf(blob) for lib,blob in blobs.items()};regions=[]
    for lib,name,start,size,digest in [
        ('file','GetSubSysDataAddr',0x15c18,1188,'fd78a02188bed677474bf45ecdc33cad695fde76f7bfc2983c9f03f7be92238a'),
        ('sysp','_Z18GetOdxXmlDsnInfoExP5TOKENhSt3mapIiSt6vectorIhSaIhEESt4lessIiESaISt4pairIKiS4_EEEPcSC_SC_RSs',
            0x2c644,1784,'260686475403f74858a38154cc517c2b17f2bb67759f1abe2d06eff021add604'),
        ('sysp','_Z16GetInfoFromBvAddP7__sFILESt3mapIiSt6vectorIhSaIhEESt4lessIiESaISt4pairIKiS4_EEEiRSsSC_',
            0x2c018,1048,'bf39fd33d87592586f67aaea806164b1f4a06591cab8ca7810598bc0b0d66f7c'),
        ('sysp','PorscheSysScanReadVerInfo',0x2ace0,100,'2b2d45c73380b5fd7ca3ebac1f5222606b2ee1a6793ba1fce8360f3943068671')]:
        elf=elves[lib];symbols=[s for s in elf.symbols if s['name']==name and s['type']==2]
        if (len(symbols)!=1 or symbols[0]['va']!=start|1 or symbols[0]['bytes']!=size
                or sha(elf.raw(start,size))!=digest):
            raise ValueError('session-subsystem-region-unreviewed')
        regions.append(dict(library=lib,name=name,entry=start,bytes=size,sha256=digest))
    blocks=[]
    for start,size,digest in [
        (0x2c67e,20,'b1ad2b9a042e5ec6a33b9d3473246b75f301d9c20e7811a3f11808da7459f47d'),
        (0x2caf8,42,'847675b0fb4fac7ec30ed5c03359bfe2ec17a4eb5bff51441bec2f61006e5fc3'),
        (0x2cb5a,16,'734916280dd424f0ec9065a7bb5829519594dc8d42e17e504cc6f991a887c600')]:
        if sha(elves['sysp'].raw(start,size))!=digest:
            raise ValueError('session-subsystem-name-block-unreviewed')
        blocks.append(dict(entry=start,bytes=size,sha256=digest))
    bindings=[]
    for lib,load,add,name,wide in [
        ('file',0x15c3e,0x15c42,'SearchIdOpenFile',True),
        ('file',0x15d98,0x15d9a,'SearchIdCloseFile',False),
        ('file',0x15da4,0x15da8,'fcloseEx',False),
        ('sysp',0x2acf8,0x2acfa,'token',False),
        ('sysp',0x2ad04,0x2ad06,'GetSubSysDataAddr',False),
        ('sysp',0x2ad10,0x2ad1c,'PorscheVerInfo',False)]:
        elf=elves[lib]
        if wide:
            first,second=struct.unpack('<HH',elf.raw(load,4))
            if first!=0xf8df or second>>12!=0 or elf.raw(add,2)!=b'\x78\x44':
                raise ValueError('session-subsystem-wide-pic-unreviewed')
            literal=((load+4)&~3)+(second&0xfff)
            got=(struct.unpack('<I',elf.raw(literal,4))[0]+add+4)&0xffffffff
        else:got=elf.pic_address(load,add)
        rel=elf.relocations.get(got);symbol=(rel or {}).get('symbol',{})
        if not rel or rel['type']!=21 or symbol.get('name')!=name or symbol.get('type')!=1 or symbol.get('bytes')!=4:
            raise ValueError('session-subsystem-binding-unreviewed')
        bindings.append(dict(library=lib,name=name,loadVa=load,addVa=add,gotVa=got,objectVa=symbol['va']))
    if elves['sysp'].raw(0x5e350,9)!=b'_BaseSys\0':
        raise ValueError('session-subsystem-default-literal-unreviewed')
    return dict(sourceSha256=hashes,regions=regions,nameBlocks=blocks,bindings=bindings,
        reviewedFamilyId=13,modernFamilyPredicate='TOKEN+2 > 10',nameTokenOffset=0x40a264,
        sourceNameClearBytes=200,versionTextTokenOffset=0x40a2c8,serviceSlotTokenOffsets=list(range(0x604,0x61c,4)),
        comparison='exact-strcmp',serviceSlotsWrittenBeforeNameComparison=True,
        unmatchedNameReturnIfFileAndReadsSucceed=1,unmatchedNameRetains='last-source-variant-six-slots',
        zeroVariantCountReturnIfFileOpen=1,zeroVariantCountRetains='prior-runtime-slots-unresolved',
        nativeReturnIsExactNameMatchProof=False,defaultSuffix='_BaseSys',defaultNameIsOriginalVersionProof=False,
        scanCaller=dict(entry=0x2ace0,bytes=100,configKey='AUTOVER',loaderReturnsChecked=False,
            recordVersionReturnChecked=False,subsystemSelectionReturnChecked=False,
            temporaryModeTokenOffset=0x409ce4,temporaryMode=5,restoresPriorMode=True,
            fullFreshVersionProven=False),
        fullVersionRuleMatchingExecuted=False,cppContainersExecuted=False,
        actualNameWriterControlFlowQualified=False,actualVersionSelectionQualified=False,**GUARDS)


def subsystem_profile_context(unit, profile, records, proof):
    """Recheck all unit links and exact slots; defaults never repair originals."""
    if proof is None:return None
    sources=[s for s in profile['diagnosticSources'] if s['requestedEcuId']==unit['ecuId']]
    if len(sources)!=1:raise ValueError('session-subsystem-single-unit-source-required')
    source=sources[0];name=profile['variant'];default=profile['module']+'_BaseSys'
    if (source['variantName']!=name or source['systemKey']!=(13<<8|unit['ecuId'])
            or source.get('fallbackUsed') is not False):
        raise ValueError('session-subsystem-catalog-context-mismatch')
    exact=[];defaults=[];attempts=[]
    for (ecu,comm_off,service_off),raw in sorted(records.items()):
        if ecu!=unit['ecuId']:continue
        variants=raw['service']['variants'];indices=[]
        for index,variant in enumerate(variants):
            row=dict(linkIndex=raw['index'],communicationSourceOffset=comm_off,
                serviceSourceOffset=service_off,serviceVariantIndex=index,
                variantName=variant['name'],sourceSlots=variant['slots'])
            if variant['name']==name:exact.append(row);indices.append(index)
            if variant['name']==default:defaults.append(row)
        attempts.append(dict(linkIndex=raw['index'],communicationSourceOffset=comm_off,
            serviceSourceOffset=service_off,sourceVariantCount=len(variants),exactNameVariantIndices=indices,
            slotsAfterCompleteNameMismatchCandidate=variants[-1]['slots'] if variants else None,
            mismatchSlotsOrigin='last-source-variant' if variants else 'prior-runtime-slots-unresolved',
            sourceReturnIsNameMatchProof=False,actualLinkSelected=False,**GUARDS))
    if not attempts:raise ValueError('session-subsystem-unit-source-missing')
    claimed=[]
    for link in source['links']:
        claimed.append(dict(linkIndex=link['linkIndex'],communicationSourceOffset=link['communicationSourceOffset'],
            serviceSourceOffset=link['serviceSourceOffset'],serviceVariantIndex=link['serviceVariantIndex'],
            variantName=source['variantName'],sourceSlots=link['sourceSlots']))
    key=lambda row:(row['communicationSourceOffset'],row['serviceSourceOffset'],row['serviceVariantIndex'])
    status='source-name-missing' if not exact else 'exact-source-name-match' if len(exact)==1 else 'source-link-ambiguous'
    if (sorted(claimed,key=key)!=sorted(exact,key=key) or source['bindingStatus']!=status
            or source['linkMatchCount']!=len(exact)):
        raise ValueError('session-subsystem-exact-source-association-mismatch')
    return dict(requestedVariant=name,sourceBindingStatus=status,exactNameMatchCount=len(exact),
        exactNameSources=exact,defaultVariantNameCandidate=default,defaultNameMatchCount=len(defaults),
        defaultNameSources=defaults,sourceLinkAttempts=attempts,fallbackUsed=False,
        originalVersionQualifiedByDefault=False,actualDefaultBranchSelected=False,
        actualVersionSelectionQualified=False,actualSourceLinkSelected=False,
        missingEvidence=['fresh-complete-identity/version','actual-version-rule-calculation-and-comparison',
                         'actual-family/link/pair-selection','fresh-diagnostic-response-and-close'],**GUARDS)


def exit_record_index(logical_blob):
    from .x431_version_response import SYS_DATA_LOGICAL_SHA256
    from scripts.x431_re.sysdata import parse_payload
    if type(logical_blob) is not bytes or sha(logical_blob)!=SYS_DATA_LOGICAL_SHA256:
        raise ValueError('session-exit-logical-original-unreviewed')
    parsed=parse_payload(logical_blob)
    if not parsed.get('ok') or parsed['familyIdQueried']!=13:
        raise ValueError('session-exit-logical-family-unreviewed')
    records={}
    for ecu,system in parsed['byEcuId'].items():
        for link in system['links']:
            key=(ecu,link['commOff'],link['serviceOff'])
            if key in records:raise ValueError('session-exit-source-link-not-unique')
            records[key]=link
    return records


def matched_exit_record(unit, source, link, records):
    raw=records.get((unit['ecuId'],link['communicationSourceOffset'],link['serviceSourceOffset']))
    if raw is None:raise ValueError('session-exit-source-link-missing')
    variants=raw['service']['variants'];index=link['serviceVariantIndex']
    if not 0<=index<len(variants) or variants[index]['name']!=source['variantName']:
        raise ValueError('session-exit-source-name-mismatch')
    native,current=raw['comm'],link['communication']
    if native['protocolKind']!=current['modeByte'] or native['class']!=current['class']:
        raise ValueError('session-exit-communication-mismatch')
    if native['class']=='static-source-confirmed':
        a=[(p['storedTx'],p['storedRx'],p['mode'],p['params']) for p in native['pairs']]
        b=[(p['storedTx'],p['storedRx'],p['subtypeByte'],p['parameterIds']) for p in current['pairs']]
        if a!=b or native['consumedEnd']!=link['serviceSourceOffset']:
            raise ValueError('session-exit-source-record-mismatch')
    return native


def exit_command_context(command, mode):
    candidate=None;flag=None
    if command['descriptorComplete']:
        raw=bytes.fromhex(command['decodedDescriptorHex'])
        if len(raw)<2 or len(raw)!=raw[1]+2 or sha(raw)!=command['descriptorSha256']:
            raise ValueError('session-exit-descriptor-source-changed')
        flag=raw[0]
        if mode==0x30 and command['commandId']==0x1a76:
            if sha(raw)!='b6159f04fbfd997ab9f99e6734e1598cf86cb04e213a8224ecc5f6644c6641e9':
                raise ValueError('session-exit-command-unreviewed')
            candidate='TP2.0-A8-close-source-candidate'
    return dict(commandIdHex=command['commandIdHex'],descriptorSha256=command['descriptorSha256'],
        descriptorComplete=command['descriptorComplete'],originalSourceDescriptorFlag=flag,
        sourceDescriptorFlagBeforeCommunication=flag if mode==0x20 else 0,
        sourceSelectedSubtypeOverride=1 if mode==0x30 else None,
        transportCloseShapeCandidate=candidate,sourcePayloadHex='A8' if candidate else None,
        protocolReference=PROTOCOL_REFERENCE if candidate else None,
        sourceIsQualifiedApplicationRead=False,actualRuntimeBufferQualified=False,
        actualCloseReplyQualified=False,actualChannelCloseQualified=False,
        actualDeviceReleaseQualified=False,**GUARDS)


def exit_source_context(unit, source, link, records, commands, proof):
    if proof is None:return None
    native=matched_exit_record(unit,source,link,records);mode=native['protocolKind']
    complete=native['class']=='static-source-confirmed'
    ids=native['trail'] if complete else []
    return dict(sourceStatus='source-exit-table-reviewed' if complete else 'communication-layout-unresolved',
        sourceExitCount=len(ids) if complete else None,sourceExitCommandIds=[f'{i:08X}' for i in ids],
        commands=[exit_command_context(commands[i],mode) for i in ids],
        sourceHasExitCommands=bool(ids) if complete else None,emptyTableIsCloseProof=False,
        finalDescriptorFlag=255,finalTailCall='PorscheSetLinkKeep',keepStopReturnIsSuccessProof=False,
        actualChannelCloseQualified=False,actualDeviceReleaseQualified=False,
        missingEvidence=['actual-exit/provider-binding','postprocessed-runtime-descriptor',
                         'complete-fresh-close-outcome','device-release-outcome'],**GUARDS)


def mode30_segment_source(elf):
    """Review the outlined subtype4 helper, without executing or binding it."""
    import struct
    digest='a959219095ed0c34b0ff6e8fdff7c7415e1fb55f1f78b77fabbe72f61fed28d1'
    if sha(elf.raw(0x10da4,0x464))!=digest:
        raise ValueError('session-segment-region-unreviewed')
    bindings=[]
    for load,add,name in [(0x10e1e,0x10e2c,'iFilterId'),(0x10e36,0x10e38,'CheckXOR'),
            (0x10e3e,0x10e40,'CommSubFunc_00000002'),(0x10f3a,0x10f42,'iFilterId'),
            (0x10f40,0x10f44,'CheckXOR'),(0x10f7a,0x10f80,'VWCANSendDataToEcuGetMultiFrameAnswer1'),
            (0x10fd6,0x10fda,'iFilterId'),(0x10fe2,0x10fe6,'CheckXOR'),
            (0x11004,0x1100a,'CommSubFunc_00000002')]:
        got=elf.pic_address(load,add);rel=elf.relocations.get(got);symbol=(rel or {}).get('symbol',{})
        if not rel or rel['type']!=21 or symbol.get('name')!=name or symbol.get('type')!=1 or symbol.get('bytes')!=4:
            raise ValueError('session-segment-binding-unreviewed')
        bindings.append(dict(name=name,loadVa=load,addVa=add,gotVa=got,objectVa=symbol['va']))
    if elf.raw(0x10e46,4)!=bytes.fromhex('DFF8BC13') or elf.raw(0x10e4e,2)!=bytes.fromhex('7944'):
        raise ValueError('session-segment-template-pic-unreviewed')
    full_chunk_va=(struct.unpack('<I',elf.raw(0x11204,4))[0]+0x10e4e+4)&0xffffffff
    templates=[]
    for role,va,expected in [
            ('full-chunk',full_chunk_va,'55AA0D6103080000200000000000000000'),
            ('final-chunk',elf.pic_address(0x10ef4,0x10ef6),'55AA0D6101080000100000000000000000'),
            ('post-aggregate',elf.pic_address(0x10fd2,0x10fd8),'55AA06610101E800A3')]:
        raw=elf.raw(va,len(expected)//2)
        if raw.hex().upper()!=expected:raise ValueError('session-segment-template-unreviewed')
        templates.append(dict(role=role,sourceVa=va,sourceHex=expected,sha256=sha(raw)))
    return dict(entry=0x10da4,regionBytes=0x464,regionSha256=digest,bindings=bindings,templates=templates,
        sequenceCounterCommunicationOffset=0x2326,sequenceCounterTokenOffset=0x2962,
        sequenceHeaderMask=15,storedSequenceCounterBits=8,
        fullChunkHeaderHighNibble=0x20,finalChunkHeaderHighNibble=0x10,
        fullChunkCallbackSite=0x10ec0,fullChunkFailureReturn=-1,
        aggregateCallbackSite=0x10fa4,aggregateFailureReturn=-1,
        counterUpdateSite=0x10fba,counterAdvancesAfterNonnegativeAggregate=True,
        counterAdvance='initial-full-chunk-count+1',completionCallSite=0x11032,
        completionPayloadHex='A3',completionFailurePreservesAdvancedCounter=True,
        completionNonnegativeIsChannelCloseProof=False,
        completionProtocolShapeCandidate='TP2.0-channel-keepalive-candidate',protocolReference=PROTOCOL_REFERENCE,
        completionIsQualifiedAcknowledgement=False,actualChannelCloseQualified=False,
        completionResponseBytesUsedForReassembly=False,
        sourceReassemblyEntry=0x1104a,actualProviderBindingQualified=False,**GUARDS)


def mode30_source(comm_blob, sysp_blob=None):
    from .x431_native_binding import _Elf
    from .x431_response_contract import PORSCHE_COMM_SHA256
    if type(comm_blob) is not bytes or sha(comm_blob) != PORSCHE_COMM_SHA256:
        raise ValueError('session-mode30-original-unreviewed')
    elf = _Elf(comm_blob)
    if sha(elf.raw(0x11208,0x118)) != '87b27bb29c67a2818a2dab187403933810a2157ea492f7cceb18f0dd4312e5ec':
        raise ValueError('session-mode30-region-unreviewed')
    bindings=[]
    for load,add,name in [(0x1123e,0x1124c,'iFilterId'),(0x1127c,0x11282,'CheckXOR'),
                          (0x1128e,0x11294,'CommSubFunc_00000002'),
                          (0x11e18,0x11e22,'iFilterId'),(0x11e54,0x11e5e,'iFilterId'),
                          (0x11e7c,0x11e7e,'CanSetParameterNormal'),(0x12238,0x1223a,'iFilterId'),
                          (0x12938,0x1293c,'CanSetParameterNormal'),(0x1296c,0x1296e,'iFilterId'),
                          (0x121ea,0x121f2,'OneToOne_SendDataToEcuGetAnswer')]:
        got=elf.pic_address(load,add);rel=elf.relocations.get(got);symbol=(rel or {}).get('symbol',{})
        if not rel or rel['type']!=21 or symbol.get('name')!=name or symbol.get('type')!=1 or symbol.get('bytes')!=4:
            raise ValueError('session-mode30-callback-source-unreviewed')
        bindings.append(dict(name=name,gotVa=got,objectVa=symbol['va']))
    regions=[]
    for name,start,size,digest in [
        ('PorscheCanInitialize',0x11c30,1324,'d3d92a2becffb770545d510b02fba9f8397118c8d5e501eb3c218744e6c7f341'),
        ('PorscheCanLinkFilter',0x1215c,220,'467625c1c916712799e4826ae781b609d6ab23438747cc5c7d16d64a7c95b33d'),
        ('UpdateCanFilterId',0x12238,16,'93c05624dacf71d303dfb1b0c4147432075281d1191b00fa5909aee46ad69977'),
        ('UpdateCanFilterIdNeedJudge',0x12920,132,'153ed22ea8c0eef4bfe9daeaee28da48af80d13e61d2f727f4a819dc919730e8'),
        ('PorscheSendCommandByIDEX_COMM_INFO',0x13308,164,'5e5cce7256bd6b17028a45d4c70ad8717f5da8ce22c0fa6b76beea9534e4ac12')]:
        symbols=[s for s in elf.symbols if s['name']==name and s['type']==2]
        if (len(symbols)!=1 or symbols[0]['va']!=start|1 or symbols[0]['bytes']!=size
                or sha(elf.raw(start,size))!=digest):
            raise ValueError('session-initialization-region-unreviewed')
        regions.append(dict(name=name,entry=start,bytes=size,sha256=digest))
    initializer=dict(regions=regions,mode30Branch=0x11de4,
        mode30DirectlyAssignsGlobalFilter=False,mode30UsesPriorOrExternalGlobalFilter=True,
        actualCallbackSideEffectsQualified=False,actualPriorGlobalFilterQualified=False,
        copiedCommunicationContextBytes=0x2358,copiedContextOffset=0x63c,
        normalParameterProvider='CanSetParameterNormal',normalParameterFailureReturn=-3,
        directSetterEntry=0x12238,directSetterChecksHardwareResult=False,
        conditionalSetterEntry=0x12920,conditionalSetterStoresOnlyAfterConfigurationReturnZero=True,
        conditionalSetterChecksEnterFrameResult=False,
        linkFilterEntry=0x1215c,initializerChecksLinkFilterReturn=False,
        commInfoCallerEntry=0x13308,commInfoCallerChecksInitializationReturn=False,
        sourceResultIsRuntimeQualification=False,**GUARDS)
    return dict(commSourceSha256=PORSCHE_COMM_SHA256,entry=0x11208,regionBytes=0x118,
        regionSha256=sha(elf.raw(0x11208,0x118)),bindings=bindings,
        requestLengthTokenOffset=0x31c,requestDataTokenOffset=0x31d,
        originalPayloadCopyPreservesAllBytes=True,filterBytesFromGlobalShifts=[24,16],
        providerCallSite=0x112bc,providerCapacity=1024,responseBodyOffset=8,
        responseLengthOffset=5,sourceHeaderTemplate='55 AA length+5 61 01 length filter-high filter-low',
        initializationFilterSource=initializer,
        segmentedSource=mode30_segment_source(elf),
        entryCallerSource=mode30_entry_source(sysp_blob) if sysp_blob is not None else None,
        globalFilterOriginQualified=False,actualCallbackBindingQualified=False,**GUARDS)


def segmented_context(command, mode, pair, proof):
    if mode!=0x30 or pair['subtypeByte']!=4 or not proof or not proof.get('segmentedSource'):
        return None
    if not command['descriptorComplete']:return None
    raw=bytes.fromhex(command['decodedDescriptorHex'])
    if len(raw)<2 or len(raw)!=raw[1]+2 or sha(raw)!=command['descriptorSha256']:
        raise ValueError('session-segment-descriptor-source-changed')
    if raw[0]!=1:return None
    from .x431_version_response import audit_subtype4_request_packing
    packing=audit_subtype4_request_packing(raw[1])
    return dict(commandIdHex=command['commandIdHex'],descriptorSha256=sha(raw),requestBytes=raw[1],
        sourceRequestPacking=packing,sourceCounterAdvanceIfAggregateNonnegative=packing['fullChunkCountCandidate']+1,
        initialSequenceCounterQualified=False,actualFinalSequenceCounterQualified=False,
        actualRequestLengthStableAcrossCallbacks=False,
        compatibleResponseWrapper='subtype4-read-chain-0x10da4',
        completionPayloadHex='A3',completionNonnegativeIsChannelCloseProof=False,
        missingEvidence=['actual-segment/provider-binding','complete-fresh-aggregate',
                         'independent-completion-outcome','channel-close-contract'],**GUARDS)


def entry_pair_context(mode, pair, pair_count, proof):
    if mode!=0x30 or not proof or not proof.get('entryCallerSource'):
        return None
    index=pair['pairIndex']
    return dict(pairIndex=index,sourcePairCount=pair_count,
        sourceStaticFilterWord=((pair['storedTx']<<16)|(pair['storedRx']&65535))&0xffffffff,
        staticWordIsRuntimeSelection=False,initializesAtThisPair=index==0,
        filterWordOrigin=('second-pair-low16-plus-prior-response-body-bytes4/5' if index in (1,2)
                          else 'source-static-word'),
        updateBeforeThisPair=index==1,wordOverwrittenBeforeUse=index in (1,2),
        dependsOnPriorResponse=index in (1,2),minimumFreshBodyBytesForAddressWord=6 if index in (1,2) else None,
        actualResponseOriginQualified=False,actualNegotiatedChannelQualified=False,**GUARDS)


def transport_context(command, mode, pair, later_pairs, proof):
    if proof is None or mode!=0x30 or pair['subtypeByte']!=1:
        return None
    identifier=command['commandId']
    if identifier not in CHANNEL_DESCRIPTORS:
        return None
    raw=bytes.fromhex(command['decodedDescriptorHex'])
    if sha(raw)!=CHANNEL_DESCRIPTORS[identifier] or not command['descriptorComplete'] or raw[0]!=1:
        raise ValueError('session-transport-descriptor-source-changed')
    payload=raw[2:]
    common=dict(commandIdHex=command['commandIdHex'],descriptorSha256=command['descriptorSha256'],
        protocolShape='VW-TP2.0-shape-candidate',protocolReference=PROTOCOL_REFERENCE,
        sourcePayloadHex=payload.hex().upper(),firstByteIsQualifiedEcuService=False,
        nativePayloadCopySourceReviewed=True,actualNegotiatedChannelQualified=False,
        sourceInterpretation='native mode30 context plus independent reference shape; not runtime observation',**GUARDS)
    if len(payload)==7 and payload[1]==0xC0 and payload[2:4]==b'\x00\x10' and payload[6]==1:
        offered=int.from_bytes(payload[4:6],'little')
        later=[dict(pairIndex=p['pairIndex'],sourceStoredTx=p['storedTx'],sourceStoredRx=p['storedRx'],
                    sourceRxShiftCandidate=p['storedRx']>>5 if not p['storedRx']&31 else None,
                    offerEqualsStaticReference=(p['storedRx']==offered<<5),
                    actualNegotiatedAddressQualified=False) for p in later_pairs]
        return dict(**common,role='transport-channel-setup-source-candidate',destinationByte=payload[0],
            setupOpcode=payload[1],offeredReceiveChannel=offered,
            setupRequestIdShiftCandidate=pair['storedTx']>>5 if not pair['storedTx']&31 else None,
            setupReplyIdShiftCandidate=pair['storedRx']>>5 if not pair['storedRx']&31 else None,
            destinationAndSetupReplyReferenceConsistent=pair['storedTx']==0x200<<5 and pair['storedRx']==(0x200+payload[0])<<5,
            laterSourcePairs=later,offerDiffersFromLaterReference=any(not row['offerEqualsStaticReference'] for row in later),
            needsActualNegotiationResponse=True)
    if payload==bytes.fromhex('A00F00FF00FF'):
        return dict(**common,role='transport-channel-parameters-source-candidate',parameterOpcode=payload[0],
            sourceBlockSizeByte=payload[1],sourceAckTimingByte=payload[2],sourceSeparationTimingByte=payload[4],
            actualBlockSizeQualified=False,actualTimingQualified=False,zeroTimingMeaningQualified=False)
    raise ValueError('session-transport-source-layout-unreviewed')


def application_request(raw):
    if (len(raw) != 29 or not envelope(raw, b'\xf0\xf8') or raw[7:9] != b'\x27\x01'
            or raw[9:18] != bytes.fromhex('640001ff020d610108')):
        return None
    address, count = int.from_bytes(raw[18:20], 'big'), raw[20]
    if address & 31 or not 1 <= count <= 7 or raw[21] not in OBSERVED_SIDS:
        return None
    request = raw[21:21+count]
    if (request[0] in (0x10, 0x1A, 0x21, 0x3E) and count != 2) or (request[0] == 0x22 and count != 3):
        return None
    return dict(txId=address >> 5, requestHex=request.hex().upper())


def response_status(request_hex, ret, expected_rx):
    if ret is None: return 'return-layout-unqualified'
    if ret['rxId'] != expected_rx: return 'address-unqualified'
    req, pdu = bytes.fromhex(request_hex), bytes.fromhex(ret['pduHex'])
    if len(pdu) == 3 and pdu[:2] == bytes([0x7F, req[0]]):
        return 'pending' if pdu[2] == 0x78 else 'negative'
    # DTC response layouts do not generally echo all request arguments.
    if req[0] in (0x18, 0x19): return 'dtc-response-contract-unqualified'
    if pdu.startswith(bytes([req[0]+0x40])+req[1:]):
        return 'positive-session-candidate' if req[0] == 0x10 else 'positive-read-candidate'
    return 'echo-mismatch'


def observed_commands(frames, pairs, boundary):
    result = []; host_index = 0
    for index, frame in enumerate(frames):
        if frame['stream'][1] != 'host_to_controller': continue
        raw = bytes.fromhex(frame['frameHex']); parsed = application_request(raw)
        row = dict(observationId=frame['frameId'], hostCommandIndex=host_index,
            requestRecord=frame['startRecord'], requestFrameSha256=frame['frameSha256'],
            adapterStream=[frame['stream'][0], frame['stream'][2]],
            capturePhase='before-coding' if frame['startRecord'] <= boundary else 'coding-workflow',
            requestHex=None, txId=None, rxId=None, pduHex=None, pduSha256=None,
            responseFrameId=None, responseFrameSha256=None, responseRecord=None,
            status='boundary-unreviewed-host-command', **GUARDS)
        host_index += 1
        if parsed is not None:
            row.update(parsed, status='unpaired')
            reply = frames[index+1] if index+1 < len(frames) else None
            if (reply and reply['stream'][1] == 'controller_to_host'
                    and (reply['stream'][0],reply['stream'][2]) == (frame['stream'][0],frame['stream'][2])
                    and reply['sequenceByte'] == frame['sequenceByte']
                    and reply['commandByte'] == (frame['commandByte'] | 0x40)):
                ret = extract_return(bytes.fromhex(reply['frameHex']))
                row.update(responseFrameId=reply['frameId'], responseFrameSha256=reply['frameSha256'],
                           responseRecord=reply['startRecord'], status=response_status(parsed['requestHex'], ret, pairs.get(parsed['txId'])))
                if ret:
                    pdu = bytes.fromhex(ret['pduHex'])
                    row.update(rxId=ret['rxId'], pduHex=ret['pduHex'], pduSha256=sha(pdu),
                        returnShape=ret['shape'], paddingHex=ret['paddingHex'],
                        uninterpretedTrailerHex=ret['uninterpretedTrailerHex'],
                        nrc=pdu[2] if len(pdu)==3 and pdu[:2]==bytes([0x7F,raw[21]]) else None,
                        uninterpretedSessionDataHex=pdu[2:].hex().upper() if row['status']=='positive-session-candidate' else None)
        result.append(row)
    return result


def command_source(language, command_id):
    found = _lookup_command(language, command_id)
    raw = bytes.fromhex(found.get('decodedPayloadHex') or '')
    complete = bool(found['recordFound'] and len(raw)>=2 and len(raw)==raw[1]+2)
    request = raw[2:] if complete else b''
    safe = bool(request and request[0] in OBSERVED_SIDS and len(request)<=7
                and (request[0] not in (0x10,0x1A,0x21,0x3E) or len(request)==2)
                and (request[0]!=0x22 or len(request)==3))
    reason = (None if safe else 'command-record-missing' if not found['recordFound'] else
              'source-descriptor-length-unreviewed' if not complete else
              'source-service-byte-unreviewed' if not request or request[0] not in OBSERVED_SIDS else
              'single-application-request-shape-unreviewed')
    return dict(commandId=command_id, commandIdHex=f'{command_id:08X}',
        recordFound=found['recordFound'], recordIndex=found.get('commandRecordIndex'),
        descriptorSha256=found.get('decodedPayloadSha256'), descriptorBytes=len(raw),
        descriptorComplete=complete, sourceFlagByte=raw[0] if complete else None, decodedDescriptorHex=raw.hex().upper(),
        requestCandidateHex=request.hex().upper() if safe else None,
        sourceServiceByte=request[0] if request else None, sourceDeclaredRequestBytes=raw[1] if len(raw)>1 else None,
        reason=reason,
        postprocessedRuntimeBufferQualified=False, **GUARDS)


def sequence_matches(requests, observed, tx, rx):
    if not requests or any(hx is None for hx in requests): return []
    matches=[]; length=len(requests)
    for start in range(len(observed)-length+1):
        batch=observed[start:start+length]
        if ([row['requestHex'] for row in batch] != requests
                or any(row['txId']!=tx or row['rxId']!=rx for row in batch)
                or len({tuple(row['adapterStream']) for row in batch})!=1
                or len({row['capturePhase'] for row in batch})!=1
                or any(row['status'] not in ('positive-session-candidate','positive-read-candidate') for row in batch)
                or any(row['hostCommandIndex'] != batch[0]['hostCommandIndex']+i for i,row in enumerate(batch))):
            continue
        matches.append(dict(observations=[row['observationId'] for row in batch],
            adapterStream=batch[0]['adapterStream'], capturePhase=batch[0]['capturePhase'],
            requestRecords=[row['requestRecord'] for row in batch],
            status='consecutive-historical-source-order-candidate', **GUARDS))
    return matches


def profile_sequences(unit, profile, observed, routes, commands, transport_proof=None, *,
                      exit_proof=None, exit_index=None, exit_commands=None, subsystem_proof=None):
    reference=routes.get(unit['ecuId']) if unit['generation']=='981' else None
    sources=[s for s in profile['diagnosticSources'] if s['requestedEcuId']==unit['ecuId']]
    links=[]
    for source in sources:
        for link in source['links']:
            pairs=[]
            for pair in link['communication']['pairs']:
                ids=pair['parameterIds']; ordered=[commands[i]['requestCandidateHex'] for i in ids]
                contexts=[transport_context(commands[i],link['communication']['modeByte'],pair,
                    [p for p in link['communication']['pairs'] if p['pairIndex']>pair['pairIndex']],transport_proof) for i in ids]
                contexts=[context for context in contexts if context is not None]
                segments=[segmented_context(commands[i],link['communication']['modeByte'],pair,transport_proof) for i in ids]
                segments=[context for context in segments if context is not None]
                # Compare literal source encoding only, without qualifying hardware conversion.
                stored_tx,stored_rx=pair['storedTx'],pair['storedRx']
                address_matches=bool(reference and stored_tx==reference['txId']<<5 and stored_rx==reference['rxId']<<5)
                matches=sequence_matches(ordered,observed,reference['txId'],reference['rxId']) if address_matches else []
                pairs.append(dict(pairIndex=pair['pairIndex'],modeByte=link['communication']['modeByte'],
                    entryPairSource=entry_pair_context(link['communication']['modeByte'],pair,
                        len(link['communication']['pairs']),transport_proof),
                    subtypeByte=pair['subtypeByte'],transportSourceContexts=contexts,segmentedSourceContexts=segments,
                    sourceCommandIds=[f'{i:08X}' for i in ids],
                    sourceRequestOrder=ordered, sourceStoredTx=stored_tx, sourceStoredRx=stored_rx,
                    literalAddressReferenceMatches=address_matches, historicalSequences=matches,
                    sourceOrderIsRuntimeProcedure=False, **GUARDS))
            reads=[]
            for slot in link['dtc']['readCommandSlots']:
                request=slot['wireCandidateHex'] if slot['class']=='static-source-confirmed' else None
                eligible=bool(request and slot['role'] in ('sessionPrecondition','dtcRead'))
                matches=[row for row in observed if reference and eligible and row['txId']==reference['txId'] and row['requestHex']==request]
                reads.append(dict(sourceIndex=slot['index'],sourceOffset=slot['sourceOffset'],role=slot['role'],
                    sourceRequestCandidateHex=request if eligible else None,
                    observationIds=[row['observationId'] for row in matches],
                    observationStatuses=dict(Counter(row['status'] for row in matches)),
                    expectedResponseContractQualified=False, **GUARDS))
            links.append(dict(linkIndex=link['linkIndex'],serviceVariantIndex=link['serviceVariantIndex'],
                communicationSourceOffset=link['communicationSourceOffset'],sourceBindingStatus=source['bindingStatus'],
                exitSourceContext=exit_source_context(unit,source,link,exit_index,exit_commands,exit_proof),
                pairs=pairs,diagnosticReadSlots=reads,actualRuntimeSequenceSelected=False, **GUARDS))
    return dict(generation=unit['generation'],ecuId=unit['ecuId'],profileId=profile['profileId'],module=profile['module'],
        variant=profile['variant'],addressReference=reference,links=links,
        subsystemSourceContext=subsystem_profile_context(unit,profile,exit_index,subsystem_proof),
        exitSourceStatus=('source-linked' if links else 'missing-diagnostic-source-link') if exit_proof is not None else None,
        missingEvidence=['complete-fresh-identity/version','actual-native-route/link/pair-selection',
                         'original-postprocessed-command-buffers','adapter-initialization/firmware',
                         'complete-diagnostic-response/close-contract'], **GUARDS)


def build(portable, expected_sha256, capture_root, language_path, output, *, comm_library=None, sysp_library=None,
          file_library=None, logical_sys_data=None):
    if sysp_library is not None and comm_library is None:
        raise ValueError('session-entry-comm-source-required')
    if (file_library is None)!=(logical_sys_data is None):
        raise ValueError('session-exit-file-and-logical-sources-required')
    if file_library is not None and (comm_library is None or sysp_library is None):
        raise ValueError('session-exit-comm-and-sysp-sources-required')
    output=Path(output)
    if output.exists():raise ValueError('session-capture-fresh-output-required')
    profiles,catalog=source_profiles(portable,expected_sha256);root=Path(capture_root)
    paths=[(root/'.local/vehicle-analysis/981-x431-20261001'/name,digest) for name,digest in
           [('app-frames.jsonl',ANCHORS['frames']),('transport-audit.json',AUDIT_SHA),('app-frame-summary.json',SUMMARY_SHA)]]
    paths += [(root/'docs/research/can-data/diagnostic/definitions.json',ANCHORS['addresses']),
              (root/'.local/vehicle-runs/981-x431-coding-20261001T095850Z/final/btsnoop_hci.log',ANCHORS['hci']),
              (Path(language_path),REVIEWED_LANGUAGE_SHA256)]
    transport_proof=None
    if comm_library is not None:
        from .x431_response_contract import PORSCHE_COMM_SHA256
        paths.append((Path(comm_library),PORSCHE_COMM_SHA256))
    if sysp_library is not None:
        from .x431_version_response import SYSP_SHA256
        paths.append((Path(sysp_library),SYSP_SHA256))
    if file_library is not None:
        from .x431_version_response import FILE_SHA256, SYS_DATA_LOGICAL_SHA256
        paths += [(Path(file_library),FILE_SHA256),(Path(logical_sys_data),SYS_DATA_LOGICAL_SHA256)]
    inputs=[verify_source(path,digest) for path,digest in paths]
    if comm_library is not None:
        transport_proof=mode30_source(Path(comm_library).read_bytes(),
            Path(sysp_library).read_bytes() if sysp_library is not None else None)
    exit_proof=None;exit_index=None;exit_commands={};subsystem_proof=None
    if file_library is not None:
        exit_proof=exit_source(Path(comm_library).read_bytes(),Path(sysp_library).read_bytes(),
            Path(file_library).read_bytes())
        exit_index=exit_record_index(Path(logical_sys_data).read_bytes())
        subsystem_proof=subsystem_source(Path(sysp_library).read_bytes(),Path(file_library).read_bytes())
    audit=json.loads(paths[1][0].read_bytes());summary=json.loads(paths[2][0].read_bytes())
    if audit['sha256']!=ANCHORS['hci'] or summary['sourceTransportSha256']!=ANCHORS['hci']:
        raise ValueError('session-capture-source-chain-mismatch')
    frames=[json.loads(line) for line in paths[0][0].read_bytes().splitlines()];verify_frames(frames)
    routes,pairs=reference_routes(json.loads(paths[3][0].read_bytes()));observed=observed_commands(frames,pairs,audit['checkpoints'][0]['record'])
    ids={i for _,p in profiles for source in p['diagnosticSources'] for link in source['links'] for pair in link['communication']['pairs'] for i in pair['parameterIds']}
    language=paths[5][0].read_bytes()
    if sha(language)!=REVIEWED_LANGUAGE_SHA256:raise ValueError('session-capture-language-changed')
    commands={i:command_source(language,i) for i in sorted(ids)}
    if exit_proof is not None:
        exit_ids=set()
        for unit,profile in profiles:
            for source in profile['diagnosticSources']:
                if source['requestedEcuId']!=unit['ecuId']:continue
                for link in source['links']:
                    native=matched_exit_record(unit,source,link,exit_index)
                    if native['class']=='static-source-confirmed':exit_ids.update(native['trail'])
        exit_commands={i:command_source(language,i) for i in sorted(exit_ids)}
    output.mkdir(parents=True)
    def write(name,value):
        (output/name).write_bytes((json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2)+'\n').encode())
    totals=Counter();unique_matches=set();dtc_statuses=Counter();transport_roles=Counter();transport_differences=0;transport_ids=set()
    with (output/'session-profiles.jsonl').open('wb') as stream:
        for unit,profile in profiles:
            row=profile_sequences(unit,profile,observed,routes,commands,transport_proof,
                exit_proof=exit_proof,exit_index=exit_index,exit_commands=exit_commands,subsystem_proof=subsystem_proof)
            context=row['subsystemSourceContext']
            if context is not None:
                has_exact=bool(context['exactNameMatchCount']);has_default=bool(context['defaultNameMatchCount'])
                totals['subsystemProfilesWithExactName' if has_exact else 'subsystemProfilesWithoutExactName']+=1
                totals['subsystemProfilesWithDefaultName' if has_default else 'subsystemProfilesWithoutDefaultName']+=1
                totals['subsystemExactSourceLinks']+=context['exactNameMatchCount']
                totals['subsystemSourceLinkAttempts']+=len(context['sourceLinkAttempts'])
                totals['subsystemMissingOriginalWithDefaultSource']+=not has_exact and has_default
            if exit_proof is not None:
                totals['exitProfilesWithLinks' if row['links'] else 'exitProfilesWithoutLinks']+=1
            for link in row['links']:
                context=link['exitSourceContext']
                if context is not None:
                    totals['exitSourceLinks']+=1
                    if context['sourceExitCount'] is None:totals['exitSourceUnresolvedLinks']+=1
                    else:
                        totals['exitSourceCommandReferences']+=context['sourceExitCount']
                        totals['exitLinksWithCommands' if context['sourceExitCount'] else 'exitLinksWithNoCommands']+=1
                        totals['exitCloseShapeReferences']+=sum(bool(c['transportCloseShapeCandidate']) for c in context['commands'])
                for pair in link['pairs']:
                    totals['segmentedSourceCommandReferences']+=len(pair['segmentedSourceContexts'])
                    totals['segmentedSourcePairReferences']+=bool(pair['segmentedSourceContexts'])
                    if pair['entryPairSource'] is not None:
                        totals['mode30EntryPairCandidates']+=1
                        totals['mode30EntryPriorResponseDependencies']+=pair['entryPairSource']['dependsOnPriorResponse']
                    for context in pair['transportSourceContexts']:
                        transport_roles[context['role']]+=1
                        transport_ids.add(context['commandIdHex'])
                        transport_differences+=bool(context.get('offerDiffersFromLaterReference'))
                    totals['sourcePairs']+=1;totals['pairCandidatesWithSequence']+=bool(pair['historicalSequences'])
                    for match in pair['historicalSequences']:unique_matches.add(tuple(match['observations']))
                for slot in link['diagnosticReadSlots']:
                    dtc_statuses.update(slot['observationStatuses'])
                totals['mode30EntryLinkCandidates']+=any(pair['entryPairSource'] is not None for pair in link['pairs'])
            stream.write((json.dumps(row,ensure_ascii=True,sort_keys=True)+'\n').encode())
    write('commands.json',list(commands.values()));write('observations.json',observed)
    sessions=[r for r in observed if r['requestHex'] and r['requestHex'].startswith('10')]
    report=dict(kind='x431-source-initialization-session-capture-audit',profileBindings=len(profiles),
        uniqueProfiles=len({p['profileId'] for _,p in profiles}),sourceCommandIds=len(commands),
        nativeMode30SourceAudit=transport_proof,transportSourceRoleCounts=dict(transport_roles),
        nativeExitSourceAudit=exit_proof,exitCommandSources=list(exit_commands.values()),
        nativeSubsystemSourceAudit=subsystem_proof,
        commandsWithTransportSourceContext=sorted(transport_ids),
        channelOffersDifferingFromLaterSourceReferences=transport_differences,
        sourceCommandGaps=sum(c['requestCandidateHex'] is None for c in commands.values()),
        sourceCommandGapReasons=dict(Counter(c['reason'] for c in commands.values() if c['reason'])),
        hostCommands=len(observed),hostBoundaryCommands=sum(r['requestHex'] is None for r in observed),
        sessions=len(sessions),sessionStatuses=dict(Counter(r['status'] for r in sessions)),
        uniqueConsecutiveHistoricalSequences=len(unique_matches),sourceCounts=dict(totals),
        diagnosticSlotObservationStatuses=dict(dtc_statuses),
        countBasis='source menu/generation/version/link/pair candidates; repeated references are not qualified procedures', **GUARDS)
    write('report.json',report)
    code_names=['session_capture.py','identity_capture.py','x431_capture.py','offline_match.py','x431_command_origin.py',
                'x431_native_binding.py','x431_response_contract.py','x431_version_response.py',
                'x431_variant_selection.py']
    code=[dict(name='scripts/diagnostics/'+name,sha256=sha(Path(__file__).with_name(name).read_bytes())) for name in code_names]
    if exit_proof is not None:
        code.append(dict(name='scripts/x431_re/sysdata.py',sha256=sha((Path(__file__).parents[1]/'x431_re/sysdata.py').read_bytes())))
        code.append(dict(name='scripts/diagnostics/x431_file_command.py',sha256=sha(Path(__file__).with_name('x431_file_command.py').read_bytes())))
    write('manifest.json',dict(schemaVersion=1,inputsAreProvenanceOnlyAtRuntime=True,catalog=catalog,inputs=inputs,
        code=code,
        outputs=[dict(name=name,bytes=(output/name).stat().st_size,sha256=sha((output/name).read_bytes()))
                 for name in ['session-profiles.jsonl','commands.json','observations.json','report.json']],**GUARDS))
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['definitions-zip','capture-root','language','output']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--definitions-sha256',required=True);p.add_argument('--comm-library',type=Path)
    p.add_argument('--sysp-library',type=Path);p.add_argument('--file-library',type=Path)
    p.add_argument('--logical-sys-data',type=Path);args=p.parse_args(argv)
    print(json.dumps(build(args.definitions_zip,args.definitions_sha256,args.capture_root,args.language,args.output,
        comm_library=args.comm_library,sysp_library=args.sysp_library,
        file_library=args.file_library,logical_sys_data=args.logical_sys_data),sort_keys=True))


if __name__=='__main__':main()
