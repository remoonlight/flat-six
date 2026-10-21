"""Source-bound recursive fixed-field candidates, never complete responses.

Each ordinary structure selects a new local PDU at its declared position.
The root may be an ordinary VALUE/structure or an already verified TABLE-STRUCT
candidate. Dynamic positions, inner tables, siblings and runtime owners remain
unqualified. Depth/path bounds are offline resource guards, not PIWIS limits.
"""
from collections import Counter
import json

from .piwis_nested_numeric import _structure_child_binding, _structure_collection, SOURCE_EVIDENCE_RVAS as NESTED_RVAS
from .piwis_response_numeric import _fixed_binding, synthetic_response_parameter_probes
from .piwis_table_struct import _parent_binding
from .piwis_table_key import probe_declared_table_key
from .piwis_table_selection import probe_table_row_selection
from .piwis_numeric import probe_dop_numeric

MAX_STRUCTURE_DEPTH = 8
MAX_PATHS_PER_ROOT = 16384
SOURCE_EVIDENCE_RVAS = {**NESTED_RVAS,
    'recursiveDepthIncrement': 0x334204, 'internalCollectionCreate': 0x330ef0,
    'internalLocalPduArgument': 0x330ee1, 'childConstructor': 0x3461c2,
    'childParameterProvider': 0x34653f, 'childProviderLocalPduArgument': 0x346538,
    'childProviderDepthIncrement': 0x34652f,
}


