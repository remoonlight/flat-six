"""Offline audit of explicit command-lookup buffer observations; no transport.

FILE's exported IdSearch object is a dynamic function slot, distinct from
STD's function of the same name. Their actual runtime binding is unqualified.
No padding, missing heap bytes or successful payload reads are invented.
"""
from __future__ import annotations

from .x431_version_response import VERSION_GUARDS


def lookup_buffer_source_metadata() -> dict:
    from .x431_command_origin import FILE_SHA256, STD_SHA256
    from .x431_native_binding import loader_source_metadata
    return {'fileSourceSha256': FILE_SHA256, 'stdSourceSha256': STD_SHA256,
        'loaderSourceAudit': loader_source_metadata(),
        'fromCmdEntry': 0x17748, 'fromCmdRegionEnd': 0x177f8,
        'fromCmdRegionSha256': 'a16978d9471f4ddd35b935983cffd366f481123e3d45cb6fe04a26be76d0e988',
        'scratchAllocationCallSite': 0x17762, 'callerHeaderZeroSite': 0x1776a,
        'idSearchCallSite': 0x177a8, 'idSearchGotVa': 0x68dac,
        'idSearchObjectVa': 0x9d71c, 'idSearchObjectSymbol': 'IdSearch',
        'callerCopyCallSite': 0x177b0, 'scratchFreeCallSite': 0x177b6,
        'idSearchEntry': 0x251c0, 'idSearchRegionEnd': 0x254e4,
        'idSearchRegionSha256': '8a47e1a020bae055bf17c63caaa173ea2e0bf58576c6c947b06247c5eabbc3d9',
        'payloadReadCallSite': 0x253fa, 'payloadDecryptCallSite': 0x25406,
        'postprocessorCallSite': 0x252ea, 'postprocessorEntry': 0x25614,
        'postprocessorRegionEnd': 0x258c8,
        'postprocessorRegionSha256': '2c1c43f7fcecaa23efc889d5808b3b36cbd1c2d42274958d31fb7f1dc78dbc29',
        'strlenCallSite': 0x25648, 'sourceCallerHeaderZeroBytes': 2,
        'sourceScratchZeroFilledBeforeLookup': False, 'sourceLookupAppendsNullTerminator': False,
        'sourcePayloadReadReturnChecked': False, 'sourceLookupReturnPropagatedByFromCmd': False,
        'sourceFromCmdNormalReturnCandidate': 0,
        'fromCmdNormalReturnProvesRecordFound': False,
        'fromCmdNormalReturnProvesFreshCallerBytes': False,
        'actualDynamicLookupBindingQualified': False, 'actualHeapBufferQualified': False}


