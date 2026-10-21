"""Pinned numeric checkConstraint candidates with explicit native policy inputs.

This evaluates one already selected constraint, not the caller's reference,
computed-constraint or conversion decisions. No runtime configuration is
inferred and no result authorizes a vehicle value or operation.
"""
from collections import Counter
import json
import math
import struct

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_response_numeric import _retained_reference_target, _source_identity, _sha256

SOURCE_EVIDENCE_RVAS = {
    'constraintCheck': 0x26e650, 'constraintDefaultRangeInfo': 0x26e978,
    'constraintPrimaryInterval': 0x26e987, 'constraintPrimaryRange': 0x26eb2a,
    'constraintPrimaryOutside': 0x26eb36, 'constraintPrimaryValid': 0x26eb7c,
    'constraintScaleCountGuard': 0x26ebc8, 'constraintScaleAtIndex': 0x26ec0b,
    'constraintScaleRange': 0x26ed1d, 'constraintFirstScaleMatch': 0x26eeb7,
    'constraintNextScale': 0x26eeea, 'constraintScaleRangeInfo': 0x26ef3a,
    'constraintOldPolicy': 0x26efa3, 'constraintDirectionPolicy': 0x26efaa,
    'constraintODX201Policy': 0x26f029, 'constraintNewValidPolicy': 0x26f1dc,
    'constraintStoreRangeInfo': 0x26f0fc, 'constraintExceptionMode': 0x26f1b7,
    'constraintInvalidScaleException': 0x26f368, 'constraintOutsideException': 0x26f4a1,
    'constraintIntervalRange': 0x24a49a, 'constraintNumericRange': 0x2685e0,
    'constraintFloat32LowerClosed': 0x268a6f, 'constraintFloat64LowerClosed': 0x268b26,
    'constraintFloat32UpperClosed': 0x2693f9, 'constraintFloat64UpperClosed': 0x2694b1,
    'constraintSignedLowerClosed': 0x268d25, 'constraintUnsignedLowerClosed': 0x268ff6,
    'constraintSignedUpperClosed': 0x2696b2, 'constraintUnsignedUpperClosed': 0x269987,
    'constraintRangeBothLimitsRequired': 0x269ab2,
}

CONFIG_FIELDS = ('constraintCheck', 'oldConstraintCheck', 'constraintCheck_ODX_21_22', 'isODX201')
RANGE_INFO_NAMES = {0x602: 'VALID', 0x603: 'NOT_DEFINED', 0x604: 'NOT_AVAILABLE',
                    0x605: 'NOT_VALID', 0x606: 'NOT_INITIALIZED', 0x607: 'CODED_TO_PHYSICAL_FAILED'}


def _row_identity(row):
    if (not isinstance(row, dict) or not _source_identity(row.get('source'))
            or not _sha256(row.get('recordSha256'))
            or any(type(row.get(k)) is not int or row[k] < lo for k, lo in
                   (('sourceOffset', 0), ('compressedBytes', 1), ('record', 0)))):
        raise ValueError('constraint-probe-source-identity-invalid')
    return (row['source']['file'], row['source']['sha256'], row['sourceOffset'], row['recordSha256'])


def _number(definition, native_type):
    if native_type not in (4, 5, 7, 11) or type(native_type) is not int:
        raise ValueError('constraint-probe-value-type-unqualified')
    if (not isinstance(definition, dict) or definition.get('nativeType') != native_type
            or definition.get('nativeValueDefined') is not True):
        raise ValueError('constraint-probe-limit-type-unqualified')
    raw = definition.get('rawHex'); size = 8 if native_type == 5 else 4
    if (not isinstance(raw, str) or len(raw) != 2 * size
            or any(c not in '0123456789abcdefABCDEF' for c in raw)):
        raise ValueError('constraint-probe-limit-bytes-invalid')
    value = struct.unpack({4: '<f', 5: '<d', 7: '<i', 11: '<I'}[native_type], bytes.fromhex(raw))[0]
    if not math.isfinite(value):
        raise ValueError('constraint-probe-limit-not-finite')
    return value


def _input(value, native_type):
    if type(native_type) is not int or native_type not in (4, 5, 7, 11):
        raise ValueError('constraint-probe-value-type-unqualified')
    if type(value) not in (int, float):
        raise ValueError('constraint-probe-input-invalid')
    if native_type in (7, 11):
        lo, hi = (-2**31, 2**31 - 1) if native_type == 7 else (0, 2**32 - 1)
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError('constraint-probe-input-invalid')
        return value
    try:
        value = struct.unpack('<f', struct.pack('<f', value))[0] if native_type == 4 else float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError('constraint-probe-input-invalid') from exc
    if not math.isfinite(value):
        raise ValueError('constraint-probe-input-invalid')
    return value


