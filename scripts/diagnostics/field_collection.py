"""Prepare a private, hash-checked collection pack. Offline only; no transport.

Request groups are research references, never a session allowlist. One recorded
application response can decode many fields later, saving limited vehicle time.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from .offline_match import FLAGS, sha, verify_source
from .realtime_preparation import DEFAULT_OUTPUT, GGP_PATH, load, record_from_parameter
from .response_values import decode_application_response, request_from_record
from .all_unit_collection import build_all_unit_pack
from scripts.x431_re.gag_lib import GgpLanguage, compact_text

TARGET_STATES = frozenset({'identity-matched', 'selector-candidate', 'system-selector-candidate'})


def request_groups(profile):
    """Group the full static catalog, beyond the desktop's 12-selection budget."""
    groups = {}
    for p in profile['parameters']:
        rec = p.get('record') or record_from_parameter(p)
        request = request_from_record(rec)
        if not request.get('ok') or not p.get('decoderReady'):
            raise ValueError('unresolved-measurement-request:' + p['id'])
        hx = request['request'].hex().upper()
        if hx != p.get('requestHex'):
            raise ValueError('parameter-request-mismatch')
        g = groups.setdefault(hx, {
            'requestHex': hx,
            'expectedPositivePrefixHex': bytes([request['sid'] + 0x40]).hex().upper() + hx[2:],
            'minimumDataBytes': 0, 'spanKind': 'exact', 'parameters': [],
            'historicalParameterCount': 0, 'historicalResponseReferences': [], **FLAGS,
        })
        span = p.get('dataSpan')
        if not span or type(span.get('dataMin')) is not int or span['dataMin'] < 0:
            raise ValueError('unresolved-measurement-span')
        g['minimumDataBytes'] = max(g['minimumDataBytes'], span['dataMin'])
        if span['kind'] != 'exact':
            g['spanKind'] = 'lowerBound'
        historical = bool(p.get('decodedSampleCount'))
        g['historicalParameterCount'] += int(historical)
        g['parameters'].append({
            'id': p['id'], 'name': p['name'], 'unit': p.get('unit'),
            'byteOffset': p.get('byteOffset'), 'bitOffset': p.get('bitOffset'),
            'nameResolved': p.get('nameResolved', False), 'hasHistoricalResponse': historical,
            'sourceOffsets': p.get('sourceOffsets', []), 'categories': p.get('categories', []), **FLAGS,
        })
        for sample in p.get('examples', [])[:1]:
            ref = {k: sample[k] for k in ('groupId', 'responseFrameId', 'pduSha256') if k in sample}
            if ref and ref not in g['historicalResponseReferences']:
                g['historicalResponseReferences'].append(ref)
    for g in groups.values():
        g['parameterCount'] = len(g['parameters'])
        g['representative'] = next((p for p in g['parameters'] if p['nameResolved']), g['parameters'][0])
        g['capturePriority'] = 'missing-response-first' if not g['historicalParameterCount'] else 'repeat-for-timing-and-reference'
        # A lower bound is enough to extract these fields, not a full PDU length claim.
        g['lengthClaim'] = 'minimum-for-selected-fields-only'
    return sorted(groups.values(), key=lambda g: (bool(g['historicalParameterCount']), g['requestHex']))


def decode_recorded_cycle(profile, selected, responses, *, source_kind):
    """Verify simulated/captured reassembled PDUs without sending anything.

    Stop on the first missing/invalid response. Never turn NRC78, a missing
    value, or a short PDU into a zero sample or a successful live result.
    """
    if source_kind not in ('simulation', 'historical-capture'):
        raise ValueError('offline-source-required')
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise ValueError('invalid-selection')
    params = {p['id']: p for p in profile['parameters']}
    if any(k not in params for k in selected):
        raise ValueError('unknown-parameter')
    subset = {**profile, 'parameters': [params[k] for k in selected]}
    groups = request_groups(subset)
    decoded, consumed = [], []
    for group in groups:
        hx = group['requestHex']
        value = responses.get(hx)
        if not isinstance(value, bytes):
            return {'ok': False, 'error': 'missing-response', 'requestHex': hx,
                    'decoded': decoded, 'consumedResponses': consumed, 'sourceKind': source_kind, **FLAGS}
        consumed.append(hx)
        for field in group['parameters']:
            p = params[field['id']]
            result = decode_application_response(p.get('record') or record_from_parameter(p), value, mode='pdu', source=source_kind)
            if not result.get('ok'):
                return {'ok': False, 'error': result.get('reason'), 'requestHex': hx,
                        'parameterId': p['id'], 'decoded': decoded, 'consumedResponses': consumed,
                        'sourceKind': source_kind, **FLAGS}
            decoded.append({'parameterId': p['id'], 'name': p['name'], 'unit': p.get('unit'),
                            'synthetic': source_kind == 'simulation', 'value': result, **FLAGS})
    return {'ok': True, 'decoded': decoded, 'consumedResponses': consumed,
            'sourceKind': source_kind, **FLAGS}


