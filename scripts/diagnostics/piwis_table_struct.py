"""Declared TABLE-STRUCT/key/child candidates over retained source records.

Only the ordinary root name branch and explicitly positioned fields qualify as
static candidates. C-locale fixtures must carry the declared key before the
structure. Runtime owners, constraints, complete row values and services remain
unqualified. No generated-name transform, recursive or dynamic layout is guessed.
"""
from collections import Counter
import json

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_response_numeric import _retained_reference_target, _source_identity, _sha256, synthetic_response_parameter_probes
from .piwis_nested_numeric import _structure_child_binding
from .piwis_table_key import _key_binding, probe_declared_table_key
from .piwis_table_selection import probe_table_row_selection
from .piwis_numeric import probe_dop_numeric

SOURCE_EVIDENCE_RVAS = {
    'tableStructVtable': 0x5f24e8, 'tableStructKeyNameGetter': 0x2087e0,
    'tableStructTableGetter': 0x208980, 'runtimeTableStructBranch': 0x333ac4,
    'runtimeKeyNameGetter': 0x333b33, 'generatedNamePrefixInit': 0x233f0,
    'generatedNamePrefixLiteral': 0x5a1700, 'runtimeKeyLookup': 0x333eac,
    'responseParameterByName': 0x341070, 'responseNameLengthCompare': 0x34137b,
    'responseNameTextCompare': 0x341381, 'runtimeKeyValue': 0x333ed5,
    'runtimeStructureByKey': 0x333ee9, 'proxyStructureByKey': 0x1fe5d0,
    'tableRowByKey': 0x236c60, 'rowParametersVtable': 0x5fa980,
    'rowParametersGetter': 0x2384e0, 'rowDopGetter': 0x1f4e20,
    'rowDopTypeBranch': 0x238550, 'structureDopVtable': 0x5c0db8,
    'dopTypeGetter': 0xbc9c0, 'rowComplexParameters': 0x23882b,
    'structureParametersGetter': 0xd8660, 'structureLocalPduSelection': 0x3341ee,
    'skipParentBytes': 0x273570, 'createInternalParameters': 0x330d90,
}


def _short_name(parameter):
    fields = [f for f in parameter.get('fields', []) if f.get('field') == 'shortName']
    if (len(fields) != 1 or fields[0].get('pool') != 'AStringData'
            or fields[0].get('resolved') is not True or fields[0].get('nullReference') is not False
            or type(fields[0].get('text')) is not str or not 1 <= len(fields[0]['text']) <= 4096):
        raise ValueError('table-structure-short-name-unresolved')
    return fields[0]['text']


def _parent_binding(response, parent_index, table, row, key_dop, structure, ascii_strings, kernel_sha256):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('table-structure-kernel-profile-mismatch')
    if (not isinstance(response, dict) or not _source_identity(response.get('source'))
            or not _sha256(response.get('recordSha256'))):
        raise ValueError('table-structure-response-source-invalid')
    collection = response.get('parameters') or {}; params = collection.get('parsedParameters')
    count = collection.get('declaredParameters')
    if (collection.get('collectionPresent') is not True or collection.get('collectionType') != 0x92
            or collection.get('parameterCollectionLayoutComplete') is not True
            or type(count) is not int or not 1 <= count <= 4096 or not isinstance(params, list) or len(params) != count):
        raise ValueError('table-structure-response-collection-incomplete')
    if type(parent_index) is not int or not 0 <= parent_index < count:
        raise ValueError('table-structure-parent-index-invalid')
    parent = params[parent_index]
    if (parent.get('parameterLayoutProfileMatched') is not True or parent.get('recordType') != 0xb0
            or parent.get('parameterType') != 0x7010 or parent.get('nativeDataType') != 23):
        raise ValueError('table-structure-parent-kind-unqualified')
    flags, position = parent.get('nativeFlags'), parent.get('serializedBytePosition')
    if (type(flags) is not int or not 0 <= flags <= 255 or not flags & 0x20 or flags & 0x80
            or parent.get('bytePositionAvailable') is not True or parent.get('isProtocolParameter') is not False
            or type(position) is not int or not 0 <= position <= 4094
            or type(parent.get('bitPosition')) is not int or parent['bitPosition'] != 0
            or (parent.get('codedType') or {}).get('present')):
        raise ValueError('table-structure-parent-position-unqualified')
    if _short_name(parent).startswith('#RtGen_'):
        raise ValueError('table-structure-generated-name-transform-unqualified')
    field = parent.get('nativeField130') or {}
    if (field.get('pool') != 'AStringData' or field.get('resolved') is not True
            or field.get('nullReference') is not False or type(field.get('text')) is not str
            or not 1 <= len(field['text']) <= 4096):
        raise ValueError('table-structure-key-name-unresolved')
    matches = [i for i, p in enumerate(params) if _short_name(p) == field['text']]
    if len(matches) != 1 or matches[0] >= parent_index:
        raise ValueError('table-structure-key-name-missing-ambiguous-or-not-preceding')
    key_index = matches[0]; key = params[key_index]
    ref = parent.get('tableReference') or {}
    if ref.get('attributes') != []:
        raise ValueError('table-structure-table-attributes-unqualified')
    _retained_reference_target(ref, table, expected_record_type=0xab)
    key_position, _, _, expected, key_layout = _key_binding(key, table, row, key_dop, ascii_strings, kernel_sha256)
    if key_position + key_layout['codedType']['bitLength'] // 8 > position:
        raise ValueError('table-structure-interleaved-key-layout-unqualified')
    row_layout = row['layout']; base = row_layout.get('baseParameter') or {}
    if row_layout.get('fieldLayoutComplete') is not True:
        raise ValueError('table-structure-row-layout-incomplete')
    layout = _retained_reference_target(base.get('objectReference') or {}, structure, expected_record_type=0xaa)
    return position, key_index, key, key_position, expected, key_layout, layout


