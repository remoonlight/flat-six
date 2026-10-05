"""All 981/982 menu units and source versions: offline collection, never sends.

Shared menu membership remains a candidate, not a claim of physical fit. Keep
unresolved definitions and units without mappings in the collection register.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .coding_read_plan import coding_read_plan
from .offline import DEFAULT_COVERAGE
from .offline_match import DEFAULT_VARIANTS, ANCHORS, FLAGS, verify_source
from .response_values import data_span, request_from_record
from .workbench import _matches_generation, _module_ecus, slim_record


def read_groups(records):
    groups, missing = {}, []
    for record in records:
        request = request_from_record(record)
        if not request.get('ok') or request.get('sid') not in (0x1A, 0x21, 0x22):
            missing.append({'sourceOffset': record.get('at'),
                            'reason': request.get('reason') or 'read-service-unresolved'})
            continue
        hx = request['request'].hex().upper()
        group = groups.setdefault(hx, {'requestHex': hx, 'minimumKnownBytes': 0,
                                      'expectedTotalBytes': None, 'fields': [], **FLAGS})
        span = data_span(record)
        if span:
            group['minimumKnownBytes'] = max(group['minimumKnownBytes'], span['dataMin'])
        else:
            missing.append({'sourceOffset': record.get('at'), 'requestHex': hx,
                            'reason': 'field-span-unresolved'})
        group['fields'].append({'sourceOffset': record.get('at'), 'name': record.get('name'),
                               'unit': record.get('unit'), 'span': span})
    return list(groups.values()), missing


def build_all_unit_pack(output, *, variants=DEFAULT_VARIANTS, coverage=None, expected_sha256=ANCHORS['variants']):
    output = Path(output)
    if output.exists():
        raise ValueError('output-already-exists')
    variants = Path(variants)
    inputs = [verify_source(variants, expected_sha256)]
    if coverage is None:
        inputs.append(verify_source(DEFAULT_COVERAGE))
        coverage = json.loads(DEFAULT_COVERAGE.read_text(encoding='utf8'))
    modules = _module_ecus(coverage)
    bindings, profiles, profile_ids = defaultdict(list), [], set()
    # Stream the large source; do not hold the whole proprietary archive in RAM.
    with variants.open(encoding='utf8') as stream:
        for line in stream:
            variant = json.loads(line)
            if not variant.get('on_target_menu') or variant.get('generation') not in (None, '9x1', '981', '982'):
                continue
            if variant['profile_id'] in profile_ids:
                raise ValueError('duplicate-profile-id')
            profile_ids.add(variant['profile_id'])
            pools = variant.get('pool_records') or {}
            identity, identity_missing = read_groups(pools.get('identity', {}).get('records', []))
            measurements, measurement_missing = read_groups(pools.get('measurement', {}).get('records', []))
            coding = coding_read_plan(variant)
            # Include named field definitions/options, but never vendor write candidates.
            for group in coding['requestGroups']:
                offsets = {field['sourceOffset'] for field in group['fields']}
                group['namedFields'] = [{k: slim_record(record, 'coding').get(k)
                    for k in ('at', 'displayName', 'formulaKind', 'enumSource', 'source')}
                    for record in pools.get('coding', {}).get('records', []) if record.get('at') in offsets]
            profile = {'profileId': variant['profile_id'], 'module': variant['module'], 'variant': variant['name'],
                'sourceGeneration': variant.get('generation'), 'sourceOffset': variant.get('off'),
                'ecuIds': modules.get(variant['module'], []),
                'generations': [g for g in ('981', '982') if _matches_generation(variant, g)],
                'applicability': 'source-version-only; vehicle identity and selector semantics required',
                'identityGroups': identity, 'identityMissing': identity_missing,
                'measurementGroups': measurements, 'measurementMissing': measurement_missing,
                'measurementDefinitionCount': len(pools.get('measurement', {}).get('records', [])),
                'dtcDefinitionCount': len(pools.get('dtc', {}).get('records', [])),
                'routineDefinitionCount': len(pools.get('routine', {}).get('records', [])),
                'coding': coding, **FLAGS}
            filename = hashlib.sha256(profile['profileId'].encode()).hexdigest() + '.json'
            profiles.append((filename, profile))
            for generation in profile['generations']:
                for ecu in profile['ecuIds']:
                    bindings[generation, ecu].append({'profileId': profile['profileId'], 'file': 'profiles/' + filename,
                        'variant': profile['variant'], 'module': profile['module'],
                        'identityReadGroups': len(identity), 'identityMissing': len(identity_missing),
                        'measurementDefinitions': profile['measurementDefinitionCount'],
                        'measurementReadGroups': len(measurements), 'measurementMissing': len(measurement_missing),
                        'codingFields': coding['definitionFieldCount'], 'codingReadGroups': len(coding['requestGroups']),
                        'codingMissing': len(coding['missing']), **FLAGS})
    verify_source(variants, inputs[0]['sha256'])
    units = []
    for generation in ('981', '982'):
        for menu in coverage.get('menu_ecus', []):
            ecu = menu['ecu_id']
            refs = bindings[generation, ecu]
            units.append({'generation': generation, 'ecuId': ecu, 'label': menu.get('label'),
                'variantCount': len(refs), 'variants': refs, 'installedOnVehicle': None,
                'status': 'candidate-definitions' if refs else 'source-mapping-missing',
                'requiredEvidence': ['full ECU identity/VIN/hardware/software and native version-selection path',
                    'exact addresses, session entry/exit, complete read responses and timestamps',
                    'DTC read/status definitions and response samples',
                    'complete original coding blocks, full scope/length and all native coding pages',
                    'verified named fields/legal values, exact write/readback and current-unit recovery',
                    'adapter/firmware qualification; success/failure/cancel/close evidence'],
                'diagnosticsQualified': False, 'liveDataQualified': False,
                'codingWriteQualified': False, 'codingRestoreQualified': False, **FLAGS})
    report = {'schemaVersion': 1, 'kind': 'all-control-unit-collection-register', 'noDeviceIO': True,
        'vehicleVerified': False, 'source': inputs[0], 'units': units,
        'menuUnitCounts': {g: sum(u['generation'] == g for u in units) for g in ('981', '982')},
        'uniqueVersionCounts': {g: sum(g in p['generations'] for _, p in profiles) for g in ('981', '982')},
        'ecuVersionBindingCounts': {g: sum(u['variantCount'] for u in units if u['generation'] == g)
                                  for g in ('981', '982')},
        'countBasis': 'source menu/version candidates; neither installed units nor executable functions', **FLAGS}
    output.mkdir(parents=True)
    (output / 'profiles').mkdir()
    outputs = []
    for filename, profile in profiles:
        target = output / 'profiles' / filename
        target.write_text(json.dumps(profile, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf8')
        outputs.append(verify_source(target))
    target = output / 'all-units.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    outputs.append(verify_source(target))
    lines = ['# 全控制单元诊断与设码采集', '',
        '981/982 全部来源菜单逐版本保留；共享版本、本车装配和可执行功能分别核对。',
        '先记录实际安装与完整身份；仅用已有 X431/PIWIS 只读菜单补采，不发送此清单中的候选请求。',
        '原码全块只读采集与设码写入分开；定义最低长度不等于完整原码范围。', '']
    for unit in units:
        lines += [f"## {unit['generation']} · {unit['label']}（来源 ECU {unit['ecuId']}）",
                  f"{unit['variantCount']} 个候选版本；状态 {unit['status']}。", '']
        for ref in unit['variants']:
            lines.append(f"- {ref['variant']}：身份 {ref['identityReadGroups']} 组；实时 {ref['measurementReadGroups']} 组；"
                         f"设码 {ref['codingFields']} 字段 / {ref['codingReadGroups']} 读取组；资料 `{ref['file']}`。")
        lines += ['', '记录：安装情况、完整身份/版本、故障码、原码全块、采样工况/周期、原始文件/哈希、缺项及失败。', '']
    checklist = output / '全控制单元采集.md'
    checklist.write_text('\n'.join(lines) + '\n', encoding='utf8')
    outputs.append(verify_source(checklist))
    manifest = {'inputs': inputs, 'outputs': outputs,
                'code': [verify_source(Path(__file__)), verify_source(Path(__file__).with_name('coding_read_plan.py')),
                         verify_source(Path(__file__).with_name('response_values.py')), verify_source(Path(__file__).with_name('workbench.py'))], **FLAGS}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    return {k: report[k] for k in ('menuUnitCounts', 'uniqueVersionCounts', 'ecuVersionBindingCounts', 'noDeviceIO', 'vehicleVerified')}
