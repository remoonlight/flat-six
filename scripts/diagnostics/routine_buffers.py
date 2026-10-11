"""Source candidates for nine retained X431 builders, never wire payloads.

Inputs are the complete source layout, before caller filtering/postprocessing.
Native buffer initialization, runtime caller/record state and transport are unknown.
"""
from __future__ import annotations

NATIVE_LINK_SHA256 = '4ec50b08d3d864ff65eb462e64eb510f7740b2978073ba529bc6a78e48a7477d'


def _act_buffer_case(records):
    groups = ((3,), (2, 0x20), (1, 0x10), (4, 8), (6,))
    indices = [next((i for i in range(len(records)-1, -1, -1) if records[i]['tag'] in tags), None)
               for tags in groups + ((5, 7),)]
    buffers = []
    for argument, tags, index in zip(('r1', 'r2', 'r3', 'stack-argument-4', 'stack-argument-5'), groups, indices):
        item = {'argumentRegister': argument, 'recordTags': list(tags), 'sourceRecordIndex': index,
                'internalBufferHex': None, 'status': 'native-buffer-not-written-by-builder'}
        if index is not None:
            a, b = records[index]['sourceFieldA'], records[index]['sourceFieldB']
            if a is None or b is None:
                item['status'] = 'native-record-word-unresolved'
            else:
                hi, lo, bhi, blo = (a >> 8) & 255, a & 255, (b >> 8) & 255, b & 255
                if hi == 0x2F:
                    data = (6, 4, hi, bhi, blo, lo)
                elif hi in (0x31, 0x32):
                    data = (6, 4, hi, lo, bhi, blo)
                elif a == 0x22:
                    data = (5, 3, lo, bhi, blo)
                else:
                    data = (5, 3, hi, blo, lo)
                item.update(internalBufferHex=bytes(data).hex(), status='declared-word-buffer-candidate')
        buffers.append(item)
    return {'builder': 'GetActCmdMode1', 'buffers': buffers,
            'recordIndexSlots': [{'recordTags': list(tags), 'sourceRecordIndex': index}
                                 for tags, index in zip(((1, 0x10), (2, 0x20), (3,), (4, 8), (6,), (5, 7)),
                                                       (indices[2], indices[1], indices[0], indices[3], indices[4], indices[5]))],
            'nativeMissingIndexValue': 65535, 'laterSameTagOverwritesEarlier': True,
            'sourceFieldAMutationCandidates': []}