def _interval_contains(interval, value, native_type):
    if not isinstance(interval, dict) or interval.get('recordType') != 0xfb:
        raise ValueError('constraint-probe-interval-unqualified')
    flags = interval.get('nativeLimitFlags')
    if type(flags) is not int or not 0 <= flags <= 255:
        raise ValueError('constraint-probe-limit-flags-invalid')
    lower_type = 0x6d03 if flags & 2 else 0x6d01 + bool(flags & 1)
    upper_type = 0x6d03 if flags & 0x20 else 0x6d01 + bool(flags & 0x10)
    if (interval.get('nativeLowerIntervalType') != lower_type
            or interval.get('nativeUpperIntervalType') != upper_type):
        raise ValueError('constraint-probe-limit-flags-mismatch')
    # Native helper evaluates both sides even when the lower comparison fails.
    # Unlimited sides skip getters; undefined closed/open limits do not.
    lower = None if lower_type == 0x6d03 else _number(interval.get('lowerLimit'), native_type)
    upper = None if upper_type == 0x6d03 else _number(interval.get('upperLimit'), native_type)
    return ((lower is None or value > lower or (lower_type == 0x6d02 and value == lower))
            and (upper is None or value < upper or (upper_type == 0x6d02 and value == upper)))


def probe_constraint_classification(row, value, native_type, *, kernel_sha256,
                                    configuration, native_property_mode, native_direction_flag):
    """Classify a numeric candidate under a fully explicit checkConstraint policy.

    Native property mode 1 raises on the policy-invalid result. Other modes
    return its range information. This does not emulate performValueConversion,
    which can skip this function (including computed constraints and mode 3).
    """
    result = {'ok': False, 'constraintSemanticsVerified': False,
        'runtimeConstraintSelectionVerified': False, 'runtimeConfigurationVerified': False,
        'physicalConversionQualified': False, 'actualVehicleResponse': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise ValueError('constraint-probe-kernel-profile-mismatch')
        identity = _row_identity(row)
        if (not isinstance(configuration, dict) or set(configuration) != set(CONFIG_FIELDS)
                or any(type(configuration[k]) is not bool for k in CONFIG_FIELDS)
                or type(native_direction_flag) is not bool
                or type(native_property_mode) is not int or not 0 <= native_property_mode <= 0xffffffff):
            raise ValueError('constraint-probe-policy-input-invalid')
        layout = row.get('layout')
        if (not isinstance(layout, dict) or layout.get('recordType') != 0x203
                or layout.get('fieldLayoutComplete') is not True):
            raise ValueError('constraint-probe-layout-unqualified')
        value = _input(value, native_type)
        primary = _interval_contains(layout.get('interval'), value, native_type)
        valid, range_info, selected = primary, 0x602 if primary else 0x605, None
        if primary:
            scales = layout.get('scaleConstraints')
            if (not isinstance(scales, dict) or scales.get('recordType') != 0x201
                    or not isinstance(scales.get('scales'), list)
                    or type(scales.get('declaredScales')) is not int
                    or scales['declaredScales'] != len(scales['scales'])
                    or not 0 <= len(scales['scales']) <= 4096):
                raise ValueError('constraint-probe-scales-unqualified')
            for index, scale in enumerate(scales['scales']):
                if not isinstance(scale, dict) or scale.get('recordType') != 0x202:
                    raise ValueError('constraint-probe-scale-unqualified')
                if not _interval_contains(scale.get('interval'), value, native_type):
                    continue
                selected, range_info = index, scale.get('nativeRangeInfo')
                if type(range_info) is not int or range_info not in RANGE_INFO_NAMES:
                    raise ValueError('constraint-probe-range-info-unqualified')
                if configuration['oldConstraintCheck']:
                    valid = range_info in (0x602, 0x604) if native_direction_flag else range_info != 0x605
                else:
                    checking = configuration['constraintCheck'] if configuration['isODX201'] else configuration['constraintCheck_ODX_21_22']
                    valid = range_info == 0x602 if checking else True
                break
        result.update(ok=True, reason=None, nativeRangeInfo=range_info,
            nativeRangeInfoName=RANGE_INFO_NAMES[range_info], primaryIntervalMatched=primary,
            selectedScaleIndex=selected, candidatePolicyValid=valid,
            candidateNativeException=native_property_mode == 1 and not valid,
            nativePropertyMode=native_property_mode, nativeDirectionFlag=native_direction_flag,
            configuration=dict(configuration), nativeDataType=native_type, inputValue=value,
            constraintIdentity=list(identity), callerGatesVerified=False)
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def _neighbor(value, kind, direction):
    if kind in (7, 11):
        return value + direction
    if kind == 5:
        return math.nextafter(value, math.inf if direction > 0 else -math.inf)
    bits = int.from_bytes(struct.pack('<f', value), 'little')
    if value == 0:
        bits = 1 if direction > 0 else 0x80000001
    else:
        bits += direction if value > 0 else -direction
    return struct.unpack('<f', bits.to_bytes(4, 'little'))[0]


def _fixtures(row):
    layout = row['layout']; primary = layout.get('interval') or {}
    kinds = [(primary.get(k) or {}).get('nativeType') for k in ('lowerLimit', 'upperLimit')]
    kind = next((k for k in kinds if k != 255), 11)
    if type(kind) is not int or kind not in (4, 5, 7, 11):
        raise ValueError('constraint-probe-value-type-unqualified')
    candidates = [('zero', 0 if kind in (7, 11) else 0.0)]
    intervals = [('primary', primary)] + [('scale-' + str(i), (s or {}).get('interval') or {})
        for i, s in enumerate((layout.get('scaleConstraints') or {}).get('scales', []))]
    for name, interval in intervals:
        for field in ('lowerLimit', 'upperLimit'):
            definition = interval.get(field)
            if isinstance(definition, dict) and definition.get('nativeType') == kind and definition.get('nativeValueDefined') is True:
                bound = _number(definition, kind)
                for suffix, value in (('-below', _neighbor(bound, kind, -1)), ('', bound), ('-above', _neighbor(bound, kind, 1))):
                    try:
                        value = _input(value, kind)
                    except ValueError:
                        continue
                    candidates.append((name + '-' + field + suffix, value))
    # A bounded synthetic audit, never a claim that fixtures cover all values.
    if len(candidates) > 65536:
        raise ValueError('constraint-probe-fixture-count-offline-limit')
    return kind, candidates


def write_constraint_probes(layout_path, binding_path, classification_path, *, kernel_sha256):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('constraint-probe-kernel-profile-mismatch')
    rows = {}
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); key = (row['source']['file'], row['sourceOffset'])
            if key in rows:
                raise ValueError('constraint-probe-duplicate-layout-record')
            rows[key] = row
    counts = Counter()
    with binding_path.open('x', encoding='utf8') as output:
        for dop in rows.values():
            if dop['layout'].get('recordType') != 0x2c:
                continue
            for field in ('internalConstraintReference', 'physicalConstraintReference'):
                ref = dop['layout'].get(field); target_row = None
                binding = {'ok': False, 'reason': 'constraint-probe-no-declared-constraint'}
                if ref is not None:
                    lookup = ref.get('indexLookup') or {}; target = lookup.get('target') or {}
                    target_row = rows.get(((lookup.get('databaseSource') or {}).get('file'), target.get('sourceOffset')))
                    try:
                        _row_identity(dop); _row_identity(target_row)
                        _retained_reference_target(ref, target_row, expected_record_type=0x203)
                        if target_row['layout'].get('fieldLayoutComplete') is not True:
                            raise ValueError('constraint-probe-layout-unqualified')
                        binding = {'ok': True, 'reason': None, 'staticIndexTargetMatched': True}
                    except ValueError as exc:
                        binding['reason'] = str(exc)
                counts[field + (':bound' if binding['ok'] else ':' + binding['reason'])] += 1
                output.write(json.dumps({'dopSource': dop['source'], 'dopSourceOffset': dop['sourceOffset'],
                    'dopRecordSha256': dop['recordSha256'], 'referenceField': field, 'constraintReference': ref,
                    'constraintRecord': {k: target_row[k] for k in ('source', 'sourceOffset', 'record', 'compressedBytes', 'recordSha256')} if target_row else None,
                    'binding': binding, 'runtimeConstraintSelectionVerified': False,
                    'executionEnabled': False, 'actualVehicleResponse': False}, ensure_ascii=True) + '\n')
    # Explicit contrasting policies; these are not discovered runtime settings.
    policies = [('explicit-odx201-check', dict(zip(CONFIG_FIELDS, (True, False, False, True))), False),
                ('explicit-odx21-22-check', dict(zip(CONFIG_FIELDS, (False, False, True, False))), True),
                ('explicit-check-disabled', dict(zip(CONFIG_FIELDS, (False, False, False, True))), False),
                ('explicit-old-direction-false', dict(zip(CONFIG_FIELDS, (False, True, False, False))), False),
                ('explicit-old-direction-true', dict(zip(CONFIG_FIELDS, (False, True, False, False))), True)]
    with classification_path.open('x', encoding='utf8') as output:
        for row in rows.values():
            if row['layout'].get('recordType') != 0x203:
                continue
            counts['constraintDefinitions'] += 1; probes = []
            try:
                kind, fixtures = _fixtures(row)
                for name, value in fixtures:
                    for policy, configuration, direction in policies:
                        for mode in (0, 1):
                            result = probe_constraint_classification(row, value, kind, kernel_sha256=kernel_sha256,
                                configuration=configuration, native_property_mode=mode, native_direction_flag=direction)
                            counts['success' if result['ok'] else result['reason']] += 1
                            if result['ok']:
                                counts['rangeInfo:' + result['nativeRangeInfoName']] += 1
                                counts['nativeException' if result['candidateNativeException'] else 'nativeReturn'] += 1
                            probes.append({'fixture': name, 'policyFixture': policy, 'result': result})
                reason = None
            except ValueError as exc:
                reason = str(exc); counts[reason] += 1
            output.write(json.dumps({'constraintRecord': row, 'fixtureOrigin': 'synthetic-explicit-constraint-policy',
                'actualVehicleResponse': False, 'executionEnabled': False, 'fixtureBlockedReason': reason,
                'probes': probes}, ensure_ascii=True) + '\n')
    return dict(counts)
