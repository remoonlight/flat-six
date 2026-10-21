"""Pinned 32-bit integer rational calculation for offline PIWIS candidates.

Both base types UINT32 select unsigned arithmetic; the other integer pairs
select signed arithmetic, including a bit-preserving UINT32 -> INT32 input
copy. Coefficients truncate separately and each multiply/add wraps at 32 bits.
CPU-invalid coefficient conversions and signed division overflow stay blocked:
the runtime floating exception environment has not been qualified.
"""
import math
import struct

SOURCE_EVIDENCE_RVAS = {
    'integerCalculation': 0x260850, 'integerInitialNumerator': 0x26088e,
    'integerPhysicalBase': 0x260935, 'integerCodedBase': 0x260984,
    'integerFloatTargetRoute': 0x2609c3, 'integerFloatSourceRoute': 0x2609cf,
    'integerUnsignedTargetGuard': 0x2609d5, 'integerUnsignedSourceGuard': 0x2609df,
    'unsignedInputType': 0x260a44, 'unsignedInputCopy': 0x260a6b,
    'unsignedInputRead': 0x260a73, 'unsignedInitialTruncation': 0x260aa4,
    'unsignedNumeratorMultiply': 0x260abd, 'unsignedNegativeCoefficient': 0x260ac6,
    'unsignedNumeratorSubtract': 0x260acf, 'unsignedNumeratorAdd': 0x260ada,
    'unsignedDenominatorInitial': 0x260aff, 'unsignedDenominatorMultiply': 0x260b1d,
    'unsignedDenominatorSubtract': 0x260b30, 'unsignedDenominatorAdd': 0x260b3c,
    'unsignedZeroDenominator': 0x260b44, 'unsignedEmptyDenominator': 0x260b4f,
    'unsignedDivide': 0x260b59, 'unsignedResultType': 0x260b84,
    'signedInputType': 0x260bf6, 'signedInputCopy': 0x260c1d,
    'signedInputRead': 0x260c25, 'signedInitialTruncation': 0x260c4b,
    'signedNumeratorMultiply': 0x260c5e, 'signedNumeratorTruncation': 0x260c62,
    'signedNumeratorAdd': 0x260c66, 'signedDenominatorInitial': 0x260c8b,
    'signedDenominatorMultiply': 0x260c9f, 'signedDenominatorTruncation': 0x260ca3,
    'signedDenominatorAdd': 0x260ca7, 'signedZeroDenominator': 0x260caf,
    'signedEmptyDenominator': 0x260cba, 'signedDivide': 0x260cc3,
    'signedResultType': 0x260cf4, 'integerSignedInputAdapter': 0x8f6d0,
    'uint32ToSignedInputRead': 0x95eef, 'int32ToSignedInputRead': 0x95f27,
    'integerSignMultiplier': 0x6527d0,
}


def integer_rational(coefficients, value, *, unsigned):
    """Return the native integer result; no floating calculation then cast."""
    if type(unsigned) is not bool or type(value) is not int:
        raise ValueError('numeric-probe-integer-input-invalid')
    if not -(1 << 31) <= value <= (1 << 32)-1:
        raise ValueError('numeric-probe-integer-input-invalid')
    if not isinstance(coefficients, dict) or coefficients.get('recordType') != 0x14:
        raise ValueError('numeric-probe-coded-to-physical-coefficients-missing')

    def wrap(number):
        number &= 0xffffffff
        return number if unsigned or number < 0x80000000 else number - 0x100000000

    value = wrap(value)

    def truncate(item, *, initial):
        raw = item.get('rawHex') if isinstance(item, dict) else None
        if (not isinstance(raw, str) or len(raw) != 16
                or any(c not in '0123456789abcdefABCDEF' for c in raw)):
            raise ValueError('numeric-probe-integer-coefficient-bytes-invalid')
        number = struct.unpack('<d', bytes.fromhex(raw))[0]
        if not math.isfinite(number):
            raise ValueError('numeric-probe-coefficient-not-finite')
        # Unsigned loops multiply negative coefficients by the pinned -1.0,
        # truncate their magnitude to signed 64 bits, then subtract low 32 bits.
        converted = abs(number) if unsigned and not initial else number
        integer = math.trunc(converted)
        bits = 64 if unsigned else 32
        if not -(1 << (bits-1)) <= integer < (1 << (bits-1)):
            raise ValueError('numeric-probe-integer-coefficient-truncation-unqualified')
        return -integer if unsigned and not initial and number < 0 else integer

    def evaluate(name, empty):
        items = coefficients.get(name)
        if not isinstance(items, list) or len(items) > 255:
            raise ValueError('numeric-probe-coefficient-count-invalid')
        if not items:
            return empty
        result = wrap(truncate(items[-1], initial=True))
        for item in reversed(items[:-1]):
            result = wrap(wrap(result * value) + truncate(item, initial=False))
        return result

    numerator, denominator = evaluate('numerator', 0), evaluate('denominator', 1)
    if denominator == 0:
        raise ValueError('numeric-probe-zero-denominator')
    if unsigned:
        return numerator // denominator
    if numerator == -(1 << 31) and denominator == -1:
        raise ValueError('numeric-probe-integer-division-overflow')
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient
