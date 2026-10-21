"""Static table/structure fields; no native execution or vehicle PDU decoding."""
from .piwis_database import RECORD_LIMIT
from .piwis_data_objects import finish_layout
from .piwis_parameters import (Reader, UnsupportedLayout, base_parameter_fields,
                              parameter_collection)

COLLECTION_LIMIT = 4096
SOURCE_EVIDENCE_RVAS = {
    'tableRegistration': 0x140c0, 'tableFactory': 0x2352b0,
    'tableConstructor': 0x2352e0, 'tableVtable': 0x5fa648, 'tableLoader': 0x235bb0,
    'tableKeyMapGetter': 0x2358a0, 'tableSemanticGetter': 0x236bf0,
    'tableKeyDopGetter': 0x236ff0, 'namedReferenceLoader': 0xde200,
    'namedReferenceConfigurationBranch': 0xde312,
    'diagComReferenceCollectionLoader': 0x9ece0, 'diagComReferenceLoader': 0x9fb70,
    'diagComStringVectorLoader': 0x9f8e0,
    'structureRegistration': 0x24a0, 'structureFactory': 0xd8540,
    'structureVtable': 0x5c0db8, 'structureLoader': 0xd8f60,
    'structureByteLengthGetter': 0xd8650, 'structureParametersObjectLoader': 0xcb770,
    'structureParametersRegistration': 0x113a0, 'structureParametersFactory': 0x202350,
    'structureParametersConstructor': 0x202380, 'structureParametersVtable': 0x5f1110,
    'structureParametersLoader': 0x2026d0,
    'tableRowRegistration': 0x140f0, 'tableRowFactory': 0x237780,
    'tableRowLoader': 0x2388f0, 'tableRowAudienceObjectLoader': 0x117490,
    'tableRowBaseParameterLoader': 0x1f7620,
    'tableRowNamedStreamsBranch': 0x238cbf,
    'namedStreamsLoader': 0xa3900, 'namedStreamsConstructor': 0xa35b0,
    'namedStreamsOpenMarker': 0x5b8c90, 'namedStreamsCloseMarker': 0x5b8c94,
    'tableRowFlagStreamName': 0x92a537, 'tableRowPreconditionsStreamName': 0x92a535,
    'tableRowPreconditionsVtable': 0x5c5ab0, 'tableRowPreconditionsLoader': 0xf12e0,
}


def header_fields(reader, pools, destinations):
    return [{'nativeDestinationOffset': dest, **reader.string(pool)}
            for pool, dest in zip(pools, destinations)]


def diag_com_references(reader):
    count = reader.uint(2)
    if count > COLLECTION_LIMIT:
        raise UnsupportedLayout('table-diag-com-count-limit')
    items = []
    for _ in range(count):
        name = reader.string('AStringData')
        if name.get('nullReference'):
            raise UnsupportedLayout('null-table-diag-com-name')
        reference = reader.reference(attributed=True)
        reference['nativeField2cRaw'] = reader.uint(1)
        reference['nativeField28Raw'] = reader.uint(2)
        reference['nativeField2dPresent'] = bool(reader.presence())
        if reference['nativeField2dPresent']:
            n = reader.uint(2)
            if n > COLLECTION_LIMIT:
                raise UnsupportedLayout('table-diag-com-string-vector-count-limit')
            reference['nativeField30Strings'] = [reader.string('AStringData') for _ in range(n)]
        items.append({'name': name, 'reference': reference})
    return {'declaredReferences': count, 'items': items, 'serviceSemanticsVerified': False}


def named_streams(raw, start):
    """Strict retained depth-zero envelope; do not scan past a missing opener."""
    if raw[start:start + 3] != b'#>\0':
        raise UnsupportedLayout('unsupported-table-row-stream-opener')
    pos, streams, names = start + 3, [], set()
    while pos < len(raw):
        name = raw[pos]
        pos += 1
        if name == 0:
            if pos != len(raw):
                raise UnsupportedLayout('table-row-stream-trailing-bytes')
            break
        if name in names:
            raise UnsupportedLayout('duplicate-table-row-stream-name')
        if len(names) >= COLLECTION_LIMIT:
            raise UnsupportedLayout('table-row-stream-count-limit')
        end = raw.find(b'#<\0', pos)
        if end < 0:
            raise UnsupportedLayout('unterminated-table-row-stream')
        streams.append({'nameRaw': name, 'recordOffset': pos, 'endOffset': end,
                        'payloadRawHex': raw[pos:end].hex()})
        names.add(name)
        pos = end + 3
    return streams


