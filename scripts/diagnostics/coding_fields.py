"""Named, bounded coding choices and collateral changes. No transport or writes."""
from .x431_values import formula_from_record, preview_coding, decode_record
from .response_values import request_from_record, data_span
from .coding_numeric import numeric_domain

OPTION_LIMIT = 256


def option_raw_values(parsed):
    if parsed.get('compoundTextMapping'):
        raise ValueError('coding_compound_enum_meaning_unqualified')
    if not parsed.get('ok') or parsed.get('kind') != 'TEXTTABLE' or parsed.get('literalMaps'):
        raise ValueError('coding_options_not_defined')
    width = parsed.get('bitLength')
    if type(width) is not int or not 1 <= width <= 32:
        raise ValueError('coding_options_not_defined')
    values = set()
    for lo, hi, _ in parsed.get('maps') or []:
        if hi - lo + 1 > OPTION_LIMIT:
            raise ValueError('coding_options_limit')
        for value in range(lo, hi + 1):
            # Source maps compare signed values; the editor patches unsigned bits.
            raw = value % (1 << width)
            if not -(1 << (width - 1)) <= value < (1 << width):
                raise ValueError('coding_options_not_defined')
            values.add(raw)
            if len(values) > OPTION_LIMIT:
                raise ValueError('coding_options_limit')
    if not values:
        raise ValueError('coding_options_not_defined')
    return sorted(values)


def coding_options(record, data):
    _, parsed = formula_from_record(record)
    options = []
    for raw in option_raw_values(parsed):
        preview = preview_coding(record, data, raw)
        if not preview.get('ok'):
            continue
        decoded = decode_record(record, bytes.fromhex(preview['afterHex']))
        if decoded.get('displayName') and decoded.get('textStatus') == 'resolved' and decoded.get('text'):
            options.append({'rawValue': raw, 'label': decoded['text']})
    return options


def coding_field_readiness(record):
    # These describe offline previews, not an enabled vehicle editor. The native
    # property byte and actual input/write workflow are still unqualified.
    status = {'namedOptionCount': 0, 'previewKind': None, 'offlinePreviewReady': False,
              'nativeEditingBehaviorVerified': False, 'propertyMeaningQualified': False,
              'codingWriteQualified': False, 'editorEnabled': False}
    request, span = request_from_record(record), data_span(record)
    if not request.get('ok') or request.get('sid') not in (0x21, 0x22):
        return {**status, 'editorBlocker': 'coding-read-request-unresolved'}
    _, parsed = formula_from_record(record)
    if parsed.get('kind') == 'LINEAR':
        domain = numeric_domain(record)
        return {**status, 'previewKind': 'source-bounded-linear',
                'offlinePreviewReady': domain['offlinePreviewReady'],
                'editorBlocker': None if domain.get('ok') else domain['reason']}
    if not span:
        return {**status, 'editorBlocker': 'coding-field-span-unresolved'}
    try:
        # Padding is synthetic, not an original vehicle value or complete block.
        options = coding_options(record, bytes(span['dataMin']))
        return {**status, 'previewKind': 'named-options', 'namedOptionCount': len(options),
                'offlinePreviewReady': bool(options),
                'editorBlocker': None if options else 'coding-options-label-or-value-unresolved'}
    except ValueError as error:
        return {**status, 'editorBlocker': str(error)}


def coding_effects(records, selected, before, after):
    """Show aliases/overlaps; reject a change touching an undecodable definition."""
    request = request_from_record(selected)
    effects = []
    for record in records:
        other = request_from_record(record)
        if not other.get('ok') or other['request'] != request.get('request'):
            continue
        span = data_span(record)
        # A missing layout in the same block cannot establish preservation.
        if not span:
            if before != after:
                raise ValueError('coding-overlapping-layout-unresolved')
            continue
        offset = record.get('byteOffset', 0)
        end = len(before) if span.get('unknownRemaining') else span['dataMin']
        if before[offset:end] == after[offset:end]:
            continue
        prior, target = decode_record(record, before), decode_record(record, after)
        if not prior.get('ok') or not target.get('ok'):
            raise ValueError('coding-overlapping-value-unresolved')
        if (prior.get('raw'), prior.get('value'), prior.get('text')) == (target.get('raw'), target.get('value'), target.get('text')):
            continue
        if not prior.get('displayName') or not target.get('displayName'):
            raise ValueError('coding-overlapping-field-unresolved')
        if target.get('formulaKind') == 'TEXTTABLE' and target.get('textStatus') != 'resolved':
            raise ValueError('coding-overlapping-value-unresolved')
        effects.append({'sourceOffset': record.get('at'), 'selected': record.get('at') == selected.get('at'),
            'name': target.get('displayName') or target.get('name') or str(record.get('at')),
            'before': {key: prior.get(key) for key in ('raw', 'value', 'text', 'phys', 'display', 'unit')},
            'after': {key: target.get(key) for key in ('raw', 'value', 'text', 'phys', 'display', 'unit')}})
        if len(effects) > 512:
            raise ValueError('coding-effects-limit')
    return effects
