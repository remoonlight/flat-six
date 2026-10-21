"""Explicit response-position candidates over exact retained index targets.

Called only by the offline database audit. Fixtures are synthetic PDU slices,
not service responses. Static index identity is distinct from runtime reference
resolution, constraints, record envelopes and ECU qualification.
"""
from collections import Counter
import json
from pathlib import Path

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_numeric import probe_dop_numeric, synthetic_dop_probes, SOURCE_EVIDENCE_RVAS as NUMERIC_RVAS
from .piwis_fixed_values import is_fixed_value_family

SOURCE_EVIDENCE_RVAS = {
    **NUMERIC_RVAS,
    'parameterLoader': 0x1f7620, 'parameterVtable': 0x5ef8f8,
    'bytePositionSetter': 0x43f80, 'positionAvailableSetter': 0x1f5d00,
    'bytePositionGetter': 0x1f28f0, 'positionAvailableGetter': 0x1f6b00,
    'proxyBytePositionGetter': 0x1fa890, 'proxyBitPositionGetter': 0x1fa650,
    'proxyPositionVtable': 0x5f0a68, 'bytePositionThunk': 0x201b88,
    'bitPositionThunk': 0x201af0, 'codedTypeGetter': 0x1f5430,
    'codedTypeDispatch': 0x1f5558, 'dopBaseGetter': 0x1f4e20,
    'responseDetermineValue': 0x3307e0, 'responseExtraction': 0x272270,
    'declaredPositionSelection': 0x2724e4, 'cursorFallback': 0x27250f,
    'standardLengthCursorUpdate': 0x27277d,
}


def _source_identity(source):
    return (isinstance(source, dict) and isinstance(source.get('file'), str) and bool(source['file'])
        and type(source.get('bytes')) is int and source['bytes'] > 0
        and _sha256(source.get('sha256')))


def _sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _fixed_binding(parameter, target_row, kernel_sha256, *, expected_record_type=0x2c):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('response-probe-kernel-profile-mismatch')
    if not isinstance(parameter, dict) or parameter.get('parameterLayoutProfileMatched') is not True:
        raise ValueError('response-probe-parameter-layout-unqualified')
    if parameter.get('recordType') != 0xa5 or parameter.get('parameterType') != 0x7001:
        raise ValueError('response-probe-plain-value-parameter-only')
    flags = parameter.get('nativeFlags')
    if type(flags) is not int or not 0 <= flags <= 255:
        raise ValueError('response-probe-parameter-flags-invalid')
    if parameter.get('bytePositionAvailable') is not True or not flags & 0x20:
        raise ValueError('response-probe-runtime-position-unqualified')
    if parameter.get('isProtocolParameter') is not False or flags & 0x80:
        raise ValueError('response-probe-protocol-resolution-unqualified')
    if type(parameter.get('bitPosition')) is not int or not 0 <= parameter['bitPosition'] <= 7:
        raise ValueError('response-probe-bit-position-unqualified')
    position = parameter.get('serializedBytePosition')
    if type(position) is not int or not 0 <= position <= 4094:
        raise ValueError('response-probe-byte-position-invalid')
    layout = _retained_reference_target(parameter.get('objectReference') or {}, target_row,
        expected_record_type=expected_record_type)
    # A plain VALUE routes getCodedType to its DOP. Do not substitute an
    # inline encoding or qualify protocol/location fallbacks in this profile.
    if (parameter.get('codedType') or {}).get('present'):
        raise ValueError('response-probe-inline-coded-type-unqualified')
    return position, layout


def _retained_reference_target(reference, target_row, *, expected_record_type):
    """Exact source/index candidate, independent of a parameter's runtime role."""
    if not isinstance(reference, dict):
        raise ValueError('response-probe-index-target-unresolved')
    lookup = reference.get('indexLookup') or {}
    if lookup.get('indexTargetMatched') is not True:
        raise ValueError('response-probe-index-target-unresolved')
    target = lookup.get('target') or {}
    if target.get('recordType') != expected_record_type:
        raise ValueError('response-probe-simple-dop-target-only' if expected_record_type == 0x2c
                         else 'nested-probe-structure-target-only' if expected_record_type == 0xaa
                         else 'response-probe-typed-definition-target-only')
    if (not _source_identity(lookup.get('databaseSource')) or not _source_identity(lookup.get('keyIndexSource'))
            or not _sha256(target.get('recordSha256'))
            or any(type(target.get(k)) is not int or target[k] < minimum for k, minimum in
                   (('sourceOffset', 0), ('compressedBytes', 1), ('decompressedBytes', 1), ('record', 0), ('objectId', 1)))):
        raise ValueError('response-probe-index-source-identity-invalid')
    object_field, container_field = reference.get('nativeField08') or {}, reference.get('nativeField0c') or {}
    if (object_field.get('storage') != 'table-reference' or object_field.get('pool') != 'AStringData'
            or type(object_field.get('id')) is not int or object_field['id'] != target.get('objectId')
            or container_field.get('resolved') is not True or container_field.get('pool') != 'AStringData'
            or not container_field.get('text') or container_field['text'] != lookup.get('container')):
        raise ValueError('response-probe-reference-index-identity-mismatch')
    if (not isinstance(target_row, dict) or target_row.get('source') != lookup.get('databaseSource')
            or Path(target_row['source']['file']).stem != lookup['container']
            or Path(lookup['keyIndexSource']['file']).stem != lookup['container']
            or any(target_row.get(k) != target.get(k) for k in
                   ('sourceOffset', 'compressedBytes', 'recordSha256', 'record'))):
        raise ValueError('response-probe-dop-record-identity-mismatch')
    layout = target_row.get('layout') or {}
    if layout.get('recordType') != expected_record_type:
        raise ValueError('response-probe-dop-layout-type-mismatch')
    return layout


