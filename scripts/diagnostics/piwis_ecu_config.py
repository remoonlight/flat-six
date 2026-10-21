"""Pinned ECU configuration mapping bodies and retained index candidates.

Reads static definitions, not vehicle original coding. Native map field names
remain neutral; update/selection, ECU identity and runtime access are unqualified.
"""
from collections import Counter
import json
from pathlib import Path
import zlib

from .piwis_database import REQUEST_KERNEL_SHA256, RECORD_LIMIT, digest, source
from .piwis_data_objects import definition_value, finish_layout
from .piwis_parameters import Reader, UnsupportedLayout

SOURCE_EVIDENCE_RVAS = {
    'infoRegistration': 0x24d0, 'infoFactory': 0xd9820,
    'infoConstructor': 0xd9da0, 'infoVtable': 0x5c1368, 'infoLoader': 0xdaed0,
    'dataMappingLoader': 0xdaf30, 'recordMappingLoader': 0xdb070,
    'dataMappingGroupCount': 0xdaf4f, 'dataMappingGroupKey': 0xdaf63,
    'dataMappingPairCount': 0xdaf72, 'dataMappingFirstField': 0xdaf95,
    'dataMappingSecondField': 0xdafa1, 'recordMappingGroupCount': 0xdb092,
    'recordMappingGroupKey': 0xdb0b3, 'recordMappingPairCount': 0xdb0be,
    'recordMappingFirstField': 0xdb0f2, 'recordMappingSecondField': 0xdb0fd,
    'recordMappingValue': 0xdb10c, 'uint32Loader': 0xb83c0,
    'dataRegistration': 0x5ba0, 'dataFactory': 0x122770,
    'dataConstructor': 0x1227a0, 'dataVtable': 0x5ccde0, 'dataLoader': 0x1258e0,
    'recordRegistration': 0x6f80, 'recordFactory': 0x12ec90,
    'recordConstructor': 0x12ecc0, 'recordVtable': 0x5cf808, 'recordLoader': 0x133a90,
    'dataRecordRegistration': 0x8390, 'dataRecordFactory': 0x140840,
    'dataRecordConstructor': 0x140870, 'dataRecordVtable': 0x5d20f0, 'dataRecordLoader': 0x144a90,
    'dataPrefixStart': 0x1259e5, 'recordPrefixStart': 0x133ac9,
    'dataRecordRefsVtable': 0x5cf9a0, 'dataRecordRefsLoader': 0xdba80,
    'dataRecordRefsCollectionLoader': 0xdc830, 'dataRecordRefsCount': 0xdc8c4,
    'dataRecordRefsKey': 0xdc976, 'dataRecordRefsItemVtable': 0x5c1310,
    'dataRecordRefsItemLoader': 0x4cb60, 'dataRecordRefsBaseLoader': 0x4c9b0,
    'dataRecordRefsAttributeCountLoader': 0x4ef80,
    'recordAudienceLoader': 0x117490, 'recordConfigurationIdValue': 0x133b35,
    'recordConfigurationIdFlagSetter': 0x132e50, 'recordConfigurationIdItemLoader': 0x134730,
    'recordDataIdFlagSetter': 0x132f60, 'recordDataIdItemLoader': 0x134a80,
    'recordDataRefsConstructor': 0x145a80, 'recordDataRefsVtable': 0x5d22c0,
    'recordDataRefsLoader': 0x495a0, 'recordDataRefsCollectionLoader': 0x4d320,
    'recordDataRefsItemVtable': 0x59c190, 'dataRecordPrefixStart': 0x144ac3,
    'dataRecordBytefieldCall': 0x144b32, 'dataRecordBytefieldLoader': 0xa57d0,
    'dataRecordBytefieldLength': 0xa5803, 'dataRecordFormatLoader': 0xf2880,
    'dataRecordFormatSetter': 0x144270, 'dataRecordIdValueCall': 0x144b50,
    'dataRecordVirtualBaseTable': 0x5d2140, 'dataRecordIdGetter': 0x141510,
    'dataRecordCodingDataLoader': 0x1450e0, 'codingDataRegistration': 0x50a0,
    'codingDataFactory': 0x11a920, 'codingDataConstructor': 0x11a950,
    'codingDataVtable': 0x5cb048, 'codingDataLoader': 0x11b070,
    'codingDataVirtualBaseTable': 0x5cb098, 'codingDataInterfaceVtable': 0x5caff8,
    'codingDataFileNameGetter': 0x11ac20, 'codingDataLateBoundGetter': 0x11ae50,
    'dataRecordSdgPresence': 0x144c08, 'dataRecordEmptySdgRefs': 0x277750,
    'dataRecordKeyGetter': 0x141ed0, 'dataRecordRuleGetter': 0x141ff0,
    'dataRecordValueSizeGetter': 0x1421b0, 'dataRecordValueSizeRead': 0x144dae,
    'dataRecordFieldC0Read': 0x144dbd, 'dataRecordEmptyAdditionalAudiences': 0x277ab0,
    'dataRecordAudienceF8Presence': 0x144dfe, 'dataRecordAudienceD8Presence': 0x144f3c,
}
MAPPING_FIELDS = (0x10, 0x20, 0x30, 0x40)
GROUP_LIMIT = 4096
PAIR_LIMIT = 65536
PREFIX_FIELDS = (('nativeField30', 'AStringData'), ('nativeField34', 'AStringData'),
    ('nativeField38', 'AStringData'), ('description', 'UStringData'),
    ('longName', 'UStringData'), ('shortName', 'AStringData'))