def row_named_fields(raw, start, tables):
    streams = named_streams(raw, start)
    if {s['nameRaw'] for s in streams} != {65, 66}:
        raise UnsupportedLayout('unsupported-table-row-stream-names')
    fields = {}
    for stream in streams:
        payload = bytes.fromhex(stream['payloadRawHex'])
        reader = Reader(payload, 0, tables)
        if stream['nameRaw'] == 65:
            fields['nativeField170Raw'] = reader.uint(1)
        else:
            count = reader.uint(2)
            if count > COLLECTION_LIMIT:
                raise UnsupportedLayout('table-row-precondition-count-limit')
            fields['declaredPreconditions'] = count
            if count:
                raise UnsupportedLayout('unsupported-table-row-nonempty-preconditions')
        # The source constructs each child with parent depth + 1. The empty
        # child envelope is retained; richer nested streams remain unsupported.
        if payload[reader.pos:] != b'#>\1':
            raise UnsupportedLayout('unsupported-table-row-nested-streams')
    return {'streams': streams, **fields, 'namedStreamsLayoutComplete': True,
            'preconditionSemanticsVerified': False, 'nativeFlagSemanticsVerified': False}


def definition_layout(raw, tables):
    if len(raw) < 2 or len(raw) > RECORD_LIMIT:
        raise ValueError('table-structure-record-bounds')
    tag = int.from_bytes(raw[:2], 'little')
    if tag not in (0xaa, 0xab, 0xac):
        return None
    reader, boundary = Reader(raw, 2, tables), 2
    row = {'recordType': tag, 'fieldLayoutComplete': False, 'blockedReason': None,
        'tableSemanticsVerified': False, 'physicalConversionQualified': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False,
        'nativeObjectType': {0xaa: 'DbDopStructure', 0xab: 'MCDDbTableImpl',
                             0xac: 'MCDDbTableParameterImpl'}[tag]}
    try:
        if tag == 0xab:
            row['fields'] = header_fields(reader,
                ('AStringData', 'AStringData', 'AStringData', 'UStringData', 'UStringData', 'AStringData'),
                (0x30, 0x34, 0x38, 0x20, 0x24, 0x28))
            boundary = reader.pos
            count = reader.uint(4)
            if count > COLLECTION_LIMIT:
                raise UnsupportedLayout('table-key-map-count-limit')
            row.update({'declaredKeys': count, 'keyMap': []})
            keys = set()
            boundary = reader.pos
            for _ in range(count):
                key = reader.string('UStringData')
                # Native UTF16 map keys must resolve. Refuse duplicate text,
                # including two different pool tokens denoting the same key.
                if key.get('text') is None or key['text'] in keys:
                    raise UnsupportedLayout('null-unresolved-or-duplicate-table-key')
                reference = reader.named_reference()
                keys.add(key['text'])
                row['keyMap'].append({'key': key, 'reference': reference})
                boundary = reader.pos
            row['semantic'] = reader.string('AStringData')
            boundary = reader.pos
            row['diagComReferences'] = diag_com_references(reader)
            boundary = reader.pos
            row['keyDopReference'] = reader.reference() if reader.presence() else None
            boundary = reader.pos
            if reader.presence():
                raise UnsupportedLayout('unsupported-table-special-data-groups')
            boundary = reader.pos
            row['fieldLayoutComplete'] = True
        elif tag == 0xaa:
            row['fields'] = header_fields(reader,
                ('AStringData', 'UStringData', 'AStringData', 'UStringData', 'AStringData', 'AStringData'),
                (0x48, 0x4c, 0x50, 0x54, 0x58, 0x5c))
            boundary = reader.pos
            row['serializedByteLength'] = reader.uint(2)
            boundary = reader.pos
            row['parameters'] = parameter_collection(raw, reader.pos, tables, 0x6d, 'structure')
            body = row['parameters']
            boundary, reader.pos = body['stopOffset'], body['inspectedOffset']
            row['fieldLayoutComplete'] = body['parameterCollectionLayoutComplete']
            row['blockedReason'] = body['blockedReason']
        else:
            row['key'] = reader.string('UStringData')
            boundary = reader.pos
            # These three optional audience paths require different native
            # object/collection layouts. Their absent branches are proven.
            for field in ('audience', 'disabledAudiences', 'enabledAudiences'):
                row[field + 'Present'] = bool(reader.presence())
                if row[field + 'Present']:
                    raise UnsupportedLayout('unsupported-table-row-' + field)
                boundary = reader.pos
            row['baseParameter'] = base_parameter_fields(reader)
            boundary = reader.pos
            row['rowPrefixLayoutComplete'] = True
            row['namedFields'] = row_named_fields(raw, reader.pos, tables)
            boundary = reader.pos = len(raw)
            row['fieldLayoutComplete'] = True
    except (ValueError, UnicodeError) as exc:
        row['blockedReason'] = str(exc)
    return finish_layout(row, raw, boundary, reader.pos)


def layout_references(layout):
    refs = [item['reference'] for item in layout.get('keyMap', [])]
    refs += [item['reference'] for item in layout.get('diagComReferences', {}).get('items', [])]
    if layout.get('keyDopReference') is not None:
        refs.append(layout['keyDopReference'])
    params = layout.get('parameters', {}).get('parsedParameters', [])
    if layout.get('baseParameter') is not None:
        params = [*params, layout['baseParameter']]
    refs += [parameter[field] for parameter in params
             for field in ('objectReference', 'tableReference') if field in parameter]
    return refs