def _result():
    return {'ok': False, 'recursivePositionRuleVerified': False, 'candidateLeafValueDecoded': False,
        'staticIndexTargetMatched': False, 'parameterBindingVerified': False,
        'referenceResolutionVerified': False, 'runtimeOwnerCollectionVerified': False,
        'sameResponseKeyNameRuleVerified': False, 'keyParameterBindingVerified': False,
        'expectedKeyValidated': False, 'runtimeLocaleVerified': False, 'rowValueDecoded': False,
        'structureEnvelopeVerified': False, 'siblingProcessingVerified': False,
        'responseEnvelopeVerified': False, 'serviceResponseMatchingVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'actualVehicleResponse': False, 'executionEnabled': False}


def _path_binding(structures, indexes, dop, kernel_sha256, root_position):
    if (not isinstance(structures, list) or not 2 <= len(structures) <= MAX_STRUCTURE_DEPTH
            or not isinstance(indexes, list) or len(indexes) != len(structures)):
        raise ValueError('recursive-probe-path-depth-or-index-count-invalid')
    identities, positions, children = set(), [], []
    total = root_position
    for number, structure in enumerate(structures):
        if not isinstance(structure, dict):
            raise ValueError('recursive-probe-structure-missing')
        identity = ((structure.get('source') or {}).get('file'), structure.get('sourceOffset'))
        if identity in identities:
            raise ValueError('recursive-probe-cycle')
        identities.add(identity)
        inner = number < len(structures)-1
        target = structures[number+1] if inner else dop
        local, child, layout = _structure_child_binding(structure.get('layout') or {}, indexes[number],
            target, kernel_sha256, expected_record_type=0xaa if inner else 0x2c)
        if inner and child.get('bitPosition') != 0:
            raise ValueError('recursive-probe-structure-bit-position-unqualified')
        total += local
        if total > 4094:
            raise ValueError('recursive-probe-combined-position-invalid')
        positions.append(local); children.append(child)
    return positions, children, layout, total


def _root_binding(root, structures, kernel_sha256, table_context):
    if table_context is not None:
        if not isinstance(table_context, dict) or not {'parentIndex', 'table', 'row', 'keyDop', 'asciiStrings'} <= table_context.keys():
            raise ValueError('recursive-probe-table-context-invalid')
        bound = _parent_binding(root, table_context['parentIndex'], table_context['table'], table_context['row'],
            table_context['keyDop'], structures[0], table_context['asciiStrings'], kernel_sha256)
        _structure_collection(structures[0].get('layout') or {})
        return bound[0], bound
    position, _ = _fixed_binding(root, structures[0], kernel_sha256, expected_record_type=0xaa)
    if root.get('nativeDataType') != 255 or root.get('bitPosition') != 0:
        raise ValueError('recursive-probe-root-type-or-bit-position-unqualified')
    _structure_collection(structures[0].get('layout') or {})
    return position, None


def probe_recursive_structure_leaf(root, structures, indexes, dop, pdu, *, kernel_sha256,
        table_context=None, fixture_locale=None):
    result = _result()
    try:
        if not isinstance(structures, list) or not structures:
            raise ValueError('recursive-probe-structure-missing')
        if type(pdu) is not bytes or len(pdu) > 4095:
            raise ValueError('recursive-probe-pdu-invalid')
        position, binding = _root_binding(root, structures, kernel_sha256, table_context)
        positions, children, layout, absolute = _path_binding(structures, indexes, dop, kernel_sha256, position)
        if binding is not None:
            if fixture_locale != 'C':
                raise ValueError('recursive-probe-C-fixture-locale-required')
            context = table_context
            key = probe_declared_table_key(binding[2], context['table'], context['row'], context['keyDop'], pdu,
                ascii_strings=context['asciiStrings'], kernel_sha256=kernel_sha256, fixture_locale=fixture_locale)
            if not key['ok']:
                raise ValueError('recursive-probe-key:' + key['reason'])
            selection = probe_table_row_selection(context['table'], context['row'],
                {'nativeType': 11, 'value': key['candidateKeyValue']},
                kernel_sha256=kernel_sha256, fixture_locale=fixture_locale)
            if not selection['ok']:
                raise ValueError('recursive-probe-row:' + selection['reason'])
            result.update({'sameResponseKeyNameRuleVerified': True,
                'candidateKeyValue': key['candidateKeyValue'], 'candidateKeyText': key['candidateKeyText'],
                'selectedMapIndex': selection['selectedMapIndex'], 'selectedRowCandidate': context['row']['recordSha256']})
        local_pdu = pdu
        for offset in [position] + positions[:-1]:
            if offset > len(local_pdu):
                raise ValueError('recursive-probe-truncated-structure-position')
            local_pdu = local_pdu[offset:]
        value = probe_dop_numeric(layout, local_pdu, positions[-1], children[-1]['bitPosition'], kernel_sha256=kernel_sha256)
        result.update(value)
        result.update({'recursivePositionRuleVerified': True, 'candidateLeafValueDecoded': value['ok'],
            'staticIndexTargetMatched': True, 'rootDeclaredBytePosition': position,
            'structureChildPositions': positions, 'structureChildIndexes': indexes.copy(),
            'absoluteBytePosition': absolute, 'structureDepth': len(structures),
            'bytePositionOrigin': 'declared-recursive-structure-leaf',
            'bindingScope': 'exact-retained-recursive-fixed-leaf-candidate'})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def synthetic_recursive_structure_probes(root, structures, indexes, dop, *, kernel_sha256, table_context=None):
    def probe(data):
        return probe_recursive_structure_leaf(root, structures, indexes, dop, data,
            kernel_sha256=kernel_sha256, table_context=table_context,
            fixture_locale='C' if table_context is not None else None)
    try:
        if not isinstance(structures, list) or not structures:
            raise ValueError('recursive-probe-structure-missing')
        position, binding = _root_binding(root, structures, kernel_sha256, table_context)
        positions, children, _, _ = _path_binding(structures, indexes, dop, kernel_sha256, position)
    except ValueError:
        return [{'case': 'recursive-binding-check', 'fixtureOrigin': 'no-pdu-unqualified-recursive-path',
            'actualVehicleResponse': False, 'result': probe(b'')}]
    fixtures = []
    for field in synthetic_response_parameter_probes(children[-1], dop, kernel_sha256=kernel_sha256):
        data = bytes.fromhex(field['pduHex'])
        for depth in reversed(range(len(positions)-1)):
            data = bytes([0x60+depth]) * positions[depth] + data
        data = bytearray(b'\x5a' * position + data)
        if binding is not None:
            width = binding[5]['codedType']['bitLength']//8
            order = 'big' if binding[5]['codedType']['nativeEncodingFieldsRaw'][2] else 'little'
            data[binding[3]:binding[3]+width] = binding[4].to_bytes(width, order)
        fixtures.append((field['case'], bytes(data)))
    if binding is not None and fixtures:
        data = bytearray(fixtures[0][1]); data[binding[3]:binding[3]+width] = (binding[4]^1).to_bytes(width, order)
        fixtures.extend([('different-key', bytes(data)),
            ('truncated-key', b'\x5a'*binding[3] + binding[4].to_bytes(width, order)[:-1])])
    return [{'case': case, 'pduHex': data.hex(), 'fixtureOrigin': 'synthetic-recursive-local-field-slices',
        'actualVehicleResponse': False, 'result': probe(data)} for case, data in fixtures]


def write_recursive_response_probes(response_path, layout_path, output_path, *, ascii_strings, kernel_sha256):
    layouts, counts = {}, Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); identity = (row['source']['file'], row['sourceOffset'])
            if identity in layouts:
                raise ValueError('recursive-probe-duplicate-layout-record')
            layouts[identity] = row
    def target(ref):
        lookup = (ref or {}).get('indexLookup') or {}
        return layouts.get(((lookup.get('databaseSource') or {}).get('file'), (lookup.get('target') or {}).get('sourceOffset')))
    def walk(structures, indexes):
        try:
            children = _structure_collection(structures[-1]['layout'])
        except ValueError as exc:
            yield structures, indexes, None, str(exc)
            return
        for i, child in enumerate(children):
            dop = target(child.get('objectReference'))
            if dop and dop['layout']['recordType'] == 0xaa:
                path = indexes+[i]
                try:
                    _, c, _ = _structure_child_binding(structures[-1]['layout'], i, dop,
                        kernel_sha256, expected_record_type=0xaa)
                    if c.get('bitPosition') != 0:
                        raise ValueError('recursive-probe-structure-bit-position-unqualified')
                    if len(structures) >= MAX_STRUCTURE_DEPTH:
                        raise ValueError('recursive-probe-depth-limit')
                    if any((s['source']['file'], s['sourceOffset']) == (dop['source']['file'], dop['sourceOffset']) for s in structures):
                        raise ValueError('recursive-probe-cycle')
                except ValueError as exc:
                    yield structures, path, dop, str(exc)
                else:
                    yield from walk(structures+[dop], path)
            elif len(structures) >= 2:
                yield structures, indexes+[i], dop, None
    def source(row):
        return {k: row[k] for k in ('source', 'record', 'sourceOffset', 'compressedBytes', 'recordSha256')}
    with response_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            response = json.loads(line); params = response['parameters']['parsedParameters']
            for parent_index, parent in enumerate(params):
                context = None
                if parent.get('recordType') == 0xb0:
                    table = target(parent.get('tableReference'))
                    if not table: continue
                    key_dop = target(table['layout'].get('keyDopReference'))
                    matches = [p for p in params if any(f.get('field') == 'shortName' and
                        f.get('text') == (parent.get('nativeField130') or {}).get('text') for f in p.get('fields', []))]
                    name = (matches[0].get('nativeTableString') or {}).get('text') if len(matches)==1 else None
                    entries = [e for e in table['layout'].get('keyMap', []) if
                        (ascii_strings.get(((e.get('reference') or {}).get('nativeField10') or {}).get('raw')) or {}).get('text') == name]
                    row = target(entries[0].get('reference')) if len(entries)==1 else None
                    structure = target((row['layout'].get('baseParameter') or {}).get('objectReference')) if row else None
                    if structure is None: continue
                    context = {'parentIndex': parent_index, 'table': table, 'row': row, 'keyDop': key_dop, 'asciiStrings': ascii_strings}
                    root = response
                elif parent.get('recordType') == 0xa5 and parent.get('parameterType') == 0x7001:
                    structure = target(parent.get('objectReference'))
                    if not structure or structure['layout']['recordType'] != 0xaa: continue
                    root = parent
                else: continue
                try:
                    _root_binding(root, [structure], kernel_sha256, context)
                except ValueError as exc:
                    counts['rootBlocked:' + str(exc)] += 1; continue
                paths = 0
                for structures, indexes, dop, error in walk([structure], []):
                    paths += 1
                    if paths > MAX_PATHS_PER_ROOT:
                        raise ValueError('recursive-probe-path-limit')
                    if error:
                        result = _result(); result['reason'] = error
                        probes = [{'case': 'recursive-binding-check', 'fixtureOrigin': 'no-pdu-unqualified-recursive-path',
                                   'actualVehicleResponse': False, 'result': result}]
                    else:
                        probes = synthetic_recursive_structure_probes(root, structures, indexes, dop,
                            kernel_sha256=kernel_sha256, table_context=context)
                    counts['paths'] += 1
                    counts['supportedPaths' if any(p['result']['ok'] for p in probes) else 'blockedPaths'] += 1
                    for p in probes:
                        r = p['result']; counts['case:' + (r['probeKind'] if r['ok'] else r['reason'])] += 1
                    output.write(json.dumps({'response': source(response), 'rootKind': 'table-structure' if context else 'ordinary-structure',
                        'parentParameterIndex': parent_index, 'parentParameter': parent, 'structureRecords': [source(s) for s in structures],
                        'childIndexes': indexes, 'childParameters': [s['layout']['parameters']['parsedParameters'][i] for s, i in zip(structures, indexes)],
                        'dopRecord': source(dop) if dop else None, 'tableRecord': source(context['table']) if context else None,
                        'rowRecord': source(context['row']) if context else None, 'keyDopRecord': source(context['keyDop']) if context else None,
                        'probes': probes, 'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True)+'\n')
    return dict(counts)
