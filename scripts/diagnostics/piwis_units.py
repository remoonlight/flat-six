"""Source-pinned offline declared-unit bindings and explicit numeric unit pairs.

The runtime chooses display units through a logical-link unit group. This
module never guesses that choice or applies SI coefficients to DOP values.
An explicitly supplied pair evaluates the native arithmetic as a candidate;
it does not qualify the complete runtime unit identity/selection or vehicle.
"""
from collections import Counter
import json
import math
import struct

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_response_numeric import _retained_reference_target, _source_identity, _sha256

SOURCE_EVIDENCE_RVAS = {
    'unitConversion': 0x27fe30, 'unitIdentityTypeCompare': 0x28004b,
    'unitIdentityIdCompare': 0x280052, 'unitIdentityCopy': 0x28006c,
    'unitDimensionFlag': 0x280077, 'unitDimensionBypass': 0x28007a,
    'unitDimensionCompare': 0x2800e7, 'unitSourceFactor': 0x28016f,
    'unitSourceFactorZero': 0x28017e, 'unitSourceOffset': 0x28020e,
    'unitToSISubtract': 0x280220, 'unitToSIDivide': 0x280225,
    'unitTargetOffset': 0x280230, 'unitTargetFactor': 0x28023e,
    'unitFromSIMultiply': 0x280249, 'unitFromSIAdd': 0x28024e,
    'unitOriginalDataType': 0x280292, 'unitOriginalTypeCopy': 0x28031a,
    'runtimeReadUnit': 0x306b0f, 'runtimeSelectUnit': 0x306b32,
    'runtimeReadDimensionFlag': 0x306b6e, 'runtimeReadUnitConversion': 0x306b7d,
    'physicalDimensionComparison': 0x2b2fa0,
    'physicalDimensionNameLength': 0x2b3036, 'physicalDimensionNameBytes': 0x2b3040,
    'physicalDimensionNameMismatch': 0x2b3047,
}


def _row_identity(row):
    if (not isinstance(row, dict) or not _source_identity(row.get('source'))
            or not _sha256(row.get('recordSha256'))
            or any(type(row.get(k)) is not int or row[k] < lo for k, lo in
                   (('sourceOffset', 0), ('compressedBytes', 1), ('record', 0)))):
        raise ValueError('unit-probe-source-identity-invalid')
    return (row['source']['file'], row['source']['sha256'], row['sourceOffset'], row['recordSha256'])


def unit_profile(row, *, kernel_sha256):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('unit-probe-kernel-profile-mismatch')
    identity = _row_identity(row)
    layout = row.get('layout')
    if not isinstance(layout, dict) or layout.get('recordType') != 0x102 or layout.get('fieldLayoutComplete') is not True:
        raise ValueError('unit-probe-unit-layout-unqualified')

    def number(field):
        raw = layout.get(field)
        if (not isinstance(raw, str) or len(raw) != 16
                or any(c not in '0123456789abcdefABCDEF' for c in raw)):
            raise ValueError('unit-probe-coefficient-bytes-invalid')
        value = struct.unpack('<d', bytes.fromhex(raw))[0]
        if not math.isfinite(value):
            raise ValueError('unit-probe-coefficient-not-finite')
        return value

    dimension = layout.get('physicalDimension')
    exponents = None
    dimension_name = None
    if dimension is not None:
        raw = dimension.get('nativeExponentFieldsRaw') if isinstance(dimension, dict) else None
        if (not isinstance(dimension, dict) or dimension.get('recordType') != 0x10c or not isinstance(raw, list) or len(raw) != 7
                or any(not isinstance(x, str) or len(x) != 8
                       or any(c not in '0123456789abcdefABCDEF' for c in x) for x in raw)):
            raise ValueError('unit-probe-dimension-bytes-invalid')
        exponents = tuple(int.from_bytes(bytes.fromhex(x), 'little', signed=True) for x in raw)
        fields = dimension.get('fields')
        if isinstance(fields, list) and fields:
            first = fields[0]
            if (isinstance(first, dict) and first.get('field') == 'shortName'
                    and first.get('pool') == 'AStringData' and first.get('resolved') is True
                    and isinstance(first.get('text'), str) and first['text'].isascii()):
                dimension_name = first['text']
    display = layout.get('displayName')
    if not isinstance(display, dict) or display.get('pool') != 'UStringData':
        raise ValueError('unit-probe-display-name-unqualified')
    return {'identity': identity, 'factor': number('nativeField48RawHex'),
            'offset': number('nativeField50RawHex'), 'exponents': exponents,
            'dimensionShortName': dimension_name,
            'displayName': display.get('text') if display.get('resolved') is True else None,
            'displayNameToken': display.get('token')}


