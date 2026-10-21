"""Offline forward conversion with pinned pre/post constraint caller gates.

Configuration, property mode, parameter data type and initial range state are
explicit fixture inputs. Runtime parameter/type resolution, unit selection,
complete envelopes and higher callers are deliberately not qualified here.
"""
from collections import Counter
import json

from .piwis_constraint_candidates import (
    CONFIG_FIELDS, RANGE_INFO_NAMES, _row_identity, probe_constraint_classification)
from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_numeric import (
    _is_signed, decode_signed_field, decode_unsigned_field, is_fixed_value_family,
    probe_dop_numeric, synthetic_dop_probes)
from .piwis_response_numeric import (
    _fixed_binding, _retained_reference_target, synthetic_response_parameter_probes)

SOURCE_EVIDENCE_RVAS = {
    'performValueConversion': 0x26f4b0, 'preModeGuard': 0x26fc94,
    'prePhysicalAvailable': 0x26fcad, 'preInternalAvailable': 0x26fcb4,
    'preParameterDataType': 0x26fcd1, 'preDataType20Guard': 0x26fcd7,
    'prePhysicalConstraint': 0x26fd18, 'preInternalConstraint': 0x26fd87,
    'preComputedConstraint': 0x26fe96, 'preComputedSkip': 0x26fe9b,
    'preCheckConstraint': 0x26feda, 'convertValue': 0x26ff40,
    'postModeGuard': 0x270070, 'postInternalAvailable': 0x270089,
    'postPhysicalAvailable': 0x270090, 'postParameterDataType': 0x2700ad,
    'postDataType20Guard': 0x2700b3, 'postInternalConstraint': 0x2700ec,
    'postPhysicalConstraint': 0x27015b, 'postComputedConstraint': 0x270264,
    'postComputedSkip': 0x270269, 'postCheckConstraint': 0x270309,
    'computedLoad': 0x10faec, 'computedSetterCall': 0x10fafc,
    'computedSetterStore': 0x1105cc, 'computedGetter': 0x110600,
    'computedGetterRead': 0x1106dc, 'computedInterfaceThunk': 0x110d80,
    'internalReferenceAvailable': 0xd34b9, 'physicalReferenceAvailable': 0xd3ab9,
    'callerFuncInfo': 0x6ed774, 'callerTryMap': 0x6ed87c, 'callerIpStateMap': 0x6ed8cf,
}

# Contrasting test configurations, never settings read from a PIWIS session.
FLOW_POLICY_FIXTURES = (
    ('explicit-odx201-check', (True, False, False, True)),
    ('explicit-odx21-22-check', (False, False, True, False)),
    ('explicit-check-disabled', (False, False, False, True)),
    ('explicit-old-check', (False, True, False, False)),
)
PHYSICAL_BASE_TYPES = {0: 7, 1: 11, 2: 4, 3: 5, 4: 14, 5: 3}


def _gate(dop, field, rows, mode, parameter_type):
    gate = {'stage': 'pre-check' if field == 'internalConstraintReference' else 'post-check',
            'referenceField': field}
    if mode == 3:
        return dict(gate, action='skip', reason='explicit-property-mode-3'), None
    if parameter_type == 20:
        return dict(gate, action='skip', reason='explicit-parameter-data-type-20'), None
    if field not in dop['layout']:
        raise ValueError('constraint-flow-reference-field-missing')
    reference = dop['layout'][field]
    if reference is None:
        return dict(gate, action='skip', reason='declared-reference-absent'), None
    if not isinstance(reference, dict):
        raise ValueError('constraint-flow-reference-invalid')
    lookup = reference.get('indexLookup') or {}
    target = lookup.get('target') or {}
    row = rows.get(((lookup.get('databaseSource') or {}).get('file'), target.get('sourceOffset')))
    _row_identity(row)
    layout = _retained_reference_target(reference, row, expected_record_type=0x203)
    if layout.get('fieldLayoutComplete') is not True:
        raise ValueError('constraint-flow-layout-unqualified')
    computed = layout.get('nativeComputedRaw')
    if type(computed) is not int or computed not in (0, 1):
        raise ValueError('constraint-flow-computed-flag-unqualified')
    gate.update(constraintIdentity=list(_row_identity(row)), staticIndexTargetMatched=True,
                nativeComputedRaw=computed)
    if computed:
        return dict(gate, action='skip', reason='declared-computed-constraint'), None
    return dict(gate, action='check', reason=None), row