CODING_DATA_FIELDS = (('shortName', 'AStringData'), ('longName', 'UStringData'),
    ('description', 'UStringData'), ('nativeField38', 'AStringData'),
    ('nativeField34', 'AStringData'), ('nativeField30', 'AStringData'),
    ('fileName', 'AStringData'))


def data_record_core_prefix(raw, tables, *, kernel_sha256):
    """Static DataRecord prefix and embedded CodingData, never vehicle values.

    The vector bytefield has a u32 length, unlike a typed MCDValue bytefield.
    File names may be late-bound patterns; no file or OEM firmware is resolved.
    """
    if len(raw) < 2 or int.from_bytes(raw[:2], 'little') != 0x18a:
        return None
    result = {'recordType': 0x18a, 'nativeClass': 'MCDDbDataRecordImpl',
        'prefixLayoutComplete': False, 'fieldLayoutComplete': False,
        'sourceByteOrder': 'retained-little-endian', 'runtimeStreamByteOrderVerified': False,
        'runtimeReferenceResolutionVerified': False, 'runtimeMappingSelectionVerified': False,
        'itemDeclarationSemanticsVerified': False, 'actualOemFileQualified': False,
        'actualVehicleOriginalCoding': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False}
    reader, boundary = Reader(raw, 2, tables), 2
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise UnsupportedLayout('ecu-config-kernel-profile-mismatch')
        if len(raw) > RECORD_LIMIT:
            raise UnsupportedLayout('ecu-config-record-size-limit')
        result['fields'] = {name: reader.string(pool) for name, pool in PREFIX_FIELDS}
        boundary = reader.pos
        result['audiencePresence'] = reader.presence()
        if result['audiencePresence']:
            raise UnsupportedLayout('ecu-config-audience-body-unqualified')
        boundary = reader.pos
        data = {'recordOffset': reader.pos, 'presence': reader.presence(),
            'valueOrigin': 'static-data-record-definition', 'actualVehicleOriginalCoding': False}
        result['byteField'] = data
        if data['presence']:
            data['declaredBytes'] = reader.uint(4)
            if data['declaredBytes'] > RECORD_LIMIT:
                raise UnsupportedLayout('ecu-config-bytefield-size-limit')
            data['rawHex'] = reader.take(data['declaredBytes']).hex()
        else:
            data['rawHex'] = ''
        data['endOffset'] = reader.pos
        boundary = reader.pos
        result['dataFormat'] = {'recordOffset': reader.pos, 'nativeU16': reader.uint(2),
            'meaningVerified': False, 'endOffset': reader.pos}
        boundary = reader.pos
        result['dataId'] = definition_value(reader)
        result['dataIdOrigin'] = 'static-data-record-definition'
        result['diagnosticDidVerified'] = False
        boundary = reader.pos
        result['hasCodingDataFlag'] = reader.presence()
        boundary = reader.pos
        result['codingDataPresence'] = reader.presence()
        boundary = reader.pos
        if result['codingDataPresence']:
            coding = {'recordOffset': reader.pos, 'recordType': reader.uint(2),
                'nativeClass': 'MCDDbCodingDataImpl', 'embeddedBodyLayoutComplete': False,
                'actualOemFileQualified': False, 'executionEnabled': False}
            result['codingData'] = coding
            if coding['recordType'] != 0x187:
                raise UnsupportedLayout('ecu-config-coding-data-type-unqualified-' + hex(coding['recordType']))
            coding['fields'] = {name: reader.string(pool) for name, pool in CODING_DATA_FIELDS}
            coding['isLateBound'] = bool(reader.presence())
            coding.update(endOffset=reader.pos, embeddedBodyLayoutComplete=True,
                fileNameMeaningVerified=True, lateBoundFlagMeaningVerified=True,
                lateBoundResolutionVerified=False)
            boundary = reader.pos
        result.update(prefixLayoutComplete=True, unsupportedReason=None, prefixEndOffset=reader.pos)
    except (ValueError, UnsupportedLayout) as exc:
        result['unsupportedReason'] = str(exc)
    return finish_layout(result, raw, boundary, reader.pos)


