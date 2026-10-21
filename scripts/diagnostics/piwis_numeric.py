"""Source-pinned offline numeric stages, never a vehicle execution path.

Supports fixed 1..32-bit UINT32/None and INT32/TwoComplement fields, ordinary
same-width masks, integer/floating linear with same-type integer coded limits, ordered
floating scale-linear ranges/defaults, closed point texttable and byte-aligned fixed
ISO88591/None bytefield identity branches, plus MIN-MAX byte/text leaves with
nonnegative minimum and termination 0/1/2. Results are candidates: record envelopes, parameter binding, constraint
references, units and ECU versions still require separate qualification.
"""
import math
import struct

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_texttable import texttable_profile, texttable_value, SOURCE_EVIDENCE_RVAS as TEXTTABLE_RVAS
from .piwis_fixed_values import (is_fixed_value_family, decode_fixed_value,
    synthetic_fixed_value_fields, SOURCE_EVIDENCE_RVAS as FIXED_VALUE_RVAS)
from .piwis_variable_values import (decode_variable_value, synthetic_variable_value_fields,
    SOURCE_EVIDENCE_RVAS as VARIABLE_VALUE_RVAS)
from .piwis_scale_limits import (integer_scale_range, integer_range_contains,
    synthetic_integer_range_values, SOURCE_EVIDENCE_RVAS as SCALE_LIMIT_RVAS)
from .piwis_scale_linear import (scale_linear_profile, select_scale_linear,
    synthetic_scale_linear_values, SOURCE_EVIDENCE_RVAS as SCALE_LINEAR_RVAS)
from .piwis_integer_conversion import (integer_rational,
    SOURCE_EVIDENCE_RVAS as INTEGER_CONVERSION_RVAS)

SOURCE_EVIDENCE_RVAS = {
    **TEXTTABLE_RVAS,
    **FIXED_VALUE_RVAS,
    **VARIABLE_VALUE_RVAS,
    **SCALE_LIMIT_RVAS,
    **SCALE_LINEAR_RVAS,
    **INTEGER_CONVERSION_RVAS,
    'responseFieldExtraction': 0x272270, 'standardLengthExtraction': 0x2737f0,
    'byteCount': 0x2665c0, 'extractBits': 0x267b80,
    'changeHighLowOrder': 0x2706d0, 'normalizeBytes': 0x2666a0,
    'standardLengthInfo': 0x2550a0, 'adjustCodedField': 0x255450,
    'decoder': 0x2551c0, 'noneEncodingDecoder': 0x257fe0,
    'codedToPhysicalEntry': 0x272b90, 'performValueConversion': 0x26f4b0,
    'linearConversion': 0x2611d0, 'floatRationalCalculation': 0x260850,
    'floatHornerNumerator': 0x260dd0, 'floatHornerDenominator': 0x260e10,
    'emptyDenominatorConstant': 0x6527b0,
    'bytesToMsbBits': 0x2579d0, 'msbBitsToBytes': 0x2578f0,
    'nonAlignedBitSlice': 0x267d24,
    'twoComplementDecoder': 0x259240, 'signedFieldExtension': 0x2555bf,
    'codedBaseToValueType': 0x26b040, 'integerToDouble': 0x94ab0,
    'int32ToDoubleBranch': 0x94bdb,
    'ordinaryBitMask': 0x267010, 'maskBeforeSignedExtension': 0x255100,
}


