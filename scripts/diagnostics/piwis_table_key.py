"""Declared TABLE-KEY field/row candidates; no complete service response.

The retained row-reference branch names one table row. Decode the explicitly
positioned identity UINT32 key and require the same canonical decimal key for
synthetic C-locale fixtures. Runtime names, constraints and ECU scope stay open.
"""
from collections import Counter
import json

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_numeric import probe_dop_numeric
from .piwis_response_numeric import _retained_reference_target

SOURCE_EVIDENCE_RVAS = {
    'tableKeyLoader': 0x2064b0, 'tableKeyVtable': 0x5f1f48,
    'tableKeyDopGetter': 0x206780, 'tableKeyTableGetter': 0x205bf0,
    'tableDopSimpleGetter': 0x236ff0, 'keyCodedTypeGetter': 0x1f5430,
    'codedTypeDispatch': 0x1f5558, 'codedTypeFromDop': 0x1f5491,
    'dataTypeGetter': 0x1f47a0, 'physicalTypeDispatch': 0x1f4934,
    'tableRowReferenceGetter': 0x206840, 'declaredRowNameGetter': 0xc6560,
    'rowReferenceFlagGetter': 0x206280, 'rowCollectionGetter': 0x236670,
    'rowNameSourceSelection': 0x236788, 'rowNameVectorAdd': 0x207f10,
    'rowNameIndexGetter': 0x2075d0, 'rowNameStringLookup': 0x20761b,
    'rowDefaultValueAvailable': 0x2069d0, 'rowDefaultKeyGetter': 0x206a10,
    'tableKeyAllowedValues': 0x205f60, 'rowKeyGetter': 0x237af0,
}


def _key_binding(parameter, table, row, key_dop, ascii_strings, kernel_sha256):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('table-key-probe-kernel-profile-mismatch')
    if (not isinstance(parameter, dict) or parameter.get('parameterLayoutProfileMatched') is not True
            or parameter.get('recordType') != 0xb2 or parameter.get('parameterType') != 0x7009
            or parameter.get('nativeDataType') != 25):
        raise ValueError('table-key-probe-parameter-kind-unqualified')
    flags, position = parameter.get('nativeFlags'), parameter.get('serializedBytePosition')
    if (type(flags) is not int or not 0 <= flags <= 255 or not flags & 0x20
            or parameter.get('bytePositionAvailable') is not True
            or type(position) is not int or not 0 <= position <= 4094):
        raise ValueError('table-key-probe-declared-position-unqualified')
    if (flags & 0x80 or parameter.get('isProtocolParameter') is not False
            or type(parameter.get('bitPosition')) is not int or parameter['bitPosition'] != 0
            or (parameter.get('codedType') or {}).get('present')):
        raise ValueError('table-key-probe-protocol-bit-or-inline-type-unqualified')
    reference = parameter.get('tableReference') or {}
    if (reference.get('attributes') != [] or type(parameter.get('nativeTableFlag')) is not int
            or parameter['nativeTableFlag'] != 1):
        raise ValueError('table-key-probe-declared-row-branch-unqualified')
    declared = parameter.get('nativeTableString') or {}
    if (declared.get('pool') != 'AStringData' or declared.get('resolved') is not True
            or declared.get('nullReference') is not False or type(declared.get('text')) is not str
            or not 1 <= len(declared['text']) <= 4096 or not isinstance(ascii_strings, dict)):
        raise ValueError('table-key-probe-declared-row-name-unresolved')
    layout = _retained_reference_target(reference, table, expected_record_type=0xab)
    items, count = layout.get('keyMap'), layout.get('declaredKeys')
    if (layout.get('fieldLayoutComplete') is not True or type(count) is not int or not 1 <= count <= 4096
            or not isinstance(items, list) or len(items) != count):
        raise ValueError('table-key-probe-table-layout-incomplete')
    matches = []
    for index, item in enumerate(items):
        token = ((item.get('reference') or {}).get('nativeField10') or {}).get('raw')
        if type(token) is not int or not 0 < token < 0x80000000:
            raise ValueError('table-key-probe-row-name-token-unqualified')
        name = (ascii_strings.get(token) or {}).get('text')
        if type(name) is not str or not name:
            raise ValueError('table-key-probe-row-name-pool-unresolved')
        if name == declared['text']:
            matches.append((index, item, token))
    if len(matches) != 1:
        raise ValueError('table-key-probe-declared-row-name-missing-or-ambiguous')
    index, item, name_token = matches[0]
    row_layout = _retained_reference_target(item.get('reference') or {}, row, expected_record_type=0xac)
    if row_layout.get('rowPrefixLayoutComplete') is not True:
        raise ValueError('table-key-probe-row-prefix-incomplete')
    key = item.get('key') or {}
    row_key = row_layout.get('key') or {}
    text = key.get('text')
    if (key.get('pool') != 'UStringData' or key.get('resolved') is not True or key.get('nullReference') is not False
            or type(text) is not str or not text.isascii() or not text.isdecimal() or len(text) > 10
            or not 0 <= int(text) <= 0xffffffff or str(int(text)) != text
            or row_key.get('pool') != 'UStringData' or row_key.get('resolved') is not True
            or row_key.get('nullReference') is not False or row_key.get('text') != text):
        raise ValueError('table-key-probe-row-key-unqualified')
    dop = _retained_reference_target(layout.get('keyDopReference') or {}, key_dop, expected_record_type=0x2c)
    compu = ((dop.get('compuPrefix') or {}).get('compuMethod') or {})
    coded = dop.get('codedType') or {}
    if (compu.get('nativeCategory') != 0 or (dop.get('physicalType') or {}).get('nativeBaseDataType') != 1
            or type(coded.get('bitLength')) is not int or coded['bitLength'] not in (8, 16)
            or int(text) >= 1 << coded['bitLength']):
        raise ValueError('table-key-probe-key-dop-profile-unqualified')
    if position + coded['bitLength'] // 8 > 4095:
        raise ValueError('table-key-probe-key-field-position-invalid')
    return position, index, name_token, int(text), dop


