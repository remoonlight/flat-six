"""Pinned MIN-MAX byte/text candidates; no vehicle execution.

The minimum is checked against
available input; maximum clips extraction rather than rejecting extra bytes.
ASCII-base decoding then truncates its extracted field at the first zero byte.
Termination 1/2 scan for a zero/FF byte within the capped input; a missing marker
at the maximum or input end is allowed by this native extraction branch.
Zero-length fields follow the pinned Unicode/bytefield empty assignment branch;
leading/parameter lengths and runtime cursor consumption remain unqualified.
"""
from .piwis_fixed_values import fixed_value_profile

SOURCE_EVIDENCE_RVAS = {
    'codedLengthResponseDispatch': 0x272579, 'minMaxExtractionCall': 0x27269e,
    'minMaxExtraction': 0x274680, 'minimumByteLengthLoad': 0x2747a8,
    'minimumAvailableInputCheck': 0x2747b8, 'terminationGetter': 0xc0970,
    'terminationZeroBranch': 0x2747dd, 'maximumByteLengthLoad': 0x274aed,
    'maximumCap': 0x274aff, 'variableExtractBits': 0x274a50,
    'variableAsciiZeroScan': 0x25578c, 'variableAsciiZeroResize': 0x25579c,
    'emptyUnicodeAssignment': 0x8be02,
    'emptyCodedFieldBranch': 0x255263, 'emptyValueStringAssignment': 0x255289,
    'emptyBytefieldStringDispatch': 0x8f338, 'emptyBytefieldStringBranch': 0x8e659,
    'emptyBytefieldVectorClear': 0x8e697, 'emptyBytefieldLengthLoop': 0x8e6d4,
    'emptyBytefieldVectorCopy': 0x8e7bc, 'valueStringTypeDispatch': 0x8bdf0,
    'minMaxMarkerBranch': 0x2747f1, 'minMaxMarkerPattern': 0x274804,
    'minMaxMarkerScanWidth': 0x274885, 'minMaxMarkerInputCap': 0x2748d8,
    'minMaxMarkerCompare': 0x274943, 'minMaxMarkerMatch': 0x274964,
    'minMaxMarkerFieldCopy': 0x2749da,
}


def variable_value_profile(layout, bit_offset, *, kernel_sha256):
    if not isinstance(layout, dict) or not isinstance(layout.get('codedType'), dict):
        raise ValueError('variable-value-probe-coded-type-invalid')
    coded = layout['codedType']
    if type(coded.get('nativeKind')) is not int or coded['nativeKind'] != 1:
        raise ValueError('variable-value-probe-min-max-only')
    minimum, maximum = coded.get('minLength'), coded.get('maxLength')
    if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum <= 0xffffffff:
        raise ValueError('variable-value-probe-length-bounds-invalid')
    termination = coded.get('nativeTerminationRaw')
    if type(termination) is not int or termination not in (0, 1, 2):
        raise ValueError('variable-value-probe-termination-unqualified')
    present = coded.get('bitMaskPresent')
    if (present is not None and present is not False) or coded.get('bitMaskRawHex') not in (None, ''):
        raise ValueError('variable-value-probe-mask-unqualified')
    # Reuse only the already pinned codec, physical type and identity checks.
    # Extraction dimensions are validated above, not reinterpreted as fixed.
    normalized = {**layout, 'codedType': {**coded, 'nativeKind': 2, 'bitLength': 8,
        'bitMaskPresent': False, 'bitMaskRawHex': None}}
    _, kind = fixed_value_profile(normalized, bit_offset, kernel_sha256=kernel_sha256)
    return minimum, maximum, kind == 'identity-iso8859-1', termination


def decode_variable_value(layout, pdu, byte_offset, bit_offset=0, *, kernel_sha256):
    minimum, maximum, text, termination = variable_value_profile(layout, bit_offset, kernel_sha256=kernel_sha256)
    if type(pdu) is not bytes or len(pdu) > 4095:
        raise ValueError('variable-value-probe-pdu-invalid')
    if type(byte_offset) is not int or not 0 <= byte_offset <= 4094:
        raise ValueError('variable-value-probe-byte-position-invalid')
    available = len(pdu) - byte_offset
    if available < minimum:
        raise ValueError('variable-value-probe-below-minimum-input')
    field = pdu[byte_offset:byte_offset + min(available, maximum)]
    candidate_input_length = len(field)
    marker, marker_index = None, -1
    if termination:
        marker = 0 if termination == 1 else 255
        marker_index = field.find(bytes((marker,)))
        if marker_index >= 0:
            field = field[:marker_index]
    decoded = field.split(b'\x00', 1)[0] if text else field
    result = {'probeKind': 'variable-iso8859-1' if text else 'variable-bytefield',
        'candidateValue': decoded.decode('latin1') if text else decoded.hex(),
        'candidateValueType': 'unicode-text' if text else 'bytefield-hex',
        'rawValue': decoded.hex(), 'rawValueType': 'bytefield-hex',
        'fieldRawHex': field.hex(), 'decodedFieldRawHex': decoded.hex(),
        'byteOffset': byte_offset, 'bitOffset': bit_offset, 'bitLength': len(field)*8,
        'minimumInputBytes': minimum, 'maximumFieldBytes': maximum,
        'availableInputBytes': available, 'extractedFieldBytes': len(field),
        'nativeTerminationRaw': termination, 'asciiZeroTruncated': len(decoded) != len(field),
        'kernelSha256': kernel_sha256, 'conversionStage': 'source-pinned-min-max-termination-zero' if not termination else 'source-pinned-min-max-byte-marker',
        'runtimeStringStorageVerified': False, 'nativeCursorVerified': False}
    if termination:
        result.update(terminationByte=marker, terminationFound=marker_index >= 0,
                      markerCandidateInputBytes=candidate_input_length)
    return result


def synthetic_variable_value_fields(layout, *, kernel_sha256):
    minimum, maximum, _, termination = variable_value_profile(layout, 0, kernel_sha256=kernel_sha256)
    if minimum > 4095:
        raise ValueError('variable-value-probe-fixture-minimum-resource-guard')
    mixed_size = min(maximum, max(minimum, 16))
    cases = [('zero', bytes(minimum)), ('minimum-high-bytes', b'\xff'*minimum),
        ('representative-bytes', bytes(0x41+i%64 for i in range(min(maximum, max(minimum, 64))))),
        ('embedded-zero-high-bytes', (b'A\x00\x80\xff'*((mixed_size+3)//4))[:mixed_size])]
    if minimum:
        cases.append(('below-minimum', bytes(minimum-1)))
    if maximum < 64:
        cases += [('maximum-field', b'Z'*maximum), ('maximum-plus-one', b'Z'*maximum+b'Q')]
    if termination:
        size = min(maximum, max(minimum, 8))
        marker = bytes((0 if termination == 1 else 255,))
        for position in sorted({0, size//2, max(0, size-1)}):
            if size:
                cases.append(('marker-at-'+str(position), b'A'*position + marker + b'B'*(size-position-1)))
    return cases
