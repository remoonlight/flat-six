"""Pinned fixed byte/text stages, used only by synthetic offline audits.

Fixed ASCII-base ISO88591 decoding promotes each input byte to a Unicode word;
it does not remove zero bytes. NoneEncoding copies bytefields. Both bases skip
the numeric byte-order normalization. Variable lengths and string storage's
runtime state remain unqualified; no transport or native code is invoked.
"""
from .piwis_database import REQUEST_KERNEL_SHA256

SOURCE_EVIDENCE_RVAS = {
    'physicalBaseToValueType': 0x26b0d0, 'physicalTypeDispatch': 0x26b124,
    'codedTypeDispatch': 0x26b0a4, 'asciiCodedUnicodePromotion': 0x25524b,
    'iso88591Decoder': 0x2573b0, 'iso88591BytePromotion': 0x257410,
    'iso88591Vtable': 0x5ffb58, 'unicodeWordAppend': 0x5f0d0,
    'setUnicode2String': 0x88690, 'convertAndCopy': 0x8f3a0,
    'noneEncodingBytefieldCopy': 0x2580aa, 'byteVectorAssignment': 0x89990,
    'identicalConversion': 0x261070, 'identicalValueCopy': 0x261130,
    'changeHighLowBaseDispatch': 0x270791, 'byteTextSkipNormalization': 0x2707ab,
    'zeroTermination': 0x255730, 'decoderVariableLengthBranch': 0x2551f4,
}


def is_fixed_value_family(coded):
    fields = coded.get('nativeEncodingFieldsRaw') if isinstance(coded, dict) else None
    return isinstance(fields, list) and fields[:2] in ([4, 8], [7, 11])


def fixed_value_profile(layout, bit_offset, *, kernel_sha256):
    if kernel_sha256 != REQUEST_KERNEL_SHA256:
        raise ValueError('fixed-value-probe-kernel-profile-mismatch')
    if (not isinstance(layout, dict) or layout.get('recordType') != 0x2c
            or layout.get('fieldLayoutComplete') is not True):
        raise ValueError('fixed-value-probe-dop-layout-incomplete')
    coded = layout.get('codedType') or {}
    if not isinstance(coded, dict) or coded.get('present') is not True or coded.get('recordType') != 0x23:
        raise ValueError('fixed-value-probe-coded-type-missing')
    if type(coded.get('nativeKind')) is not int or coded['nativeKind'] != 2:
        raise ValueError('fixed-value-probe-standard-length-only')
    width = coded.get('bitLength')
    # 4095 is the offline PDU resource guard, not a vendor field-width limit.
    if type(width) is not int or not 8 <= width <= 4095*8 or width % 8:
        raise ValueError('fixed-value-probe-byte-aligned-width-only')
    if type(bit_offset) is not int or bit_offset != 0:
        raise ValueError('fixed-value-probe-zero-bit-position-only')
    fields = coded.get('nativeEncodingFieldsRaw')
    if (not isinstance(fields, list) or len(fields) != 4
            or any(type(x) is not int for x in fields) or fields[:2] not in ([4, 8], [7, 11])):
        raise ValueError('fixed-value-probe-encoding-unqualified')
    if fields[2] not in (0, 1) or fields[3] != 0:
        raise ValueError('fixed-value-probe-order-or-condensed-mask-unqualified')
    if coded.get('bitMaskPresent') is not False or coded.get('bitMaskRawHex') not in (None, ''):
        raise ValueError('fixed-value-probe-mask-unqualified')
    physical_type, prefix = layout.get('physicalType') or {}, layout.get('compuPrefix') or {}
    if not isinstance(physical_type, dict) or not isinstance(prefix, dict):
        raise ValueError('fixed-value-probe-conversion-layout-invalid')
    method = prefix.get('compuMethod') or {}
    if not isinstance(method, dict):
        raise ValueError('fixed-value-probe-conversion-layout-invalid')
    physical, category = physical_type.get('nativeBaseDataType'), method.get('nativeCategory')
    text = fields[:2] == [4, 8]
    if type(physical) is not int or physical != (4 if text else 5) or type(category) is not int or category != 0:
        raise ValueError('fixed-value-probe-identical-conversion-only')
    return width//8, 'identity-iso8859-1' if text else 'identity-bytefield'


def decode_fixed_value(layout, pdu, byte_offset, bit_offset=0, *, kernel_sha256):
    size, kind = fixed_value_profile(layout, bit_offset, kernel_sha256=kernel_sha256)
    if type(pdu) is not bytes or len(pdu) > 4095:
        raise ValueError('fixed-value-probe-pdu-invalid')
    if type(byte_offset) is not int or not 0 <= byte_offset <= 4094:
        raise ValueError('fixed-value-probe-byte-position-invalid')
    if byte_offset + size > len(pdu):
        raise ValueError('fixed-value-probe-truncated-field')
    field = pdu[byte_offset:byte_offset+size]
    raw = field.hex()
    return {'probeKind': kind, 'rawValue': raw,
        'rawValueType': 'bytefield-hex', 'candidateValueType': 'unicode-text' if kind == 'identity-iso8859-1' else 'bytefield-hex',
        'candidateValue': field.decode('latin1') if kind == 'identity-iso8859-1' else raw,
        'fieldRawHex': raw, 'byteOffset': byte_offset, 'bitOffset': bit_offset,
        'bitLength': size*8, 'kernelSha256': kernel_sha256,
        'conversionStage': 'source-pinned-fixed-byte-text',
        'runtimeStringStorageVerified': False}


def synthetic_fixed_value_fields(layout, *, kernel_sha256):
    size, _ = fixed_value_profile(layout, 0, kernel_sha256=kernel_sha256)
    return [('zero', bytes(size)), ('maximum-bytes', b'\xff'*size),
        ('ascending-bytes', bytes(i % 256 for i in range(size))),
        ('embedded-zero-high-bytes', (b'A\x00\x80\xff' * ((size+3)//4))[:size]),
        ('truncated', bytes(size-1))]