def _integer_profile(coded, bit_offset, kernel_sha256, *, signed=False):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('numeric-probe-kernel-profile-mismatch')
    if not isinstance(coded, dict) or coded.get('present') is not True or coded.get('recordType') != 0x23:
        raise ValueError('numeric-probe-coded-type-missing')
    if coded.get('nativeKind') != 2:
        raise ValueError('numeric-probe-fixed-standard-length-only')
    width = coded.get('bitLength')
    if type(width) is not int or not 1 <= width <= 32:
        raise ValueError('numeric-probe-uint32-width-only')
    if type(bit_offset) is not int or not 0 <= bit_offset <= 7:
        raise ValueError('numeric-probe-bit-position-invalid')
    fields = coded.get('nativeEncodingFieldsRaw')
    if (not isinstance(fields, list) or len(fields) != 4
            or any(type(x) is not int for x in fields) or fields[0:2] != ([0, 3] if signed else [1, 11])):
        raise ValueError('numeric-probe-int32-two-complement-only' if signed
                         else 'numeric-probe-uint32-none-encoding-only')
    if fields[2] not in (0, 1) or fields[3] != 0:
        raise ValueError('numeric-probe-order-or-condensed-mask-unqualified')
    _mask_bytes(coded)
    return (bit_offset + width + 7) // 8, fields[2]


def _mask_bytes(coded):
    present, raw = coded.get('bitMaskPresent'), coded.get('bitMaskRawHex')
    if present is False and not raw:
        return None
    size = (coded['bitLength'] + 7) // 8
    if (present is not True or not isinstance(raw, str) or len(raw) != size * 2
            or any(c not in '0123456789abcdefABCDEF' for c in raw)):
        raise ValueError('numeric-probe-mask-unqualified')
    return bytes.fromhex(raw)


def _uint_profile(coded, bit_offset, kernel_sha256):
    return _integer_profile(coded, bit_offset, kernel_sha256)


def _decode_integer_field(pdu, coded, byte_offset, bit_offset, kernel_sha256, *, signed=False):
    size, high_low = _integer_profile(coded, bit_offset, kernel_sha256, signed=signed)
    if type(pdu) is not bytes or len(pdu) > 4095:
        raise ValueError('numeric-probe-pdu-invalid')
    if type(byte_offset) is not int or not 0 <= byte_offset <= 4094:
        raise ValueError('numeric-probe-byte-position-invalid')
    if byte_offset + size > len(pdu):
        raise ValueError('numeric-probe-truncated-field')
    chunk = pdu[byte_offset:byte_offset + size]
    # 267d24 selects [MSB-vector-size - bitPosition - width,
    # MSB-vector-size - bitPosition). Packing right-aligns the selected bits.
    value = int.from_bytes(chunk, 'big' if high_low else 'little')
    width = coded['bitLength']
    value = (value >> bit_offset) & ((1 << width) - 1)
    mask = _mask_bytes(coded)
    if mask is not None:
        # 27398c normalizes input order before bit extraction. 26711c ANDs
        # the right-aligned bytes with the serialized mask in that order.
        # Ordinary masking leaves the declared width (and sign bit) intact.
        value &= int.from_bytes(mask, 'big')
    # 255450 tests the selected field's width-1 sign bit; the TwoComplement
    # branch fills unused upper bits and extends to four bytes before 259240.
    return value - (1 << width) if signed and value & (1 << (width - 1)) else value


def decode_unsigned_field(pdu, coded, byte_offset, bit_offset=0, *, kernel_sha256):
    """Decode pinned UINT32/None only; caller owns parameter/source binding."""
    return _decode_integer_field(pdu, coded, byte_offset, bit_offset, kernel_sha256)


def decode_signed_field(pdu, coded, byte_offset, bit_offset=0, *, kernel_sha256):
    """Decode pinned INT32/TwoComplement only, extending the declared width."""
    return _decode_integer_field(pdu, coded, byte_offset, bit_offset, kernel_sha256, signed=True)


def _is_signed(coded):
    fields = coded.get('nativeEncodingFieldsRaw') if isinstance(coded, dict) else None
    return isinstance(fields, list) and fields[:2] == [0, 3]


