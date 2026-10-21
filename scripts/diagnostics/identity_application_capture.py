"""Compare complete application captures with every source version of one ECU.

No transport, inferred addresses, version selection or coding baseline. Full
responses and field observations remain separate; repeated values never replace
earlier observations. Source comparisons do not prove responder identity.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path

from .coding_capture import digest, native_result_error, scan_capture
from .identity_capture import FLAGS, field_result, source_profiles
from .response_values import normalize_response, request_from_record
from .source_catalog import _catalog, _code_status, _document
from .identity_version_capture import CaptureVersionRules

GUARDS = {**FLAGS, 'ecuIdentityQualified': False, 'ecuVersionQualified': False,
          'freshReadProven': False, 'baselineCreated': False,
          'sourceSelectionIsVersionProof': False, 'requestedTargetIsResponderProof': False}


def catalog_profiles(library, catalog_id):
    root, manifest = _catalog(library, catalog_id)
    status = _code_status(manifest)
    if not status['generatorCodeMatchesCurrent']:
        raise ValueError('identity-capture-generator-drift')
    index = _document(root, manifest, 'all-units.json')
    documents, bindings, seen = {}, [], set()
    for unit in index['units']:
        for ref in unit['variants']:
            name = ref['file']
            if name not in documents:
                documents[name] = _document(root, manifest, name)
            profile = documents[name]
            key = (unit['generation'], unit['ecuId'], ref['profileId'])
            if (key in seen or unit['generation'] not in ('981', '982')
                    or profile['profileId'] != ref['profileId'] or unit['ecuId'] not in profile['ecuIds']
                    or unit['generation'] not in profile['generations']):
                raise ValueError('identity-capture-profile-binding-invalid')
            seen.add(key)
            bindings.append((unit, profile))
    return bindings, {'sourceCatalogId': catalog_id, 'verifiedProfileFiles': len(documents), **status}


def catalog_scope(library, catalog_id, generation, ecu_id):
    bindings, sources = catalog_profiles(library, catalog_id)
    profiles = [p for u, p in bindings if u['generation'] == generation and u['ecuId'] == ecu_id]
    if not profiles:
        raise ValueError('identity-application-source-unit-not-unique-or-missing')
    profiles = selected_profiles(bindings, generation, ecu_id, profiles[0]['profileId'])
    targets = [{'targetProfileId': p['profileId'], 'association': 'source-target-id'} for p in profiles]
    if generation == '981' and ecu_id in (1, 9):
        targets.append({'targetProfileId': {1: 'porsche-981-2014-dme', 9: 'porsche-981-2014-gateway'}[ecu_id],
                        'association': 'explicit-named-runtime-comparison'})
    return {'captureTargets': targets, 'candidateVersionCount': len(profiles), 'sources': sources, **GUARDS}


def selected_profiles(bindings, generation, ecu_id, target):
    if generation not in ('981', '982') or type(ecu_id) is not int:
        raise ValueError('identity-application-selector-invalid')
    profiles = [p for u, p in bindings if u['generation'] == generation and u['ecuId'] == ecu_id]
    if not profiles or len({p['profileId'] for p in profiles}) != len(profiles):
        raise ValueError('identity-application-source-unit-not-unique-or-missing')
    allowed = {p['profileId'] for p in profiles}
    if generation == '981' and ecu_id in (1, 9):
        allowed.add({1: 'porsche-981-2014-dme', 9: 'porsche-981-2014-gateway'}[ecu_id])
    if not isinstance(target, str) or target not in allowed:
        raise ValueError('identity-application-target-source-mismatch')
    return profiles


def review(capture, bindings, sources, generation, ecu_id, target, run_id, output, *, language_path=None):
    profiles = selected_profiles(bindings, generation, ecu_id, target)
    version_rules = CaptureVersionRules(profiles, ecu_id)
    if not isinstance(run_id, str) or not 0 < len(run_id) <= 240:
        raise ValueError('identity-application-run-invalid')
    lookup = defaultdict(list)
    definitions = []
    for profile in profiles:
        seen_groups = set()
        for group in profile['identityGroups']:
            hx = group['requestHex']
            if hx in seen_groups:
                raise ValueError('identity-application-duplicate-source-group')
            seen_groups.add(hx)
            for index, field in enumerate(group['fields']):
                request = request_from_record(field['decodingDefinition'])
                if not request.get('ok') or request['request'].hex().upper() != hx:
                    raise ValueError('identity-application-field-request-conflict')
                key = (profile['profileId'], hx, index)
                lookup[hx].append((key, field))
        definitions.append({'profileId': profile['profileId'], 'module': profile['module'],
            'variant': profile['variant'], 'sourceGeneration': profile.get('sourceGeneration'),
            'identityGroups': profile['identityGroups'], 'identityMissing': profile['identityMissing'], **GUARDS})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    counts, field_counts, sessions = Counter(), Counter(), Counter()
    observed_fields, synthetic_fields = set(), set()
    observed_groups, synthetic_groups = set(), set()
    first_values, conflicts = {}, set()
    matched = 0
    with (output / 'responses.jsonl').open('x', encoding='utf8') as raw, \
            (output / 'identity-values.jsonl').open('x', encoding='utf8') as values:
        def write(stream, row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')

        def visit(row):
            nonlocal matched
            if row.get('error'):
                counts['capture-packet-error'] += 1
                write(raw, {**row, 'status': 'capture-packet-error', 'scopeAssigned': False, **GUARDS})
                return
            meta = row['context']
            sessions[(meta['targetProfileId'], meta['runId'])] += 1
            if (meta['targetProfileId'], meta['runId']) != (target, run_id):
                return
            matched += 1
            version_rules.observe(row, 'cleanup-response-unqualified' if meta['duringCleanup'] else native_result_error(meta))
            hx, fields = meta['requestHex'], lookup.get(meta['requestHex'], [])
            reason = 'request-not-in-identity-scope' if not fields else (
                'cleanup-response-unqualified' if meta['duringCleanup'] else native_result_error(meta))
            if not reason:
                result = normalize_response(bytes.fromhex(row['pduHex']), fields[0][1]['decodingDefinition'], 'pdu')
                if not result.get('ok'):
                    reason = result.get('reason') or 'identity-response-invalid'
            status = reason or ('synthetic-identity-response' if row['synthetic'] else 'identity-response-observed')
            counts[status] += 1
            write(raw, {**row, 'status': status, 'requestHex': hx, 'candidateSourceFieldCount': len(fields), **GUARDS})
            for key, field in fields:
                if reason:
                    decoded = {'sourceOffset': field['sourceOffset'], 'value': {'ok': False, 'reason': reason},
                               'independentDecoderAgreement': False, 'issue': reason, **GUARDS}
                else:
                    try:
                        decoded = field_result(field, hx, {'pduHex': row['pduHex'], 'observationId': row['packet']})
                        json.dumps(decoded, allow_nan=False)
                    except (ValueError, TypeError, KeyError, ArithmeticError, UnicodeError) as error:
                        decoded = {'sourceOffset': field['sourceOffset'], 'value': {'ok': False,
                            'reason': 'identity-decode-error-' + type(error).__name__},
                            'independentDecoderAgreement': False, 'issue': type(error).__name__, **GUARDS}
                category = 'synthetic' if row['synthetic'] else 'observed'
                agreed = bool(decoded['value'].get('ok') and decoded['independentDecoderAgreement'])
                field_counts[category + ('-independent-agreement' if agreed else '-failed-or-disagreed')] += 1
                if agreed:
                    (synthetic_fields if row['synthetic'] else observed_fields).add(key)
                    (synthetic_groups if row['synthetic'] else observed_groups).add(key[:2])
                    # Constant memory per source field; only report whether a
                    # change occurred. Full values remain in the value stream.
                    signature = hashlib.sha256(json.dumps({k: decoded['value'].get(k) for k in
                        ('value', 'phys', 'text', 'hex', 'enumText')}, sort_keys=True,
                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                    version_key = (category, *key)
                    if first_values.setdefault(version_key, signature) != signature:
                        conflicts.add(version_key)
                write(values, {**decoded, 'packet': row['packet'], 'requestHex': hx,
                    'timestampUs': row['timestampUs'], 'timestampSource': meta['timestampSource'],
                    'captureTargetProfileId': target, 'sourceProfileId': key[0], 'sourceFieldIndex': key[2],
                    'runId': run_id, 'synthetic': row['synthetic'], 'sourceName': field.get('name'), **GUARDS})
        scanned = scan_capture(capture, visit)
        for stream in (raw, values):
            stream.flush()
            os.fsync(stream.fileno())
    summaries = []
    for profile in profiles:
        pid = profile['profileId']
        required = {(pid, group['requestHex'], i) for group in profile['identityGroups'] for i, _ in enumerate(group['fields'])}
        summaries.append({'sourceProfileId': pid, 'sourceGroupCount': len(profile['identityGroups']),
            'sourceFieldCount': len(required), 'groupsWithObservedFieldAgreement': sum(p == pid for p, _ in observed_groups),
            'groupsWithSyntheticFieldAgreement': sum(p == pid for p, _ in synthetic_groups),
            'observedFieldsWithAgreement': len(required & observed_fields),
            'syntheticFieldsWithAgreement': len(required & synthetic_fields),
            'missingObservedFields': [{'requestHex': hx, 'fieldIndex': i} for _, hx, i in sorted(required - observed_fields)],
            'conflictingFields': [{'source': kind, 'requestHex': hx, 'fieldIndex': i}
                for kind, p, hx, i in sorted(conflicts) if p == pid],
            'sourceMissing': profile['identityMissing'], **GUARDS})
    (output / 'identity-profiles.json').write_text(json.dumps({'profiles': definitions, **GUARDS},
        ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    language_path = language_path or os.environ.get('PORSCHE981_LANGUAGE') or library / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
    report = {'kind': 'application-all-source-version-identity-comparison', 'schemaVersion': 1,
        'generation': generation, 'ecuId': ecu_id, 'captureTargetProfileId': target, 'runId': run_id,
        'sourceCatalogBindingCount': len(bindings), 'sourceCatalogUniqueProfiles': len({p['profileId'] for _, p in bindings}),
        'candidateVersionCount': len(profiles), 'matchedPackets': matched, 'counts': dict(counts),
        'fieldObservationCounts': dict(field_counts), 'profiles': summaries, 'sources': sources,
        'availableSessions': [dict(targetProfileId=p, runId=r, packets=n) for (p, r), n in sessions.items()],
        'capture': scanned, 'selectedVehicleVariant': None,
        'versionRuleComparison': version_rules.finish(language_path,
            capture_complete=scanned['prefixComplete'] and not scanned['packetErrors']
                and scanned['endStatisticsPresent']),
        'qualificationGaps': ['actual-responder-identity', 'fresh-complete-identity', 'full-response-length-contract',
            'native-version-selection-and-session', 'independent-adapter-response'], **GUARDS}
    report['artifacts'] = {n: digest(output / n) for n in ('responses.jsonl', 'identity-values.jsonl', 'identity-profiles.json')}
    report['code'] = [{'path': 'scripts/diagnostics/' + n, 'sha256': digest(Path(__file__).with_name(n))}
        for n in ('identity_application_capture.py', 'identity_capture.py', 'coding_capture.py', 'response_values.py',
            'decoder_reference.py', 'x431_values.py', 'x431_formula.py', 'source_catalog.py',
            'identity_version_capture.py', 'x431_variant_selection.py')]
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    return report


def build(portable, expected_sha256, capture, generation, ecu_id, target, run_id, output):
    bindings, sources = source_profiles(portable, expected_sha256)
    return review(capture, bindings, sources, generation, ecu_id, target, run_id, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--definitions-zip', required=True, type=Path)
    parser.add_argument('--definitions-sha256', required=True)
    parser.add_argument('--capture', required=True, type=Path)
    parser.add_argument('--generation', required=True, choices=('981', '982'))
    parser.add_argument('--ecu-id', required=True, type=int)
    parser.add_argument('--capture-target', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = build(args.definitions_zip, args.definitions_sha256, args.capture, args.generation,
        args.ecu_id, args.capture_target, args.run_id, args.output)
    print(json.dumps({k: result[k] for k in ('candidateVersionCount', 'matchedPackets', 'counts', 'fieldObservationCounts')}, sort_keys=True))
    return 0 if result['capture']['prefixComplete'] and not result['capture']['packetErrors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
