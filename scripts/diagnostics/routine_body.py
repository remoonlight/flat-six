"""Source layout candidate for retained X431 GetActChileCmdDop bodies, never a recipe.

Offsets are relative to the body after the routine name header. The native reader
ignores the first body byte as a loop count and stops at tag 0x81 or seven records.
Field meanings, caller version, preprocessing and vehicle execution remain unqualified.
"""
from __future__ import annotations
from .routine_buffers import NATIVE_LINK_SHA256, routine_buffer_candidates
from .routine_dispatch import routine_dispatch_candidates

MAX_BODY_BYTES = 65536
MAX_SOURCE_STRING_BYTES = 1024  # Local resource protection, not a native/vehicle limit.
MAX_SOURCE_FIELDS = 200  # Local protection; no native safety claim for malformed files.
NATIVE_STRING_TAGS = frozenset((0x11, 0x12, 0x13, 0x14, 0x60, 0x70, 0x80))
NATIVE_NO_FIRST_WORD_TAGS = frozenset((0x05, 0x07, 0x40))


def _native_context_candidates(records):
    """Describe two conditional source transformations, without choosing a caller.

    GetActChileCmdDop TOKEN[2] 13/14 omits tag 0 and tag 4 with zero
    *field count*. SpeActModeBehoben compares its string argument exactly;
    that argument is not assumed to be the catalog profile or module name.
    """
    kept = [i for i, row in enumerate(records)
            if row['tag'] != 0 and not (row['tag'] == 4 and row['fieldCount'] == 0)]
    patches = []
    for index in kept:
        word = records[index]['sourceFieldA']
        if word is not None and word & 0xF000 == 0x3000:
            # Native BFI inserts integer 3 into bits 4..31, preserving bits 0..3.
            patches.append({'sourceRecordIndex': index, 'originalSourceFieldA': word,
                            'candidateSourceFieldA': (word & 0xF) | 0x30})
    return {'status': 'source-native-context-candidates', 'nativeLibrarySha256': NATIVE_LINK_SHA256,
            'readerSelection': {'tokenByteOffset': 2, 'modernReaderMinimum': 11,
                                'modernReader': 'GetActChileCmdDop', 'olderReader': 'GetActChileCmdDopold'},
            'nineXOneReaderCase': {'tokenByte2EqualsAny': [13, 14], 'keptSourceRecordIndices': kept,
                                  'omittedSourceRecordIndices': [i for i in range(len(records)) if i not in kept],
                                  'omittedWhen': ['tag-zero', 'tag-four-and-zero-field-count']},
            'postprocessCase': {'nativeStringArgumentEquals': 'PCM31_A3_2',
                                'argumentOriginQualified': False, 'sourceFieldAPatches': patches,
                                'undeclaredNativeWordSourceIndices': [i for i in kept if records[i]['sourceFieldA'] is None],
                                'completeNativeRecordStateKnown': False,
                                'scope': 'after-nine-x-one-reader-case'},
            'actualCallerSelected': False, 'nativeCallerVersionQualified': False,
            'recipeQualified': False, 'executionEnabled': False, 'liveVerified': False,
            'requestHex': None, 'writePayload': None}


def routine_body_layout(body):
    result = {'status': 'unresolved', 'reader': 'GetActChileCmdDop', 'offsetBase': 'body',
              'firstBodyByte': None, 'records': [], 'recordCount': 0, 'fieldCount': 0,
              'consumedBytes': 0, 'terminalTag': None, 'nativeContextCandidates': None, 'nativeBufferCandidates': None,
              'nativeDispatchCandidates': None,
              'firstBodyByteSemanticsQualified': False, 'recordFieldSemanticsQualified': False,
              'nativeCallerVersionQualified': False, 'recipeQualified': False,
              'executionEnabled': False, 'liveVerified': False, 'requestHex': None, 'writePayload': None}
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        return {**result, 'reason': 'routine-body-input-invalid'}
    position = 0

    def take(size):
        nonlocal position
        if position + size > len(body):
            raise ValueError('routine-body-truncated')
        start = position; position += size
        return body[start:position]

    def number(size):
        return int.from_bytes(take(size), 'little')

    def source_string():
        offset = position
        size = number(2)
        if not 1 <= size <= MAX_SOURCE_STRING_BYTES:
            raise ValueError('routine-body-string-length-unqualified')
        raw = take(size)
        if raw[-1] != 0 or b'\0' in raw[:-1]:
            raise ValueError('routine-body-string-framing-unqualified')
        return {'offset': offset, 'bytes': size, 'rawHex': raw.hex()}

    try:
        result['firstBodyByte'] = number(1)
        records = []
        for _ in range(7):
            offset = position; tag = number(1)
            if tag == 0x81:
                if position != len(body):
                    raise ValueError('routine-body-unconsumed-tail')
                return {**result, 'status': 'source-body-layout-candidate', 'records': records,
                        'recordCount': len(records), 'fieldCount': sum(r['fieldCount'] for r in records),
                        'consumedBytes': position, 'terminalTag': tag,
                        'nativeContextCandidates': _native_context_candidates(records),
                        'nativeBufferCandidates': routine_buffer_candidates(records),
                        'nativeDispatchCandidates': routine_dispatch_candidates(records),
                        'reason': 'routine-body-semantics-caller-and-recovery-required'}
            row = {'offset': offset, 'tag': tag, 'sourceFieldA': None, 'sourceFieldB': None,
                   'prefixString': None}
            direct_string = tag in NATIVE_STRING_TAGS
            if direct_string:
                row['prefixString'] = source_string()
                count = number(1) if tag in (0x12, 0x80) else 0
            else:
                if tag not in NATIVE_NO_FIRST_WORD_TAGS:
                    row['sourceFieldA'] = number(4)
                row['sourceFieldB'] = number(4)
                count = number(1)
            if count > MAX_SOURCE_FIELDS:
                raise ValueError('routine-body-field-count-protection')
            fields = []
            for _ in range(count):
                field = {'offset': position, 'sourceString': source_string(),
                         'sourceFieldC': None if direct_string else number(4),
                         'sourceByte': number(1), 'sourceWord': number(2),
                         'sourceByte2': number(1), 'sourceFieldD': number(4)}
                field['end'] = position; fields.append(field)
            row.update(fieldCount=count, fields=fields, end=position)
            records.append(row)
        raise ValueError('routine-body-native-record-limit')
    except ValueError as error:
        return {**result, 'reason': str(error), 'failureOffset': position}
