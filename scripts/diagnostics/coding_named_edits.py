"""Build named-choice and source-bounded numeric coding drafts, without writes.

The retained GetTEXTTABLEXX selector uses each source interval's lower endpoint
as its target. Preserve an original value within that interval when reverting to
the current choice. Never expose the other interval values as a raw editor.
"""
from .x431_values import decode_record, formula_from_record, insert_raw
from .response_values import request_from_record, data_span
from .coding_numeric import physical_target, preview_numeric_candidate
from .coding_text import preview_text_candidate
import hashlib
import json
import re


# PIWIS 9x1 2830.1.0, TUER_VORNE.bv.db SHA-256
# 012fb14ab7738b6301b00dae20daa60034df9b1f714cbc51c367b15f73a27375.
# The A2_1 response's 0600 row resolves byte 3 bits 3/7 and byte 7
# bit 0 to DOP 121 (0=aktiv, 1=nicht aktiv); byte 7 bit 4 uses DOP
# 108 (0=Audi, 1=nicht aktiv). Both original texttable strategies and
# the exact nested field positions were checked against the original DLL.
# Only these four complete X431 source records bridge the broken compound
# display. BaseSys and other versions retain their existing qualification.
_PIWIS_DOOR_CHOICES = {
    8312236: ('23edcd162d029baac1bb00248c56464a6f66aa533a1badfc9a767bc319042bef', ('启用', '未启用')),
    8312436: ('90bfdd720783bf527953e9a4782e6f225510863e066dba1c965a449db80bdfce', ('启用', '未启用')),
    8313375: ('83d6d2aa2823ba6956b11fcc8c7128f9dcc642b84200c2eb36cac5b4f0bbb462', ('启用', '未启用')),
    8313575: ('5c91609bd67f3f0afebc09ce393d5bfe5e4ba1e198f77bf393a3734d7f6ca02c', ('Audi', '未启用')),
}


def piwis_door_choice_labels(record):
    """An exact retained record binding, never a translation/prefix fallback."""
    bound = _PIWIS_DOOR_CHOICES.get(record.get('at'))
    if bound is None:
        return None
    fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if fingerprint != bound[0]:
        raise ValueError('coding-factory-choice-source-mismatch')
    return bound[1]


def coding_field_purpose_known(record):
    """Explicit reserved/undefined source labels are not a known user purpose."""
    labels=[record.get('name')]+[r.get('text') for r in record.get('labels',[]) if isinstance(r,dict)]
    reserved=r'(?:预留|保留|备用|未定义|未使用|reserve[ds]?|unused|not\s+used|reserviert|unbenutzt)(?:\s*[-_#]?\s*[0-9]+)?'
    return not any(isinstance(value,str) and re.fullmatch(reserved,value.strip(),re.IGNORECASE) for value in labels)


def _preview_named_target(record, original, raw):
    """Pack an endpoint or original selected by the closed named-choice path.

    The raw-value preview's 256-value limit is not a count of menu options.
    A large source interval still provides one named target: its lower bound.
    This private helper is never an arbitrary-byte renderer or transport API.
    """
    _,parsed=formula_from_record(record)
    if (parsed.get('kind')!='TEXTTABLE' or type(raw) is not int
            or not isinstance(original,(bytes,bytearray))):
        return dict(ok=False,reason='coding-named-target-unqualified')
    before=bytes(original)
    patch=insert_raw(parsed,before,record['byteOffset'],record['bitOffset'],raw)
    if not patch.get('ok'):return patch
    after=patch['after'];decoded=decode_record(record,after)
    if not decoded.get('ok') or decoded.get('raw')!=raw:
        return dict(ok=False,reason=decoded.get('reason') or 'coding-named-target-unrepresentable')
    return dict(ok=True,beforeHex=before.hex().upper(),afterHex=after.hex().upper(),
        changedBitMaskHex=bytes(a^b for a,b in zip(before,after,strict=True)).hex().upper(),
        executionEnabled=False,liveVerified=False,writePayload=None)


