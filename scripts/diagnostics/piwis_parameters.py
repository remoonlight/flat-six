"""Data-only request/response parameter prefixes for the retained native profile.

The caller must verify the kernel hash in piwis_database.audit. Supported native
branches stop before unknown layouts; this is neither a PDU builder nor a decoder
of vehicle responses. No vendor code or vehicle transport is used.
"""
from .piwis_database import RECORD_LIMIT, digest, serialized_string

PARAMETER_LIMIT = 4096
ENCODING_STRATEGIES = {0: 'PackedBCDEncoding', 1: 'UnpackedBCDEncoding',
    2: 'OneComplementEncoding', 3: 'TwoComplementEncoding', 4: 'SignMagnitudeEncoding',
    5: 'UTF8Encoding', 6: 'UCS2Encoding', 7: 'IEEE754Encoding',
    8: 'ISO88591Encoding', 9: 'ISO88592Encoding', 10: 'Windows1252Encoding',
    11: 'NoneEncoding'}
PARAMETER_FIELDS = (
    ('description', 'UStringData', 0x20), ('longName', 'UStringData', 0x24),
    ('shortName', 'AStringData', 0x28), ('nativeField30', 'AStringData', 0x30),
    ('nativeField34', 'AStringData', 0x34), ('nativeField38', 'AStringData', 0x38),
)
PARAMETER_TYPES = dict(enumerate((
    'VALUE', 'RESERVED', 'CODED_CONST', 'PHYS_CONST', 'LENGTH_KEY',
    'MATCHING_REQUEST_PARAM', 'SYSTEM', 'DYNAMIC', 'TABLE_KEY', 'TABLE_STRUCT',
    'TABLE_ENTRY', 'GENERATED', 'NRC_CONST'), 0x7001))
SOURCE_EVIDENCE_RVAS = {
    'requestCollectionLoader': 0x1551e0, 'requestParametersLoader': 0x216220,
    'parameterOdxFlagTest': 0x1f7886, 'parameterOdxObjectLoader': 0xc35f0,
    'odxLinkRegistration': 0x3d70, 'odxLinkFactory': 0x1071a0,
    'odxLinkVtable': 0x5c8010, 'odxLinkLoader': 0x107270,
    'odxLinkIdGetter': 0x37cb0, 'odxLinkDocGetter': 0x513f0,
    'odxLinkLayerDocTypeGetter': 0x107250,
    'parametersLoader': 0x2026d0, 'parameterObjectLoader': 0xc0c90,
    'baseParameterRegistration': 0x11340, 'baseParameterFactory': 0x1f1ef0,
    'baseParameterLoader': 0x1f7620, 'parameterVtable': 0x5ef8f8,
    'odxBytePositionGetter': 0x1f2900, 'valueLoader': 0xa4c00,
    'bytePositionGetter': 0x1f28f0, 'bytePositionAvailableGetter': 0x1f6b00,
    'valueTypeSwitch': 0xa5680, 'uint32ValueBranch': 0xa548a,
    'unicodeValueBranch': 0xa5633, 'codedTypeRegistration': 0x2230,
    'codedTypeLoader': 0xbfeb0, 'bytefieldLoader': 0xa57d0,
    'bytefieldPresentBranch': 0xa57fe, 'objectReferenceVtable': 0x5988e0,
    'objectReferenceLoader': 0x31a60, 'tableKeyRegistration': 0x11430,
    'tableKeyLoader': 0x2064b0, 'tableKeyObjectLoader': 0x2070f0,
    'tableReferenceVtable': 0x5bbab0, 'attributedReferenceLoader': 0x4c9b0,
    'tableStructRegistration': 0x11460, 'tableStructFactory': 0x208510,
    'tableStructConstructor': 0x208540, 'tableStructLoader': 0x209400,
    'nrcValuesObjectLoader': 0x199e90, 'valuesRegistration': 0x1a110,
    'valuesFactory': 0x2d0270, 'valuesConstructor': 0x2d02a0,
    'valuesVtable': 0x60d3a8, 'valuesLoader': 0x2d0530,
    'matchingRequestRegistration': 0x108c0, 'matchingRequestFactory': 0x1e7cd0,
    'matchingRequestConstructor': 0x1e7d00, 'matchingRequestLoader': 0x1e8020,
    'matchingRequestBytePositionGetter': 0x1e8060, 'matchingRequestLengthGetter': 0x1e8070,
    'codedMaxLengthSetter': 0xc0720, 'codedMinLengthSetter': 0xc07f0,
    'codedTerminationSetter': 0xc08c0,
    'codedBaseDataTypeGetter': 0xc0650, 'codedEncodingGetter': 0xc0630,
    'codedByteOrderGetter': 0xc0670, 'codedCondensedMaskGetter': 0xc0690,
    'encodingStrategyRegister': 0x257080,
    'ieee754EncodingRegistration': 0x14450, 'iso88591EncodingRegistration': 0x14480,
    'iso88592EncodingRegistration': 0x144b0, 'noneEncodingRegistration': 0x144e0,
    'oneComplementEncodingRegistration': 0x14510, 'packedBCDEncodingRegistration': 0x14540,
    'signMagnitudeEncodingRegistration': 0x14570, 'twoComplementEncodingRegistration': 0x145a0,
    'ucs2EncodingRegistration': 0x145d0, 'unpackedBCDEncodingRegistration': 0x14600,
    'utf8EncodingRegistration': 0x14630, 'windows1252EncodingRegistration': 0x14660,
}


