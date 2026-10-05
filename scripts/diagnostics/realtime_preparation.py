"""Compile/query local realtime definitions and historic responses. No device I/O.

Input is the hash-checked offline matcher bundle, never renderer supplied paths.
Generated variant files are private local artifacts, not application seed data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct

from .offline_match import ROOT, DEFAULT_VARIANTS, SOURCE_PATH, ADDRESS_PATH, FLAGS, sha, dump, verify_source, explicit_address_pairs
from .x431_capture import ADDRESSES
from .response_values import decode_application_response, data_span, request_from_record
from .x431_values import classify_decode
from .x431_formula import formula_from_record
from scripts.x431_re.request_fields import parse_identity_suffix7, derived_read_request
from scripts.x431_re.gag_lib import GgpLanguage, compact_text

DEFAULT_MATCH = ROOT / '.local/vehicle-analysis/981-offline-match-20261003-all-units'
DEFAULT_OUTPUT = ROOT / '.local/diagnostics/realtime-preparation'
READY_ACTIONS = frozenset({'ready-units', 'ready-parameters', 'ready-plan', 'ready-replay', 'ready-acquire',
    'catalog-units', 'catalog-parameters', 'catalog-plan', 'catalog-replay', 'catalog-coding-plan'})
SELECTION_LIMIT = 12  # User-selected provisional display/acquisition limit; X431 capacity unverified.
POINT_LIMIT = 240
GGP_PATH = Path(os.environ.get('PORSCHE981_LANGUAGE') or ROOT / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP')
NODE_ECU = {'gateway': 9, 'dme': 1, 'pdk': 2, 'selector': 68, 'airbag': 4,
    'psm': 5, 'pasm': 19, 'eps': 69, 'epb': 50, 'bcm-front': 32, 'bcm-rear': 35,
    'steering-column': 54, 'door-driver': 30, 'door-passenger': 31, 'seat-driver': 76,
    'seat-passenger': 78, 'roof': 39, 'rdk': 55, 'hvac': 7, 'pcm': 70, 'cluster': 8,
    'clock': 29, 'parking': 18, 'headlamp-left': 47, 'headlamp-right': 48,
    'shaker': 87, 'reverse-camera': 81, 'headlamp-sg': 88, 'acc': 64,
    'front-camera': 74, 'swa-left': 96, 'swa-right': 95, 'ecu-75': 75, 'ecu-65': 65, 'ecu-165': 165}
PRIORITY = {'identity-matched': 0, 'selector-candidate': 1, 'system-selector-candidate': 2}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def hci_times(path):
    """One-based btsnoop record times, in original microseconds, not frame indices."""
    times = {}
    with Path(path).open('rb') as f:
        header = f.read(16)
        if len(header) != 16 or header[:8] != b'btsnoop\0':
            raise ValueError('invalid-btsnoop-header')
        n = 0
        while block := f.read(24):
            if len(block) != 24:
                raise ValueError('truncated-btsnoop-record')
            original, included, flags, drops, stamp = struct.unpack('>IIIIQ', block)
            if included > original or included > 16 * 1024 * 1024:
                raise ValueError('invalid-btsnoop-length')
            if len(f.read(included)) != included:
                raise ValueError('truncated-btsnoop-payload')
            n += 1
            times[n] = stamp
    return times


def identity_record(rec):
    out = dict(rec)
    try:
        suffix = parse_identity_suffix7(bytes.fromhex(rec.get('suffix_hex', '')))
        request = derived_read_request(bytes.fromhex(rec.get('request_prefix_hex', '')))
    except (ValueError, TypeError):
        return {**out, 'decoderReady': False, 'decoderIssue': 'malformed-identity-record'}
    if not suffix.get('ok') or request.get('status') != 'ok':
        return {**out, 'decoderReady': False, 'decoderIssue': 'identity-layout-or-request-unresolved'}
    out.update(byteOffset=suffix['byteOffset'], bitOffset=suffix['bitOffset'],
        readSID=request['readSID'], pid=request['pid'],
        read_request_candidate_hex=request['request_hex'])
    _, parsed = formula_from_record(out)
    ready = classify_decode(parsed, out['byteOffset'], out['bitOffset'])
    out.update(decoderReady=bool(ready.get('ok')), decoderIssue=ready.get('reason'),
        requestHex=request['request_hex'], dataSpan=data_span(out), **FLAGS)
    return out


def resolve_dynamic_name(text, lookup, seen=frozenset()):
    """Resolve bounded DSTREAM component references, without inventing native formatting."""
    if not isinstance(text, str) or not text.startswith('SubIndexNum='):
        return text, []
    parts = text.split('#')
    try:
        count = int(parts[0].split('=', 1)[1])
    except (ValueError, IndexError):
        return None, []
    if count != len(parts) - 1 or not 1 <= count <= 12 or len(seen) >= 8:
        return None, []
    names, refs = [], []
    for part in parts[1:]:
        if not re.fullmatch(r'0x[0-9a-fA-F]{2}(,0x[0-9a-fA-F]{2}){3}', part):
            return None, []
        key = int.from_bytes(bytes(int(c, 16) for c in part.split(',')), 'big')
        if key in seen:
            return None, []
        value, nested = resolve_dynamic_name(lookup(key), lookup, seen | {key})
        if not value:
            return None, []
        names.append(value); refs.extend([key, *nested])
    return ' · '.join(names), refs


def display_name(rec, lookup=None):
    name = rec.get('name')
    if isinstance(name, str) and name and not name.startswith('SubIndexNum='):
        return name, True
    if lookup and name:
        resolved, refs = resolve_dynamic_name(name, lookup)
        if resolved:
            rec['nameReferences'] = [{'namespace': 'DSTREAM_CN.GAG', 'id': f'{key:08X}'} for key in refs]
            rec['originalName'] = name
            return resolved, True
    labels = [r.get('text') for r in rec.get('labels', []) if isinstance(r, dict) and r.get('text')]
    if labels:
        return ' / '.join(labels), True
    return f"{rec.get('requestHex') or '请求未解析'} · 字节 {rec.get('byteOffset', '?')} / 位 {rec.get('bitOffset', '?')}", False


def record_from_parameter(p):
    # The matcher preserved the explicit wire request and source formula.
    request = bytes.fromhex(p['requestHex']) if p.get('requestHex') else b''
    return {**p, 'wireSID': request[0] if request else None,
        'rawSID': request[0] if request else None,
        'disabledreadrequestcandidate': {'payload_hex': p.get('requestHex')}}


def replay_series(rec, groups, times):
    series = []
    for group in groups:
        if group['requestHex'] != rec.get('requestHex'):
            continue
        points, errors, cache = [], Counter(), {}
        for sample in group['samples']:
            hx = sample['pduHex']
            if hx not in cache:
                cache[hx] = decode_application_response(rec, bytes.fromhex(hx), mode='pdu', source=group['groupId'])
            result = cache[hx]
            if not result.get('ok'):
                errors[result.get('reason', 'unknown')] += 1
                continue
            stamp = times.get(sample['responseRecord'])
            if stamp is None:
                raise ValueError('response-timestamp-missing')
            # Keep only source-derived values, never decoded cache or VIN identity values.
            value = result.get('value')
            if result.get('numeric') and result.get('phys') is not None:
                value = float(result['phys'])
                if not math.isfinite(value):
                    raise ValueError('non-finite-physical-value')
            points.append({'timestampUs': stamp, 'value': value,
                'text': result.get('display') if result.get('display') is not None else result.get('text'),
                'physicalExact': result.get('phys'), 'rawValue': result.get('raw'), 'numeric': result.get('numeric', False),
                'responseFrameId': sample['responseFrameId'], 'responseRecord': sample['responseRecord'],
                'responseFrameSha256': sample['responseFrameSha256'],
                'pduSha256': hashlib.sha256(bytes.fromhex(hx)).hexdigest()})
        points.sort(key=lambda p: (p['timestampUs'], p['responseRecord']))
        total = len(points)
        if total:
            first = points[0]['timestampUs']
            if total > POINT_LIMIT:
                points = [points[round(i * (total - 1) / (POINT_LIMIT - 1))] for i in range(POINT_LIMIT)]
            for p in points:
                p['elapsedMs'] = (p.pop('timestampUs') - first) / 1000
            series.append({'groupId': group['groupId'], 'adapterStream': group['adapterStream'],
                'txId': group['txId'], 'rxId': group['rxId'], 'capturePhase': group['capturePhase'],
                'sourceStartUs': str(first), 'pointCount': total, 'displayPointCount': len(points),
                'downsampled': total > len(points), 'errors': dict(errors), 'points': points})
        elif errors:
            series.append({'groupId': group['groupId'], 'pointCount': 0, 'displayPointCount': 0,
                'capturePhase': group['capturePhase'], 'errors': dict(errors), 'points': []})
    return series


def slim_parameter(p):
    keys = ('id', 'name', 'nameResolved', 'unit', 'requestHex', 'byteOffset', 'bitOffset',
        'decoderReady', 'decoderIssue', 'dataSpan', 'status', 'decodedSampleCount',
        'source', 'definitionOffset', 'sourceOffsets', 'duplicateCount', 'originalName', 'nameReferences', 'categories')
    return {k: p.get(k) for k in keys}


def zero_definition(p):
    """Hide zero placeholder labels, never a measured numeric zero or byte offset."""
    label = str(p.get('name', '')).strip()
    return bool(re.fullmatch(r'(?:名称|定义)?\s*(?:为|[:：=])?\s*(?:0+(?:\.0+)?|0[xX]0+)', label))


def plan_for(profile, selected):
    if not isinstance(selected, list) or not 1 <= len(selected) <= SELECTION_LIMIT:
        raise ValueError('selection-limit')
    if len(set(selected)) != len(selected):
        raise ValueError('duplicate-selection')
    params = {p['id']: p for p in profile['parameters']}
    if any(k not in params for k in selected):
        raise ValueError('unknown-parameter')
    groups, blocked = {}, []
    for key in selected:
        p = params[key]
        if not p.get('decoderReady') or not p.get('requestHex'):
            blocked.append({'parameterId': key, 'reason': p.get('decoderIssue') or 'request-unresolved'})
            continue
        g = groups.setdefault(p['requestHex'], {'requestHex': p['requestHex'], 'parameterIds': [],
            'minimumDataBytes': 0, 'spanKind': 'exact', **FLAGS})
        g['parameterIds'].append(key)
        span = p.get('dataSpan')
        if span:
            g['minimumDataBytes'] = max(g['minimumDataBytes'], span['dataMin'])
            if span['kind'] != 'exact':
                g['spanKind'] = 'lowerBound'
        else:
            g['spanKind'] = 'unresolved'
    identities = defaultdict(list)
    for rec in profile['identity']:
        if rec.get('requestHex'):
            name, resolved = display_name(rec)
            identities[rec['requestHex']].append({'name': name, 'decoderReady': rec['decoderReady'],
                'byteOffset': rec.get('byteOffset'), 'bitOffset': rec.get('bitOffset'),
                'sourceOffset': rec.get('at'), 'dataSpan': rec.get('dataSpan')})
    return {'profileId': profile['profileId'], 'selectedCount': len(selected),
        'requestCountPerCycle': len(groups), 'groups': list(groups.values()), 'blocked': blocked,
        'identityGroups': [{'requestHex': hx, 'fields': fields, **FLAGS} for hx, fields in identities.items()],
        'addressCandidates': profile.get('addressCandidates', []),
        'checklist': profile['checklist'], 'parameters': [slim_parameter(params[k]) for k in selected],
        'intervalClaim': None, 'selectionLimitBasis': 'user-provisional-12; X431 transport capacity unverified',
        **FLAGS}


def compile_bundle(match_dir, output, variants_path=DEFAULT_VARIANTS, hci_path=SOURCE_PATH):
    match_dir, output = Path(match_dir), Path(output)
    if output.exists():
        raise ValueError('output-already-exists')
    manifest = load(match_dir / 'manifest.json')
    # Reuse verified results only after proving their sources and generated outputs unchanged.
    inputs = [verify_source(match_dir / 'manifest.json')]
    for item in manifest['inputs'] + manifest['outputs']:
        inputs.append(verify_source(Path(item['path']), item['sha256']))
    inputs.append(verify_source(variants_path, next(i['sha256'] for i in manifest['inputs'] if Path(i['path']).name == 'variants.jsonl')))
    inputs.append(verify_source(hci_path, next(i['sha256'] for i in manifest['inputs'] if Path(i['path']).name == 'btsnoop_hci.log')))
    inputs.append(verify_source(ROOT / '.local/x431-re/2026-09-27-protocol/package/libPORSCHE_VERF.so',
        '92acc9fb3178fc4595abcb44c1925951426d0cd43ceee4cbba83e1319165d0eb'))
    inputs.append(verify_source(GGP_PATH, '7caf1c27e475511e4a75db8fe92008684f5632eeb9fbb4484cbc16fa0eec7de3'))
    language = GgpLanguage(GGP_PATH)
    def lookup_name(key):
        return compact_text(language.lookup_id('DSTREAM_CN.GAG', key))
    def category_for(p):
        key = p.get('groupIdHex') or 'ungrouped'
        text = lookup_name(int(key, 16)) if key != 'ungrouped' else None
        resolved, refs = resolve_dynamic_name(text, lookup_name) if text else (None, [])
        return {'id': key, 'label': resolved or (f'分组 {key}' if key != 'ungrouped' else '未分组'),
            'nameResolved': bool(resolved), 'sourceNamespace': 'DSTREAM_CN.GAG'}
    summaries = {v['profileId']: v for v in load(match_dir / 'variants.json')}
    groups = {g['groupId']: g for g in load(match_dir / 'request-groups.json')}
    times = hci_times(hci_path)
    pairs = explicit_address_pairs(load(ADDRESS_PATH))
    parameters = defaultdict(dict)
    total = 0
    with (match_dir / 'parameters.jsonl').open(encoding='utf-8') as f:
        for line in f:
            p = json.loads(line)
            total += 1
            if p.get('generation') == '982':
                continue
            key = p['logicalParameterKey']
            prior = parameters[p['profileId']].get(key)
            if prior:
                prior['sourceOffsets'].append(p['definitionOffset'])
                prior['duplicateCount'] += 1
                category = category_for(p)
                if category['id'] not in {c['id'] for c in prior['categories']}:
                    prior['categories'].append(category)
                continue
            p.update(id=key, sourceOffsets=[p['definitionOffset']], duplicateCount=1, categories=[category_for(p)])
            p['originalName'] = p.get('name')
            p['name'], p['nameResolved'] = display_name(p, lookup_name)
            p['record'] = record_from_parameter(p)
            p['replay'] = replay_series(p['record'], [groups[k] for k in p['captureGroupIds']], times)
            parameters[p['profileId']][key] = p
    units = load(match_dir / 'units.json')
    output.mkdir(parents=True)
    (output / 'profiles').mkdir()
    index, stats = [], Counter()
    reverse_nodes = {eid: node for node, eid in NODE_ECU.items()}
    with Path(variants_path).open(encoding='utf-8') as f:
        for line in f:
            variant = json.loads(line)
            pid = variant['profile_id']
            summary = summaries.get(pid)
            if not summary or variant.get('generation') == '982':
                continue
            identity = [identity_record(r) for r in variant.get('pool_records', {}).get('identity', {}).get('records', [])]
            params = list(parameters.get(pid, {}).values())
            tx = ADDRESSES.get(variant['module'])
            if 4 in summary['ecuIds']:
                tx = 0x715
            if 70 in summary['ecuIds']:
                tx = 0x773
            routed = [g for g in groups.values() if tx is not None and g['txId'] == tx and g['rxId'] == pairs.get(tx)]
            identity_results = []
            for rec in identity:
                # VIN and serial identity values stay in original private evidence; do not export them.
                if rec.get('requestHex') in ('22F190', '1A90', '22F18C'):
                    continue
                series = replay_series(rec, routed, times) if rec.get('decoderReady') else []
                if series:
                    name, _ = display_name(rec)
                    identity_results.append({'name': name, 'requestHex': rec.get('requestHex'),
                        'sourceOffset': rec.get('at'), 'series': series, **FLAGS})
            address_candidates = [{k: g[k] for k in ('txId', 'rxId', 'adapterStream', 'capturePhase')} for g in routed]
            # An address inferred through another variant is not automatically bound to this ECU.
            unique_addresses = {json.dumps(a, sort_keys=True): a for a in address_candidates}
            checklist = [
                {'item': 'identity-record-layout', 'state': 'offline-complete', 'ready': sum(bool(r.get('decoderReady')) for r in identity), 'total': len(identity)},
                {'item': 'measurement-decoder', 'state': 'offline-complete', 'ready': sum(bool(p.get('decoderReady')) for p in params), 'total': len(params)},
                {'item': 'vehicle-fit-and-full-version', 'state': 'vehicle-required'},
                {'item': 'independent-vlinker-read-and-timing', 'state': 'vehicle-required'},
                {'item': 'field-validation', 'state': 'vehicle-required'},
            ]
            if summary.get('selectorMatchKind') == 'system-name-only':
                checklist.append({'item': 'selector-suffix-semantics', 'state': 'unresolved-static-evidence'})
            if any(not p['nameResolved'] for p in params):
                checklist.append({'item': 'missing-source-labels', 'state': 'source-evidence-missing',
                    'count': sum(not p['nameResolved'] for p in params)})
            if any(not r.get('decoderReady') for r in identity) or any(not p.get('decoderReady') for p in params):
                checklist.append({'item': 'unsupported-formula-evidence', 'state': 'unresolved-static-evidence'})
            profile = {**summary, 'parameters': params, 'identity': identity,
                'identityDecoderIssue': None, 'identityReplay': identity_results,
                'addressCandidates': list(unique_addresses.values()),
                'checklist': checklist, **FLAGS}
            filename = hashlib.sha256(pid.encode()).hexdigest() + '.json'
            dump(output / 'profiles' / filename, profile)
            row = {k: profile[k] for k in ('profileId', 'name', 'module', 'ecuIds', 'status')}
            row.update(file='profiles/' + filename, sha256=sha(output / 'profiles' / filename),
                parameterCount=len(params), replayParameterCount=sum(any(s['pointCount'] for s in p['replay']) for p in params),
                identityCount=len(identity), identityDecoderCount=sum(bool(r.get('decoderReady')) for r in identity),
                checklist=checklist)
            index.append(row)
            stats.update(profiles=1, parameters=len(params), identityRecords=len(identity),
                identityDecoders=row['identityDecoderCount'], replayParameters=row['replayParameterCount'],
                identityReplayFields=len(identity_results),
                resolvedNames=sum(bool(p['nameResolved']) for p in params),
                measurementDecoders=sum(bool(p.get('decoderReady')) for p in params))
    unit_index = []
    for unit in units:
        variants = [{k: v[k] for k in ('profileId', 'name', 'module', 'status', 'parameterCount', 'replayParameterCount', 'identityCount', 'identityDecoderCount', 'checklist')}
            for v in index if unit['ecuId'] in v['ecuIds']]
        variants.sort(key=lambda v: (PRIORITY.get(v['status'], 3), v['name']))
        unit_index.append({'ecuId': unit['ecuId'], 'systemId': reverse_nodes.get(unit['ecuId'], f"ecu-{unit['ecuId']}"),
            'label': unit['label'], 'status': unit['status'], 'variants': variants, **FLAGS})
    dump(output / 'index.json', {'schemaVersion': 1, 'generation': '981', 'units': unit_index,
        'profiles': index, 'counts': {**stats, 'units': len(unit_index), 'sourceMeasurements': total}, **FLAGS})
    dump(output / 'capture-checklist.json', {'units': unit_index,
        'procedure': ['核对本车装配与完整 ECU 身份', '单独记录 X431 选参清单与只读响应、时间和地址',
            '保存原始记录及哈希，核对公式、单位、枚举、请求周期', '经 vLinker 独立只读复现后再开放桌面车辆采集'], **FLAGS})
    report = ['# 实时数据离线准备', '', f"{len(unit_index)} 个菜单单元，{stats['profiles']} 个变体，{stats['parameters']} 个去重参数。",
        f"身份记录 {stats['identityRecords']}，可离线解码 {stats['identityDecoders']}；已有响应的参数 {stats['replayParameters']}。", '',
        f"参数名称已展开 {stats['resolvedNames']} 项，来源缺名称 {stats['parameters'] - stats['resolvedNames']} 项。动态名称按来源组成显示，不声称复现原生排版。",
        '列表、分组计划和历史回放可在桌面实时数据页使用；这些清单不构成本车装配或独立车辆验证。',
        '每个变体保留缺口；不支持的公式或版本条件仍需补静态证据，不能用相近版本替代。', '',
        '身份布局证据：VERF GetOdxXmlVesInfoEx 0x13810..0x13846，PorscheVerInfo 0x129b4..0x129f2。',
        '图形时间来自 btsnoop 记录微秒；不同适配器、地址、采集阶段分开回放。', '']
    for u in unit_index:
        report.append(f"- {u['label']}：{len(u['variants'])} 个变体；{u['status']}")
    (output / 'report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    # Check sources at the end too, before publishing the ready index.
    for item in inputs:
        verify_source(Path(item['path']), item['sha256'])
    # Derived application PDUs are explicitly private rehearsal material. The
    # original capture stays outside the distributable definition directories.
    dump(output / 'rehearsal-fixtures.json', [g for g in groups.values() if
        g['txId'] == 0x7E0 and g['rxId'] == 0x7E8 and g['adapterStream'] == [2, 2]
        and g['capturePhase'] == 'before-coding'])
    code = [verify_source(Path(__file__)), *[verify_source(ROOT / 'scripts' / file) for file in (
        'x431_re/request_fields.py', 'x431_re/gag_lib.py', 'x431_re/gag_codec.py', 'x431_re/ggp_index.py',
        'diagnostics/response_values.py', 'diagnostics/x431_values.py', 'diagnostics/x431_formula.py',
        'diagnostics/manufacturer_acquisition.py', 'diagnostics/manufacturer_transport.py',
        'diagnostics/elm.py', 'diagnostics/sessions.py', 'diagnostics/catalog.py',
        'diagnostics/qualification.py', 'diagnostics/session_simulator.py')]]
    outputs = [verify_source(p) for p in sorted(output.rglob('*')) if p.is_file()]
    dump(output / 'manifest.json', {'schemaVersion': 2, 'inputs': inputs,
        'inputsAreProvenanceOnlyAtRuntime': True,
        'code': [{**item, 'path': Path(item['path']).relative_to(ROOT).as_posix()} for item in code],
        'outputs': [{**item, 'path': Path(item['path']).relative_to(output.resolve()).as_posix()} for item in outputs], **FLAGS})
    return {**stats, 'units': len(unit_index), 'sourceMeasurements': total, 'output': str(output.resolve()), **FLAGS}


def handle_catalog(req):
    # Full 981/982 catalogue needs no capture, hardware or historical identity.
    # It deliberately does not manufacture vehicle matching or replay points.
    from .workbench import (_variants_path, load_or_build_index, _load_variant,
        _matches_generation, _profile_guard)
    from .offline import DEFAULT_COVERAGE
    variants = _variants_path()
    generation = req.get('generation')
    if generation not in ('981', '982'):
        raise ValueError('wrong-generation')
    index = load_or_build_index(variants, load(DEFAULT_COVERAGE))
    reverse = {ecu: system for system, ecu in NODE_ECU.items()}
    def result(**values):
        return {'ok': True, **values, **FLAGS}
    if req['action'] == 'catalog-units':
        units = {}
        for row in index.get('rows', []):
            if not _matches_generation(row, generation):
                continue
            for ecu in row.get('ecuIds', []):
                if ecu not in reverse:
                    continue
                unit = units.setdefault(ecu, {'systemId': reverse[ecu], 'ecuId': ecu, 'label': reverse[ecu], 'variants': []})
                unit['variants'].append({'profileId': row['profileId'], 'name': row['name'], 'status': 'catalog-unqualified',
                    'parameterCount': row['poolCounts'].get('measurement') or 0, 'replayParameterCount': 0,
                    'identityCount': row['poolCounts'].get('identity') or 0, 'identityDecoderCount': 0})
        return result(present=index.get('present', False), units=list(units.values()), counts={}, selectionLimit=SELECTION_LIMIT)
    row = next((r for r in index.get('rows', []) if r['profileId'] == req.get('profileId')), None)
    if row is None:
        raise ValueError('unknown-profile')
    guarded = _profile_guard(req, row)
    if guarded:
        return guarded
    variant = _load_variant(variants, row)
    if req['action'] == 'catalog-coding-plan':
        if set(req) - {'action', 'generation', 'ecuId', 'profileId'}:
            raise ValueError('coding-read-forbidden-field')
        if type(req.get('ecuId')) is not int or req['ecuId'] not in row.get('ecuIds', []):
            raise ValueError('profile-ecu-mismatch')
        from .coding_read_plan import coding_read_plan
        return result(plan=coding_read_plan(variant, generation))
    records = variant.get('pool_records', {}).get('measurement', {}).get('records', [])
    language = GgpLanguage(GGP_PATH) if GGP_PATH.is_file() and any(str(record.get('name', '')).startswith('SubIndexNum=') for record in records) else None
    def lookup_name(key):
        return compact_text(language.lookup_id('DSTREAM_CN.GAG', key)) if language else None
    parameters = []
    for position, record in enumerate(records):
        request = request_from_record(record)
        _text, parsed = formula_from_record(record)
        classified = classify_decode(parsed, record.get('byteOffset'), record.get('bitOffset'))
        candidate = request['request'].hex().upper() if request.get('ok') else None
        name, name_resolved = display_name({**record, 'requestHex': candidate}, lookup_name if language else None)
        key = hashlib.sha256(json.dumps([row['profileId'], position, record], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        group = record.get('groupIdHex')
        if not isinstance(group, str) or not re.fullmatch(r'[0-9A-F]{8}', group):
            group = 'ungrouped'
        parameters.append({**record, 'id': key, 'name': name, 'nameResolved': name_resolved,
            'requestHex': candidate, 'decoderReady': bool(classified.get('ok') and candidate),
            'decoderIssue': classified.get('reason') if not classified.get('ok') else request.get('reason'),
            'dataSpan': data_span(record), 'status': 'catalog-unqualified', 'decodedSampleCount': 0,
            'categories': [{'id': group, 'label': f'分组 {group}' if group != 'ungrouped' else '未分组'}], 'replay': []})
    profile = {'profileId': row['profileId'], 'parameters': parameters, 'identity': [], 'addressCandidates': [],
        'checklist': ['目录版本是手动选择，未与本车身份匹配', '缺少本次身份、会话与路由核实', '该目录没有实车响应，无法生成车辆数值']}
    if req['action'] == 'catalog-parameters':
        visible = [p for p in parameters if not zero_definition(p)]
        categories = {}
        for parameter in visible:
            category = parameter['categories'][0]
            categories.setdefault(category['id'], {**category, 'count': 0})['count'] += 1
        query = req.get('search', '').casefold(); group = req.get('groupId')
        if group is not None and group not in categories:
            raise ValueError('unknown-category')
        filtered = [p for p in visible if (not query or query in (p['name'] + ' ' + (p['requestHex'] or '')).casefold())
            and (group is None or p['categories'][0]['id'] == group)]
        offset, limit = req.get('offset', 0), req.get('limit', 40)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('page-limit')
        return result(items=[slim_parameter(p) for p in filtered[offset:offset + limit]], total=len(filtered), categories=list(categories.values()), checklist=profile['checklist'])
    plan = plan_for(profile, req.get('parameterIds'))
    if req['action'] == 'catalog-plan':
        return result(plan=plan)
    selected = set(req['parameterIds'])
    return result(source='definition-only-no-vehicle-response', parameters=[{**slim_parameter(p), 'series': [], 'missingResponse': True} for p in parameters if p['id'] in selected])


def handle_ready(req, root=None):
    root = Path(root) if root is not None else Path(os.environ.get('PORSCHE981_REALTIME_PREPARATION') or DEFAULT_OUTPUT)
    def result(**value):
        return {'ok': True, 'liveVerified': False, **value, **FLAGS}
    try:
        if req['action'].startswith('catalog-'):
            return handle_catalog(req)
        if req.get('generation', '981') != '981':
            raise ValueError('wrong-generation')
        if not (root / 'manifest.json').is_file():
            return result(present=False, units=[], counts={}) if req['action'] == 'ready-units' else {'ok': False, 'error': 'preparation-missing', **FLAGS}
        manifest = load(root / 'manifest.json')
        known = {Path(i['path']).name: i for i in manifest['outputs']}
        verify_source(root / 'index.json', known['index.json']['sha256'])
        index = load(root / 'index.json')
        if req['action'] == 'ready-units':
            return result(present=True, units=index['units'], counts=index['counts'], selectionLimit=SELECTION_LIMIT)
        row = next((p for p in index['profiles'] if p['profileId'] == req.get('profileId')), None)
        if row is None:
            raise ValueError('unknown-profile')
        if type(req.get('ecuId')) is not int or req['ecuId'] not in row['ecuIds']:
            raise ValueError('profile-ecu-mismatch')
        file = row['file']
        if not re.fullmatch(r'profiles/[0-9a-f]{64}\.json', file):
            raise ValueError('invalid-profile-file')
        verify_source(root / file, row['sha256'])
        profile = load(root / file)
        if profile.get('generation') == '982':
            raise ValueError('wrong-generation')
        if req['action'] == 'ready-parameters':
            query = (req.get('search') or '').casefold()
            visible = [p for p in profile['parameters'] if not zero_definition(p)]
            categories = {}
            for p in visible:
                for category in p.get('categories', []):
                    group = categories.setdefault(category['id'], {**category, 'count': 0})
                    group['count'] += 1
            group_id = req.get('groupId')
            if group_id is not None and group_id not in categories:
                raise ValueError('unknown-category')
            params = [p for p in visible if
                (not query or query in (p['name'] + ' ' + (p['requestHex'] or '')).casefold())
                and (group_id is None or any(c['id'] == group_id for c in p.get('categories', [])))]
            offset, limit = req.get('offset', 0), req.get('limit', 40)
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError('page-limit')
            return result(profileId=row['profileId'], items=[slim_parameter(p) for p in params[offset:offset + limit]],
                total=len(params), offset=offset, categories=list(categories.values()),
                checklist=profile['checklist'], identityReplay=profile.get('identityReplay', []))
        plan = plan_for(profile, req.get('parameterIds'))
        if req['action'] == 'ready-acquire':
            if set(req) - {'action', 'generation', 'ecuId', 'profileId', 'parameterIds'}:
                raise ValueError('manufacturer-forbidden-field')
            # Production ELM framing runs against a private fixture BytePort.
            # Stale code or changed evidence fails before opening that fixture.
            from .manufacturer_transport import transport_rehearsal
            for item in manifest['code']:
                file = Path(item['path'])
                if manifest.get('schemaVersion') == 2:
                    if file.is_absolute() or '..' in file.parts or not item['path'].startswith('scripts/'):
                        raise ValueError('manufacturer-code-path-invalid')
                    file = ROOT / file
                verify_source(file, item['sha256'])
            if manifest.get('schemaVersion') == 2:
                groups_path = root / 'rehearsal-fixtures.json'
                expected = next((item['sha256'] for item in manifest['outputs']
                                if item['path'] == 'rehearsal-fixtures.json'), None)
            else:
                groups_path = DEFAULT_MATCH / 'request-groups.json'
                expected = next((item['sha256'] for item in manifest['inputs']
                                if Path(item['path']).resolve() == groups_path.resolve()), None)
            if not expected:
                raise ValueError('manufacturer-response-source-missing')
            verify_source(groups_path, expected)
            return transport_rehearsal(profile, req.get('parameterIds'), load(groups_path))
        if req['action'] == 'ready-plan':
            from .manufacturer_transport import DME_PROFILE, prepared_read_contract
            if profile.get('profileId') == DME_PROFILE:
                try:
                    plan['preparedTransport'] = prepared_read_contract(profile, req.get('parameterIds'))
                except ValueError as error:
                    plan['preparedTransport'] = {'executionEnabled': False, 'liveVerified': False,
                        'error': str(error)}
            return result(plan=plan)
        if req['action'] == 'ready-replay':
            selected = set(req['parameterIds'])
            return result(source='X431 historical application PDU', timestampSource='btsnoop-record-microseconds',
                parameters=[{**slim_parameter(p), 'series': p['replay'],
                    'missingResponse': not any(s['pointCount'] for s in p['replay'])}
                    for p in profile['parameters'] if p['id'] in selected])
        raise ValueError('invalid-action')
    except (ValueError, KeyError, OSError, TypeError) as e:
        return {'ok': False, 'error': str(e), 'liveVerified': False, **FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--match-dir', type=Path, default=DEFAULT_MATCH)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--variants', type=Path, default=DEFAULT_VARIANTS)
    args = parser.parse_args()
    print(json.dumps(compile_bundle(args.match_dir, args.output_dir, args.variants), ensure_ascii=False))


if __name__ == '__main__':
    main()