def _child_binding(response, parent_index, table, row, key_dop, structure, child_index, dop, ascii_strings, kernel_sha256):
    parent = _parent_binding(response, parent_index, table, row, key_dop, structure, ascii_strings, kernel_sha256)
    local, child, child_dop = _structure_child_binding(parent[-1], child_index, dop, kernel_sha256)
    if parent[0] + local > 4094:
        raise ValueError('table-structure-combined-position-invalid')
    return parent, local, child, child_dop


def probe_table_structure_child(response, parent_index, table, row, key_dop, structure, child_index, dop, pdu,
        *, ascii_strings, kernel_sha256, fixture_locale=None):
    result = {'ok': False, 'staticIndexTargetMatched': False, 'sameResponseKeyNameRuleVerified': False,
        'tableStructurePositionRuleVerified': False, 'candidateChildValueDecoded': False,
        'runtimeOwnerCollectionVerified': False, 'parameterBindingVerified': False, 'keyParameterBindingVerified': False,
        'referenceResolutionVerified': False, 'expectedKeyValidated': False, 'runtimeLocaleVerified': False,
        'audienceAndPreconditionsVerified': False, 'rowValueDecoded': False, 'structureEnvelopeVerified': False,
        'siblingProcessingVerified': False, 'responseEnvelopeVerified': False, 'serviceResponseMatchingVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False, 'actualVehicleResponse': False, 'executionEnabled': False}
    try:
        if fixture_locale != 'C':
            raise ValueError('table-structure-C-fixture-locale-required')
        binding, local, child, layout = _child_binding(response, parent_index, table, row, key_dop,
            structure, child_index, dop, ascii_strings, kernel_sha256)
        position, key_index, key, _, _, _, _ = binding
        key_result = probe_declared_table_key(key, table, row, key_dop, pdu,
            ascii_strings=ascii_strings, kernel_sha256=kernel_sha256, fixture_locale=fixture_locale)
        if not key_result['ok']:
            raise ValueError('table-structure-key:' + key_result['reason'])
        selection = probe_table_row_selection(table, row, {'nativeType': 11, 'value': key_result['candidateKeyValue']},
            kernel_sha256=kernel_sha256, fixture_locale=fixture_locale)
        if not selection['ok']:
            raise ValueError('table-structure-row:' + selection['reason'])
        if position > len(pdu):
            raise ValueError('table-structure-truncated-parent-position')
        value = probe_dop_numeric(layout, pdu[position:], local, child['bitPosition'], kernel_sha256=kernel_sha256)
        result.update(value)
        result.update({'staticIndexTargetMatched': True, 'sameResponseKeyNameRuleVerified': True,
            'tableStructurePositionRuleVerified': True, 'candidateChildValueDecoded': value['ok'],
            'candidateKeyValue': key_result['candidateKeyValue'], 'candidateKeyText': key_result['candidateKeyText'],
            'keyParameterIndex': key_index, 'parentParameterIndex': parent_index, 'childIndex': child_index,
            'selectedMapIndex': selection['selectedMapIndex'], 'selectedRowCandidate': row['recordSha256'],
            'selectedStructureCandidate': structure['recordSha256'], 'parentDeclaredBytePosition': position,
            'childDeclaredBytePosition': local, 'absoluteBytePosition': position + local,
            'bytePositionOrigin': 'declared-table-structure-child', 'fixtureLocale': fixture_locale,
            'bindingScope': 'exact-retained-root-table-structure-child-candidate'})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def synthetic_table_structure_child_probes(response, parent_index, table, row, key_dop, structure, child_index, dop,
        *, ascii_strings, kernel_sha256):
    def probe(pdu):
        return probe_table_structure_child(response, parent_index, table, row, key_dop, structure, child_index, dop,
            pdu, ascii_strings=ascii_strings, kernel_sha256=kernel_sha256, fixture_locale='C')
    try:
        binding, _, child, _ = _child_binding(response, parent_index, table, row, key_dop, structure,
            child_index, dop, ascii_strings, kernel_sha256)
    except ValueError:
        return [{'case': 'table-structure-binding-check', 'fixtureOrigin': 'no-pdu-unqualified-table-structure-binding',
            'actualVehicleResponse': False, 'result': probe(b'')}]
    position, _, _, key_position, expected, key_layout, _ = binding
    width = key_layout['codedType']['bitLength'] // 8
    order = 'big' if key_layout['codedType']['nativeEncodingFieldsRaw'][2] else 'little'
    fixtures = []
    for field in synthetic_response_parameter_probes(child, dop, kernel_sha256=kernel_sha256):
        if 'pduHex' not in field:
            return [{'case': 'table-structure-child-check', 'fixtureOrigin': 'no-pdu-unqualified-table-child',
                'actualVehicleResponse': False, 'result': probe(b'')}]
        data = bytearray(b'\x5a' * position + bytes.fromhex(field['pduHex']))
        data[key_position:key_position + width] = expected.to_bytes(width, order)
        fixtures.append((field['case'], bytes(data)))
    if fixtures:
        data = bytearray(fixtures[0][1]); data[key_position:key_position+width] = (expected ^ 1).to_bytes(width, order)
        fixtures.append(('different-key', bytes(data)))
        fixtures.append(('truncated-key', b'\x5a' * key_position + expected.to_bytes(width, order)[:-1]))
    return [{'case': case, 'pduHex': data.hex(), 'fixtureOrigin': 'synthetic-table-key-and-structure-child-fields',
        'actualVehicleResponse': False, 'result': probe(data)} for case, data in fixtures]


def write_table_structure_child_probes(response_path, layout_path, output_path, *, ascii_strings, kernel_sha256):
    layouts, counts = {}, Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); identity = (row['source']['file'], row['sourceOffset'])
            if identity in layouts:
                raise ValueError('table-structure-duplicate-layout-record')
            layouts[identity] = row
    def target(ref):
        lookup = (ref or {}).get('indexLookup') or {}
        return layouts.get(((lookup.get('databaseSource') or {}).get('file'), (lookup.get('target') or {}).get('sourceOffset')))
    with response_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            response = json.loads(line); params = response['parameters']['parsedParameters']
            for parent_index, parent in enumerate(params):
                if parent['recordType'] != 0xb0: continue
                counts['parents'] += 1
                table = target(parent.get('tableReference')); row = key_dop = structure = None
                matches = [p for p in params if any(f.get('field') == 'shortName' and
                    f.get('text') == (parent.get('nativeField130') or {}).get('text') for f in p.get('fields', []))]
                if table:
                    key_dop = target(table['layout'].get('keyDopReference'))
                    name = (matches[0].get('nativeTableString') or {}).get('text') if len(matches) == 1 else None
                    entries = [e for e in table['layout'].get('keyMap', []) if
                        (ascii_strings.get(((e.get('reference') or {}).get('nativeField10') or {}).get('raw')) or {}).get('text') == name]
                    if len(entries) == 1:
                        row = target(entries[0].get('reference'))
                        if row: structure = target((row['layout'].get('baseParameter') or {}).get('objectReference'))
                try:
                    binding = _parent_binding(response, parent_index, table, row, key_dop, structure, ascii_strings, kernel_sha256)
                    children = (binding[-1].get('parameters') or {}).get('parsedParameters') or [None]
                except ValueError:
                    children = [None]
                for child_index, child in enumerate(children):
                    dop = target(child.get('objectReference')) if child else None
                    probes = synthetic_table_structure_child_probes(response, parent_index, table, row, key_dop,
                        structure, child_index, dop, ascii_strings=ascii_strings, kernel_sha256=kernel_sha256)
                    counts['childPaths'] += 1
                    for p in probes:
                        r = p['result']; counts['case:' + (r['probeKind'] if r['ok'] else r['reason'])] += 1
                    counts['supportedChildPaths' if any(p['result']['ok'] for p in probes) else 'blockedChildPaths'] += 1
                    evidence = {'responseSource': response['source'], 'responseSourceOffset': response['sourceOffset'],
                        'responseCompressedBytes': response['compressedBytes'], 'responseRecordSha256': response['recordSha256'],
                        'parentParameterIndex': parent_index, 'parentParameter': parent, 'childIndex': child_index,
                        'childParameter': child, 'probes': probes, 'actualVehicleResponse': False, 'executionEnabled': False}
                    for name, value in (('table', table), ('row', row), ('keyDop', key_dop), ('structure', structure), ('dop', dop)):
                        evidence[name + 'Source'] = value['source'] if value else None
                        evidence[name + 'SourceOffset'] = value['sourceOffset'] if value else None
                        evidence[name + 'RecordSha256'] = value['recordSha256'] if value else None
                    output.write(json.dumps(evidence, ensure_ascii=True) + '\n')
    return dict(counts)