class UnsupportedLayout(ValueError):
    pass


class Reader:
    def __init__(self, raw, pos, tables):
        self.raw, self.pos, self.tables = raw, pos, tables

    def take(self, size):
        if size < 0 or self.pos + size > len(self.raw):
            raise ValueError('truncated-native-parameter-record')
        value = self.raw[self.pos:self.pos + size]
        self.pos += size
        return value

    def uint(self, size):
        return int.from_bytes(self.take(size), 'little')

    def string(self, pool):
        row, end = serialized_string(self.raw, self.pos, pool, self.tables)
        self.pos = end
        return row

    def presence(self):
        value = self.uint(1)
        if value not in (0, 1):
            raise UnsupportedLayout('unsupported-object-presence-' + str(value))
        return value

    def reference(self, attributed=False):
        # The two AString fields are not yet bound to database/vehicle objects.
        row = {'nativeField08': self.string('AStringData'),
               'nativeField0c': self.string('AStringData'), 'bindingVerified': False}
        if attributed:
            row['attributes'] = [self.string('AStringData') for _ in range(self.uint(1))]
        return row

    def named_reference(self):
        row = self.reference()
        start, value = self.pos, self.uint(4)
        # de200 selects either uint32 or AString according to configuration
        # "0012". With the high bit clear both consume exactly four bytes.
        # The runtime setting is unknown: keep the common raw field, and stop
        # before an ambiguous inline string rather than guessing its length.
        if value & 0x80000000:
            raise UnsupportedLayout('ambiguous-named-reference-encoding')
        row['nativeField10'] = {'recordOffset': start, 'raw': value,
            'configurationModeVerified': False, 'stringSemanticsVerified': False}
        return row


def serialized_value(reader, context='default-value'):
    start, value_type = reader.pos, reader.uint(1)
    value = {'recordOffset': start, 'nativeDataType': value_type}
    if value_type == 11:
        value.update({'type': 'eA_UINT32', 'value': reader.uint(4)})
    elif value_type == 14:
        value.update({'type': 'eA_UNICODE2STRING', 'string': reader.string('UStringData')})
    elif value_type in (0, 255):
        value.update({'nativeValueDefined': False, 'rawHex': ''})
    elif value_type == 1:
        value['string'] = reader.string('AStringData')
    elif value_type in (2, 3):
        value['present'] = bool(reader.presence())
        if value['present']:
            value['rawHex'] = reader.take(reader.uint(2)).hex()
    elif value_type in (4, 5, 6, 7, 10):
        value['rawHex'] = reader.take({4: 4, 5: 8, 6: 2, 7: 4, 10: 2}[value_type]).hex()
    else:
        raise UnsupportedLayout('unsupported-' + context + '-type-' + str(value_type))
    return value


