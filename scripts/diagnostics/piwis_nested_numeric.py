"""One-level fixed structure field candidates, never complete ECU responses.

The retained ordinary structure branch slices the input at the parent's declared
position. Each explicitly positioned child then reads within that local slice.
No dynamic cursors, sibling processing, table keys or recursive structures are
qualified. The serialized structure length is retained data, not a slice bound.
"""
from collections import Counter
import json

from .piwis_response_numeric import _fixed_binding, synthetic_response_parameter_probes
from .piwis_numeric import probe_dop_numeric

SOURCE_EVIDENCE_RVAS = {
    'runtimeResponseCollectionCreate': 0x345d60, 'runtimeDeclaredPositionSelection': 0x34601e,
    'runtimeResponseConstructor': 0x3376e0, 'runtimeParameterConstructor': 0x303b80,
    'runtimeLocalPositionStorage': 0x303dc8, 'structureConstructor': 0xd8590,
    'parameterTypeGetter': 0x1f4500, 'parameterTypeDopDispatch': 0x1f4778,
    'runtimeTypeInterfaceVtable': 0x5f0a68, 'runtimeParameterTypeThunk': 0x201d80,
    'parameterProxyTypeGetter': 0x1fc480, 'structureDispatch': 0x336b50,
    'structureParameterProvider': 0x3339a0, 'ordinaryStructureParameters': 0x33415d,
    'parameterStructureParametersGetter': 0x1f2970, 'structureParametersGetter': 0xd8660,
    'structureLocalPduSelection': 0x3341ee, 'skipParentBytes': 0x273570,
    'structureInternalParameters': 0x33421f, 'createInternalParameters': 0x330d90,
    'responseDataTypeDispatch': 0x336cf8,
}


def _nested_binding(parent, structure_row, child_index, dop_row, kernel_sha256):
    position, layout = _fixed_binding(parent, structure_row, kernel_sha256, expected_record_type=0xaa)
    if parent.get('nativeDataType') != 255 or parent.get('bitPosition') != 0:
        raise ValueError('nested-probe-parent-type-or-bit-position-unqualified')
    child_position, child, dop = _structure_child_binding(layout, child_index, dop_row, kernel_sha256)
    if position + child_position > 4094:
        raise ValueError('nested-probe-combined-position-invalid')
    return position, child_position, child, dop


def _structure_collection(layout):
    """Validate the actual structure collection without inventing a parent VALUE."""
    collection = layout.get('parameters') or {}
    rows = collection.get('parsedParameters')
    count = collection.get('declaredParameters')
    if (layout.get('fieldLayoutComplete') is not True
            or collection.get('parameterCollectionLayoutComplete') is not True
            or collection.get('collectionPresent') is not True or collection.get('collectionType') != 0x6d
            or type(count) is not int or not 1 <= count <= 4096
            or not isinstance(rows, list) or len(rows) != count):
        raise ValueError('nested-probe-structure-collection-incomplete')
    return rows


def _structure_child_binding(layout, child_index, dop_row, kernel_sha256, *, expected_record_type=0x2c):
    rows = _structure_collection(layout)
    count = len(rows)
    if type(child_index) is not int or not 0 <= child_index < count:
        raise ValueError('nested-probe-child-index-invalid')
    child = rows[child_index]
    child_position, dop = _fixed_binding(child, dop_row, kernel_sha256, expected_record_type=expected_record_type)
    if child.get('nativeDataType') != 255:
        raise ValueError('nested-probe-child-type-unqualified')
    return child_position, child, dop