def verified_profiles(bundle):
    root = Path(bundle).resolve()
    manifest_path = root / 'manifest.json'
    manifest = load(manifest_path)
    inputs = [verify_source(manifest_path)]
    # Verify original sources/code and the index before choosing any profile.
    for item in manifest['inputs'] + manifest['code']:
        inputs.append(verify_source(Path(item['path']), item['sha256']))
    index_hash = next(i['sha256'] for i in manifest['outputs'] if Path(i['path']).name == 'index.json')
    inputs.append(verify_source(root / 'index.json', index_hash))
    index = load(root / 'index.json')
    profiles, name_gaps = [], []
    for row in index['profiles']:
        source = (root / row['file']).resolve()
        if not source.is_relative_to(root) or source.suffix != '.json':
            raise ValueError('invalid-profile-path')
        inputs.append(verify_source(source, row['sha256']))
        profile = load(source)
        if profile['profileId'] != row['profileId'] or profile['status'] != row['status']:
            raise ValueError('profile-index-mismatch')
        if any(profile.get(k) != v for k, v in FLAGS.items()):
            raise ValueError('vehicle-authority-in-offline-profile')
        for p in profile['parameters']:
            if not p.get('nameResolved'):
                name_gaps.append({'profileId': profile['profileId'], 'parameterId': p['id'],
                    'requestHex': p['requestHex'], 'originalName': p.get('originalName'),
                    'sourceOffsets': p.get('sourceOffsets'), **FLAGS})
        if row['status'] in TARGET_STATES:
            profiles.append(profile)
    # Retain source gaps as source gaps; do not send the user to another ECU
    # solely to repair a blank static label in an unmatched variant.
    inputs.append(verify_source(GGP_PATH, '7caf1c27e475511e4a75db8fe92008684f5632eeb9fbb4484cbc16fa0eec7de3'))
    language = GgpLanguage(GGP_PATH)
    blank_components = {}
    for gap in name_gaps:
        for token in (gap['originalName'] or '').split('#')[1:]:
            key = int.from_bytes(bytes(int(v, 16) for v in token.split(',')), 'big')
            source = language.lookup_id('DSTREAM_CN.GAG', key)
            if not compact_text(source):
                entry = blank_components.setdefault(f'{key:08X}', {'sourcePresent': source is not None,
                    'sourceTextEmpty': source is not None, 'referenceCount': 0})
                entry['referenceCount'] += 1
    return index, profiles, inputs, {'parameters': name_gaps, 'components': blank_components,
        'collectionPriority': 'source-research-first; capture UI labels only if the actual ECU/version is encountered', **FLAGS}


