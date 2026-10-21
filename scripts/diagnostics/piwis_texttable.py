"""Retained TexttableConversion's closed UINT32 point → Unicode branch.

Data only. Require an exact, complete coded-to-physical index, an explicit
forward base and closed point limits. Opposite-direction fallback, ranges,
bytefields and runtime constraint/reference qualification remain unsupported.
"""

SOURCE_EVIDENCE_RVAS = {
    'texttableConversion': 0x2652c0, 'texttableVtable': 0x601720,
    'texttableDirectionSelection': 0x265470, 'texttableCodedIndexSelection': 0x26562e,
    'texttableIndexCopy': 0xd7c60, 'texttableIndexNodeCopy': 0xb12e0,
    'texttableUint32IndexSearch': 0x2656ae, 'texttableIndexScaleSelection': 0x2656f2,
    'texttableCodedLimitSelection': 0x265788, 'texttablePhysicalConstantSelection': 0x2657cc,
    'texttableClosedPointSelection': 0x2657eb, 'texttableDefaultSelection': 0x265cfb,
    'texttablePhysicalConstantGetter': 0xbdfa0, 'texttableScaleIndexGetter': 0xbf160,
    'texttableScaleCollectionVtable': 0x5bc7c0,
    'physicalDestinationTypeDispatch': 0x2706b0,
    'physicalValueConversion': 0x254e20, 'valueTypeMap': 0x26b040,
}


def _unsigned(value):
    if (not isinstance(value, dict) or value.get('nativeType') != 11
            or value.get('nativeValueDefined') is not True):
        raise ValueError('numeric-probe-texttable-uint32-limit-only')
    raw = value.get('rawHex')
    if (not isinstance(raw, str) or len(raw) != 8
            or any(c not in '0123456789abcdefABCDEF' for c in raw)):
        raise ValueError('numeric-probe-texttable-limit-bytes-invalid')
    try:
        return int.from_bytes(bytes.fromhex(raw), 'little')
    except ValueError as exc:
        raise ValueError('numeric-probe-texttable-limit-bytes-invalid') from exc


def _text(value):
    if (not isinstance(value, dict) or value.get('nativeType') != 14
            or value.get('nativeValueDefined') is not True):
        raise ValueError('numeric-probe-texttable-unicode-output-only')
    string = value.get('string') or {}
    if (not isinstance(string, dict) or string.get('pool') != 'UStringData'
            or string.get('resolved') is not True or string.get('nullReference') is not False
            or not isinstance(string.get('text'), str)):
        raise ValueError('numeric-probe-texttable-string-unresolved')
    return string


def texttable_profile(layout):
    """Validate the whole supported branch before looking up a particular value."""
    method = (layout.get('compuPrefix') or {}).get('compuMethod') or {}
    if method.get('nativeCategory') != 3 or (layout.get('physicalType') or {}).get('nativeBaseDataType') != 4:
        raise ValueError('numeric-probe-texttable-conversion-type-unqualified')
    base = method.get('nativeField28')
    if not isinstance(base, dict) or base.get('recordType') != 5:
        raise ValueError('numeric-probe-texttable-forward-base-required')
    scales = base.get('scales') or {}
    rows = scales.get('scales')
    count = scales.get('declaredScales')
    if (type(count) is not int or not 1 <= count <= 4096
            or not isinstance(rows, list) or len(rows) != count):
        raise ValueError('numeric-probe-texttable-scales-invalid')
    entries = {}
    for number, row in enumerate(rows):
        if not isinstance(row, dict) or row.get('recordType') != 0x19:
            raise ValueError('numeric-probe-texttable-scale-missing')
        limits = []
        for field in ('lowerLimitCoded', 'upperLimitCoded'):
            limit = row.get(field)
            if (not isinstance(limit, dict) or limit.get('recordType') != 0x37
                    or limit.get('nativeInterval') != 0x6d02):
                raise ValueError('numeric-probe-texttable-closed-points-only')
            limits.append(_unsigned(limit.get('value')))
        if limits[0] != limits[1] or limits[0] in entries:
            raise ValueError('numeric-probe-texttable-point-ambiguous')
        entries[limits[0]] = {'scaleIndex': number, 'string': _text(row.get('nativeField88'))}
    pairs = layout.get('codedToPhysicalIndexMap')
    if not isinstance(pairs, list) or len(pairs) != count:
        raise ValueError('numeric-probe-texttable-complete-index-required')
    observed = {}
    for pair in pairs:
        if (not isinstance(pair, dict) or type(pair.get('key')) is not int
                or type(pair.get('value')) is not int or pair['key'] in observed
                or not 0 <= pair['key'] <= 0xffffffff or not 0 <= pair['value'] < count):
            raise ValueError('numeric-probe-texttable-index-invalid')
        observed[pair['key']] = pair['value']
    if observed != {key: entry['scaleIndex'] for key, entry in entries.items()}:
        raise ValueError('numeric-probe-texttable-index-point-mismatch')
    default = base.get('default')
    if not isinstance(default, dict):
        raise ValueError('numeric-probe-texttable-default-invalid')
    if default.get('nativeValueDefined') is False and default.get('nativeType') in (0, 255):
        default = None
    else:
        default = _text(default)
    return {'entries': entries, 'default': default}


def texttable_value(profile, value):
    entry = profile['entries'].get(value)
    if entry is not None:
        string = entry['string']
        selection = {'kind': 'closed-point', 'scaleIndex': entry['scaleIndex']}
    elif profile['default'] is not None:
        string = profile['default']
        selection = {'kind': 'explicit-forward-default', 'scaleIndex': None}
    else:
        raise ValueError('numeric-probe-texttable-value-unmapped')
    return string['text'], {**selection, 'string': string}