def probe_nested_fixed_response_parameter(parent, structure_row, child_index, dop_row, pdu, *, kernel_sha256):
    result = {'ok': False, 'staticIndexTargetMatched': False, 'parentStaticIndexTargetMatched': False,
        'nestedPositionRuleVerified': False, 'parameterBindingVerified': False,
        'referenceResolutionVerified': False, 'responseEnvelopeVerified': False,
        'serviceResponseMatchingVerified': False, 'physicalConversionQualified': False,
        'structureEnvelopeVerified': False, 'siblingProcessingVerified': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    try:
        position, local, child, dop = _nested_binding(parent, structure_row, child_index, dop_row, kernel_sha256)
        if type(pdu) is not bytes or len(pdu) > 4095:
            raise ValueError('nested-probe-pdu-invalid')
        if position > len(pdu):
            raise ValueError('nested-probe-truncated-parent-position')
        value = probe_dop_numeric(dop, pdu[position:], local, child['bitPosition'], kernel_sha256=kernel_sha256)
        result.update(value)
        result.update({'staticIndexTargetMatched': True, 'parentStaticIndexTargetMatched': True,
            'nestedPositionRuleVerified': True, 'bytePositionOrigin': 'declared-one-level-structure-child',
            'parentDeclaredBytePosition': position, 'childDeclaredBytePosition': local,
            'absoluteBytePosition': position + local, 'childIndex': child_index,
            'bindingScope': 'exact-retained-one-level-structure-index-candidate'})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def synthetic_nested_response_parameter_probes(parent, structure_row, child_index, dop_row, *, kernel_sha256):
    try:
        position, _, child, _ = _nested_binding(parent, structure_row, child_index, dop_row, kernel_sha256)
    except ValueError:
        return [{'case': 'nested-binding-check', 'fixtureOrigin': 'no-pdu-unqualified-nested-binding',
            'actualVehicleResponse': False, 'result': probe_nested_fixed_response_parameter(
                parent, structure_row, child_index, dop_row, b'', kernel_sha256=kernel_sha256)}]
    probes = []
    for local in synthetic_response_parameter_probes(child, dop_row, kernel_sha256=kernel_sha256):
        payload = b'\x5a' * position + bytes.fromhex(local['pduHex'])
        probes.append({'case': local['case'], 'pduHex': payload.hex(),
            'fixtureOrigin': 'synthetic-one-level-structure-child-field-slice', 'actualVehicleResponse': False,
            'result': probe_nested_fixed_response_parameter(parent, structure_row, child_index,
                dop_row, payload, kernel_sha256=kernel_sha256)})
    return probes


def write_nested_response_numeric_probes(response_path, layout_path, output_path, *, kernel_sha256):
    layouts, counts = {}, Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line)
            key = (row['source']['file'], row['sourceOffset'])
            if key in layouts:
                raise ValueError('nested-probe-duplicate-layout-record')
            layouts[key] = row
    def target_row(parameter):
        lookup = (parameter.get('objectReference') or {}).get('indexLookup') or {}
        return layouts.get(((lookup.get('databaseSource') or {}).get('file'),
                            (lookup.get('target') or {}).get('sourceOffset')))
    with response_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            response = json.loads(line)
            for parent_index, parent in enumerate(response['parameters']['parsedParameters']):
                lookup = (parent.get('objectReference') or {}).get('indexLookup') or {}
                if (parent.get('recordType') != 0xa5 or parent.get('parameterType') != 0x7001
                        or (lookup.get('target') or {}).get('recordType') != 0xaa):
                    continue
                counts['structureParentReferences'] += 1
                structure = target_row(parent)
                try:
                    _fixed_binding(parent, structure, kernel_sha256, expected_record_type=0xaa)
                except ValueError as exc:
                    counts['parentBlocked:' + str(exc)] += 1
                    children = [None]
                else:
                    children = ((structure.get('layout') or {}).get('parameters') or {}).get('parsedParameters') or [None]
                for child_index, child in enumerate(children):
                    dop = target_row(child) if child else None
                    probes = synthetic_nested_response_parameter_probes(parent, structure, child_index, dop,
                        kernel_sha256=kernel_sha256)
                    counts['outputPaths'] += 1
                    first = probes[0]['result']
                    counts['supportedChildPaths' if first['ok'] else 'unsupported:' + first['reason']] += 1
                    for p in probes:
                        r = p['result'];counts['case:' + (r['probeKind'] if r['ok'] else r['reason'])] += 1
                    output.write(json.dumps({'source': response['source'], 'record': response['record'],
                        'sourceOffset': response['sourceOffset'], 'compressedBytes': response['compressedBytes'],
                        'recordSha256': response['recordSha256'], 'parentParameterIndex': parent_index,
                        'parentParameter': parent, 'structureSource': structure['source'] if structure else None,
                        'structureSourceOffset': structure['sourceOffset'] if structure else None,
                        'structureRecordSha256': structure['recordSha256'] if structure else None,
                        'childIndex': child_index, 'childParameter': child,
                        'dopSource': dop['source'] if dop else None, 'dopSourceOffset': dop['sourceOffset'] if dop else None,
                        'dopRecordSha256': dop['recordSha256'] if dop else None, 'probes': probes,
                        'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
    return dict(counts)