def probe_fixed_response_parameter(parameter, target_row, pdu, *, kernel_sha256):
    result = {'ok': False, 'staticIndexTargetMatched': False,
        'declaredPositionRuleVerified': False, 'parameterBindingVerified': False,
        'referenceResolutionVerified': False, 'responseEnvelopeVerified': False,
        'serviceResponseMatchingVerified': False, 'physicalConversionQualified': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    try:
        position, layout = _fixed_binding(parameter, target_row, kernel_sha256)
        value = probe_dop_numeric(layout, pdu, position, parameter['bitPosition'], kernel_sha256=kernel_sha256)
        result.update(value)
        result.update({'staticIndexTargetMatched': True, 'declaredPositionRuleVerified': True,
            'bytePositionOrigin': 'declared-response-root-parameter',
            'declaredBytePosition': position, 'bindingScope': 'exact-retained-index-candidate'})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def synthetic_response_parameter_probes(parameter, target_row, *, kernel_sha256):
    try:
        position, layout = _fixed_binding(parameter, target_row, kernel_sha256)
    except ValueError:
        return [{'case': 'binding-check', 'fixtureOrigin': 'no-pdu-unqualified-binding',
            'actualVehicleResponse': False, 'result': probe_fixed_response_parameter(
                parameter, target_row, b'', kernel_sha256=kernel_sha256)}]
    probes = []
    for field in synthetic_dop_probes(layout, kernel_sha256=kernel_sha256):
        # Prefix distinguishes declared offset from zero. A fixed-field suffix
        # tests its exact width; variable fixtures must retain their actual end.
        bit = parameter['bitPosition']
        coded = layout.get('codedType') or {}
        width = coded.get('bitLength')
        field_bytes = bytes.fromhex(field['fieldHex'])
        if type(width) is int and 1 <= width <= 32 and field['case'] != 'truncated':
            order = 'big' if coded.get('nativeEncodingFieldsRaw', [0, 0, 1])[2] else 'little'
            raw = int.from_bytes(field_bytes, order) & ((1 << width) - 1)
            field_bytes = (raw << bit).to_bytes((width + bit + 7) // 8, order)
        elif field['case'] == 'truncated' and type(width) is int and 1 <= width <= 32:
            field_bytes = bytes((width + bit + 7) // 8 - 1)
        payload = b'\xa7' * position + field_bytes
        variable = coded.get('nativeKind') == 1 and is_fixed_value_family(coded)
        if not variable and field['case'] != 'truncated' and len(payload) < 4095:
            payload += b'\xd9' * min(2, 4095 - len(payload))
        probes.append({'case': field['case'], 'fixtureOrigin': 'synthetic-response-root-field-slice',
            'pduHex': payload.hex(), 'actualVehicleResponse': False,
            'result': probe_fixed_response_parameter(parameter, target_row, payload,
                kernel_sha256=kernel_sha256)})
    return probes


def write_response_numeric_probes(response_path, layout_path, output_path, *, kernel_sha256):
    """Join newly generated audit files, not externally imported qualifications."""
    layouts, counts = {}, Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line)
            key = (row['source']['file'], row['sourceOffset'])
            if key in layouts:
                raise ValueError('response-probe-duplicate-layout-record')
            layouts[key] = row
    with response_path.open(encoding='utf8') as stream, output_path.open('x', encoding='utf8') as output:
        for line in stream:
            response = json.loads(line)
            for number, parameter in enumerate(response['parameters']['parsedParameters']):
                lookup = (parameter.get('objectReference') or {}).get('indexLookup') or {}
                target = lookup.get('target') or {}
                target_row = layouts.get(((lookup.get('databaseSource') or {}).get('file'), target.get('sourceOffset')))
                probes = synthetic_response_parameter_probes(parameter, target_row, kernel_sha256=kernel_sha256)
                first = probes[0]['result']
                counts['parameters'] += 1
                counts['supportedParameters' if first['ok'] else 'unsupported:' + first['reason']] += 1
                for probe in probes:
                    value = probe['result']
                    counts['case:' + (value['probeKind'] if value['ok'] else value['reason'])] += 1
                output.write(json.dumps({'source': response['source'], 'record': response['record'],
                    'sourceOffset': response['sourceOffset'], 'compressedBytes': response['compressedBytes'],
                    'recordSha256': response['recordSha256'], 'parameterIndex': number,
                    'parameter': parameter, 'dopSource': target_row['source'] if target_row else None,
                    'dopSourceOffset': target_row['sourceOffset'] if target_row else None,
                    'dopRecordSha256': target_row['recordSha256'] if target_row else None,
                    'probes': probes, 'actualVehicleResponse': False, 'executionEnabled': False},
                    ensure_ascii=True) + '\n')
    return dict(counts)