def audit_command_lookup_buffers(observation: dict) -> dict:
    from .x431_command_origin import _bytes, audit_command_postprocessing
    from .x431_native_binding import audit_loader_sequence
    fields = {'decodedPayloadHex', 'lookupCapacity', 'recordFound', 'payloadReadReturnedBytes'}
    optional = {'idSearchReturnValue', 'searchBufferHex', 'scratchAtLookupReturnHex', 'callerAfterCopyHex',
                'loaderSequenceObservation'}
    if not isinstance(observation, dict) or not fields <= set(observation) or set(observation)-fields-optional:
        raise ValueError('command-lookup-buffer-fields-invalid')
    capacity = observation['lookupCapacity']
    if type(capacity) is not int or not 1 <= capacity <= 32765:
        raise ValueError('command-lookup-capacity-invalid')
    found = observation['recordFound']
    if type(found) is not bool:
        raise ValueError('command-lookup-record-found-required')
    body = _bytes(observation['decodedPayloadHex'], 65533)
    if not found and body:
        raise ValueError('command-lookup-missing-record-has-payload')
    count = min(len(body), capacity)
    read = observation['payloadReadReturnedBytes']
    if read is not None and (type(read) is not int or not 0 <= read <= count):
        raise ValueError('command-lookup-payload-read-count-invalid')
    if not count and read is not None:
        raise ValueError('command-lookup-payload-read-not-called')
    result = observation.get('idSearchReturnValue')
    if result is not None and (type(result) is not int or not -0x80000000 <= result <= 0x7fffffff):
        raise ValueError('command-lookup-return-invalid')
    loader = audit_loader_sequence(observation['loaderSequenceObservation']) if 'loaderSequenceObservation' in observation else None
    buffers = {key: _bytes(observation[key], capacity if key == 'callerAfterCopyHex' else capacity+2)
               for key in optional-{'idSearchReturnValue','loaderSequenceObservation'} if key in observation}
    scratch = buffers.get('scratchAtLookupReturnHex')
    caller = buffers.get('callerAfterCopyHex')
    if scratch is not None and caller is not None:
        overlap = min(len(scratch), len(caller))
        if scratch[:overlap] != caller[:overlap]:
            raise ValueError('command-lookup-caller-copy-mismatch')
    out = {'kind': 'x431-offline-command-lookup-buffer-audit',
        'nativeSourceAudit': lookup_buffer_source_metadata(), 'lookupCapacity': capacity,
        'sourceAllocatedScratchBytes': capacity+2, 'sourceCallerCopyBytes': capacity,
        'callerHeaderZeroBytesOverwrittenByCopy': min(capacity, 2),
        'sourceRecordFound': found, 'decodedPayloadBytes': len(body),
        'sourcePayloadReadRequestedBytesCandidate': count,
        'observedPayloadReadReturnedBytes': read, 'observedIdSearchReturnValue': result,
        'observedBufferByteCounts': {k: len(v) for k, v in buffers.items()},
        'nativeLoaderSequenceAudit': loader,
        'payloadReadCompleteCandidate': count > 0 and read == count,
        'postprocessingAudit': None, 'knownSourceCallerPrefixHex': None,
        'callerUnqualifiedTailBytes': capacity, 'lookupBufferChainCompleteCandidate': False,
        'observationsProveActualRuntimeOrigin': False,
        'status': 'payload-read-incomplete-or-unobserved', **VERSION_GUARDS}
    if result is not None and result < 0:
        out['status'] = 'lookup-error-hidden-by-caller'
        return out
    if not count:
        if result not in (None, 0):
            raise ValueError('command-lookup-empty-return-mismatch')
        out['status'] = 'empty-record-caller-copies-unqualified-scratch' if found else 'missing-record-caller-copies-unqualified-scratch'
        return out
    if read != count:
        # GagDecrypt is still called with count; a short read does not prove
        # even the decoded prefix. Do not feed such bytes into strlen audit.
        return out
    post_input = {'decodedPayloadHex': body.hex(), 'lookupCapacity': capacity}
    if 'searchBufferHex' in buffers:
        post_input['searchBufferHex'] = buffers['searchBufferHex'].hex()
    post = audit_command_postprocessing(post_input)
    out['postprocessingAudit'] = post
    if not post['sourcePostprocessingComplete']:
        out['status'] = 'command-postprocessing-unqualified'
        return out
    if result is not None and result != post['nativeLookupReturnCandidate']:
        raise ValueError('command-lookup-return-source-mismatch')
    prefix = bytes.fromhex(post['sourceWrittenPrefixHex'])
    for key in ('scratchAtLookupReturnHex', 'callerAfterCopyHex'):
        value = buffers.get(key)
        if value is not None and value[:min(len(prefix), len(value))] != prefix[:len(value)]:
            raise ValueError('command-lookup-written-prefix-mismatch')
    chain = (result == count and scratch is not None and caller is not None
             and len(scratch) >= count and len(caller) >= count
             and (loader is None or loader['completeLoaderChainCandidate']))
    out.update(knownSourceCallerPrefixHex=prefix.hex(), callerUnqualifiedTailBytes=capacity-count,
               lookupBufferChainCompleteCandidate=chain,
               status='observed-caller-prefix-source-bypass-candidate' if chain else 'known-source-prefix-with-unqualified-caller-copy')
    if loader is not None and not loader['completeLoaderChainCandidate']:
        out['status'] = 'native-loader-chain-unqualified'
    return out