def probe_dop_constraint_flow(dop, rows, pdu, byte_offset, bit_offset=0, *,
        kernel_sha256, configuration, native_property_mode,
        native_parameter_data_type, initial_native_range_info):
    """Only coded-to-physical; stop before a later stage on rejection/exception."""
    result = {'ok': False, 'reason': None, 'stages': [],
        'direction': 'coded-to-physical', 'nativeDirectionFlag': False,
        'callerGateSourcePinned': False, 'parameterDataTypeResolutionVerified': False,
        'runtimeConstraintSelectionVerified': False, 'runtimeConfigurationVerified': False,
        'constraintSemanticsVerified': False, 'physicalConversionQualified': False,
        'parameterBindingVerified': False, 'unitSemanticsVerified': False,
        'actualVehicleResponse': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False, 'higherCallerExceptionHandlingVerified': False}
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise ValueError('constraint-flow-kernel-profile-mismatch')
        _row_identity(dop)
        if (not isinstance(configuration, dict) or set(configuration) != set(CONFIG_FIELDS)
                or any(type(configuration[k]) is not bool for k in CONFIG_FIELDS)
                or type(native_property_mode) is not int or not 0 <= native_property_mode <= 0xffffffff
                or type(native_parameter_data_type) is not int or not 0 <= native_parameter_data_type <= 0xffffffff
                or type(initial_native_range_info) is not int or initial_native_range_info not in RANGE_INFO_NAMES):
            raise ValueError('constraint-flow-policy-input-invalid')
        layout = dop.get('layout')
        if (not isinstance(layout, dict) or layout.get('recordType') != 0x2c
                or layout.get('fieldLayoutComplete') is not True or not isinstance(rows, dict)):
            raise ValueError('constraint-flow-dop-layout-unqualified')
        result.update(configuration=dict(configuration), nativePropertyMode=native_property_mode,
            nativeParameterDataType=native_parameter_data_type,
            initialNativeRangeInfo=initial_native_range_info, lastNativeRangeInfo=initial_native_range_info,
            lastNativeRangeInfoName=RANGE_INFO_NAMES[initial_native_range_info], callerGateSourcePinned=True)
        coded = layout.get('codedType') or {}
        raw, raw_type = None, None
        if not is_fixed_value_family(coded):
            signed = _is_signed(coded)
            raw = (decode_signed_field if signed else decode_unsigned_field)(
                pdu, coded, byte_offset, bit_offset, kernel_sha256=kernel_sha256)
            raw_type = 7 if signed else 11
            result['stages'].append({'stage': 'field-decode', 'action': 'decoded',
                                     'rawValue': raw, 'nativeDataType': raw_type})
        else:
            # Byte/text extractors currently expose no qualified input MCD type.
            result['stages'].append({'stage': 'field-decode', 'action': 'deferred-byte-text',
                                     'nativeInputDataTypeVerified': False})
        for field in ('internalConstraintReference', 'physicalConstraintReference'):
            if field == 'physicalConstraintReference':
                converted = probe_dop_numeric(layout, pdu, byte_offset, bit_offset,
                                               kernel_sha256=kernel_sha256)
                result['stages'].append({'stage': 'conversion', 'action': 'converted' if converted['ok'] else 'blocked',
                                         'result': converted})
                if not converted['ok']:
                    raise ValueError(converted['reason'])
            gate, row = _gate(dop, field, rows, native_property_mode, native_parameter_data_type)
            result['stages'].append(gate)
            if gate['action'] == 'skip':
                continue
            if field == 'internalConstraintReference':
                value, kind = raw, raw_type
            else:
                value = converted['candidateValue']
                kind = converted.get('nativeResultDataType')
                if kind is None:
                    kind = PHYSICAL_BASE_TYPES.get((layout.get('physicalType') or {}).get('nativeBaseDataType'))
            if kind not in (4, 5, 7, 11):
                raise ValueError('constraint-flow-non-numeric-' + gate['stage'] + '-unqualified')
            classification = probe_constraint_classification(row, value, kind,
                kernel_sha256=kernel_sha256, configuration=configuration,
                native_property_mode=native_property_mode, native_direction_flag=False)
            gate['classification'] = classification
            if not classification['ok']:
                raise ValueError(classification['reason'])
            result.update(lastNativeRangeInfo=classification['nativeRangeInfo'],
                          lastNativeRangeInfoName=classification['nativeRangeInfoName'])
            if classification['candidateNativeException']:
                gate['action'] = 'native-exception'
                result['candidateNativeExceptionStage'] = gate['stage']
                result['callerLocalCatchCoversConstraintCall'] = False
                raise ValueError('constraint-flow-' + gate['stage'] + '-native-exception')
        result.update(ok=True, reason=None, candidateValue=converted['candidateValue'],
                      conversion=converted)
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def probe_response_constraint_flow(parameter, dop, rows, pdu, **policy):
    try:
        position, _ = _fixed_binding(parameter, dop, policy['kernel_sha256'])
    except ValueError as exc:
        return {'ok': False, 'reason': str(exc), 'parameterBindingVerified': False,
                'executionEnabled': False, 'actualVehicleResponse': False}
    result = probe_dop_constraint_flow(dop, rows, pdu, position, parameter['bitPosition'], **policy)
    result.update(staticIndexTargetMatched=True, declaredPositionRuleVerified=True,
        bytePositionOrigin='declared-response-root-parameter', declaredBytePosition=position,
        responseEnvelopeVerified=False, serviceResponseMatchingVerified=False)
    return result