def _typed_numeric(value, native_type):
    if type(native_type) is not int or native_type not in (4, 5, 7, 11):
        raise ValueError('unit-probe-value-type-unqualified')
    if type(value) not in (int, float):
        raise ValueError('unit-probe-value-not-finite')
    if native_type in (7, 11):
        lo, hi = (-(1 << 31), (1 << 31)-1) if native_type == 7 else (0, (1 << 32)-1)
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError('unit-probe-integer-input-invalid')
        return value
    try:
        if not math.isfinite(value):
            raise ValueError('unit-probe-value-not-finite')
        if native_type == 4:
            value = struct.unpack('<f', struct.pack('<f', value))[0]
        else:
            value = float(value)
    except OverflowError as exc:
        raise ValueError('unit-probe-float32-overflow' if native_type == 4 else 'unit-probe-value-not-finite') from exc
    if not math.isfinite(value):
        raise ValueError('unit-probe-value-not-finite')
    return value


def probe_unit_conversion(source_row, target_row, value, native_type, *, kernel_sha256,
                          check_dimensions=True):
    result = {'ok': False, 'unitSemanticsVerified': False, 'runtimeUnitSelectionVerified': False,
              'runtimeUnitIdentityVerified': False, 'physicalConversionQualified': False,
              'ecuApplicabilityVerified': False, 'executionEnabled': False,
              'runtimeFloatingEnvironmentVerified': False, 'actualVehicleResponse': False}
    try:
        if type(check_dimensions) is not bool:
            raise ValueError('unit-probe-dimension-flag-invalid')
        source = unit_profile(source_row, kernel_sha256=kernel_sha256)
        target = unit_profile(target_row, kernel_sha256=kernel_sha256)
        original = _typed_numeric(value, native_type)
        # Exact same retained record is a sufficient identity case. Different
        # records never borrow runtime IDs to infer the native identity bypass.
        same = source['identity'] == target['identity']
        if not same:
            if check_dimensions:
                if source['exponents'] is None or target['exponents'] is None:
                    raise ValueError('unit-probe-dimension-missing')
                if source['dimensionShortName'] is None or target['dimensionShortName'] is None:
                    raise ValueError('unit-probe-dimension-name-unqualified')
                if (source['dimensionShortName'] != target['dimensionShortName']
                        or source['exponents'] != target['exponents']):
                    raise ValueError('unit-probe-dimension-mismatch')
            if source['factor'] == 0:
                raise ValueError('unit-probe-source-factor-zero')
            converted = float(original) - source['offset']
            converted /= source['factor']
            converted *= target['factor']
            converted += target['offset']
            if not math.isfinite(converted):
                raise ValueError('unit-probe-result-not-finite')
            if native_type in (7, 11):
                integer = math.trunc(converted)
                bits = 32 if native_type == 7 else 64
                if not -(1 << (bits-1)) <= integer < (1 << (bits-1)):
                    raise ValueError('unit-probe-integer-cast-unqualified')
                original = integer if native_type == 7 else integer & 0xffffffff
            else:
                original = _typed_numeric(converted, native_type)
        result.update(ok=True, reason=None, candidateValue=original, nativeDataType=native_type,
            sourceDisplayName=source['displayName'], targetDisplayName=target['displayName'],
            dimensionComparisonRequested=check_dimensions, dimensionCompared=check_dimensions and not same,
            conversionKind='same-retained-unit' if same else 'explicit-source-target-unit',
            sourceUnitIdentity=list(source['identity']), targetUnitIdentity=list(target['identity']))
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def write_unit_probes(layout_path, binding_path, conversion_path, *, kernel_sha256):
    """Generated audit only: declared DOP units and explicit compatible pairs."""
    rows = {}
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); key = (row['source']['file'], row['sourceOffset'])
            if key in rows:
                raise ValueError('unit-probe-duplicate-layout-record')
            rows[key] = row
    units = [r for r in rows.values() if r['layout'].get('recordType') == 0x102]
    if len(units) > 4096:
        raise ValueError('unit-probe-unit-count-offline-limit')
    profiles = [unit_profile(r, kernel_sha256=kernel_sha256) for r in units]
    counts = Counter()
    with binding_path.open('x', encoding='utf8') as output:
        for dop in rows.values():
            if dop['layout'].get('recordType') != 0x2c:
                continue
            ref = dop['layout'].get('unitReference'); binding = {'ok': False, 'reason': 'unit-probe-no-declared-unit'}
            target_row = None
            if ref is not None:
                lookup = ref.get('indexLookup') or {}; target = lookup.get('target') or {}
                target_row = rows.get(((lookup.get('databaseSource') or {}).get('file'), target.get('sourceOffset')))
                try:
                    _retained_reference_target(ref, target_row, expected_record_type=0x102)
                    profile = unit_profile(target_row, kernel_sha256=kernel_sha256)
                    binding = {'ok': True, 'reason': None, 'displayName': profile['displayName'],
                        'displayNameToken': profile['displayNameToken'], 'exponents': profile['exponents'],
                        'dimensionShortName': profile['dimensionShortName'],
                        'staticIndexTargetMatched': True, 'unitSemanticsVerified': False}
                except ValueError as exc:
                    binding['reason'] = str(exc)
            counts['dopDefinitions'] += 1
            counts['declaredUnitBindings' if binding['ok'] else binding['reason']] += 1
            output.write(json.dumps({'dopSource': dop['source'], 'dopSourceOffset': dop['sourceOffset'],
                'dopRecordSha256': dop['recordSha256'], 'unitReference': ref,
                'unitRecord': {k:target_row[k] for k in ('source','sourceOffset','record','compressedBytes','recordSha256')} if target_row else None,
                'binding': binding, 'runtimeUnitSelectionVerified': False, 'physicalConversionQualified': False,
                'executionEnabled': False, 'actualVehicleResponse': False}, ensure_ascii=True)+'\n')
    fixtures = [(5, v) for v in (-40.0, 0.0, 1234.5)] + [(4, v) for v in (1.5, 100.0)] + [(7, -1), (7, 100), (11, 0), (11, 100)]
    with conversion_path.open('x', encoding='utf8') as output:
        for s, source in enumerate(units):
            for t, target in enumerate(units):
                same = s == t
                compatible = (profiles[s]['exponents'] is not None
                    and profiles[s]['dimensionShortName'] is not None
                    and profiles[s]['dimensionShortName'] == profiles[t]['dimensionShortName']
                    and profiles[s]['exponents'] == profiles[t]['exponents'])
                if not same and not compatible:
                    continue
                counts['explicitUnitPairs'] += 1
                results = [{'nativeDataType': kind, 'inputValue': value,
                    'result': probe_unit_conversion(source, target, value, kind,
                        kernel_sha256=kernel_sha256, check_dimensions=True)} for kind, value in fixtures]
                for r in results: counts['success' if r['result']['ok'] else r['result']['reason']] += 1
                output.write(json.dumps({'sourceUnitRecord': source, 'targetUnitRecord': target,
                    'fixtureOrigin': 'synthetic-explicit-unit-pair', 'actualVehicleResponse': False,
                    'runtimeUnitSelectionVerified': False, 'executionEnabled': False,
                    'probes': results}, ensure_ascii=True)+'\n')
    counts['unitRecords'] = len(units)
    return dict(counts)
