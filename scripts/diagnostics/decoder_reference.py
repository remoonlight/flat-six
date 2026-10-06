"""Independent offline checks for the three formula families in the DME profile.

Reads literal source text directly (no production formula parser/extractor),
uses individual bits and rational arithmetic, and never opens a transport.
This validates software against the stated definition, not its vehicle meaning.
"""
from fractions import Fraction
import re


def _integer(token):
    token = token.strip()
    if token.startswith('0x-'):
        return -int(token[3:], 16)
    if token.lower().startswith('0x') or re.search('[a-fA-F]', token):
        return int(token, 16)
    return int(token, 10)


def _definition(record):
    formula = record['formula']
    text = formula['text'] if isinstance(formula, dict) else formula
    kind, body = text.split(':', 1)
    if kind not in ('IDENTICAL', 'LINEAR', 'TEXTTABLE'):
        raise ValueError('reference-formula-unsupported')
    fields = dict(re.findall(r'(\w+)=([^,;]+)', body))
    return kind, body, fields


def _mapped(body, value):
    for lo, hi, target in re.findall(r'\[([^\],]+)(?:,([^\]]+))?\]->(0x[0-9a-fA-F]+)', body):
        if _integer(lo) <= value <= _integer(hi or lo):
            return target[2:].upper()
    return None


def reference_value(record, data):
    """Reference data-field decoder with an intentionally limited contract."""
    kind, body, fields = _definition(record)
    offset, shift = record['byteOffset'], record['bitOffset']
    width = int(fields['BitLength'])
    size = (shift + width + 7) // 8
    if len(data) < offset + size:
        return {'ok': False, 'reason': 'truncated'}
    chunk = data[offset:offset + size]
    datatype = fields.get('BaseDataType', fields.get('DataType', ''))
    if kind == 'IDENTICAL' and datatype in ('A_ASCIISTRING', 'A_BYTEFIELD'):
        if shift or width % 8:
            raise ValueError('reference-byte-layout-unsupported')
        hx = chunk.hex().upper()
        if datatype == 'A_BYTEFIELD':
            return {'ok': True, 'raw': hx, 'value': hx, 'numeric': False, 'text': None}
        codecs = {'ISO-8859-2': 'iso8859-2', 'ISO-8859-1': 'latin-1', 'ASCII': 'ascii'}
        value = chunk.split(b'\x00', 1)[0].decode(codecs[fields['Encoding']])
        return {'ok': True, 'raw': hx, 'value': value, 'text': value, 'numeric': False}
    if datatype not in ('A_UINT32', 'A_INT32', 'A_UINT16', 'A_INT16', 'A_UINT8', 'A_INT8'):
        raise ValueError('reference-datatype-unsupported')
    if len(chunk) > 1 and fields.get('HighLow') not in ('0', '1'):
        raise ValueError('reference-endian-unsupported')
    # Index each bit from the least significant end of the declared byte order.
    ordered = chunk if fields.get('HighLow') == '0' else chunk[::-1]
    raw = sum(((ordered[(shift + bit) // 8] // (2 ** ((shift + bit) % 8))) % 2) * (2 ** bit)
              for bit in range(width))
    mask = fields.get('BitMask', '0')
    if mask.lower() != 'null' and int(mask, 16):
        raw &= int(mask, 16)
    encoding = fields.get('Encoding', 'Undefined')
    if encoding not in ('Undefined', '2C'):
        raise ValueError('reference-encoding-unsupported')
    signed = datatype.startswith('A_INT') or encoding == '2C' or '0x-' in body
    value = raw - 2 ** width if signed and raw >= 2 ** (width - 1) else raw
    regular, invalid = body, ''
    split = re.split(r';[Ii][Cc]:', body, maxsplit=1)
    if len(split) == 2:
        regular, invalid = split
    if _mapped(invalid, value):
        return {'ok': False, 'reason': 'invalid_reserved', 'raw': raw, 'value': value}
    for key, below in (('Lower', True), ('Upper', False)):
        token = fields.get(key)
        if token is not None and token.lower() != 'null':
            boundary = _integer(token)
            if (below and value < boundary) or (not below and value > boundary):
                return {'ok': False, 'reason': 'out_of_bounds', 'raw': raw, 'value': value}
    expected = {'ok': True, 'raw': raw, 'value': value, 'numeric': True}
    if kind == 'LINEAR':
        denominator = Fraction(fields['Xb'])
        if denominator == 0:
            return {'ok': False, 'reason': 'zero_denominator'}
        expected['phys'] = (Fraction(value) * Fraction(fields['Xc']) - Fraction(fields['Xa'])) / denominator
        expected['unit'] = record.get('unit')
    elif kind == 'TEXTTABLE':
        target = _mapped(regular, value)
        if target is None:
            return {'ok': False, 'reason': 'texttable_unmapped', 'raw': raw, 'value': value}
        table = record.get('enumText') or {}
        text = next((table[key] for key in (target, target.upper(), target.lower(), '0x' + target, '0X' + target)
                     if key in table), None)
        expected.update(textId=target, text=text, numeric=False)
    return expected


def compare_reference(expected, actual):
    """Return mismatched keys; tolerate only numerical decimal roundoff."""
    failures = []
    for key, value in expected.items():
        observed = actual.get(key)
        if key == 'phys':
            try:
                difference = abs(Fraction(str(observed)) - value)
                if difference > max(Fraction(1, 10 ** 12), abs(value) / 10 ** 12):
                    failures.append(key)
            except (ValueError, TypeError, ZeroDivisionError):
                failures.append(key)
        elif observed != value:
            failures.append(key)
    return failures


def reference_cases(record):
    """Deterministic boundary/pattern payloads; never reuse a saved sample."""
    _, body, fields = _definition(record)
    width, shift, offset = int(fields['BitLength']), record['bitOffset'], record['byteOffset']
    size = offset + (shift + width + 7) // 8
    datatype = fields.get('BaseDataType', fields.get('DataType', ''))
    patterns = [bytes([byte]) * size for byte in (0, 0xFF, 0x55, 0xAA)]
    if datatype in ('A_ASCIISTRING', 'A_BYTEFIELD'):
        patterns.append((b'A\xA1\x00z' * ((size + 3) // 4))[:size])
    else:
        values = {0, 1, (1 << width) - 1, 1 << (width - 1), (1 << (width - 1)) - 1}
        for lo, hi, _ in re.findall(r'\[([^\],]+)(?:,([^\]]+))?\]->(0x[0-9a-fA-F]+)', body):
            values.update((_integer(lo), _integer(hi or lo)))
        for key in ('Lower', 'Upper'):
            if fields.get(key) not in (None, 'null'):
                bound = _integer(fields[key]); values.update((bound - 1, bound, bound + 1))
        for value in sorted(values):
            payload = bytearray([0xA5] * size)
            for bit in range(width):
                byte_index = (bit + shift) // 8
                index = offset + (byte_index if fields.get('HighLow') == '0' else size - offset - 1 - byte_index)
                bit_mask = 1 << ((bit + shift) % 8)
                payload[index] = (payload[index] & ~bit_mask) | (((value >> bit) & 1) * bit_mask)
            patterns.append(bytes(payload))
    return list(dict.fromkeys(patterns))
