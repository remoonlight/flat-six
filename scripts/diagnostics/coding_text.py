"""Source text drafts. Preserve complete blocks; no vehicle I/O or native writes.

ZERO-terminated text uses literal source encoding and one explicit terminator.
The old native variable inverse mutates its caller header and ORs bytes; that
behavior is not this complete-block draft encoder or a vehicle write recipe.
"""
from .response_values import request_from_record
from .x431_formula import formula_from_record
from .x431_values import decode_record

CODECS = {'ISO-8859-2': 'iso8859-2', 'ISO-8859-1': 'latin-1', 'ASCII': 'ascii'}


def _layout(record, original):
    _, parsed = formula_from_record(record)
    request = request_from_record(record)
    width = parsed.get('bitLength')
    byte = record.get('byteOffset')
    if (not parsed.get('ok') or parsed.get('kind') != 'IDENTICAL'
            or parsed.get('baseDataType') != 'A_ASCIISTRING'
            or parsed.get('encoding') not in CODECS or parsed.get('bitMask') not in (None, 0)
            or type(byte) is not int or byte < 0 or record.get('bitOffset') != 0
            or not request.get('ok') or request.get('sid') not in (0x21, 0x22)):
        raise ValueError('coding-text-definition-unqualified')
    if type(original) is not bytes:
        raise ValueError('coding-text-original-incomplete')
    if width is not None:
        if (parsed.get('highLow') != 1 or type(width) is not int
                or not 8 <= width <= 32760 or width % 8):
            raise ValueError('coding-text-definition-unqualified')
        length = width // 8
        if byte + length > len(original):
            raise ValueError('coding-text-original-incomplete')
        return parsed, byte, length, length
    lo, hi = parsed.get('minLength'), parsed.get('maxLength')
    if (parsed.get('lengthInfo') != 'MinMax' or parsed.get('termination') != 'ZERO'
            or type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= 4095
            or hi < 2 or byte + hi != len(original)):
        raise ValueError('coding-text-variable-complete-tail-required')
    # Full source-qualified storage is fixed; a terminator must fit inside it.
    # Do not resize an owned original or infer a slot from the next field.
    return parsed, byte, lo, hi - 1


def _encode(value, encoding, minimum, maximum):
    if (not isinstance(value, str) or not minimum <= len(value) <= maximum
            or value and not value.isprintable()):
        raise ValueError('coding-text-source-length-required')
    try:
        data = value.encode(CODECS[encoding], errors='strict')
    except UnicodeEncodeError:
        raise ValueError('coding-text-encoding-invalid') from None
    if len(data) != len(value) or data.decode(CODECS[encoding]) != value:
        raise ValueError('coding-text-encoding-invalid')
    return data


def text_domain(record, original):
    try:
        parsed, byte, minimum, maximum = _layout(record, original)
        decoded = decode_record(record, original)
        if not decoded.get('displayName'):
            raise ValueError('coding-text-purpose-unresolved')
        variable = parsed.get('bitLength') is None
        data = original[byte:byte + maximum] if not variable else original[byte:].split(b'\x00', 1)[0]
        current = data.decode(CODECS[parsed['encoding']])
        # Embedded terminators, padding and controls remain in the full original
        # block. Never silently treat a shortened display as an editable target.
        if (current and not current.isprintable() or not minimum <= len(current) <= maximum
                or variable and not decoded.get('ok')):
            current = None
        return dict(ok=True, length=maximum, encoding=parsed['encoding'], currentValue=current,
            **(dict(minimumLength=minimum, termination='ZERO') if variable else {}))
    except (ValueError, UnicodeError) as error:
        return dict(ok=False, reason=str(error))


def preview_text_candidate(record, original, value):
    parsed, byte, minimum, maximum = _layout(record, original)
    domain = text_domain(record, original)
    if not domain['ok']:
        raise ValueError(domain['reason'])
    data = _encode(value, parsed['encoding'], minimum, maximum)
    variable = parsed.get('bitLength') is None
    payload = data + b'\x00' if variable else data
    after = original[:byte] + payload + original[byte + len(payload):]
    decoded = decode_record(record, after)
    if (not decoded.get('ok') or decoded.get('text') != value
            or decoded.get('rawHex') != data.hex().upper()):
        raise ValueError('coding-text-roundtrip-failed')
    return dict(ok=True, afterHex=after.hex().upper(), rawHex=data.hex().upper(),
                current=domain['currentValue'], target=value)