def named_coding_choices(record, original):
    if not coding_field_purpose_known(record):raise ValueError('coding-field-purpose-unqualified')
    factory_labels = piwis_door_choice_labels(record)
    _, parsed = formula_from_record(record)
    if (not parsed.get('ok') or parsed.get('kind') != 'TEXTTABLE'
            or (parsed.get('compoundTextMapping') and factory_labels is None) or parsed.get('literalMaps')
            or type(parsed.get('bitLength')) is not int or not 1 <= parsed['bitLength'] <= 32):
        raise ValueError('coding-named-options-unqualified')
    maps = parsed.get('maps') or []
    if not 1 <= len(maps) <= 256:
        raise ValueError('coding-named-options-unqualified')
    current = decode_record(record, original)
    # Some source chains end in an empty field label. The coding reader
    # already names these fields from the preceding nonempty source label.
    # Reuse that name for the draft; leave the original value decoder alone.
    # Explicit names and SubIndexNum placeholders must not acquire a fallback.
    if (isinstance(current.get('displayName'), str) and not current['displayName'].strip()
            and current.get('rawName') is None):
        name = next((text for text in reversed(current.get('labelTexts', [])) if text.strip()), '')
        current = {**current, 'displayName': name}
    if (not current.get('displayName') or not current.get('ok')
            and not (current.get('reason') in ('texttable_unmapped', 'invalid_reserved')
                     and type(current.get('raw')) is int)):
        raise ValueError('coding-current-value-unresolved')
    width = parsed['bitLength']
    current_id = None; choices = []
    for index, (lower, upper, _) in enumerate(maps):
        # Identical option IDs are never synthesized from translated labels.
        # Two source entries with the same label remain distinct source entries.
        if not -(1 << (width - 1)) <= lower <= upper < 1 << width:
            raise ValueError('coding-named-options-unqualified')
        raw = lower % (1 << width)
        preview = _preview_named_target(record, original, raw)
        if not preview.get('ok'):
            continue
        value = decode_record(record, bytes.fromhex(preview['afterHex']))
        if factory_labels is None and (value.get('textStatus') != 'resolved' or not value.get('text')):
            continue
        label = factory_labels[raw] if factory_labels is not None else value['text']
        option_id = f'source-option-{index}'
        if (current_id is None and type(current.get('value')) is int
                and lower <= current['value'] <= upper
                and (factory_labels is not None or current.get('text') == value['text'])):
            current_id = option_id
        choices.append(dict(id=option_id, label=label, rawValue=raw))
    if not choices:
        raise ValueError('coding-named-options-unqualified')
    if factory_labels is not None and current_id is not None:
        label = next(option['label'] for option in choices if option['id'] == current_id)
        current = {**current, 'text': label, 'display': label}
    result=dict(currentOptionId=current_id, current=current, options=choices)
    if sum(upper-lower+1 for lower,upper,_ in maps)>256:
        labels=record.get('labels')
        if isinstance(labels,list) and len(labels)>1:
            parents=[label['text'].strip() for label in labels[:-1]
                if isinstance(label,dict) and isinstance(label.get('text'),str) and label['text'].strip()]
            group=' / '.join(parents)
            if group and len(group)<=4096:result['sourceGroup']=group
    return result


def _request(record):
    request = request_from_record(record)
    if not request.get('ok') or request['sid'] not in (0x21, 0x22):
        raise ValueError('coding-edit-read-binding-unqualified')
    return request['request'].hex().upper()


def _value(decoded):
    return tuple(decoded.get(k) for k in ('raw', 'rawHex', 'value', 'text', 'phys', 'display'))


def _alias(record, selected):
    # Same source formula and exact bit layout, not just the same translation.
    _, a = formula_from_record(record); _, b = formula_from_record(selected)
    return (a == b and record.get('byteOffset') == selected.get('byteOffset')
            and record.get('bitOffset') == selected.get('bitOffset'))


def preview_named_coding_changes(records, originals, edits, guard=lambda: None):
    if not isinstance(edits, list) or any(not isinstance(e,dict) or set(e)!={'fieldId','optionId'} for e in edits):
        raise ValueError('coding-edit-selection-invalid')
    return {**preview_coding_field_changes(records,originals,edits,guard), 'kind':'source-named-coding-draft'}