def floating_rational(coefficients, value):
    """Native double Horner order; empty numerator=0, empty denominator=1."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('numeric-probe-input-not-finite')
    def vector(name):
        items = coefficients.get(name)
        if not isinstance(items, list) or len(items) > 255:
            raise ValueError('numeric-probe-coefficient-count-invalid')
        numbers = []
        for item in items:
            try:
                raw = bytes.fromhex(item['rawHex'])
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError('numeric-probe-coefficient-bytes-invalid') from exc
            if len(raw) != 8:
                raise ValueError('numeric-probe-coefficient-width-invalid')
            number = struct.unpack('<d', raw)[0]
            if not math.isfinite(number):
                raise ValueError('numeric-probe-coefficient-not-finite')
            numbers.append(number)
        return numbers
    def horner(numbers, empty):
        if not numbers:
            return empty
        result = numbers[-1]
        for number in reversed(numbers[:-1]):
            result = result * float(value) + number
        return result
    numerator = horner(vector('numerator'), 0.0)
    denominator = horner(vector('denominator'), 1.0)
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        raise ValueError('numeric-probe-intermediate-not-finite')
    if denominator == 0:
        raise ValueError('numeric-probe-zero-denominator')
    result = numerator / denominator
    if not math.isfinite(result):
        raise ValueError('numeric-probe-result-not-finite')
    return result


def _conversion(layout, *, signed=False):
    if layout.get('recordType') != 0x2c or layout.get('fieldLayoutComplete') is not True:
        raise ValueError('numeric-probe-dop-layout-incomplete')
    physical = layout.get('physicalType') or {}
    target = physical.get('nativeBaseDataType')
    method = (layout.get('compuPrefix') or {}).get('compuMethod') or {}
    category = method.get('nativeCategory')
    if type(target) is not int or type(category) is not int:
        raise ValueError('numeric-probe-conversion-type-invalid')
    if category == 0 and target == (0 if signed else 1):
        return 'identity-int32' if signed else 'identity-uint32', None, target
    if category == 3 and target == 4 and not signed:
        return 'texttable-point-enum', texttable_profile(layout), target
    if category == 2 and target in (2, 3):
        return 'scale-linear-float', scale_linear_profile(method, signed=signed), target
    if category != 1 or target not in (0, 1, 2, 3):
        raise ValueError('numeric-probe-conversion-unqualified')
    # False direction in 272b90 -> 26f4b0 -> strategy ABI's second bool selects
    # base +28 and coefficient +20. Opposite-base fallbacks are not qualified.
    base = method.get('nativeField28') or {}
    scales = base.get('scales') or {}
    rows = scales.get('scales')
    if scales.get('declaredScales') != 1 or not isinstance(rows, list) or len(rows) != 1 or not rows[0]:
        raise ValueError('numeric-probe-single-linear-scale-only')
    scale = rows[0]
    limits = integer_scale_range(scale, signed=signed)
    coefficients = scale.get('nativeField20')
    if not coefficients or coefficients.get('recordType') != 0x14:
        raise ValueError('numeric-probe-coded-to-physical-coefficients-missing')
    if target in (0, 1):
        return 'linear-integer', {'limits': limits, 'coefficients': coefficients,
                                 'unsigned': target == 1 and not signed}, target
    if any(bound[0] is not None for bound in limits):
        return 'linear-range-float', {'limits': limits, 'coefficients': coefficients}, target
    return 'linear-float', coefficients, target


def probe_dop_numeric(layout, pdu, byte_offset, bit_offset=0, *, kernel_sha256):
    """Candidate for offline evidence only; never claims complete vehicle values."""
    result = {'ok': False, 'physicalConversionQualified': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False,
        'parameterBindingVerified': False, 'constraintReferencesVerified': False,
        'unitSemanticsVerified': False, 'runtimeFloatingEnvironmentVerified': False}
    try:
        if not isinstance(layout, dict):
            raise ValueError('numeric-probe-dop-layout-invalid')
        coded = layout.get('codedType') or {}
        if is_fixed_value_family(coded):
            decoder = decode_variable_value if coded.get('nativeKind') == 1 else decode_fixed_value
            result.update(decoder(layout, pdu, byte_offset, bit_offset, kernel_sha256=kernel_sha256))
            result.update(ok=True, reason=None)
            return result
        signed = _is_signed(coded)
        decoder = decode_signed_field if signed else decode_unsigned_field
        raw = decoder(pdu, coded, byte_offset, bit_offset, kernel_sha256=kernel_sha256)
        kind, coefficients, target = _conversion(layout, signed=signed)
        if kind == 'texttable-point-enum':
            value, selection = texttable_value(coefficients, raw)
        elif kind == 'linear-range-float':
            if not integer_range_contains(coefficients['limits'], raw):
                raise ValueError('numeric-probe-outside-coded-scale-range')
            value = floating_rational(coefficients['coefficients'], raw)
        elif kind == 'linear-integer':
            if not integer_range_contains(coefficients['limits'], raw):
                raise ValueError('numeric-probe-outside-coded-scale-range')
            value = integer_rational(coefficients['coefficients'], raw,
                                     unsigned=coefficients['unsigned'])
        elif kind == 'scale-linear-float':
            selected_coefficients, default, selection = select_scale_linear(coefficients, raw)
            value = floating_rational(selected_coefficients, raw) if selected_coefficients is not None else default
        else:
            value = raw if kind in ('identity-uint32', 'identity-int32') else floating_rational(coefficients, raw)
        if target == 2:
            try:
                value = struct.unpack('<f', struct.pack('<f', value))[0]
            except OverflowError as exc:
                raise ValueError('numeric-probe-float32-overflow') from exc
            if not math.isfinite(value):
                raise ValueError('numeric-probe-float32-not-finite')
        size = (coded['bitLength'] + bit_offset + 7) // 8
        result.update({'ok': True, 'probeKind': kind, 'rawValue': raw, 'candidateValue': value,
            'fieldRawHex': pdu[byte_offset:byte_offset+size].hex(),
            'byteOffset': byte_offset, 'bitOffset': bit_offset, 'bitLength': coded['bitLength'],
            'kernelSha256': kernel_sha256, 'reason': None})
        if kind == 'texttable-point-enum':
            result.update({'enumSelection': selection, 'conversionStage': 'source-pinned-texttable-point'})
        elif kind == 'linear-range-float':
            result.update({'codedScaleRangeChecked': True, 'scaleIndex': 0,
                           'conversionStage': 'source-pinned-linear-coded-range'})
        elif kind == 'scale-linear-float':
            result.update({'codedScaleRangeChecked': True, 'scaleSelection': selection,
                           'scaleIndex': selection['scaleIndex'],
                           'conversionStage': 'source-pinned-scale-linear-coded-ranges'})
        elif kind == 'linear-integer':
            result.update({'codedScaleRangeChecked': True, 'scaleIndex': 0,
                           'conversionStage': 'source-pinned-linear-integer-arithmetic',
                           'nativeArithmetic': 'unsigned32' if coefficients['unsigned'] else 'signed32',
                           'nativeResultDataType': 11 if coefficients['unsigned'] else 7,
                           'declaredPhysicalBaseDataType': target})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def synthetic_dop_probes(layout, *, kernel_sha256):
    """Bounded synthetic fields for source-definition checks, never ECU samples."""
    if is_fixed_value_family(layout.get('codedType')):
        try:
            factory = synthetic_variable_value_fields if layout['codedType'].get('nativeKind') == 1 else synthetic_fixed_value_fields
            fields = factory(layout, kernel_sha256=kernel_sha256)
        except ValueError:
            fields = [('zero', b'')]
        return [{'case': name, 'fieldHex': payload.hex(), 'fixtureOrigin': 'synthetic-field-bytes',
            'bytePositionOrigin': 'probe-only-zero', 'actualVehicleResponse': False,
            'result': probe_dop_numeric(layout, payload, 0, kernel_sha256=kernel_sha256)}
            for name, payload in fields]
    width = (layout.get('codedType') or {}).get('bitLength')
    size = (width + 7) // 8 if type(width) is int and 1 <= width <= 32 else 4
    cases = [('zero', bytes(size))]
    # Enumerations may have no entry for zero. Start with a declared reachable
    # point, test every other reachable point, then values outside the map.
    try:
        signed = _is_signed(layout.get('codedType'))
        _integer_profile(layout.get('codedType'), 0, kernel_sha256, signed=signed)
        kind, profile, _ = _conversion(layout, signed=signed)
        if kind in ('linear-range-float', 'scale-linear-float', 'linear-integer'):
            order = 'big' if layout['codedType']['nativeEncodingFieldsRaw'][2] else 'little'
            mask_bytes = _mask_bytes(layout['codedType'])
            mask = int.from_bytes(mask_bytes, 'big') if mask_bytes is not None else None
            values = (synthetic_integer_range_values(profile['limits'], width, signed=signed, mask=mask)
                      if kind in ('linear-range-float', 'linear-integer') else synthetic_scale_linear_values(
                          profile, width, signed=signed, mask=mask))
            cases = [(name, (value & ((1 << width)-1)).to_bytes(size, order)) for name, value in values]
            cases.append(('truncated', bytes(size-1)))
            return [{'case': name, 'fieldHex': payload.hex(), 'fixtureOrigin': 'synthetic-field-bytes',
                'bytePositionOrigin': 'probe-only-zero', 'actualVehicleResponse': False,
                'result': probe_dop_numeric(layout, payload, 0, kernel_sha256=kernel_sha256)}
                for name, payload in cases]
        if kind == 'texttable-point-enum':
            order = 'big' if layout['codedType']['nativeEncodingFieldsRaw'][2] else 'little'
            mask_bytes = _mask_bytes(layout['codedType'])
            mask = int.from_bytes(mask_bytes, 'big') if mask_bytes is not None else (1 << width)-1
            keys = sorted(k for k in profile['entries'] if k < 1 << width and k & mask == k)
            if not keys:
                raise ValueError('numeric-probe-texttable-no-reachable-point')
            cases = [('enum-point-' + str(k), k.to_bytes(size, order)) for k in keys]
            missing = next((k for k in range(len(keys) + 1)
                            if k not in keys and k < 1 << width and k & mask == k), None)
            if missing is not None:
                cases.append(('enum-unmapped', missing.to_bytes(size, order)))
            cases.append(('enum-maximum', ((1 << width)-1).to_bytes(size, order)))
            cases.append(('truncated', bytes(size-1)))
            return [{'case': name, 'fieldHex': payload.hex(), 'fixtureOrigin': 'synthetic-field-bytes',
                'bytePositionOrigin': 'probe-only-zero', 'actualVehicleResponse': False,
                'result': probe_dop_numeric(layout, payload, 0, kernel_sha256=kernel_sha256)}
                for name, payload in cases]
    except ValueError:
        pass  # Unsupported definitions still yield one explicit blocked case.
    first = probe_dop_numeric(layout, cases[0][1], 0, kernel_sha256=kernel_sha256)
    if first['ok']:
        high_low = layout['codedType']['nativeEncodingFieldsRaw'][2]
        order = 'big' if high_low else 'little'
        if _is_signed(layout['codedType']):
            cases += [('positive-maximum', ((1 << (width-1))-1).to_bytes(size, order)),
                ('negative-minimum', (1 << (width-1)).to_bytes(size, order)),
                ('minus-one', ((1 << width)-1).to_bytes(size, order)),
                ('ascending-bytes', bytes((0x12, 0x34, 0x56, 0x78))[:size]),
                ('truncated', bytes(size-1))]
        else:
            cases += [('one', (1).to_bytes(size, order)), ('maximum', b'\xff'*size),
            ('ascending-bytes', bytes((0x12, 0x34, 0x56, 0x78))[:size]),
            ('high-bit', (1 << (width-1)).to_bytes(size, order)),
            ('truncated', bytes(size-1))]
    return [{'case': name, 'fieldHex': payload.hex(), 'fixtureOrigin': 'synthetic-field-bytes',
        'bytePositionOrigin': 'probe-only-zero', 'actualVehicleResponse': False,
        'result': first if name == 'zero' else probe_dop_numeric(layout, payload, 0, kernel_sha256=kernel_sha256)}
        for name, payload in cases]