def nrc_values(reader):
    row = {'present': bool(reader.presence())}
    if row['present']:
        tag = reader.uint(2)
        if tag != 0xfa:
            raise UnsupportedLayout('unsupported-nrc-values-object-' + hex(tag))
        count = reader.uint(2)
        if count > PARAMETER_LIMIT:
            raise UnsupportedLayout('nrc-value-count-limit')
        row.update({'recordType': tag, 'declaredValues': count,
                    'values': [serialized_value(reader, 'nrc-value') for _ in range(count)]})
    return row


def diag_coded_type(reader):
    """Known coded-type fields and registered strategy names; no value decoding."""
    coded = {'present': bool(reader.presence())}
    if not coded['present']:
        return coded
    coded['recordType'] = reader.uint(2)
    if coded['recordType'] != 0x23:
        raise UnsupportedLayout('unsupported-coded-type-tag-' + hex(coded['recordType']))
    coded['nativeKind'] = reader.uint(1)
    if coded['nativeKind'] not in (0, 1, 2):
        # Kind 3 has an embedded parameter with a separate unqualified branch.
        raise UnsupportedLayout('unsupported-coded-type-kind-' + str(coded['nativeKind']))
    if coded['nativeKind'] == 1:
        coded.update({'maxLength': reader.uint(4), 'minLength': reader.uint(4),
                      'nativeTerminationRaw': reader.uint(1)})
    else:
        coded['bitLength'] = reader.uint(4)
    if coded['nativeKind'] == 2:
        coded['bitMaskPresent'] = bool(reader.presence())
        if coded['bitMaskPresent']:
            coded['bitMaskRawHex'] = reader.take(reader.uint(4)).hex()
    coded['nativeEncodingFieldsRaw'] = list(reader.take(4))
    fields = coded['nativeEncodingFieldsRaw']
    coded['nativeEncodingFieldsNamed'] = {'baseDataType': fields[0], 'encoding': fields[1],
        'highLowByteOrder': fields[2], 'condensedBitMask': fields[3]}
    coded['encodingFieldNamesVerified'] = True
    coded['encodingStrategy'] = ENCODING_STRATEGIES.get(fields[1])
    coded['encodingStrategyNameVerified'] = fields[1] in ENCODING_STRATEGIES
    coded['encodingSemanticsVerified'] = False
    return coded