def preview_coding_field_changes(records, originals, edits, guard=lambda: None):
    """Combine choices against complete caller-owned blocks, preserving all bits.

    Accept source option IDs or an exact physical value with complete declared
    source bounds, never caller-provided raw values/formulas. The owned reader must still qualify source, identity,
    complete scope, fresh originals, backups and the eventual operation client.
    """
    if (not isinstance(records, list) or not 1 <= len(records) <= 10000
            or not isinstance(originals, dict) or not originals
            or any(not isinstance(k, str) or type(v) is not bytes or not 1 <= len(v) <= 4095
                   for k, v in originals.items())
            or not isinstance(edits, list) or not 1 <= len(edits) <= 512):
        raise ValueError('coding-edit-input-invalid')
    by_id = {}; requests = {}
    for record in records:
        guard()
        at = record.get('at')
        if type(at) is not int or at < 0 or str(at) in by_id:
            raise ValueError('coding-edit-source-coverage-invalid')
        field_id = str(at); by_id[field_id] = record
        requests[field_id] = _request(record)
        if requests[field_id] not in originals:
            raise ValueError('coding-edit-original-block-missing')
    after = {k: bytearray(v) for k, v in originals.items()}
    changed = {k: bytearray(len(v)) for k, v in originals.items()}
    selected = {}; changes = []
    for edit in edits:
        guard()
        if (not isinstance(edit, dict) or set(edit) not in ({'fieldId', 'optionId'}, {'fieldId','physicalValue'}, {'fieldId','textValue'})
                or not isinstance(edit['fieldId'], str) or edit['fieldId'] not in by_id
                or edit['fieldId'] in selected
                or not isinstance(edit.get('optionId',edit.get('physicalValue',edit.get('textValue'))),str)):
            raise ValueError('coding-edit-selection-invalid')
        field_id = edit['fieldId']; record = by_id[field_id]; hx = requests[field_id]
        if not coding_field_purpose_known(record):raise ValueError('coding-field-purpose-unqualified')
        original = originals[hx]
        if 'optionId' in edit:
            choices = named_coding_choices(record, original)
            option = next((o for o in choices['options'] if o['id'] == edit['optionId']), None)
            if option is None:
                raise ValueError('coding-edit-option-unqualified')
            raw = choices['current']['raw'] if edit['optionId'] == choices['currentOptionId'] else option['rawValue']
            preview = _preview_named_target(record, original, raw)
            detail = dict(optionId=edit['optionId'],name=choices['current']['displayName'],
                before=choices['current'].get('text') or '未识别的当前值',after=option['label'])
        elif 'textValue' in edit:
            preview = preview_text_candidate(record,original,edit['textValue'])
            raw = preview['rawHex']
            current = decode_record(record,original)
            detail = dict(textValue=edit['textValue'],name=current['displayName'],
                before=preview['current'] if preview['current'] is not None else '未识别的当前值',
                after=edit['textValue'])
        else:
            target_physical = physical_target(edit['physicalValue'])
            preview = preview_numeric_candidate(record,original,target_physical)
            if not preview.get('ok'):
                raise ValueError(preview['reason'])
            raw = preview['rawValue']
            current = decode_record(record,original)
            if (not current.get('displayName') or not current.get('ok')
                    and not (current.get('reason') in ('invalid_reserved','out_of_bounds')
                             and type(current.get('raw')) is int)):
                raise ValueError('coding-current-value-unresolved')
            detail = dict(physicalValue=edit['physicalValue'].strip(),name=current['displayName'],
                before=(current.get('display') or str(current.get('value'))) if current.get('ok') else '未识别的当前值',
                after=edit['physicalValue'].strip(),
                beforePhysical=current.get('phys'),targetPhysical=target_physical,unit=current.get('unit'))
        if not preview.get('ok'):
            raise ValueError('coding-edit-target-unrepresentable')
        target = bytes.fromhex(preview['afterHex'])
        selected[field_id] = dict(raw=raw, rawKey='rawHex' if 'textValue' in edit else 'raw', record=record, request=hx)
        for index, (before_byte, target_byte) in enumerate(zip(original, target, strict=True)):
            mask = before_byte ^ target_byte
            after[hx][index] = (after[hx][index] & ~mask) | (target_byte & mask)
            changed[hx][index] |= mask
        if original != target:
            changes.append(dict(fieldId=field_id,requestHex=hx,**detail))
    # Combining bit changes cannot silently override another selected value.
    for selection in selected.values():
        guard()
        value = decode_record(selection['record'], bytes(after[selection['request']]))
        if not value.get('ok') or value.get(selection['rawKey']) != selection['raw']:
            raise ValueError('coding-edit-target-conflict')
    aliases = []
    for field_id, record in by_id.items():
        guard(); hx = requests[field_id]; before = originals[hx]; target = bytes(after[hx])
        if before == target or field_id in selected:
            continue
        span = data_span(record)
        if not span:
            raise ValueError('coding-edit-overlapping-layout-unresolved')
        offset = record.get('byteOffset', 0)
        end = len(before) if span.get('unknownRemaining') else span['dataMin']
        if before[offset:end] == target[offset:end]:
            continue
        prior, value = decode_record(record, before), decode_record(record, target)
        if (type(prior.get('raw')) is int or isinstance(prior.get('rawHex'), str)) and _value(prior) == _value(value):
            continue
        if not coding_field_purpose_known(record):raise ValueError('coding-edit-reserved-field-changed')
        if not prior.get('ok') or not value.get('ok'):
            raise ValueError('coding-edit-overlapping-value-unresolved')
        _, parsed = formula_from_record(record)
        # Opaque complete-byte projections mirror the block, rather than
        # granting an independent arbitrary-byte edit. Unknown bits outside
        # selected field masks are preserved by construction and checked below.
        aggregate = parsed.get('kind') == 'IDENTICAL' and parsed.get('baseDataType') == 'A_BYTEFIELD'
        mirror = any(s['request'] == hx and _alias(record, s['record']) for s in selected.values())
        if not aggregate and not mirror:
            raise ValueError('coding-edit-unselected-field-changed')
        aliases.append(dict(fieldId=field_id, requestHex=hx, kind='block-projection' if aggregate else 'same-field-alias'))
    blocks = []
    for hx, before in originals.items():
        guard(); target = bytes(after[hx]); mask = bytes(changed[hx])
        if len(before) != len(target) or any((a ^ b) & ~m for a,b,m in zip(before,target,mask,strict=True)):
            raise ValueError('coding-edit-preservation-failed')
        blocks.append(dict(requestHex=hx, beforeHex=before.hex().upper(), afterHex=target.hex().upper(),
            changedBitMaskHex=bytes(a ^ b for a,b in zip(before,target,strict=True)).hex().upper()))
    return dict(kind='source-coding-field-draft', blocks=blocks, changes=changes, aliases=aliases,
        executionEnabled=False, writePayload=None)