def data_record_body_prefix(raw, tables, *, kernel_sha256):
    """Native DataRecord loader through absent SDG/additional-audience tails.

    Preserve the envelope and neutral C0 field. A value-size declaration has no
    qualified unit and cannot become a vehicle request/response length.
    """
    result = data_record_core_prefix(raw, tables, kernel_sha256=kernel_sha256)
    if result is None:
        return None
    result.update(corePrefixLayoutComplete=result['prefixLayoutComplete'],
        loaderBodyLayoutComplete=False, nativeFieldC0MeaningVerified=False,
        valueSizeUnitVerified=False, additionalAudienceRolesVerified=False)
    if not result['prefixLayoutComplete']:
        return result
    result['corePrefixEndOffset'] = result.pop('prefixEndOffset')
    result['prefixLayoutComplete'] = False
    reader = Reader(raw, result['corePrefixEndOffset'], tables)
    boundary = reader.pos
    try:
        result['specialDataGroupPresence'] = reader.presence()
        if result['specialDataGroupPresence']:
            raise UnsupportedLayout('ecu-config-special-data-group-body-unqualified')
        boundary = reader.pos
        result['key'] = reader.string('AStringData')
        boundary = reader.pos
        result['rule'] = reader.string('AStringData')
        boundary = reader.pos
        result['configurationRecordValueSize'] = {'recordOffset': reader.pos,
            'nativeU32': reader.uint(4), 'endOffset': reader.pos, 'unitVerified': False}
        boundary = reader.pos
        result['nativeFieldC0'] = reader.string('AStringData')
        boundary = reader.pos
        for field in ('additionalAudienceF8Presence', 'additionalAudienceD8Presence'):
            result[field] = reader.presence()
            if result[field]:
                raise UnsupportedLayout('ecu-config-' + field + '-body-unqualified')
            boundary = reader.pos
        result.update(prefixLayoutComplete=True, loaderBodyLayoutComplete=True,
            unsupportedReason=None, prefixEndOffset=reader.pos)
    except (ValueError, UnsupportedLayout) as exc:
        result['unsupportedReason'] = str(exc)
    return finish_layout(result, raw, boundary, reader.pos)