def odx_link(reader):
    """Pinned DbOdxLink serialization; document/ID resolution stays unqualified."""
    start = reader.pos
    row = {'recordOffset': start, 'present': bool(reader.presence()),
        'runtimeOdxResolutionVerified': False, 'referenceIdentityQualified': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    if row['present']:
        row['recordType'] = reader.uint(2)
        if row['recordType'] != 0x250:
            raise UnsupportedLayout('unsupported-odx-link-object-' + hex(row['recordType']))
        row.update(nativeClass='DbOdxLink', idRef=reader.string('AStringData'),
            docRef=reader.string('AStringData'), isLayerDocType=bool(reader.presence()))
    row['endOffset'] = reader.pos
    return row


def base_parameter_fields(reader):
    """Fields read by 1f7620, reused by table rows after their own prefix."""
    fields = []
    for name, pool, destination in PARAMETER_FIELDS:
        fields.append({'field': name, 'nativeDestinationOffset': destination, **reader.string(pool)})
    row = {'fields': fields,
           'bitPosition': reader.uint(1), 'serializedBytePosition': reader.uint(4)}
    flags = reader.uint(1)
    row.update({'nativeFlags': flags, 'bytePositionAvailable': bool(flags & 0x20),
                'isProtocolParameter': bool(flags & 0x80)})
    if flags & 1:
        row['defaultValue'] = serialized_value(reader)
    row['displayLevel'] = reader.uint(4)
    if flags & 2:
        row['semantic'] = reader.string('AStringData')
    row['sysParam'] = reader.string('AStringData')
    kind = 0x7000 + reader.uint(1)
    row.update({'parameterType': kind, 'parameterTypeName': PARAMETER_TYPES.get(kind),
                'nativeDataType': reader.uint(1)})
    if flags & 4:
        row['codedType'] = diag_coded_type(reader)
    if flags & 8:
        row['objectReference'] = reader.reference()
    if flags & 0x10:
        row['odxLink'] = odx_link(reader)
    if flags & 0x40:
        raise UnsupportedLayout('unsupported-special-data-group-extension')
    if kind == 0x7013:
        row['nrcValues'] = nrc_values(reader)
    return row


def parameter(reader):
    start = reader.pos
    if not reader.presence():
        raise UnsupportedLayout('null-parameter-object')
    tag = reader.uint(2)
    if tag not in (0xa5, 0xb2, 0xb0, 0xa7):
        raise UnsupportedLayout('unsupported-parameter-type-' + hex(tag))
    row = {'recordOffset': start, 'recordType': tag, **base_parameter_fields(reader)}
    if tag == 0xb2:
        # The inline table-key branch is a different object layout. Only the
        # absent-inline branch followed by an attributed table reference is known.
        if reader.presence():
            raise UnsupportedLayout('unsupported-inline-table-key-object')
        row['tableReference'] = reader.reference(attributed=True)
        row['nativeTableFlag'] = reader.uint(1)
        row['nativeTableString'] = reader.string('AStringData')
    elif tag == 0xb0:
        row['nativeField130'] = reader.string('AStringData')
        row['tableReference'] = reader.reference(attributed=True)
    elif tag == 0xa7:
        row['requestBytePosition'] = reader.uint(4)
        row['byteLength'] = reader.uint(4)
    row.update({'endOffset': reader.pos, 'serializedBytes': reader.pos - start,
                'serializedSha256': digest(reader.raw[start:reader.pos]),
                'parameterLayoutProfileMatched': True, 'ecuApplicabilityVerified': False})
    return row


def parameter_collection(raw, start, tables, expected_tag, kind):
    """Retain proven prefixes and exact unread bytes; never resync after a gap."""
    if len(raw) > RECORD_LIMIT or start < 0 or start > len(raw):
        raise ValueError(kind + '-parameter-source-bounds')
    reader = Reader(raw, start, tables)
    boundary, parsed, declared, blocked, tag, present = start, [], None, None, None, None
    try:
        present = bool(reader.presence())
        if present:
            tag = reader.uint(2)
            if tag != expected_tag:
                raise UnsupportedLayout('unsupported-' + kind + '-collection-type-' + hex(tag))
            declared = reader.uint(2)
            if declared > PARAMETER_LIMIT:
                raise UnsupportedLayout(kind + '-parameter-count-limit')
            boundary = reader.pos
            for _ in range(declared):
                parsed.append(parameter(reader))
                boundary = reader.pos
        else:
            declared, boundary = 0, reader.pos
    except ValueError as exc:
        blocked = str(exc)
    return {'startOffset': start, 'collectionPresent': present, 'collectionType': tag,
            'declaredParameters': declared, 'parsedParameters': parsed,
            'parameterCollectionLayoutComplete': blocked is None,
            'blockedReason': blocked, 'stopOffset': boundary, 'inspectedOffset': reader.pos,
            'remainingBytes': len(raw) - boundary, 'remainingSha256': digest(raw[boundary:]),
            'remainingRawHex': raw[boundary:].hex(), kind + 'BodyVerified': False,
            'ecuApplicabilityVerified': False, 'executionEnabled': False}


def request_parameters(raw, start, tables):
    return parameter_collection(raw, start, tables, 0x79, 'request')


def response_parameters(raw, start, tables):
    return parameter_collection(raw, start, tables, 0x92, 'response')