def probe_declared_table_key(parameter, table, row, key_dop, pdu, *, ascii_strings, kernel_sha256, fixture_locale=None):
    result = {'ok': False, 'staticIndexTargetMatched': False, 'declaredRowNameRuleVerified': False,
        'declaredPositionRuleVerified': False, 'keyParameterBindingVerified': False,
        'referenceResolutionVerified': False, 'expectedKeyValidated': False,
        'runtimeLocaleVerified': False, 'audienceAndPreconditionsVerified': False,
        'rowValueDecoded': False, 'responseEnvelopeVerified': False, 'serviceResponseMatchingVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'actualVehicleResponse': False, 'executionEnabled': False}
    try:
        if fixture_locale != 'C':
            raise ValueError('table-key-probe-C-fixture-locale-required')
        position, index, token, expected, dop = _key_binding(parameter, table, row, key_dop, ascii_strings, kernel_sha256)
        value = probe_dop_numeric(dop, pdu, position, 0, kernel_sha256=kernel_sha256)
        if not value['ok']:
            result['reason'] = value['reason']
            return result
        if value['probeKind'] != 'identity-uint32':
            raise ValueError('table-key-probe-identity-key-only')
        if value['rawValue'] != expected:
            result['decodedKeyCandidate'] = value['rawValue']
            raise ValueError('table-key-probe-declared-row-key-mismatch')
        result.update({'ok': True, 'reason': None, 'probeKind': 'declared-table-key',
            'candidateKeyValue': value['rawValue'], 'candidateKeyText': str(expected),
            'fieldRawHex': value['fieldRawHex'], 'declaredBytePosition': position,
            'bitLength': value['bitLength'], 'selectedMapIndex': index,
            'selectedNameToken': token, 'selectedRowTarget': row['recordSha256'],
            'staticIndexTargetMatched': True, 'declaredRowNameRuleVerified': True,
            'declaredPositionRuleVerified': True, 'expectedKeyCandidateMatched': True,
            'fixtureLocale': fixture_locale, 'bindingScope': 'exact-retained-declared-table-row-key-field-candidate'})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def synthetic_declared_table_key_probes(parameter, table, row, key_dop, *, ascii_strings, kernel_sha256):
    try:
        position, _, _, expected, dop = _key_binding(parameter, table, row, key_dop, ascii_strings, kernel_sha256)
        # Reuse the numeric stage's rejection of unsupported masks/encodings.
        first = probe_dop_numeric(dop, b'\0' * (position + dop['codedType']['bitLength'] // 8),
            position, 0, kernel_sha256=kernel_sha256)
        if not first['ok']:
            raise ValueError(first['reason'])
    except ValueError:
        return [{'case': 'table-key-binding-check', 'fixtureOrigin': 'no-pdu-unqualified-table-key-binding',
            'actualVehicleResponse': False, 'result': probe_declared_table_key(parameter, table, row, key_dop,
                b'', ascii_strings=ascii_strings, kernel_sha256=kernel_sha256, fixture_locale='C')}]
    coded = dop['codedType']; width = coded['bitLength'] // 8
    order = 'big' if coded['nativeEncodingFieldsRaw'][2] == 1 else 'little'
    field = expected.to_bytes(width, order)
    wrong = (expected ^ 1).to_bytes(width, order)
    suffix = b'\xd9' * min(2, 4095 - position - width)
    fixtures = [('declared-key', b'\xa7' * position + field + suffix),
        ('different-key', b'\xa7' * position + wrong + suffix),
        ('truncated-key', b'\xa7' * position + field[:-1])]
    return [{'case': case, 'pduHex': data.hex(), 'fixtureOrigin': 'synthetic-declared-table-key-field',
        'actualVehicleResponse': False, 'result': probe_declared_table_key(parameter, table, row, key_dop,
            data, ascii_strings=ascii_strings, kernel_sha256=kernel_sha256, fixture_locale='C')}
        for case, data in fixtures]


def write_declared_table_key_probes(response_path, layout_path, output_path, *, ascii_strings, kernel_sha256):
    layouts, counts = {}, Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); identity = (row['source']['file'], row['sourceOffset'])
            if identity in layouts:
                raise ValueError('table-key-probe-duplicate-layout-record')
            layouts[identity] = row
    def target(reference):
        lookup = (reference or {}).get('indexLookup') or {}
        return layouts.get(((lookup.get('databaseSource') or {}).get('file'), (lookup.get('target') or {}).get('sourceOffset')))
    with response_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            response = json.loads(line)
            for index, parameter in enumerate(response['parameters']['parsedParameters']):
                if parameter['recordType'] != 0xb2:
                    continue
                table = target(parameter.get('tableReference')); row = key_dop = None
                if table:
                    name = (parameter.get('nativeTableString') or {}).get('text')
                    entries = [i for i in table['layout'].get('keyMap', []) if
                        (ascii_strings.get(((i.get('reference') or {}).get('nativeField10') or {}).get('raw')) or {}).get('text') == name]
                    if len(entries) == 1:
                        row = target(entries[0].get('reference'))
                    key_dop = target(table['layout'].get('keyDopReference'))
                probes = synthetic_declared_table_key_probes(parameter, table, row, key_dop,
                    ascii_strings=ascii_strings, kernel_sha256=kernel_sha256)
                counts['parameters'] += 1
                for p in probes:
                    r = p['result']; counts['case:' + (r['probeKind'] if r['ok'] else r['reason'])] += 1
                evidence = {'source': response['source'], 'sourceOffset': response['sourceOffset'],
                    'compressedBytes': response['compressedBytes'], 'recordSha256': response['recordSha256'],
                    'parameterIndex': index, 'parameter': parameter, 'probes': probes,
                    'actualVehicleResponse': False, 'executionEnabled': False}
                for name, value in (('table', table), ('row', row), ('keyDop', key_dop)):
                    evidence[name + 'Source'] = value['source'] if value else None
                    evidence[name + 'SourceOffset'] = value['sourceOffset'] if value else None
                    evidence[name + 'RecordSha256'] = value['recordSha256'] if value else None
                output.write(json.dumps(evidence, ensure_ascii=True) + '\n')
    return dict(counts)