def _policy_cases(dop, fixtures, run, counts):
    # Declared physical base supplies a labelled test type, not resolved runtime
    # getParameterType. Unknown types stay blocked rather than using a fallback.
    kind = PHYSICAL_BASE_TYPES.get((dop.get('layout', {}).get('physicalType') or {}).get('nativeBaseDataType')) if dop else None
    cases = []
    for fixture in fixtures:
        for name, flags in FLOW_POLICY_FIXTURES:
            for mode in (0, 1, 3):
                policy = {'kernel_sha256': REQUEST_KERNEL_SHA256,
                    'configuration': dict(zip(CONFIG_FIELDS, flags)), 'native_property_mode': mode,
                    'native_parameter_data_type': kind, 'initial_native_range_info': 0x606}
                result = run(fixture, policy)
                counts['success' if result['ok'] else 'blocked:' + result['reason']] += 1
                for stage in result.get('stages', []):
                    if stage['stage'] in ('pre-check', 'post-check'):
                        counts[stage['stage'] + ':' + (stage['reason'] or stage['action'])] += 1
                cases.append({'case': fixture['case'], 'policyFixture': name,
                    'fieldHex': fixture.get('fieldHex'), 'pduHex': fixture.get('pduHex'),
                    'parameterDataTypeOrigin': 'synthetic-declared-physical-base-test-input',
                    'initialRangeInfoOrigin': 'explicit-synthetic-NOT_INITIALIZED', 'result': result})
    return cases


def write_constraint_flow_probes(layout_path, output_path, *, kernel_sha256,
        response_path=None, response_output_path=None):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('constraint-flow-kernel-profile-mismatch')
    rows, counts = {}, Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); key = (row['source']['file'], row['sourceOffset'])
            if key in rows:
                raise ValueError('constraint-flow-duplicate-layout-record')
            rows[key] = row
    with output_path.open('x', encoding='utf8') as output:
        for dop in rows.values():
            if dop['layout'].get('recordType') != 0x2c:
                continue
            fixtures = synthetic_dop_probes(dop['layout'], kernel_sha256=kernel_sha256)
            probes = _policy_cases(dop, fixtures, lambda f, p: probe_dop_constraint_flow(
                dop, rows, bytes.fromhex(f['fieldHex']), 0, **p), counts)
            counts['dopDefinitions'] += 1
            counts['dopDefinitionsWithSuccess' if any(p['result']['ok'] for p in probes) else 'dopDefinitionsBlocked'] += 1
            output.write(json.dumps({'dopRecord': dop, 'fixtureOrigin': 'synthetic-forward-constraint-flow',
                'probes': probes, 'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
    if response_path is not None:
        if response_output_path is None:
            raise ValueError('constraint-flow-response-output-required')
        with response_path.open(encoding='utf8') as stream, response_output_path.open('x', encoding='utf8') as output:
            for line in stream:
                response = json.loads(line)
                for index, parameter in enumerate(response['parameters']['parsedParameters']):
                    lookup = (parameter.get('objectReference') or {}).get('indexLookup') or {}
                    target = lookup.get('target') or {}
                    dop = rows.get(((lookup.get('databaseSource') or {}).get('file'), target.get('sourceOffset')))
                    fixtures = synthetic_response_parameter_probes(parameter, dop, kernel_sha256=kernel_sha256)
                    probes = _policy_cases(dop, fixtures, lambda f, p: probe_response_constraint_flow(
                        parameter, dop, rows, bytes.fromhex(f.get('pduHex', '')), **p), counts)
                    counts['responseParameters'] += 1
                    counts['responseParametersWithSuccess' if any(p['result']['ok'] for p in probes) else 'responseParametersBlocked'] += 1
                    output.write(json.dumps({'source': response['source'], 'sourceOffset': response['sourceOffset'],
                        'recordSha256': response['recordSha256'], 'parameterIndex': index, 'parameter': parameter,
                        'probes': probes, 'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
    return dict(counts)
