"""Pinned integer coded limits for offline floating linear candidates only.

The native range helper tests the decoded (and already masked) value before
conversion. Open/closed bounds must have the same UINT32/INT32 value type.
Undefined unlimited bounds retain the older unrestricted candidate behavior.
This does not qualify physical constraints, units or vehicle applicability.
"""

SOURCE_EVIDENCE_RVAS = {
    'linearCodedLowerLimit': 0x261471, 'linearCodedUpperLimit': 0x2614a0,
    'linearRangeGuard': 0x2614ef, 'linearRangeFailure': 0x2614f6,
    'limitRangeAdapter': 0x2683e0, 'limitRangeComparison': 0x2685e0,
    'signedLowerOpen': 0x268d00, 'signedLowerClosed': 0x268d25,
    'signedUpperOpen': 0x26968d, 'signedUpperClosed': 0x2696b2,
    'unsignedLowerOpen': 0x268fd1, 'unsignedLowerClosed': 0x268ff6,
    'unsignedUpperOpen': 0x269962, 'unsignedUpperClosed': 0x269987,
    'rangeBothLimitsRequired': 0x269ab2,
}


def integer_limit_profile(bound, *, signed=False):
    """Return (value or None, inclusive); only serialized same-type bounds."""
    error = 'numeric-probe-scale-limit-unqualified'
    if not isinstance(bound, dict):
        raise ValueError(error)
    interval, value = bound.get('nativeInterval'), bound.get('value')
    if type(interval) is not int or not isinstance(value, dict):
        raise ValueError(error)
    native_type = value.get('nativeType')
    if type(native_type) is not int:
        raise ValueError(error)
    if interval == 0x6d03:
        if (native_type not in (0, 255) or value.get('nativeValueDefined') is not False
                or value.get('rawHex') not in (None, '')):
            raise ValueError(error)
        return None, False
    if (bound.get('recordType') != 0x37 or interval not in (0x6d01, 0x6d02)
            or native_type != (7 if signed else 11)
            or value.get('nativeValueDefined') is not True):
        raise ValueError(error)
    raw = value.get('rawHex')
    if (not isinstance(raw, str) or len(raw) != 8
            or any(c not in '0123456789abcdefABCDEF' for c in raw)):
        raise ValueError(error)
    return int.from_bytes(bytes.fromhex(raw), 'little', signed=signed), interval == 0x6d02


def integer_scale_range(scale, *, signed=False):
    if not isinstance(scale, dict):
        raise ValueError('numeric-probe-scale-limit-unqualified')
    return (integer_limit_profile(scale.get('lowerLimitCoded'), signed=signed),
            integer_limit_profile(scale.get('upperLimitCoded'), signed=signed))


def integer_range_contains(profile, value):
    (lower, lower_closed), (upper, upper_closed) = profile
    return ((lower is None or value > lower or (lower_closed and value == lower))
            and (upper is None or value < upper or (upper_closed and value == upper)))


def synthetic_integer_range_values(profile, width, *, signed=False, mask=None):
    """Reachable interval interior plus integer neighbors of each coded bound.

    Select a masked integer using a bounded digit search, never enumerate the
    full 32-bit domain. Fixture generation is separate from range evaluation.
    """
    minimum = -(1 << (width-1)) if signed else 0
    maximum = (1 << (width-1))-1 if signed else (1 << width)-1
    (lower, lc), (upper, uc) = profile
    lo = max(minimum, lower + (not lc) if lower is not None else minimum)
    hi = min(maximum, upper - (not uc) if upper is not None else maximum)
    mask = ((1 << width)-1) if mask is None else mask & ((1 << width)-1)

    def reachable(a, b):
        # Unsigned bit patterns have a monotone range on each signed half.
        def visit(bit, prefix):
            span = (1 << (bit+1))-1
            if prefix > b or prefix + span < a:
                return None
            if bit < 0:
                return prefix
            result = visit(bit-1, prefix)
            if result is None and mask & (1 << bit):
                result = visit(bit-1, prefix | (1 << bit))
            return result
        return visit(width-1, 0)

    interior = None
    if lo <= hi:
        if signed and lo < 0:
            value = reachable(lo + (1 << width), min(hi, -1) + (1 << width))
            if value is not None:
                interior = value - (1 << width)
        if interior is None and hi >= 0:
            interior = reachable(max(lo, 0), hi)
    cases = [('linear-range-interior', interior)] if interior is not None else []
    cases += [('range-zero', 0), ('range-domain-minimum', minimum), ('range-domain-maximum', maximum)]
    for name, bound in (('lower', lower), ('upper', upper)):
        if bound is not None:
            cases += [('range-' + name + suffix, bound + delta)
                      for suffix, delta in (('-minus-one', -1), ('', 0), ('-plus-one', 1))]
    return [(name, value) for name, value in cases if minimum <= value <= maximum
            and (value & ((1 << width)-1)) & mask == (value & ((1 << width)-1))]
