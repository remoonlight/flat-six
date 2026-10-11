"""Reproducible whole-source coding audit. No ECU session or write recipe."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .coding_fields import coding_field_readiness, coding_options
from .coding_read_plan import coding_read_plan
from .decoder_reference import reference_patch, reference_value
from .definition_decoder_audit import DefinitionAuditor, record_hash
from .response_values import data_span
from .response_values import request_from_record
from .x431_values import preview_coding, decode_record
from scripts.x431_re.request_fields import coding_profile_variant


def audit_named_patches(record):
    span = data_span(record)
    out = {'status': 'unverified', 'cases': 0, 'mismatches': [], 'vehicleVerified': False}
    try:
        if not span:
            raise ValueError('coding-field-span-unresolved')
        for pattern in (0, 0xA5, 0xFF):
            before = bytes([pattern]) * (span['dataMin'] + 3)
            options = coding_options(record, before)
            if not options:
                raise ValueError('coding-options-label-or-value-unresolved')
            for option in options:
                raw = option['rawValue']
                expected = reference_patch(record, before, raw)
                actual = preview_coding(record, before, raw)
                out['cases'] += 1
                if (not actual.get('ok') or bytes.fromhex(actual['afterHex']) != expected
                        or reference_value(record, expected).get('raw') != raw
                        or decode_record(record, expected).get('text') != option['label']):
                    out['mismatches'].append({'pattern': pattern, 'rawValue': raw})
        out['status'] = 'mismatch' if out['mismatches'] else 'matched'
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        out['reason'] = str(error)
    return out


def compile_audit(variants_path, output):
    from .workbench import _matches_generation, _module_ecus
    from .offline import DEFAULT_COVERAGE
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('coding-audit-output-not-empty')
    output.mkdir(parents=True, exist_ok=True)
    with Path(variants_path).open('rb') as source:
        source_sha = hashlib.file_digest(source, 'sha256').hexdigest()
    coverage = json.loads(DEFAULT_COVERAGE.read_text(encoding='utf-8'))
    module_ecus = _module_ecus(coverage)
    coverage_sha = hashlib.sha256(DEFAULT_COVERAGE.read_bytes()).hexdigest()
    decoder, patch_cache, counts, blockers, profiles = DefinitionAuditor(), {}, Counter(), Counter(), []
    preview_counts = Counter()
    readiness_cache = {}
    def semantic_key(record):
        return record_hash({**{k: record.get(k) for k in ('formula', 'enumText', 'byteOffset', 'bitOffset')},
            'fieldNameResolved': bool(decode_record(record, b'').get('displayName'))})
    def readiness_for(record):
        request = request_from_record(record)
        key = (semantic_key(record), bool(request.get('ok') and request.get('sid') in (0x21, 0x22)))
        if key not in readiness_cache:
            readiness_cache[key] = coding_field_readiness(record)
        return readiness_cache[key]
    with Path(variants_path).open(encoding='utf-8') as source, (output / 'fields.jsonl').open('w', encoding='utf-8') as rows:
        for line in source:
            variant = coding_profile_variant(json.loads(line))
            meta = {'generation': variant.get('generation'), 'ecuIds': sorted(set(module_ecus.get(variant['module'], [])))}
            generations = [g for g in ('981', '982') if _matches_generation(meta, g)]
            if not generations:
                continue
            records = variant.get('pool_records', {}).get('coding', {}).get('records', [])
            profile_counts, profile_blockers = Counter(), Counter()
            for record in records:
                decoded = decoder.check(record)
                readiness = readiness_for(record)
                key = semantic_key(record)
                if key not in patch_cache:
                    patch_cache[key] = audit_named_patches(record)
                patch = patch_cache[key]
                profile_counts['fields'] += 1
                profile_counts['decoder:' + decoded['status']] += 1
                profile_counts['patch:' + patch['status']] += 1
                if readiness['editorBlocker']:
                    profile_blockers[readiness['editorBlocker']] += 1
                rows.write(json.dumps({'profileId': variant['profile_id'], 'sourceOffset': record.get('at'),
                    'source': record.get('source'), 'definitionSha256': record_hash(record), 'decoder': decoded,
                    'namedPatch': patch, **readiness, 'executionEnabled': False, 'writePayload': None}, ensure_ascii=False) + '\n')
            plan = coding_read_plan(variant, generations[0], field_readiness=readiness_for)
            preview_counts.update(plan['previewReadiness'])
            profiles.append({'profileId': variant['profile_id'], 'module': variant['module'], 'variant': variant['name'],
                'generations': generations, 'ecuIds': meta['ecuIds'], 'counts': dict(profile_counts),
                'editorBlockers': dict(profile_blockers), 'previewReadiness': plan['previewReadiness'],
                'editorReadinessScope': 'offline-field-preview-only', 'requestGroups': len(plan['requestGroups']),
                'readScopeMissing': plan['missing'], 'completeLengthQualified': False, 'writeRecipeQualified': False,
                'vehicleCodingVerified': False, 'vehicleRestoreVerified': False})
            counts.update(profile_counts); blockers.update(profile_blockers)
    with Path(variants_path).open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != source_sha:
            raise ValueError('coding-audit-source-changed')
    if hashlib.sha256(DEFAULT_COVERAGE.read_bytes()).hexdigest() != coverage_sha:
        raise ValueError('coding-audit-coverage-source-changed')
    summary = {'schemaVersion': 1, 'kind': 'all-unit-coding-source-audit', 'sourceSha256': source_sha,
        'coverageSourceSha256': coverage_sha, 'mappedUnitIds': sorted({i for p in profiles for i in p['ecuIds']}),
        'profiles': profiles, 'profileCount': len(profiles), 'counts': dict(counts), 'editorBlockers': dict(blockers),
        'previewReadiness': dict(preview_counts), 'editorReadinessScope': 'offline-field-preview-only',
        'decoder': decoder.summary(), 'uniquePatchDefinitions': len(patch_cache),
        'uniquePatchCases': sum(r['cases'] for r in patch_cache.values()), 'vehicleVerified': False,
        'executionEnabled': False, 'writePayload': None}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return {key: value for key, value in summary.items() if key != 'profiles'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    from .workbench import _variants_path
    result = compile_audit(_variants_path(), args.output_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['counts'].get('decoder:mismatch') or result['counts'].get('patch:mismatch') else 0


if __name__ == '__main__':
    raise SystemExit(main())