def build_pack(bundle, output):
    output = Path(output)
    if output.exists():
        raise ValueError('output-already-exists')
    index, profiles, inputs, name_gaps = verified_profiles(bundle)
    units = []
    for p in profiles:
        groups = request_groups(p)
        identity = defaultdict(list)
        for r in p['identity']:
            if r.get('requestHex'):
                identity[r['requestHex']].append({
                    'sourceOffset': r.get('at'), 'byteOffset': r.get('byteOffset'),
                    'bitOffset': r.get('bitOffset'), 'dataSpan': r.get('dataSpan'), **FLAGS})
        units.append({'profileId': p['profileId'], 'name': p['name'], 'module': p['module'],
            'ecuIds': p['ecuIds'], 'qualificationState': p['status'],
            'parameterCount': len(p['parameters']), 'requestGroupCount': len(groups),
            'historicalParameterCount': sum(g['historicalParameterCount'] for g in groups),
            'historicalRequestGroupCount': sum(bool(g['historicalParameterCount']) for g in groups),
            'unresolvedNameCount': sum(not v.get('nameResolved') for v in p['parameters']),
            'identityGroups': [{'requestHex': hx, 'fields': fields, **FLAGS} for hx, fields in identity.items()],
            'groups': groups,
            'captureBatches': [{'batch': offset // 12 + 1,
                'selectionBasis': 'one representative per request; 12 is a UI planning budget, not proven ECU capacity',
                'selections': [{'requestHex': g['requestHex'], 'representative': g['representative'],
                    'fieldsInResponse': g['parameterCount'], 'priority': g['capturePriority'], **FLAGS}
                    for g in groups[offset:offset + 12]], **FLAGS} for offset in range(0, len(groups), 12)],
            'remainingChecklist': p['checklist'], **FLAGS})
    priority = {1: 0, 9: 1, 2: 2, 32: 3, 35: 4, 4: 5, 70: 6}
    units.sort(key=lambda u: min(priority.get(eid, 99) for eid in u['ecuIds']))
    pack = {'schemaVersion': 1, 'scope': 'private-offline-collection-reference',
        'catalogCounts': index['counts'], 'units': units, 'sourceNameGaps': name_gaps,
        'otherMenuUnits': [{'systemId': u['systemId'], 'label': u['label'], 'status': u['status']}
                           for u in index['units'] if not any(u['ecuId'] in p['ecuIds'] for p in profiles)],
        'requiredRawArtifacts': ['continuous btsnoop_hci.log including session entry and exit',
            'full ECU identity screens and raw responses', 'original coding buffers and all pages in order',
            'measurement selection names/units and timestamps', 'vehicle condition and adapter/software version',
            'raw serial trace and result/manifest for independent project runs',
            'clock basis/offset uncertainty, errors, partial results and close state'],
        'procedure': ['Collect identity before measurements; keep one active diagnostic client.',
            'Capture via existing X431 read-only menus; static request groups are not approved sends.',
            'Prefer missing response groups, then repeated stable samples; stop at the time budget.',
            'Preserve raw files and hashes. Decode, compare and investigate after disconnecting.'], **FLAGS}
    output.mkdir(parents=True)
    all_units = build_all_unit_pack(output / 'all-control-units')
    pack['allControlUnits'] = {'register': 'all-control-units/all-units.json', **all_units}
    target = output / 'collection-pack.json'
    target.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 接车采集资料包', '', '生成自本机哈希核对资料；不枚举设备、不打开串口、不访问运行数据库。',
        '请求仅供 X431 已有只读菜单的离线关联；不改变 vLinker 白名单，也不授权设码或清码。', '',
        '完整目标为 981/982 所有控制单元及设码；下面七个重点单元仅为已有匹配资料的优先批次。',
        '全量逐版本身份、实时请求组及原码范围见 [全控制单元采集](all-control-units/全控制单元采集.md)。', '',
        '## 按请求组采集，回家再解码', '']
    for unit in units:
        missing = unit['requestGroupCount'] - unit['historicalRequestGroupCount']
        lines += [f"### {unit['module']} / {unit['name']}",
            f"资格：{unit['qualificationState']}；{unit['parameterCount']} 项 / {unit['requestGroupCount']} 请求组。",
            f"已有响应覆盖 {unit['historicalParameterCount']} 项 / {unit['historicalRequestGroupCount']} 组；尚缺 {missing} 组。",
            '先补完整身份和缺响应组；不要为同一响应内的多个字段重复采集。', '']
        for group in unit['groups']:
            state = '缺响应优先补采' if not group['historicalParameterCount'] else '已有历史响应，补周期/工况对照'
            lines.append(f"- `{group['requestHex']}`：{group['parameterCount']} 项，数据长度下限 {group['minimumDataBytes']} 字节；{state}。")
        lines += ['', '建议选参批次（仅当 X431 实际版本/菜单相符时使用）：']
        for batch in unit['captureBatches']:
            lines.append(f"- 第 {batch['batch']} 批：" + '；'.join(
                f"{s['representative']['name']}（{s['requestHex']}）" for s in batch['selections']))
        lines.append('')
    lines += ['', '完整名称、单位、位置、响应前缀与来源引用见 collection-pack.json。',
              '最低长度是字段解码要求，不代表完整响应长度或本车支持。候选版本仍须现场核实。']
    report = output / 'collection-checklist.md'
    report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    manifest = {'inputs': inputs, 'code': [verify_source(Path(__file__)),
        verify_source(Path(__file__).with_name('response_values.py')),
        verify_source(Path(__file__).with_name('x431_values.py'))],
                'outputs': [verify_source(target), verify_source(report),
                            verify_source(output / 'all-control-units/manifest.json')], **FLAGS}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'ok': True, 'units': len(units), 'output': str(output.resolve()),
            'allControlUnits': all_units, 'collectionPackSha256': sha(target), **FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_pack(args.bundle, args.output_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
