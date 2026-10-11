"""Independent offline checks for bounded source formula families.

Reads literal source text directly (no production formula parser/extractor),
uses individual bits and rational arithmetic, and never opens a transport.
This validates software against the stated definition, not its vehicle meaning.
"""
from fractions import Fraction
import re


def _reference_piwis_mask_bit(coded, bit):
    if coded.get('bitMaskPresent') is False and not coded.get('bitMaskRawHex'):
        return 1
    raw = coded.get('bitMaskRawHex')
    size = (coded['bitLength'] + 7) // 8
    if (coded.get('bitMaskPresent') is not True or not isinstance(raw, str)
            or len(raw) != 2*size or any(c not in '0123456789abcdefABCDEF' for c in raw)
            or coded['nativeEncodingFieldsRaw'][3] != 0):
        raise ValueError('reference-piwis-mask-unsupported')
    index = size - 1 - bit//8
    return (int(raw[2*index:2*index+2], 16) >> (bit % 8)) & 1


def reference_piwis_unsigned(coded, data, offset, bit_offset=0):
    """Independent indexed bit sum for the pinned UINT32/None stage."""
    width = coded['bitLength']
    fields = coded['nativeEncodingFieldsRaw']
    if (type(width) is not int or not 1 <= width <= 32 or type(bit_offset) is not int
            or not 0 <= bit_offset <= 7 or fields[:2] != [1, 11] or fields[2] not in (0, 1)):
        raise ValueError('reference-piwis-uint-profile-unsupported')
    size = (width + bit_offset + 7) // 8
    if offset < 0 or offset + size > len(data):
        raise ValueError('reference-piwis-truncated')
    return sum(((data[offset + (size - 1 - (bit+bit_offset) // 8 if fields[2] else (bit+bit_offset) // 8)]
                 >> ((bit+bit_offset) % 8)) & 1) * _reference_piwis_mask_bit(coded, bit)
               * (2 ** bit) for bit in range(width))


def reference_piwis_signed(coded, data, offset, bit_offset=0):
    """Independent bit weights: the highest selected bit has negative weight."""
    width = coded['bitLength']
    fields = coded['nativeEncodingFieldsRaw']
    if (type(width) is not int or not 1 <= width <= 32 or type(bit_offset) is not int
            or not 0 <= bit_offset <= 7 or fields[:2] != [0, 3] or fields[2] not in (0, 1)):
        raise ValueError('reference-piwis-int-profile-unsupported')
    size = (width + bit_offset + 7) // 8
    if offset < 0 or offset + size > len(data):
        raise ValueError('reference-piwis-truncated')
    return sum(((data[offset + (size - 1 - (bit+bit_offset) // 8 if fields[2] else (bit+bit_offset) // 8)]
                 >> ((bit+bit_offset) % 8)) & 1) * _reference_piwis_mask_bit(coded, bit)
               * (-(2 ** bit) if bit == width-1 else 2 ** bit)
               for bit in range(width))


def reference_piwis_fixed_value(coded, data, offset, bit_offset=0):
    """Independent indexed bytes / Unicode code points; no slice or codec."""
    width, fields = coded.get('bitLength'), coded.get('nativeEncodingFieldsRaw')
    if (coded.get('present') is not True or coded.get('recordType') != 0x23
            or type(coded.get('nativeKind')) is not int or coded['nativeKind'] != 2
            or type(width) is not int or not 8 <= width <= 32760 or width % 8
            or type(bit_offset) is not int or bit_offset != 0
            or not isinstance(fields, list) or len(fields) != 4
            or any(type(x) is not int for x in fields) or fields[:2] not in ([4, 8], [7, 11])
            or fields[2] not in (0, 1) or fields[3] != 0
            or coded.get('bitMaskPresent') is not False or coded.get('bitMaskRawHex') not in (None, '')):
        raise ValueError('reference-piwis-fixed-value-profile-unsupported')
    if type(data) is not bytes or len(data) > 4095 or type(offset) is not int or not 0 <= offset <= 4094:
        raise ValueError('reference-piwis-fixed-value-input-invalid')
    if offset + width//8 > len(data):
        raise ValueError('reference-piwis-truncated')
    return ''.join(chr(data[offset+i]) if fields[0] == 4 else format(data[offset+i], '02x')
                   for i in range(width//8))


def reference_piwis_variable_value(coded, data, offset, bit_offset=0):
    """Independent byte counters for bounded MIN-MAX/marker candidates."""
    minimum, maximum, fields = coded.get('minLength'), coded.get('maxLength'), coded.get('nativeEncodingFieldsRaw')
    if (type(coded.get('nativeKind')) is not int or coded['nativeKind'] != 1
            or coded.get('present') is not True or coded.get('recordType') != 0x23
            or type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum <= 0xffffffff
            or type(coded.get('nativeTerminationRaw')) is not int or coded['nativeTerminationRaw'] not in (0, 1, 2)
            or type(bit_offset) is not int or bit_offset != 0
            or not isinstance(fields, list) or len(fields) != 4 or any(type(x) is not int for x in fields)
            or fields[:2] not in ([4, 8], [7, 11]) or fields[2] not in (0, 1) or fields[3] != 0
            or (coded.get('bitMaskPresent') is not None and coded.get('bitMaskPresent') is not False)
            or coded.get('bitMaskRawHex') not in (None, '')):
        raise ValueError('reference-piwis-variable-value-profile-unsupported')
    if type(data) is not bytes or len(data) > 4095 or type(offset) is not int or not 0 <= offset <= 4094:
        raise ValueError('reference-piwis-variable-value-input-invalid')
    if len(data)-offset < minimum:
        raise ValueError('reference-piwis-below-minimum-input')
    extracted, decoded, stopped, marker_found = [], [], False, False
    termination = coded['nativeTerminationRaw']
    for i in range(len(data)-offset):
        if i == maximum:
            break
        byte = data[offset+i]
        if termination and byte == (0 if termination == 1 else 255):
            marker_found = True
            break
        extracted.append(byte)
        if fields[0] == 4 and byte == 0:
            stopped = True
        if not stopped:
            decoded.append(byte)
    result = {'fieldRawHex': ''.join(format(byte, '02x') for byte in extracted),
        'rawValue': ''.join(format(byte, '02x') for byte in decoded),
        'candidateValue': ''.join(chr(byte) if fields[0] == 4 else format(byte, '02x') for byte in decoded),
        'extractedFieldBytes': len(extracted), 'asciiZeroTruncated': len(decoded) != len(extracted)}
    if termination:
        result.update(terminationByte=0 if termination == 1 else 255, terminationFound=marker_found,
                      markerCandidateInputBytes=min(len(data)-offset, maximum))
    return result


def reference_piwis_integer_scale_contains(scale, value, *, signed=False):
    """Independent serialized bit weights and forbidden-side comparisons."""
    if type(value) is not int:
        raise ValueError('reference-piwis-scale-input-invalid')
    failures = []
    for name, lower in (('lowerLimitCoded', True), ('upperLimitCoded', False)):
        bound = scale.get(name)
        if not isinstance(bound, dict) or not isinstance(bound.get('value'), dict):
            raise ValueError('reference-piwis-scale-limit-unsupported')
        interval, typed = bound.get('nativeInterval'), bound['value']
        native_type = typed.get('nativeType')
        if type(interval) is not int or type(native_type) is not int:
            raise ValueError('reference-piwis-scale-limit-unsupported')
        if interval == 0x6d03:
            if (native_type not in (0, 255) or typed.get('nativeValueDefined') is not False
                    or typed.get('rawHex') not in (None, '')):
                raise ValueError('reference-piwis-scale-limit-unsupported')
            continue
        raw = typed.get('rawHex')
        if (bound.get('recordType') != 0x37 or interval not in (0x6d01, 0x6d02)
                or native_type != (7 if signed else 11) or typed.get('nativeValueDefined') is not True
                or not isinstance(raw, str) or len(raw) != 8
                or any(c not in '0123456789abcdefABCDEF' for c in raw)):
            raise ValueError('reference-piwis-scale-limit-unsupported')
        number = sum(((int(raw[2*(bit//8):2*(bit//8)+2], 16) >> (bit%8)) & 1)
                     * (-2**bit if signed and bit == 31 else 2**bit) for bit in range(32))
        failures.append((value < number if lower else value > number)
                        or (interval == 0x6d01 and value == number))
    return not any(failures)


def reference_piwis_scale_linear(layout, value):
    """Scan original coded bounds; exact coefficient powers / default bits."""
    base = layout['compuPrefix']['compuMethod']['nativeField28']
    signed = layout['codedType']['nativeEncodingFieldsRaw'][:2] == [0, 3]
    matches = [index for index, scale in enumerate(base['scales']['scales'])
               if reference_piwis_integer_scale_contains(scale, value, signed=signed)]
    if matches:
        index = min(matches)
        return {'value': reference_piwis_rational(base['scales']['scales'][index]['nativeField20'], value),
                'selection': {'kind': 'first-matching-coded-range', 'scaleIndex': index}, 'negativeZero': False}
    default = base['default']; kind = default['nativeType']
    if kind in (0, 255):
        raise ValueError('reference-piwis-scale-linear-no-match')
    widths = {4: 4, 5: 8, 6: 2, 7: 4, 10: 2, 11: 4}
    raw = default.get('rawHex')
    if (kind not in widths or default.get('nativeValueDefined') is not True
            or not isinstance(raw, str) or len(raw) != widths[kind]*2
            or any(c not in '0123456789abcdefABCDEF' for c in raw)):
        raise ValueError('reference-piwis-scale-linear-default-unsupported')
    bits = [((int(raw[(b//8)*2:(b//8)*2+2], 16) >> (b%8)) & 1) for b in range(widths[kind]*8)]
    negative_zero = False
    if kind in (4, 5):
        fraction_width, exponent_width = (23, 8) if kind == 4 else (52, 11)
        mantissa = sum(bit * 2**i for i, bit in enumerate(bits[:fraction_width]))
        exponent = sum(bit * 2**i for i, bit in enumerate(bits[fraction_width:-1]))
        if exponent == 2**exponent_width-1:
            raise ValueError('reference-piwis-scale-linear-default-not-finite')
        bias = 2**(exponent_width-1)-1
        number = Fraction(mantissa + (2**fraction_width if exponent else 0)) * Fraction(2)**(
            (exponent-bias if exponent else 1-bias) - fraction_width) * (-1 if bits[-1] else 1)
        negative_zero = bool(bits[-1] and mantissa == 0 and exponent == 0)
    else:
        number = Fraction(sum(bit * (-2**i if kind in (6, 7) and i == len(bits)-1 else 2**i)
                              for i, bit in enumerate(bits)))
    return {'value': number, 'negativeZero': negative_zero,
            'selection': {'kind': 'explicit-forward-default', 'scaleIndex': None, 'defaultNativeType': kind}}


def reference_piwis_integer_rational(coefficients, value, *, unsigned):
    """Read IEEE bits as fractions; evaluate a modular polynomial power sum.

    This intentionally avoids production double decoding and Horner order.
    Coefficient truncation occurs before polynomial evaluation, not afterwards.
    """
    modulus = 2**32

    def integer_coefficient(item, initial):
        raw = item['rawHex']
        if len(raw) != 16 or any(c not in '0123456789abcdefABCDEF' for c in raw):
            raise ValueError('reference-piwis-integer-coefficient-width')
        bit = [((int(raw[(i//8)*2:(i//8)*2+2], 16) >> (i%8)) & 1) for i in range(64)]
        exponent = sum(b * 2**i for i, b in enumerate(bit[52:63]))
        if exponent == 2047:
            raise ValueError('reference-piwis-coefficient-not-finite')
        mantissa = sum(b * 2**i for i, b in enumerate(bit[:52]))
        magnitude = Fraction(mantissa + (2**52 if exponent else 0)) * Fraction(2)**(
            exponent-1075 if exponent else -1074)
        number = -(magnitude.numerator // magnitude.denominator) if bit[63] else magnitude.numerator // magnitude.denominator
        conversion_operand = abs(number) if unsigned and not initial else number
        bits = 64 if unsigned else 32
        if not -2**(bits-1) <= conversion_operand < 2**(bits-1):
            raise ValueError('reference-piwis-integer-truncation-unsupported')
        return number

    def polynomial(name, empty):
        vector = coefficients[name]
        if not isinstance(vector, list) or len(vector) > 255:
            raise ValueError('reference-piwis-coefficient-count')
        number = sum(integer_coefficient(item, i == len(vector)-1) * pow(value, i, modulus)
                     for i, item in enumerate(vector)) % modulus if vector else empty
        return number - modulus if not unsigned and number >= modulus//2 else number

    numerator, denominator = polynomial('numerator', 0), polynomial('denominator', 1)
    if denominator == 0:
        raise ValueError('reference-piwis-zero-denominator')
    if not unsigned and numerator == -2**31 and denominator == -1:
        raise ValueError('reference-piwis-integer-division-overflow')
    rational = Fraction(numerator, denominator)
    quotient = abs(rational.numerator) // rational.denominator
    return -quotient if rational < 0 else quotient


def reference_piwis_rational(coefficients, value):
    """Binary64 coefficients read by bits, polynomial power sum in exact fractions.

    This deliberately differs from production struct decoding and double Horner
    evaluation; callers compare with the appropriate floating rounding tolerance.
    """
    def coefficient(item):
        raw = bytes.fromhex(item['rawHex'])
        if len(raw) != 8:
            raise ValueError('reference-piwis-coefficient-width')
        bits = sum(byte * 2 ** (8 * i) for i, byte in enumerate(raw))
        exponent = (bits >> 52) & 2047
        if exponent == 2047:
            raise ValueError('reference-piwis-coefficient-not-finite')
        mantissa = bits & (2 ** 52 - 1)
        significand = mantissa if exponent == 0 else mantissa + 2 ** 52
        power = -1074 if exponent == 0 else exponent - 1075
        return Fraction(significand) * Fraction(2) ** power * (-1 if bits >> 63 else 1)
    def polynomial(name, empty):
        vector = coefficients[name]
        if not vector:
            return Fraction(empty)
        return sum((coefficient(item) * Fraction(value) ** i for i, item in enumerate(vector)), Fraction(0))
    denominator = polynomial('denominator', 1)
    if denominator == 0:
        raise ValueError('reference-piwis-zero-denominator')
    return polynomial('numerator', 0) / denominator


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
    if kind not in ('IDENTICAL', 'LINEAR', 'TEXTTABLE', 'SCALE-LINEAR', 'TAB-INTP', 'COMPUCODE'):
        raise ValueError('reference-formula-unsupported')
    fields = dict(re.findall(r'(\w+)=([^,;]+)', body))
    width = int(fields.get('BitLength', '0'))
    datatype = fields.get('BaseDataType', fields.get('DataType', ''))
    variable = kind == 'IDENTICAL' and datatype in ('A_ASCIISTRING', 'A_BYTEFIELD') and not width
    if not variable and not 1 <= width <= (32760 if kind == 'IDENTICAL' and datatype in ('A_ASCIISTRING', 'A_BYTEFIELD') else 32):
        raise ValueError('reference-width-unsupported')
    offset, shift = record['byteOffset'], record['bitOffset']
    if type(offset) is not int or type(shift) is not int or not 0 <= offset <= 4094 or not 0 <= shift <= 7:
        raise ValueError('reference-layout-unsupported')
    if offset + (shift + width + 7) // 8 > 4095:
        raise ValueError('reference-payload-limit')
    if variable and (shift or fields.get('Termination') not in ('ZERO', 'END-OF-PDU')
                     or not 0 <= int(fields['MinLength']) <= int(fields['MaxLength']) <= 65535):
        raise ValueError('reference-variable-layout-unsupported')
    return kind, body, fields


def _mapped(body, value):
    for lo, hi, target in re.findall(r'\[([^\],]+)(?:,([^\]]+))?\]->(0x[0-9a-fA-F]+)', body):
        # Endpoint radix is independent of decimal coefficients / length fields.
        def endpoint(text):
            if text.lower().startswith('0x-'):
                return -int(text[3:], 16)
            if text.startswith('-'):
                raise ValueError('reference-map-sign-unsupported')
            return int(text, 16)
        if endpoint(lo) <= value <= endpoint(hi or lo):
            return target[2:].upper()
    return None


def reference_patch(record, data, raw):
    """Independent patch oracle: literal layout, one bit at a time."""
    _, _, fields = _definition(record)
    width, offset, shift = int(fields['BitLength']), record['byteOffset'], record['bitOffset']
    size = (width + shift + 7) // 8
    if type(raw) is not int or not 0 <= raw < (1 << width) or len(data) < offset + size:
        raise ValueError('reference-patch-invalid')
    mask = fields.get('BitMask', '0')
    field_mask = int(mask, 16) if mask.lower() != 'null' else 0
    out = bytearray(data)
    for bit in range(width):
        if field_mask and not (field_mask & (1 << bit)):
            continue
        byte = (shift + bit) // 8
        index = offset + (byte if fields.get('HighLow') == '0' else size - 1 - byte)
        flag = 1 << ((shift + bit) % 8)
        out[index] = (out[index] & ~flag) | (((raw >> bit) & 1) * flag)
    return bytes(out)


def reference_value(record, data):
    """Reference data-field decoder with an intentionally limited contract."""
    kind, body, fields = _definition(record)
    offset, shift = record['byteOffset'], record['bitOffset']
    width = int(fields.get('BitLength', '0'))
    size = (shift + width + 7) // 8
    if len(data) < offset + size:
        return {'ok': False, 'reason': 'truncated'}
    chunk = data[offset:offset + size]
    datatype = fields.get('BaseDataType', fields.get('DataType', ''))
    if kind == 'IDENTICAL' and datatype in ('A_ASCIISTRING', 'A_BYTEFIELD'):
        if shift or width % 8:
            raise ValueError('reference-byte-layout-unsupported')
        if not width:
            chunk = data[offset:]
            maximum = int(fields['MaxLength'])
            if fields['Termination'] == 'ZERO':
                if b'\x00' not in chunk:
                    return {'ok': False, 'reason': 'missing_terminator'}
                length = chunk.index(0)
                if length > maximum:
                    return {'ok': False, 'reason': 'overlong'}
                chunk = chunk[:length]
            else:
                chunk = chunk[:maximum]
            if len(chunk) < int(fields['MinLength']):
                return {'ok': False, 'reason': 'truncated'}
        hx = chunk.hex().upper()
        if datatype == 'A_BYTEFIELD':
            return {'ok': True, 'raw': hx, 'value': hx, 'numeric': False, 'text': None}
        codecs = {'ISO-8859-2': 'iso8859-2', 'ISO-8859-1': 'latin-1', 'ASCII': 'ascii'}
        try:
            value = chunk.split(b'\x00', 1)[0].decode(codecs[fields['Encoding']])
        except UnicodeDecodeError:
            return {'ok': False, 'reason': 'encoding_invalid', 'rawHex': hx}
        return {'ok': True, 'raw': hx, 'value': value, 'text': value, 'numeric': False}
    if datatype not in ('A_UINT32', 'A_INT32', 'A_UINT16', 'A_INT16', 'A_UINT8', 'A_INT8', 'A_FLOAT32') and not (kind == 'TEXTTABLE' and datatype == 'A_BYTEFIELD'):
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
    if datatype == 'A_FLOAT32':
        if width != 32 or kind not in ('IDENTICAL', 'LINEAR'):
            raise ValueError('reference-float-layout-unsupported')
        exponent, mantissa = (raw >> 23) & 255, raw & ((1 << 23) - 1)
        if exponent == 255:
            return {'ok': False, 'reason': 'nan', 'raw': raw}
        significand = mantissa if exponent == 0 else mantissa + (1 << 23)
        power = -149 if exponent == 0 else exponent - 150
        value = Fraction(significand) * (Fraction(2) ** power) * (-1 if raw >> 31 else 1)
        expected = {'ok': True, 'raw': raw, 'value': value, 'numeric': True}
        if kind == 'LINEAR':
            denominator = Fraction(fields['Xb'])
            if denominator == 0:
                return {'ok': False, 'reason': 'zero_denominator'}
            expected['phys'] = (value * Fraction(fields['Xc']) - Fraction(fields['Xa'])) / denominator
            expected['unit'] = record.get('unit')
        return expected
    if encoding not in ('Undefined', '2C', 'BCD-P'):
        raise ValueError('reference-encoding-unsupported')
    signed = datatype.startswith('A_INT') or encoding == '2C' or '0x-' in body
    value = raw - 2 ** width if signed and raw >= 2 ** (width - 1) else raw
    if encoding == 'BCD-P':
        if kind == 'TEXTTABLE':
            raise ValueError('reference-encoding-unsupported')
        digits = [(raw >> (4 * index)) & 15 for index in range((width + 7) // 8 * 2)]
        if any(digit > 9 for digit in digits):
            return {'ok': False, 'reason': 'encoding_bcd_invalid', 'raw': raw}
        value = sum(digit * 10 ** index for index, digit in enumerate(digits))
    regular, invalid = body, ''
    split = re.split(r';[Ii][Cc]:', body, maxsplit=1)
    if len(split) == 2:
        regular, invalid = split
    if _mapped(invalid, value):
        return {'ok': False, 'reason': 'invalid_reserved', 'raw': raw, 'value': value}
    for key, below in (() if kind in ('SCALE-LINEAR', 'TAB-INTP') else (('Lower', True), ('Upper', False))):
        token = fields.get(key)
        if token is not None and token.lower() != 'null':
            boundary = _integer(token)
            if (below and value < boundary) or (not below and value > boundary):
                return {'ok': False, 'reason': 'out_of_bounds', 'raw': raw, 'value': value}
    expected = {'ok': True, 'raw': raw, 'value': value, 'numeric': True}
    if kind == 'COMPUCODE':
        if fields.get('EntryPoint') != 'execute':
            raise ValueError('reference-compucode-entry-unsupported')
        name = fields.get('CodeFile', '')
        low, high = raw % 256, (raw // 256) % 256
        if name == 'Formel54.class':
            expected['value'] = raw % 65536
        elif name in ('Formel21.class', 'Formel119.class'):
            expected.update(value=low * high, phys=Fraction(low * high, 1000 if name == 'Formel21.class' else 100))
        elif name == 'Formel5.class':
            expected.update(value=(low - 100) * high, phys=Fraction((low - 100) * high, 10))
        elif name == 'Formel33.class':
            if high == 0:
                return {'ok': False, 'reason': 'zero_denominator', 'raw': raw}
            expected.update(value=low, phys=Fraction(low * 100, high))
        else:
            raise ValueError('reference-compucode-unsupported')
        if 'phys' in expected: expected['unit'] = record.get('unit')
    elif kind in ('LINEAR', 'SCALE-LINEAR', 'TAB-INTP'):
        coefficients = fields
        if kind != 'LINEAR':
            segments = [dict(re.findall(r'(\w+)=([^,;]+)', section)) for section in regular.split(';')]
            coefficients = next((part for part in segments if 'Lower' in part and 'Upper' in part
                                 and _integer(part['Lower']) <= value <= _integer(part['Upper'])), None)
            if coefficients is None:
                return {'ok': False, 'reason': 'out_of_bounds', 'raw': raw, 'value': value}
        denominator = Fraction(coefficients['Xb'])
        if denominator == 0:
            return {'ok': False, 'reason': 'zero_denominator'}
        expected['phys'] = (Fraction(value) * Fraction(coefficients['Xc']) - Fraction(coefficients['Xa'])) / denominator
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
    kind, body, fields = _definition(record)
    width, shift, offset = int(fields.get('BitLength', '0')), record['bitOffset'], record['byteOffset']
    size = offset + (shift + width + 7) // 8
    datatype = fields.get('BaseDataType', fields.get('DataType', ''))
    if not width:
        minimum, maximum = int(fields['MinLength']), int(fields['MaxLength'])
        lengths = {0, max(0, minimum - 1), minimum, minimum + 1, maximum - 1, maximum, maximum + 1, 4095 - offset}
        patterns = []
        for length in sorted(lengths):
            if not 0 <= length <= 4095 - offset: continue
            payload = b'\xA5' * offset + b'A' * length
            patterns.append(payload)  # Missing terminator or exact END-OF-PDU boundary.
            if fields['Termination'] == 'ZERO' and len(payload) < 4095:
                patterns.extend((payload + b'\x00', payload + b'\x00z' if len(payload) < 4094 else payload + b'\x00'))
        return list(dict.fromkeys(patterns))
    patterns = [bytes([byte]) * size for byte in
                ((0, 0x41, 0x55, 0x7F) if datatype == 'A_ASCIISTRING' and fields.get('Encoding') == 'ASCII'
                 else (0, 0xFF, 0x55, 0xAA))]
    if kind == 'IDENTICAL' and datatype in ('A_ASCIISTRING', 'A_BYTEFIELD'):
        pattern = b'AB\x00z' if fields.get('Encoding') == 'ASCII' else b'A\xA1\x00z'
        patterns.append((pattern * ((size + 3) // 4))[:size])
    else:
        values = {0, 1, (1 << width) - 1, 1 << (width - 1), (1 << (width - 1)) - 1}
        if fields.get('Encoding') == 'BCD-P':
            values.update((0x12, 0x99, 0x1A, 0x1234))
        if datatype == 'A_FLOAT32':
            values.update((0x3FC00000, 0xBFC00000, 0x7F800000, 0x00800000, 0x007FFFFF))
        if kind == 'COMPUCODE':
            values.update((0x0100, 0x0203, 0x026E, 0x020A, 0xFF0064, 0x0101, 0xFF00))
        for lo, hi, _ in re.findall(r'\[([^\],]+)(?:,([^\]]+))?\]->(0x[0-9a-fA-F]+)', body):
            for token in (lo, hi or lo):
                if token.lower().startswith('0x-'):
                    values.add(-int(token[3:], 16))
                elif token.startswith('-'):
                    raise ValueError('reference-map-sign-unsupported')
                else:
                    values.add(int(token, 16))
        for token in re.findall(r'(?:Lower|Upper)=([^,;]+)', body):
            if token != 'null':
                bound = _integer(token); values.update((bound - 1, bound, bound + 1))
        for value in sorted(values):
            payload = bytearray([0xA5] * size)
            for bit in range(width):
                byte_index = (bit + shift) // 8
                index = offset + (byte_index if fields.get('HighLow') == '0' else size - offset - 1 - byte_index)
                bit_mask = 1 << ((bit + shift) % 8)
                payload[index] = (payload[index] & ~bit_mask) | (((value >> bit) & 1) * bit_mask)
            patterns.append(bytes(payload))
    return list(dict.fromkeys(patterns))


def _round_binary_fraction(value, fraction_bits, exponent_bits):
    """IEEE nearest/even reference, using integer division rather than struct casts."""
    if not value:
        return Fraction(0)
    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    exponent = magnitude.numerator.bit_length() - magnitude.denominator.bit_length()
    if magnitude < Fraction(2) ** exponent:
        exponent -= 1
    bias = 2**(exponent_bits-1)-1
    quantum = Fraction(2) ** max(exponent-fraction_bits, 1-bias-fraction_bits)
    scaled = magnitude / quantum
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    if 2*remainder > scaled.denominator or (2*remainder == scaled.denominator and quotient % 2):
        quotient += 1
    rounded = quotient * quantum
    if rounded >= Fraction(2) ** (bias+1):
        raise ValueError('reference-unit-float-overflow')
    return sign * rounded


def reference_piwis_unit_conversion(source_layout, target_layout, value, native_type):
    """Independent explicit-pair math; caller validates identities and dimensions."""
    def coefficient(layout, field):
        bits = int.from_bytes(bytes.fromhex(layout[field]), 'little')
        exponent, mantissa = (bits >> 52) & 2047, bits & (2**52-1)
        if exponent == 2047:
            raise ValueError('reference-unit-coefficient-not-finite')
        magnitude = Fraction(mantissa + (2**52 if exponent else 0)) * Fraction(2)**(
            (exponent-1023 if exponent else -1022)-52)
        return -magnitude if bits >> 63 else magnitude
    source_factor, source_offset = (coefficient(source_layout, f) for f in ('nativeField48RawHex', 'nativeField50RawHex'))
    target_factor, target_offset = (coefficient(target_layout, f) for f in ('nativeField48RawHex', 'nativeField50RawHex'))
    if not source_factor:
        raise ValueError('reference-unit-zero-factor')
    number = Fraction(value)
    if native_type == 4:
        number = _round_binary_fraction(number, 23, 8)
    number = _round_binary_fraction(number-source_offset, 52, 11)
    number = _round_binary_fraction(number/source_factor, 52, 11)
    number = _round_binary_fraction(number*target_factor, 52, 11)
    number = _round_binary_fraction(number+target_offset, 52, 11)
    if native_type in (7, 11):
        integer = (abs(number.numerator)//number.denominator) * (-1 if number < 0 else 1)
        bits = 32 if native_type == 7 else 64
        if not -(2**(bits-1)) <= integer < 2**(bits-1):
            raise ValueError('reference-unit-integer-cast-unqualified')
        return integer if native_type == 7 else integer % 2**32
    return float(_round_binary_fraction(number, 23, 8) if native_type == 4 else number)


def reference_piwis_texttable(layout, value):
    """Independent closed-point reference; scan limits, never use the index map."""
    base = layout['compuPrefix']['compuMethod']['nativeField28']
    matches = []
    for number, row in enumerate(base['scales']['scales']):
        limits = []
        for name in ('lowerLimitCoded', 'upperLimitCoded'):
            bound = row[name]
            if bound['nativeInterval'] != 0x6d02 or bound['value']['nativeType'] != 11:
                raise ValueError('reference-texttable-point-only')
            raw = bound['value']['rawHex']
            if len(raw) != 8:
                raise ValueError('reference-texttable-limit-width')
            limits.append(sum(int(raw[2*i:2*i+2], 16) * 256**i for i in range(4)))
        if limits[0] != limits[1]:
            raise ValueError('reference-texttable-range-unsupported')
        if value == limits[0]:
            matches.append((row['nativeField88']['string']['text'], number))
    if len(matches) > 1:
        raise ValueError('reference-texttable-ambiguous')
    if matches:
        return {'ok': True, 'text': matches[0][0], 'scaleIndex': matches[0][1], 'kind': 'closed-point'}
    default = base['default']
    if default['nativeValueDefined'] and default['nativeType'] == 14:
        return {'ok': True, 'text': default['string']['text'], 'scaleIndex': None, 'kind': 'explicit-forward-default'}
    return {'ok': False, 'reason': 'unmapped'}
