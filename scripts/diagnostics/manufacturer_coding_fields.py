"""Source-bound coding display and drafts; neither grants vehicle write authority."""
from pathlib import Path
from fractions import Fraction
import hashlib
import json
import os

from .offline_match import ANCHORS
from .x431_values import decode_record
from scripts.x431_re.request_fields import coding_profile_variant
from .uds_coding_source import SCOPES, coding_field_read_bindings
from .coding_named_edits import preview_named_coding_changes, preview_coding_field_changes, named_coding_choices, coding_field_purpose_known, piwis_door_choice_labels
from .coding_numeric import numeric_domain, numeric_physical_value
from .coding_text import text_domain


def _coding_definition(source, guard):
    root = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    file = Path(os.environ.get('PORSCHE981_VARIANTS') or root / '.local/x431-re/2026-09-27-981982/expansion/variants.jsonl')
    guard()
    before = file.stat()
    digest = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            guard(); digest.update(chunk)
    if digest.hexdigest() != ANCHORS['variants']:
        raise ValueError('coding-display-source-mismatch')
    variant = None
    with file.open(encoding='utf8') as stream:
        for line in stream:
            guard(); row = json.loads(line)
            if row.get('profile_id') == source['profileId']:
                variant = coding_profile_variant(row); break
    if variant is None:
        raise ValueError('coding-display-source-missing')
    after = file.stat()
    if any(getattr(before, k) != getattr(after, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
        raise ValueError('coding-display-source-changed')
    return variant


def _read_bindings(source):
    scope_row = next((row for row in json.loads(SCOPES.read_text(encoding='utf8'))['profiles']
                      if row['profileId'] == source['profileId']), None)
    bindings = coding_field_read_bindings(source, scope_row) if scope_row else {}
    return scope_row, bindings


def coding_display_fields(source, blocks, guard, *, draft_choices=False):
    variant = _coding_definition(source, guard)
    records = {r['at']: r for r in variant['pool_records']['coding']['records']}
    data = {b['requestHex']: bytes.fromhex(b['dataHex']) for b in blocks}
    fields = []
    scope_row, bindings = _read_bindings(source)
    display_group = scope_row['displayGroup'] if scope_row else {
        'CAN_CAN_Gateway':'网络', 'DME_BDE_Continental':'发动机', 'Getriebesteuerung_PDK':'变速箱'}.get(source.get('module'), '灯光')
    for group in source['coding']['requestGroups']:
        names = {r['at']: r['displayName'] for r in group['namedFields']}
        for field in group['fields']:
            guard(); at = field['sourceOffset']; record = records[at]
            decoded = decode_record(record, data[bindings.get(group['requestHex'], group['requestHex'])])
            name = names.get(at)
            if not isinstance(name, str) or not name.strip():
                raise ValueError('coding-display-name-missing')
            value = next((decoded[k] for k in ('text', 'display', 'value', 'raw')
                          if decoded.get(k) is not None), None) if decoded.get('ok') else None
            factory_labels = piwis_door_choice_labels(record)
            if factory_labels is not None and decoded.get('ok') and type(decoded.get('raw')) is int and decoded['raw'] in (0, 1):
                value = factory_labels[decoded['raw']]
            display = str(value) if value is not None else '未解析'
            # Large opaque byte fields are still preserved in the complete
            # original blocks. A bounded display must not reject that backup.
            raw_hex = decoded.get('rawHex')
            if (len(display) > 4096 and decoded.get('numeric') is False
                    and decoded.get('text') is None and isinstance(raw_hex, str)
                    and value == raw_hex):
                display = f'二进制数据（{len(raw_hex) // 2} 字节）'
            if decoded.get('unit'):
                display += ' ' + decoded['unit']
            shown = dict(id=str(at), name=name, group=display_group, editable=False,
                description='写入及恢复尚未完成。', currentOptionId=None,
                currentDisplay=display, options=[])
            if draft_choices and not coding_field_purpose_known(record):
                shown['description']='预留或未定义字段，仅供核对。'
            elif draft_choices:
                try:
                    choices = named_coding_choices(record,data[bindings.get(group['requestHex'],group['requestHex'])])
                    options=[];positions={}
                    for option in choices['options']:
                        key=(option['rawValue'],option['label'])
                        if key not in positions:
                            positions[key]=len(options);options.append({k:option[k] for k in ('id','label')})
                        elif option['id']==choices['currentOptionId']:
                            options[positions[key]]={k:option[k] for k in ('id','label')}
                    shown.update(editable=True,currentOptionId=choices['currentOptionId'],
                        options=options,
                        description='')
                    if choices.get('sourceGroup'):shown['sourceGroup']=choices['sourceGroup']
                except ValueError:
                    pass  # A readable field does not imply a qualified input.
                if not shown['editable']:
                    domain=numeric_domain(record)
                    if domain.get('ok'):
                        current=str(numeric_physical_value(record,decoded['value'])) if decoded.get('ok') else None
                        envelope=[str(Fraction(int(v['numerator']),int(v['denominator']))) for v in domain['physicalEnvelope']]
                        shown.update(editable=True,description='',numeric=dict(minimum=envelope[0],maximum=envelope[1],
                            currentValue=current,unit=record.get('unit') if isinstance(record.get('unit'),str) else ''))
                if not shown['editable']:
                    domain=text_domain(record,data[bindings.get(group['requestHex'],group['requestHex'])])
                    if domain.get('ok'):
                        shown.update(editable=True,description='',text={k:v for k,v in domain.items() if k!='ok'})
            fields.append(shown)
    if len(fields) != source['coding']['definitionFieldCount'] or len({f['id'] for f in fields}) != len(fields):
        raise ValueError('coding-display-coverage-mismatch')
    return fields


def coding_named_edit_draft(source, blocks, edits, guard):
    return _coding_edit_draft(source,blocks,edits,guard,named_only=True)


def coding_edit_draft(source, blocks, edits, guard):
    return _coding_edit_draft(source,blocks,edits,guard,named_only=False)


def _coding_edit_draft(source, blocks, edits, guard, *, named_only):
    """Internal source-bound draft; owned operation qualification remains separate.

    No caller-supplied record, formula, target bytes or write service is accepted.
    Preserve returned complete blocks and source read aliases when constructing
    the draft. Do not call this result a writable or restored vehicle baseline.
    """
    variant = _coding_definition(source, guard)
    records = variant['pool_records']['coding']['records']
    groups = source['coding']['requestGroups']
    source_offsets = [r['at'] for r in records]
    planned_offsets = [f['sourceOffset'] for g in groups for f in g['fields']]
    if (len(source_offsets) != source['coding']['definitionFieldCount']
            or len(set(source_offsets)) != len(source_offsets)
            or sorted(source_offsets) != sorted(planned_offsets)):
        raise ValueError('coding-edit-source-coverage-invalid')
    scope_row, bindings = _read_bindings(source)
    data = {}
    for block in blocks:
        guard(); hx = block['requestHex']
        if hx in data:
            raise ValueError('coding-edit-original-block-duplicate')
        data[hx] = bytes.fromhex(block['dataHex'])
    mapped_requests = [bindings.get(g['requestHex'],g['requestHex']) for g in groups]
    factory_rows=(scope_row or {}).get('factoryOnlyReadRequests',[])
    if not isinstance(factory_rows,list) or any(not isinstance(r,dict) or not isinstance(r.get('requestHex'),str) for r in factory_rows):
        raise ValueError('coding-edit-factory-scope-invalid')
    factory_requests=[r['requestHex'] for r in factory_rows]
    if (len(set(mapped_requests)) != len(mapped_requests) or len(set(factory_requests))!=len(factory_requests)
            or set(factory_requests)&set(mapped_requests) or set(factory_requests)&{g['requestHex'] for g in groups}
            or set(data) != set(mapped_requests)|set(factory_requests)):
        raise ValueError('coding-edit-original-coverage-invalid')
    originals = {g['requestHex']: data[bindings.get(g['requestHex'],g['requestHex'])] for g in groups}
    # Factory-only groups have no editable X431 fields. Keep every complete
    # original in the draft unchanged; do not discard them or accept extra IDs.
    originals.update({hx:data[hx] for hx in factory_requests})
    build = preview_named_coding_changes if named_only else preview_coding_field_changes
    draft = build(records, originals, edits, guard)
    for block in draft['blocks']:
        logical = block['requestHex']
        block['requestHex'] = bindings.get(logical,logical)
    for item in draft['changes'] + draft['aliases']:
        item['requestHex'] = bindings.get(item['requestHex'],item['requestHex'])
    return {**draft, 'profileId':source['profileId'],
            'sourceDefinitionSha256': source['coding'].get('definitionSha256')}