def ecu_config_mapping_layout(raw, tables, *, kernel_sha256):
    if len(raw) < 2 or int.from_bytes(raw[:2], 'little') != 0x21:
        return None
    result = {'recordType': 0x21, 'nativeClass': 'DbEcuConfigInfo', 'mappings': [],
        'fieldLayoutComplete': False, 'runtimeMappingSelectionVerified': False,
        'sourceByteOrder': 'retained-little-endian', 'runtimeStreamByteOrderVerified': False,
        'mapInsertionSemanticsVerified': False, 'actualVehicleOriginalCoding': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    reader = Reader(raw, 2, tables)
    boundary, total = 2, 0
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise UnsupportedLayout('ecu-config-kernel-profile-mismatch')
        if len(raw) > RECORD_LIMIT:
            raise UnsupportedLayout('ecu-config-record-size-limit')
        for field in MAPPING_FIELDS:
            mapping = {'nativeField': field, 'recordOffset': reader.pos,
                'declaredGroups': reader.uint(4), 'groups': []}
            result['mappings'].append(mapping)
            if mapping['declaredGroups'] > GROUP_LIMIT:
                raise UnsupportedLayout('ecu-config-group-count-limit')
            seen = set()
            for _ in range(mapping['declaredGroups']):
                group = {'recordOffset': reader.pos, 'key': reader.string('AStringData'),
                         'declaredPairs': reader.uint(4), 'pairs': []}
                mapping['groups'].append(group)
                # Preserve serialized sequence, but do not guess map replacement
                # or merging for repeated keys until the insertion helper is pinned.
                token = group['key']['token']
                if (group['key'].get('storage') != 'table-reference'
                        or group['key'].get('resolved') is not True):
                    raise UnsupportedLayout('ecu-config-map-key-identity-unqualified')
                if token in seen:
                    raise UnsupportedLayout('ecu-config-duplicate-map-key-unqualified')
                seen.add(token)
                total += group['declaredPairs']
                if group['declaredPairs'] > PAIR_LIMIT or total > PAIR_LIMIT:
                    raise UnsupportedLayout('ecu-config-pair-count-limit')
                for _ in range(group['declaredPairs']):
                    pair = {'recordOffset': reader.pos,
                        'serializedField1': reader.string('AStringData'),
                        'serializedField2': reader.string('AStringData'),
                        'expectedTargetRecordType': 0x184 if field == 0x40 else 0x180,
                        'runtimeReferenceResolutionVerified': False}
                    group['pairs'].append(pair)
                    if field == 0x40:
                        pair['value'] = definition_value(reader)
                        pair['valueOrigin'] = 'static-configuration-mapping-definition'
                        pair['actualVehicleOriginalCoding'] = False
                    pair['endOffset'] = reader.pos
                group['endOffset'] = reader.pos
            mapping['endOffset'] = reader.pos
            boundary = reader.pos
        result.update(fieldLayoutComplete=True, unsupportedReason=None,
                      totalSerializedPairs=total, layoutEndOffset=reader.pos)
    except (ValueError, UnsupportedLayout) as exc:
        result['unsupportedReason'] = str(exc)
    return finish_layout(result, raw, boundary, reader.pos)


def _target_prefix(raw, tables, expected):
    if len(raw) < 2 or int.from_bytes(raw[:2], 'little') != expected:
        raise ValueError('ecu-config-target-record-type-mismatch')
    reader = Reader(raw, 2, tables)
    fields = {name: reader.string(pool) for name, pool in PREFIX_FIELDS}
    return {'recordType': expected, 'fields': fields, 'prefixEndOffset': reader.pos,
        'prefixSha256': digest(raw[:reader.pos]), 'remainingRawHex': raw[reader.pos:].hex(),
        'fieldLayoutComplete': False, 'recordEnvelopeVerified': False,
        'actualVehicleOriginalCoding': False, 'executionEnabled': False}


def _decode_indexed_record(blob, target):
    encoded = blob[target['sourceOffset']:target['sourceOffset'] + target['compressedBytes']]
    decompressor = zlib.decompressobj()
    decoded = decompressor.decompress(encoded, RECORD_LIMIT + 1)
    if (not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail
            or len(encoded) != target['compressedBytes']
            or len(decoded) != target['decompressedBytes'] or digest(decoded) != target['recordSha256']):
        raise ValueError('ecu-config-target-record-identity-mismatch')
    return decoded


def config_data_record_reference_prefix(raw, tables, *, kernel_sha256):
    """Known reference collection prefix only; the rest of Data stays unread."""
    if len(raw) < 2 or int.from_bytes(raw[:2], 'little') != 0x180:
        return None
    result = {'recordType': 0x180, 'nativeClass': 'MCDDbConfigurationDataImpl',
        'prefixLayoutComplete': False, 'fieldLayoutComplete': False, 'references': [],
        'sourceByteOrder': 'retained-little-endian', 'runtimeStreamByteOrderVerified': False,
        'runtimeReferenceResolutionVerified': False, 'mapInsertionSemanticsVerified': False,
        'runtimeMappingSelectionVerified': False, 'actualVehicleOriginalCoding': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    reader, boundary = Reader(raw, 2, tables), 2
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise UnsupportedLayout('ecu-config-kernel-profile-mismatch')
        if len(raw) > RECORD_LIMIT:
            raise UnsupportedLayout('ecu-config-record-size-limit')
        result['fields'] = {name: reader.string(pool) for name, pool in PREFIX_FIELDS}
        boundary = reader.pos
        result['collectionOffset'] = reader.pos
        result['declaredReferences'] = reader.uint(2)
        if result['declaredReferences'] > GROUP_LIMIT:
            raise UnsupportedLayout('ecu-config-reference-count-limit')
        seen = set()
        for _ in range(result['declaredReferences']):
            entry = {'recordOffset': reader.pos, 'key': reader.string('AStringData')}
            result['references'].append(entry)
            key = entry['key']
            if key.get('storage') != 'table-reference' or key.get('resolved') is not True or key.get('nullReference'):
                raise UnsupportedLayout('ecu-config-reference-key-identity-unqualified')
            if key['token'] in seen:
                raise UnsupportedLayout('ecu-config-duplicate-reference-key-unqualified')
            seen.add(key['token'])
            entry['reference'] = reader.reference(attributed=True)
            entry['value'] = definition_value(reader)
            entry.update(endOffset=reader.pos, expectedTargetRecordType=0x184,
                valueOrigin='static-configuration-reference-definition',
                actualVehicleOriginalCoding=False, runtimeReferenceResolutionVerified=False)
            boundary = reader.pos
        result.update(prefixLayoutComplete=True, unsupportedReason=None, prefixEndOffset=reader.pos)
        boundary = reader.pos
    except (ValueError, UnsupportedLayout) as exc:
        result['unsupportedReason'] = str(exc)
    return finish_layout(result, raw, boundary, reader.pos)


def config_record_data_reference_prefix(raw, tables, *, kernel_sha256):
    """Known Record prefix; present audience/ID-item bodies remain unsupported."""
    if len(raw) < 2 or int.from_bytes(raw[:2], 'little') != 0x184:
        return None
    result = {'recordType': 0x184, 'nativeClass': 'MCDDbConfigurationRecordImpl',
        'prefixLayoutComplete': False, 'fieldLayoutComplete': False, 'references': [],
        'sourceByteOrder': 'retained-little-endian', 'runtimeStreamByteOrderVerified': False,
        'runtimeReferenceResolutionVerified': False, 'mapInsertionSemanticsVerified': False,
        'runtimeMappingSelectionVerified': False, 'itemDeclarationSemanticsVerified': False,
        'attributeMeaningVerified': False, 'actualVehicleOriginalCoding': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    reader, boundary = Reader(raw, 2, tables), 2
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise UnsupportedLayout('ecu-config-kernel-profile-mismatch')
        if len(raw) > RECORD_LIMIT:
            raise UnsupportedLayout('ecu-config-record-size-limit')
        result['fields'] = {name: reader.string(pool) for name, pool in PREFIX_FIELDS}
        boundary = reader.pos
        result['audiencePresence'] = reader.presence()
        if result['audiencePresence']:
            raise UnsupportedLayout('ecu-config-audience-body-unqualified')
        boundary = reader.pos
        result['configurationId'] = definition_value(reader)
        result['configurationIdOrigin'] = 'static-configuration-record-definition'
        boundary = reader.pos
        for declared, present, reason in (
                ('hasConfigurationIdFlag', 'configurationIdItemPresence', 'ecu-config-configuration-id-item-body-unqualified'),
                ('hasDataIdFlag', 'dataIdItemPresence', 'ecu-config-data-id-item-body-unqualified')):
            result[declared] = reader.presence()
            boundary = reader.pos
            result[present] = reader.presence()
            if result[present]:
                raise UnsupportedLayout(reason)
            boundary = reader.pos
        result['collectionOffset'] = reader.pos
        result['declaredReferences'] = reader.uint(2)
        if result['declaredReferences'] > GROUP_LIMIT:
            raise UnsupportedLayout('ecu-config-reference-count-limit')
        seen = set()
        for _ in range(result['declaredReferences']):
            entry = {'recordOffset': reader.pos, 'key': reader.string('AStringData')}
            result['references'].append(entry)
            key = entry['key']
            if key.get('storage') != 'table-reference' or key.get('resolved') is not True or key.get('nullReference'):
                raise UnsupportedLayout('ecu-config-reference-key-identity-unqualified')
            if key['token'] in seen:
                raise UnsupportedLayout('ecu-config-duplicate-reference-key-unqualified')
            seen.add(key['token'])
            entry['reference'] = reader.reference(attributed=True)
            entry['value'] = definition_value(reader)
            entry.update(endOffset=reader.pos, expectedTargetRecordType=0x18a,
                valueOrigin='static-configuration-record-reference-definition',
                actualVehicleOriginalCoding=False, runtimeReferenceResolutionVerified=False,
                attributeMeaningVerified=False)
            boundary = reader.pos
        result.update(prefixLayoutComplete=True, unsupportedReason=None, prefixEndOffset=reader.pos)
        boundary = reader.pos
    except (ValueError, UnsupportedLayout) as exc:
        result['unsupportedReason'] = str(exc)
    return finish_layout(result, raw, boundary, reader.pos)


def write_ecu_config_data_reference_targets(prefix_path, indexed_sources, tables, output_path, *, kernel_sha256):
    return _write_ecu_config_reference_targets(prefix_path, indexed_sources, tables, output_path,
        kernel_sha256=kernel_sha256, record_references=False)


def write_ecu_config_record_reference_targets(prefix_path, indexed_sources, tables, output_path, *, kernel_sha256):
    return _write_ecu_config_reference_targets(prefix_path, indexed_sources, tables, output_path,
        kernel_sha256=kernel_sha256, record_references=True)


def _write_ecu_config_reference_targets(prefix_path, indexed_sources, tables, output_path, *, kernel_sha256, record_references):
    """Exact retained container/ID/short-name candidates, never runtime links."""
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('ecu-config-kernel-profile-mismatch')
    if record_references:
        parent_type, target_type, parent_field, count_name = 0x184, 0x18a, 'configurationRecord', 'configurationRecords'
        parent_parser = config_record_data_reference_prefix
        source_error = 'ecu-config-record-index-source-mismatch'
        identity_error = 'ecu-config-record-identity-mismatch'
        prefix_error = 'ecu-config-record-prefix-identity-mismatch'
    else:
        parent_type, target_type, parent_field, count_name = 0x180, 0x184, 'dataObject', 'dataRecords'
        parent_parser = config_data_record_reference_prefix
        source_error = 'ecu-config-data-index-source-mismatch'
        identity_error = 'ecu-config-data-record-identity-mismatch'
        prefix_error = 'ecu-config-data-prefix-identity-mismatch'
    counts, blobs, prefixes = Counter(), {}, {}
    with prefix_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            row = json.loads(line); layout, src = row['layout'], row['source']
            counts[count_name] += 1
            if layout['prefixLayoutComplete'] is not True:
                counts[count_name + 'Blocked'] += 1
                continue
            indexed = indexed_sources.get(src['file'])
            if indexed is None or indexed['databaseSource'] != src:
                raise ValueError(source_error)
            if src['file'] not in blobs:
                raw, current = source(src['file'])
                if current != src:
                    raise ValueError('ecu-config-source-changed-during-binding')
                blobs[src['file']] = raw
            parent = next((target for target in indexed['objects'].values()
                if target['sourceOffset'] == row['sourceOffset']), None)
            if (parent is None or parent['recordType'] != parent_type
                    or parent['recordSha256'] != row['recordSha256']
                    or parent['compressedBytes'] != row['compressedBytes'] or parent['record'] != row['record']):
                raise ValueError(identity_error)
            parent_raw = _decode_indexed_record(blobs[src['file']], parent)
            if parent_parser(parent_raw, tables, kernel_sha256=kernel_sha256) != layout:
                raise ValueError(prefix_error)
            counts[count_name + 'Validated'] += 1
            if record_references:
                counts['staticConfigurationIdType:' + str(layout['configurationId']['nativeType'])] += 1
                counts['collectionSize:' + str(layout['declaredReferences'])] += 1
            for entry in layout['references']:
                counts['references'] += 1
                counts['staticValueType:' + str(entry['value']['nativeType'])] += 1
                target, prefix = None, None
                binding = {'ok': False, 'reason': None, 'staticIndexTargetMatched': False,
                    'containerFileNameMatched': False, 'targetShortNameMatched': False,
                    'runtimeReferenceResolutionVerified': False, 'runtimeStreamByteOrderVerified': False,
                    'runtimeMappingSelectionVerified': False, 'actualVehicleOriginalCoding': False,
                    'ecuApplicabilityVerified': False, 'executionEnabled': False}
                if record_references:
                    binding['attributeMeaningVerified'] = False
                try:
                    obj, container = (entry['reference'][key] for key in ('nativeField08', 'nativeField0c'))
                    if (obj.get('storage') != 'table-reference' or obj.get('resolved') is not True
                            or obj.get('nullReference') or type(obj.get('id')) is not int):
                        raise ValueError('ecu-config-target-object-id-unqualified')
                    # Literal retained filename match only. Runtime container
                    # resolution/update/variant selection remains unqualified.
                    expected_container = Path(src['file']).name.removesuffix('.db')
                    if (container.get('resolved') is not True or container.get('nullReference')
                            or container.get('text') != expected_container):
                        raise ValueError('ecu-config-target-container-file-mismatch')
                    target = indexed['objects'].get(obj['id'])
                    if target is None:
                        raise ValueError('ecu-config-target-index-object-missing')
                    if target['recordType'] != target_type:
                        raise ValueError('ecu-config-target-record-type-mismatch')
                    cache_key = (src['file'], target['sourceOffset'])
                    if cache_key not in prefixes:
                        target_raw = _decode_indexed_record(blobs[src['file']], target)
                        prefixes[cache_key] = _target_prefix(target_raw, tables, target_type)
                    prefix = prefixes[cache_key]
                    actual, declared = prefix['fields']['shortName'], entry['key']
                    if (actual.get('resolved') is not True or actual.get('nullReference')
                            or actual['token'] != declared['token'] or actual.get('text') != declared.get('text')):
                        raise ValueError('ecu-config-target-short-name-mismatch')
                    binding.update(ok=True, staticIndexTargetMatched=True,
                        containerFileNameMatched=True, targetShortNameMatched=True)
                except (ValueError, zlib.error) as exc:
                    binding['reason'] = str(exc)
                counts['boundReferences' if binding['ok'] else 'blocked:' + binding['reason']] += 1
                output.write(json.dumps({'source': src, 'sourceOffset': row['sourceOffset'],
                    'recordSha256': row['recordSha256'], parent_field: parent, 'entry': entry,
                    'target': target, 'targetRecordPrefix': prefix, 'binding': binding}, ensure_ascii=True) + '\n')
    counts['uniqueTargetPrefixes'] = len(prefixes)
    return dict(counts)


def write_ecu_config_mapping_targets(mapping_path, indexed_sources, tables, output_path, *, kernel_sha256):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('ecu-config-kernel-profile-mismatch')
    counts, blobs, prefixes = Counter(), {}, {}
    with mapping_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            mapping_record = json.loads(line); layout = mapping_record['layout']
            counts['mappingRecords'] += 1
            if layout['fieldLayoutComplete'] is not True:
                counts['mappingRecordsBlocked'] += 1
                continue
            counts['mappingRecordsComplete'] += 1
            src = mapping_record['source']; indexed = indexed_sources.get(src['file'])
            if indexed is None or indexed['databaseSource'] != src:
                raise ValueError('ecu-config-mapping-index-source-mismatch')
            if src['file'] not in blobs:
                raw, current = source(src['file'])
                if current != src:
                    raise ValueError('ecu-config-source-changed-during-binding')
                blobs[src['file']] = raw
            root_target = next((target for target in indexed['objects'].values()
                if target['sourceOffset'] == mapping_record['sourceOffset']), None)
            if (root_target is None or root_target['recordType'] != 0x21
                    or root_target['recordSha256'] != mapping_record['recordSha256']
                    or root_target['compressedBytes'] != mapping_record['compressedBytes']
                    or root_target['record'] != mapping_record['record']):
                raise ValueError('ecu-config-root-record-identity-mismatch')
            root_raw = _decode_indexed_record(blobs[src['file']], root_target)
            if ecu_config_mapping_layout(root_raw, tables, kernel_sha256=kernel_sha256) != layout:
                raise ValueError('ecu-config-root-layout-identity-mismatch')
            counts['rootRecordsValidated'] += 1
            for mapping in layout['mappings']:
                for group in mapping['groups']:
                    for pair in group['pairs']:
                        counts['serializedPairs'] += 1
                        expected = pair['expectedTargetRecordType']
                        target, prefix = None, None
                        binding = {'ok': False, 'reason': None, 'staticIndexTargetMatched': False,
                            'targetShortNameMatched': False, 'runtimeReferenceResolutionVerified': False,
                            'runtimeStreamByteOrderVerified': False,
                            'runtimeMappingSelectionVerified': False, 'actualVehicleOriginalCoding': False,
                            'ecuApplicabilityVerified': False, 'executionEnabled': False}
                        try:
                            identity = pair['serializedField1']
                            if (identity.get('pool') != 'AStringData' or identity.get('storage') != 'table-reference'
                                    or identity.get('resolved') is not True or type(identity.get('id')) is not int):
                                raise ValueError('ecu-config-target-object-id-unqualified')
                            target = indexed['objects'].get(identity['id'])
                            if target is None:
                                raise ValueError('ecu-config-target-index-object-missing')
                            if target['recordType'] != expected:
                                raise ValueError('ecu-config-target-record-type-mismatch')
                            key = (src['file'], target['sourceOffset'])
                            if key not in prefixes:
                                decoded = _decode_indexed_record(blobs[src['file']], target)
                                prefixes[key] = _target_prefix(decoded, tables, expected)
                            prefix = prefixes[key]
                            actual, declared = prefix['fields']['shortName'], pair['serializedField2']
                            if (actual.get('resolved') is not True or declared.get('resolved') is not True
                                    or actual.get('nullReference') or declared.get('nullReference')
                                    or actual.get('pool') != 'AStringData' or declared.get('pool') != 'AStringData'
                                    or actual['token'] != declared['token'] or actual.get('text') != declared.get('text')):
                                raise ValueError('ecu-config-target-short-name-mismatch')
                            binding.update(ok=True, staticIndexTargetMatched=True, targetShortNameMatched=True)
                        except (ValueError, zlib.error) as exc:
                            binding['reason'] = str(exc)
                        counts['boundPairs' if binding['ok'] else 'blocked:' + binding['reason']] += 1
                        counts['targetType:' + str(expected)] += 1
                        if mapping['nativeField'] == 0x40:
                            counts['staticValueType:' + str(pair['value']['nativeType'])] += 1
                        output.write(json.dumps({'source': src, 'sourceOffset': mapping_record['sourceOffset'],
                            'recordSha256': mapping_record['recordSha256'], 'nativeMappingField': mapping['nativeField'],
                            'groupKey': group['key'], 'pair': pair, 'target': target,
                            'targetRecordPrefix': prefix, 'binding': binding}, ensure_ascii=True) + '\n')
    counts['uniqueTargetPrefixes'] = len(prefixes)
    return dict(counts)