def _string_buffer_case(records, builder, tags):
    # Native strlen is evaluated for every record, including non-output tags.
    # Prefix bytes are skipped without a "0x" check; non-hex strtol forms remain
    # unresolved here. Do not emulate scratch overwrite or wraparound writes.
    previous_count = 2
    buffers = [{'argumentRegister': argument, 'recordTag': tag, 'sourceRecordIndex': None,
                'internalBufferHex': None, 'status': 'native-buffer-not-written-by-builder'}
               for argument, tag in zip(('r1', 'r2', 'r3', 'stack-argument-4'), tags)]
    unknown_strings = []
    for index, row in enumerate(records):
        prefix = row.get('prefixString')
        value = None
        if prefix is None:
            previous_count = None
            unknown_strings.append(index)
            status = 'native-record-string-unresolved'
        else:
            text = bytes.fromhex(prefix['rawHex'])[:-1]
            if len(text) < 5:
                if previous_count is None or 2 + (previous_count & 255) > 256:
                    status = 'native-prior-string-length-unresolved'
                else:
                    value = bytes(2 + (previous_count & 255)).hex()
                    status = 'declared-string-buffer-candidate'
            else:
                count = (len(text)-1)//2
                pair_bytes = text[2:2+2*((len(text)-2)//2)]
                previous_count = count
                if count > 254:
                    previous_count = None
                    status = 'native-string-scratch-layout-unresolved'
                elif any(c not in b'0123456789abcdefABCDEF' for c in pair_bytes):
                    status = 'native-strtol-pair-unresolved'
                else:
                    data = bytes.fromhex(pair_bytes.decode('ascii'))
                    value = (bytes(((count+2)&255, count)) + data + bytes(count-len(data))).hex()
                    status = 'declared-string-buffer-candidate'
        if row['tag'] in tags:
            buffers[tags.index(row['tag'])].update(sourceRecordIndex=index, internalBufferHex=value, status=status)
    return {'builder': builder, 'buffers': buffers,
            'recordIndexSlots': [{'recordTag': b['recordTag'], 'sourceRecordIndex': b['sourceRecordIndex']} for b in buffers],
            'nativeMissingIndexValue': 65535, 'laterSameTagOverwritesEarlier': True,
            'sourceFieldAMutationCandidates': [], 'undeclaredNativeStringSourceIndices': unknown_strings,
            'nativeScratchBytes': 256, 'localDerivationMaxDataBytes': 254,
            'shortStringLengthDependsOnEarlierRecords': True, 'nativeStringPrefixValidated': False,
            'nonHexStrtolFormsQualified': False}


def _remaining_act_word_case(records, builder):
    mode_three = builder == 'GetActCmdMode3'
    groups = ((1,), (2,), (3,), (5,), (6,)) if mode_three else \
        ((1, 0x27), (2,), (3,), (4,), (6, 0x26))
    slot_groups = ((1,), (2,), (3,), (4,), (6,), (5, 7)) if mode_three else \
        groups + ((5, 7),)
    last = lambda tags: next((i for i in range(len(records)-1, -1, -1)
                              if records[i]['tag'] in tags), None)
    buffers = []
    for argument, tags in zip(('r1', 'r2', 'r3', 'stack-argument-4', 'stack-argument-5'), groups):
        index = last(tags)
        item = {'argumentRegister': argument, 'recordTags': list(tags), 'sourceRecordIndex': index,
                'internalBufferHex': None, 'status': 'native-buffer-not-written-by-builder'}
        if index is not None:
            a, b = records[index]['sourceFieldA'], records[index]['sourceFieldB']
            if a is None or b is None:
                item['status'] = 'native-record-word-unresolved'
            else:
                hi, lo, bhi, blo = (a >> 8) & 255, a & 255, (b >> 8) & 255, b & 255
                if hi == 0x2F:
                    data = (6, 4, hi, bhi, blo, lo)
                elif a == 0x22:
                    data = (5, 3, lo, bhi, blo)
                elif mode_three:
                    data = (4, 2, lo, blo)
                else:
                    data = (5, 3, hi, blo, lo)
                item.update(internalBufferHex=bytes(data).hex(), status='declared-word-buffer-candidate')
        buffers.append(item)
    return {'builder': builder, 'buffers': buffers,
            'recordIndexSlots': [{'recordTags': list(tags), 'sourceRecordIndex': last(tags)} for tags in slot_groups],
            'nativeMissingIndexValue': 65535, 'laterSameTagOverwritesEarlier': True,
            'sourceFieldAMutationCandidates': []}


def _act_mode_five_case(records):
    # Native clears scratch per record, but r8 (the copy count) survives.
    # Every record affects it, including tags which never write an output.
    count = 0
    tags = (0x23, 0x24, 0x25)
    buffers = [{'argumentRegister': argument, 'recordTags': list(tags) if i == 0 else [],
                'sourceRecordIndex': None, 'internalBufferHex': None, 'copiedBytes': None,
                'nativeDataCountCandidate': None, 'status': 'native-buffer-not-written-by-builder'}
               for i, argument in enumerate(('r1', 'r2', 'r3', 'stack-argument-4', 'stack-argument-5'))]
    unknown_words = []
    for index, row in enumerate(records):
        a, b = row['sourceFieldA'], row['sourceFieldB']
        value = copied = None
        if a is None:
            unknown_words.append(index)
            count = None
            status = 'native-record-word-unresolved'
        else:
            lo = a & 255
            if lo in (1, 2, 3):
                count = 2
                value = bytes((3, 2, (a >> 8) & 255, lo)).hex()
            elif lo in (0x28, 0x85):
                count = 3 if lo == 0x28 else 5
                if b is not None:
                    data = (4, 3, lo, b & 255, 0) if lo == 0x28 else (7, 5, lo, b & 255, 255, 255, 255)
                    value = bytes(data).hex()
            elif count is not None:
                scratch = bytearray(2 + (count & 255))
                if len(scratch) > 2:
                    scratch[2] = lo
                value = scratch.hex()
            status = 'declared-word-buffer-candidate' if value is not None else \
                ('native-prior-data-count-unresolved' if count is None else 'native-record-word-unresolved')
            copied = 2 + (count & 255) if count is not None else None
        if row['tag'] in tags:
            buffers[0].update(sourceRecordIndex=index, internalBufferHex=value, copiedBytes=copied,
                              nativeDataCountCandidate=count, status=status)
    return {'builder': 'GetActCmdMode5', 'buffers': buffers,
            'recordIndexSlots': [{'recordTags': b['recordTags'], 'sourceRecordIndex': b['sourceRecordIndex']}
                                 for b in buffers] + [{'recordTags': [], 'sourceRecordIndex': None}],
            'nativeMissingIndexValue': 65535, 'laterSameTagOverwritesEarlier': True,
            'sourceFieldAMutationCandidates': [], 'undeclaredNativeWordSourceIndices': unknown_words,
            'nativeScratchBytes': 256, 'dataCountDependsOnEarlierRecords': True,
            'internalHeaderLengthEqualityQualified': False}


def _routine_mode_five_case(records):
    words = [r['sourceFieldA'] for r in records]
    mutations = []
    for index, a in enumerate(words):
        if a is not None and 0x100 <= a < 0x80000000 and a & 0xF000 == 0x3000:
            words[index] = (a & 15) | 0x30
            mutations.append({'sourceRecordIndex': index, 'originalSourceFieldA': a,
                              'candidateSourceFieldA': words[index]})
    indices = {tag: next((i for i in range(len(records)-1, -1, -1) if records[i]['tag'] == tag), None)
               for tag in (0x10, 0x20, 0x30, 0x40)}
    buffers = []
    for argument, tag in zip(('r1', 'r2', 'r3'), (0x10, 0x20, 0x40)):
        index = indices[tag]
        item = {'argumentRegister': argument, 'recordTag': tag, 'sourceRecordIndex': index,
                'internalBufferHex': None, 'status': 'native-buffer-not-written-by-builder'}
        if index is not None:
            a, b = words[index], records[index]['sourceFieldB']
            if a is None or b is None:
                item['status'] = 'native-record-word-unresolved'
            else:
                item.update(internalBufferHex=bytes((4, 2, a & 255, b & 255)).hex(),
                            status='declared-word-buffer-candidate')
        buffers.append(item)
    return {'builder': 'GetRoutCmdMode5', 'buffers': buffers,
            'recordIndexSlots': [{'recordTag': tag, 'sourceRecordIndex': index} for tag, index in indices.items()],
            'nativeMissingIndexValue': 65535, 'laterSameTagOverwritesEarlier': True,
            'sourceFieldAMutationCandidates': mutations}


def routine_buffer_candidates(records):
    cases = []
    for reader, index_tag in (('GetRoutCmdMode1', 0x40), ('GetRoutCmdMode2_9x1', 0x50)):
        words = [r['sourceFieldA'] for r in records]
        mutations = []
        if reader == 'GetRoutCmdMode2_9x1':
            for index, word in enumerate(words):
                # Native signed CMP/BLT excludes negative 32-bit values.
                if word is not None and 0x100 <= word < 0x80000000 and word & 0xF000 == 0x3000:
                    words[index] = (word & 0xF) | 0x30
                    mutations.append({'sourceRecordIndex': index, 'originalSourceFieldA': word,
                                      'candidateSourceFieldA': words[index]})
        indices = {tag: next((i for i in range(len(records) - 1, -1, -1) if records[i]['tag'] == tag), None)
                   for tag in (0x10, 0x20, 0x30, index_tag)}
        buffers = []
        for argument, tag in zip(('r1', 'r2', 'r3'), (0x10, 0x20, 0x30)):
            index = indices[tag]
            item = {'argumentRegister': argument, 'recordTag': tag, 'sourceRecordIndex': index,
                    'internalBufferHex': None, 'status': 'native-buffer-not-written-by-builder'}
            if index is not None:
                a, b = words[index], records[index]['sourceFieldB']
                if a is None or b is None:
                    item['status'] = 'native-record-word-unresolved'
                else:
                    data = bytes((6, 4, (a >> 8) & 255, a & 255, (b >> 8) & 255, b & 255)) if index_tag == 0x40 \
                        else bytes((4, 2, a & 255, b & 255))
                    item.update(internalBufferHex=data.hex(), status='declared-word-buffer-candidate')
            buffers.append(item)
        cases.append({'builder': reader, 'buffers': buffers,
                      'recordIndexSlots': [{'recordTag': tag, 'sourceRecordIndex': index} for tag, index in indices.items()],
                      'nativeMissingIndexValue': 65535, 'laterSameTagOverwritesEarlier': True,
                      'sourceFieldAMutationCandidates': mutations})
    cases.extend((_act_buffer_case(records),
                  _string_buffer_case(records, 'GetRoutCmdMode3', (0x60, 0x70, 0x80)),
                  _string_buffer_case(records, 'GetRoutCmdMode4', (0x11, 0x14, 0x12, 0x13))))
    cases.extend((_remaining_act_word_case(records, 'GetActCmdMode3'),
                  _act_mode_five_case(records),
                  _remaining_act_word_case(records, 'GetActCmdMode7'),
                  _routine_mode_five_case(records)))
    return {'status': 'source-native-buffer-candidates', 'nativeLibrarySha256': NATIVE_LINK_SHA256,
            'inputBasis': 'declared-source-fields-before-caller-filter-and-postprocessing', 'cases': cases,
            'undeclaredNativeWordSourceIndices': [i for i, r in enumerate(records) if r['sourceFieldA'] is None],
            'callerPostprocessingApplied': False, 'actualCallerSelected': False,
            'completeNativeInputQualified': False, 'internalHeaderSemanticsQualified': False,
            'wirePayloadQualified': False, 'recipeQualified': False, 'executionEnabled': False,
            'liveVerified': False, 'requestHex': None, 'writePayload': None}
