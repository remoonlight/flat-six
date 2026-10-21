"""Source-pinned ScaleLinearConversion selection, offline candidates only."""
import math
import struct

from .piwis_scale_limits import integer_scale_range, integer_range_contains, synthetic_integer_range_values

SOURCE_EVIDENCE_RVAS = {
    'scaleLinearConversion': 0x262940, 'scaleLinearCodedBase': 0x262a8b,
    'scaleLinearFirstMatchGuard': 0x262be3, 'scaleLinearIndexedScale': 0x262bfb,
    'scaleLinearCodedLower': 0x262d45, 'scaleLinearCodedUpper': 0x262da6,
    'scaleLinearRangeGuard': 0x262e26, 'scaleLinearCodedCoefficients': 0x262e65,
    'scaleLinearCalculation': 0x262ec7, 'scaleLinearMatchStops': 0x262ede,
    'scaleLinearNextScale': 0x262eea, 'scaleLinearDefaultGuard': 0x262f2a,
    'scaleLinearDefaultCopy': 0x262f94, 'scaleLinearPhysicalDefaultType': 0x263180,
    'scaleLinearDefaultConversion': 0x2631b7, 'scaleLinearUndefinedNoMatch': 0x26355d,
    'valueToCodedValue': 0x254e20, 'defaultValueConvertAndCopy': 0x254e83,
    'defaultFloat32Copy': 0x8f5aa, 'defaultFloat64Copy': 0x8f5f1,
    'defaultDoubleToFloat': 0x947e7, 'defaultFloatToDouble': 0x94b1a,
}
SCALE_PROBE_LIMIT = 4096  # Offline resource guard, not a native scale limit.


def scale_linear_profile(method, *, signed=False):
    base = method.get('nativeField28')
    if not isinstance(base, dict) or base.get('recordType') != 5:
        raise ValueError('numeric-probe-scale-linear-coded-base-missing')
    collection = base.get('scales')
    if not isinstance(collection, dict):
        raise ValueError('numeric-probe-scale-linear-scales-missing')
    count, rows = collection.get('declaredScales'), collection.get('scales')
    if (type(count) is not int or not 0 <= count <= SCALE_PROBE_LIMIT
            or not isinstance(rows, list) or len(rows) != count):
        raise ValueError('numeric-probe-scale-linear-scale-count-invalid')
    scales = []
    for row in rows:
        if not isinstance(row, dict) or row.get('recordType') != 0x19:
            raise ValueError('numeric-probe-scale-linear-scale-missing')
        limits = integer_scale_range(row, signed=signed)
        coefficients = row.get('nativeField20')
        if not isinstance(coefficients, dict) or coefficients.get('recordType') != 0x14:
            raise ValueError('numeric-probe-coded-to-physical-coefficients-missing')
        scales.append({'limits': limits, 'coefficients': coefficients})
    default = base.get('default')
    if (not isinstance(default, dict) or type(default.get('nativeType')) is not int
            or type(default.get('nativeValueDefined')) is not bool):
        raise ValueError('numeric-probe-scale-linear-default-missing')
    if default['nativeType'] in (0, 255):
        if default['nativeValueDefined'] or default.get('rawHex') not in (None, ''):
            raise ValueError('numeric-probe-scale-linear-default-unqualified')
    elif not default['nativeValueDefined']:
        raise ValueError('numeric-probe-scale-linear-default-unqualified')
    return {'scales': scales, 'default': default}


def _floating_default(value):
    kind, raw = value['nativeType'], value.get('rawHex')
    widths = {4: 4, 5: 8, 6: 2, 7: 4, 10: 2, 11: 4}
    if (kind not in widths or not isinstance(raw, str) or len(raw) != widths[kind]*2
            or any(c not in '0123456789abcdefABCDEF' for c in raw)):
        raise ValueError('numeric-probe-scale-linear-default-unqualified')
    data = bytes.fromhex(raw)
    number = struct.unpack('<f' if kind == 4 else '<d', data)[0] if kind in (4, 5) else float(
        int.from_bytes(data, 'little', signed=kind in (6, 7)))
    if not math.isfinite(number):
        raise ValueError('numeric-probe-scale-linear-default-not-finite')
    return number


def select_scale_linear(profile, value):
    for index, scale in enumerate(profile['scales']):
        if integer_range_contains(scale['limits'], value):
            return scale['coefficients'], None, {'kind': 'first-matching-coded-range', 'scaleIndex': index}
    default = profile['default']
    if default['nativeType'] in (0, 255):
        raise ValueError('numeric-probe-scale-linear-no-match')
    return None, _floating_default(default), {'kind': 'explicit-forward-default',
        'scaleIndex': None, 'defaultNativeType': default['nativeType']}


def synthetic_scale_linear_values(profile, width, *, signed=False, mask=None):
    interiors, others = [], []
    if not profile['scales']:
        return synthetic_integer_range_values(((None, False), (None, False)), width, signed=signed, mask=mask)
    for index, scale in enumerate(profile['scales']):
        for name, value in synthetic_integer_range_values(scale['limits'], width, signed=signed, mask=mask):
            case = ('scale-' + str(index) + '-' + name, value)
            (interiors if name == 'linear-range-interior' else others).append(case)
    # Equal values need only one field; selection is separately checked against
    # the full serialized scale order, including overlap and empty intervals.
    seen, cases = set(), []
    for case in interiors + others:
        if case[1] not in seen:
            cases.append(case)
            seen.add(case[1])
    return cases
