"""Produce a bounded per-version 981/982 definition/readiness register, no device I/O."""
from __future__ import annotations
import argparse
import json
import hashlib
import re
import os
from collections import Counter, defaultdict
from pathlib import Path
from .coding_read_plan import coding_read_plan
from scripts.x431_re.request_fields import coding_profile_variant
from .offline_match import ROOT, DEFAULT_VARIANTS, verify_source, ANCHORS
from .workbench import _matches_generation, _module_ecus
from .offline import DEFAULT_COVERAGE
from .response_values import request_from_record
from .realtime_preparation import identity_record, display_name, GGP_PATH
from .response_values import data_span
from scripts.x431_re.gag_lib import GgpLanguage, compact_text
from .definition_decoder_audit import DefinitionAuditor, record_hash
from .catalog import DEFAULT_CATALOG, load_catalog, profile_by_id, operation
from .qualification import STATIC_VARIANT_LINKS
from .sessions import SESSION_BY_PROFILE, IDENTITY_FIELDS
from .historical_coverage import HistoricalCoverage
from .routine_definitions import routine_definition
from .realtime_preparation import DEFAULT_OUTPUT, DEFAULT_MATCH
from .realtime_preparation_accept import validate_bundle

CATEGORIES = ('identity', 'measurement', 'coding', 'dtc', 'routine', 'firmware')


def transport_preparation(variant, generation, ecu_id, catalog):
    """Reuse only an exact existing named identity/session binding, never infer one."""
    context = {'generation': generation, 'ecuId': ecu_id, 'profileId': variant['profile_id'],
        'catalogProfileId': None, 'address': None, 'session': None, 'identityRequests': [],
        'adapterQualification': 'not-established', 'executionEnabled': False, 'liveVerified': False,
        'qualificationGaps': ['fresh-complete-identity', 'independent-adapter-read-response', 'observed-cadence']}
    matched = next((name for (ecu, name), link in STATIC_VARIANT_LINKS.items()
                    if generation == '981' and ecu == ecu_id and link['profile_id'] == variant['profile_id']), None)
    if not matched:
        context['qualificationGaps'] += ['exact-session/address', 'full-identity-constraints']
        return context
    named = profile_by_id(catalog, matched)
    request, positive = SESSION_BY_PROFILE[matched]
    context.update(catalogProfileId=matched,
        address={'txId': named['txId'], 'rxId': named['rxId']},
        session={'requestHex': request, 'positivePrefixHex': positive},
        identityRequests=[dict(field=field, **operation(named, oid)) for field, oid in IDENTITY_FIELDS[matched]],
        identityConstraints=named['identityConstraints'])
    return context


def unresolved_name_source(record, lookup):
    """Trace missing/empty dynamic-name components without inventing a label."""
    def inspect(key, text=None, seen=frozenset()):
        if key in seen or len(seen) >= 8:
            return 'definition-unresolved', []
        if text is None:
            text = lookup(key)
        if text is None:
            return 'source-entry-missing', []
        if text == '':
            return 'source-empty', [f'{key:08X}']
        if not text.startswith('SubIndexNum='):
            return 'definition-unresolved', []
        parts = text.split('#')
        try:
            if int(parts[0].split('=', 1)[1]) != len(parts) - 1 or not 1 <= len(parts) - 1 <= 12:
                return 'definition-unresolved', []
        except (ValueError, IndexError):
            return 'definition-unresolved', []
        statuses, empty_ids = [], []
        for part in parts[1:]:
            if not re.fullmatch(r'0x[0-9a-fA-F]{2}(,0x[0-9a-fA-F]{2}){3}', part):
                return 'definition-unresolved', []
            child = int.from_bytes(bytes(int(value, 16) for value in part.split(',')), 'big')
            status, ids = inspect(child, seen=seen | {key})
            statuses.append(status); empty_ids.extend(ids)
        if any(status in ('source-entry-missing', 'source-component-missing') for status in statuses):
            return 'source-component-missing', empty_ids
        if empty_ids:
            return 'source-component-empty', sorted(set(empty_ids))
        return 'definition-unresolved', []
    name_id = record.get('nameID') or record.get('text_id_hex') or record.get('dstream_name_id_hex')
    try:
        key = int(name_id, 16) if isinstance(name_id, str) else name_id
        if type(key) is not int or not 0 <= key <= 0xFFFFFFFF:
            return 'invalid-source-name-id', []
        return inspect(key, record.get('name') or None)
    except (ValueError, TypeError):
        return 'invalid-source-name-id', []


def function_rows(variant, generation, ecu_id, lookup=None, audits=None, historical=None):
    """One row per ECU/version/function group with exact, non-executable refs."""
    for category in CATEGORIES:
        records = variant.get('pool_records', {}).get(category, {}).get('records', [])
        groups = defaultdict(list)
        for original in records:
            record = identity_record(original) if category == 'identity' else dict(original)
            request = request_from_record(record) if category in ('identity', 'measurement', 'coding') else {}
            hx = request['request'].hex().upper() if request.get('ok') else None
            label, named = display_name(record, lookup)
            routine = routine_definition(record) if category == 'routine' else None
            if routine:
                label, named = routine['name'], bool(routine['name'])
            if category == 'dtc':
                label, named = record.get('text'), bool(record.get('text'))
            name_id = routine['nameId'] if routine else record.get('nameID') or record.get('text_id_hex') or record.get('dstream_name_id_hex')
            name_status = 'resolved' if named else 'unresolved'
            if routine:
                name_status = routine['nameStatus']
            empty_name_ids = []
            if not routine and not named and lookup and name_id is not None:
                name_status, empty_name_ids = unresolved_name_source(record, lookup)
            span = data_span(record) if category in ('identity', 'measurement', 'coding') else None
            key = hx or (record.get('code') if category == 'dtc' else None) or f"offset:{record.get('at')}"
            gaps = []
            if not named: gaps.append('name-unresolved')
            if category in ('identity', 'measurement', 'coding'):
                if not hx: gaps.append(request.get('reason') or 'read-request-unresolved')
                if not span: gaps.append('field-decoder-unresolved')
            if category == 'dtc': gaps.append('dtc-read-and-status-definition-required')
            if category == 'routine': gaps.append('routine-recipe-and-recovery-required')
            if routine and routine['status'] == 'unresolved': gaps.append(routine['reason'])
            digest = record_hash(original)
            historical_evidence = historical.check(record, variant['profile_id'], generation, ecu_id) if historical and category == 'measurement' else None
            groups[key].append({'sourceOffset': record.get('at'), 'source': record.get('source'),
                'recordSha256': digest,
                'decoderAudit': (audits or {}).get((category, digest)),
                'historicalEvidence': historical_evidence,
                **({'routineDefinition': routine} if routine else {}),
                'name': label, 'nameResolved': named, 'nameId': name_id, 'nameStatus': name_status,
                'emptySourceNameIds': empty_name_ids,
                'unit': record.get('unit'), 'requestHex': hx, 'dataSpan': span,
                'byteOffset': record.get('byteOffset'), 'bitOffset': record.get('bitOffset'),
                'formula': record.get('formula'), 'enumText': record.get('enumText'), 'definitionGaps': gaps})
        if not groups: groups['missing'] = []
        for key, references in groups.items():
            definition_gaps = sorted({gap for record in references for gap in record['definitionGaps']})
            if not references: definition_gaps.append('source-definition-missing')
            gaps = ['model/variant-applicability-unverified', 'fresh-identity-and-version-required',
                    'adapter/session-and-address-required']
            if ecu_id is None: gaps.append('menu-ecu-mapping-missing')
            if category == 'measurement': gaps += ['independent-read-response-required', 'cadence-unverified']
            if category == 'coding': gaps += ['full-original-block-scope-required', 'write/readback/restore-recipe-required']
            if category == 'firmware': gaps += ['OEM-file-authenticity-and-match-required', 'wired-transport-and-recovery-required']
            if category == 'dtc': gaps.append('complete-repair-procedure-unverified')
            identity = [generation, ecu_id, variant['profile_id'], category, key]
            read_preparation = None
            if category == 'measurement' and references and references[0]['requestHex']:
                hx = references[0]['requestHex']
                request = bytes.fromhex(hx)
                eligible = [ref for ref in references if not ref['definitionGaps']
                            and ref['decoderAudit'] and ref['decoderAudit']['status'] == 'matched']
                read_preparation = {'requestHex': hx,
                    'positivePrefixHex': (bytes([request[0] + 0x40]) + request[1:]).hex().upper(),
                    'minimumDataBytes': max((ref['dataSpan']['dataMin'] for ref in eligible), default=None),
                    'selectionLimit': 12, 'intervalClaim': None,
                    'selectionBatches': [[{'sourceOffset': ref['sourceOffset'], 'recordSha256': ref['recordSha256']}
                                          for ref in eligible[start:start + 12]] for start in range(0, len(eligible), 12)],
                    'excludedFields': [{'recordSha256': ref['recordSha256'], 'sourceOffset': ref['sourceOffset'],
                        'reason': ref['definitionGaps'] or ((ref['decoderAudit'] or {}).get('status', 'not-audited'))}
                        for ref in references if ref not in eligible],
                    'address': None, 'session': None, 'adapterQualification': 'not-established',
                    'executionEnabled': False, 'liveVerified': False}
            yield {'rowId': hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                'generation': generation, 'ecuId': ecu_id, 'profileId': variant['profile_id'],
                'module': variant['module'], 'variant': variant['name'], 'sourceGeneration': variant.get('generation'),
                'membership': variant.get('membership'), 'category': category, 'functionKey': key,
                'sharedVariantCandidate': variant.get('generation') in (None, '9x1'),
                'definitionStatus': 'missing' if not references else 'unresolved' if definition_gaps else 'parsed-source',
                'definitionGaps': definition_gaps, 'qualificationGaps': gaps,
                'evidenceLevel': 'capture-consistent' if any((r['historicalEvidence'] or {}).get('status') == 'capture-consistent' for r in references) else
                    'capture-candidate' if any((r['historicalEvidence'] or {}).get('status') == 'capture-candidate' for r in references) else 'static-source-only',
                'adapterQualification': 'not-established',
                'readPreparation': read_preparation,
                'executionEnabled': False, 'liveVerified': False, 'fittedClaim': False, 'references': references}


def build(output, *, variants=DEFAULT_VARIANTS, bundle=DEFAULT_OUTPUT, match=DEFAULT_MATCH,
          original_source_root=None, source_root=ROOT):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / '.local') or output == ROOT / '.local' or output.exists():
        raise ValueError('new-private-output-required')
    variants = Path(variants)
    source = verify_source(variants, ANCHORS['variants'])
    validate_bundle(bundle)
    coverage_source = verify_source(DEFAULT_COVERAGE)
    coverage = json.loads(DEFAULT_COVERAGE.read_text(encoding='utf8'))
    catalog_source = verify_source(DEFAULT_CATALOG)
    catalog = load_catalog()
    historical = HistoricalCoverage.load(bundle, match, original_source_root=original_source_root,
                                          source_root=source_root)
    audit_code = [verify_source(Path(__file__)), *[verify_source(Path(__file__).with_name(name)) for name in (
        'definition_decoder_audit.py', 'decoder_reference.py', 'response_values.py', 'x431_values.py',
        'x431_formula.py', 'realtime_preparation.py', 'qualification.py', 'catalog.py', 'sessions.py', 'historical_coverage.py',
        'routine_definitions.py', 'routine_body.py', 'routine_buffers.py', 'routine_dispatch.py', 'routine_returns.py', 'workbench.py')]]
    audit_code.append(verify_source(Path(__file__).parents[1] / 'x431_re/request_fields.py'))
    # Derive bindings directly from the hash-checked coverage input, preserving
    # duplicate source references separately from unique ECU/version pairs.
    modules = _module_ecus(coverage)
    language_source = verify_source(GGP_PATH, '7caf1c27e475511e4a75db8fe92008684f5632eeb9fbb4484cbc16fa0eec7de3')
    language = GgpLanguage(GGP_PATH)
    def lookup(key): return compact_text(language.lookup_id('DSTREAM_CN.GAG', key))
    output.mkdir(parents=True)
    matrix_path = output / 'function-matrix.jsonl'
    counts, definition_gaps, unresolved_names, seen = Counter(), Counter(), {}, set()
    rows = []
    auditor = DefinitionAuditor()
    audit_counts = Counter()
    audit_path = output / 'decoder-audit.jsonl'
    missing_path = output / 'missing-captures.jsonl'
    missing_groups = 0
    with variants.open(encoding='utf8') as stream, matrix_path.open('w', encoding='utf8') as matrix, audit_path.open('w', encoding='utf8') as audit_file, missing_path.open('w', encoding='utf8') as missing_file:
        for line in stream:
            variant = coding_profile_variant(json.loads(line))
            if variant['profile_id'] in seen: raise ValueError('duplicate-source-profile')
            seen.add(variant['profile_id'])
            raw_bindings = modules.get(variant['module'], [])
            meta = {'generation': variant.get('generation'), 'ecuIds': sorted(set(raw_bindings)),
                    'menuBindingReferenceCount': len(raw_bindings)}
            pools = variant.get('pool_records', {})
            audits = {}
            generations = [gen for gen in ('981', '982') if _matches_generation(meta, gen)]
            if not generations:
                continue
            for category in ('identity', 'measurement'):
                for original in pools.get(category, {}).get('records', []):
                    record = identity_record(original) if category == 'identity' else original
                    checked = auditor.check(record)
                    digest = record_hash(original)
                    audits[(category, digest)] = checked
                    audit_counts[f"{variant['module']}:{category}:{checked['status']}"] += 1
                    audit_file.write(json.dumps({'profileId': variant['profile_id'], 'module': variant['module'],
                        'generations': generations, 'ecuIds': meta['ecuIds'], 'category': category,
                        'sourceOffset': original.get('at'), 'recordSha256': digest, **checked}, ensure_ascii=False) + '\n')
            coding = coding_read_plan(variant)
            measurements = pools.get('measurement', {}).get('records', [])
            requests = {r['request'].hex().upper() for record in measurements if (r := request_from_record(record)).get('ok')}
            for generation in ('981', '982'):
                if not _matches_generation(meta, generation): continue
                for ecu_id in meta['ecuIds'] or [None]:
                    for row in function_rows(variant, generation, ecu_id, lookup, audits, historical):
                        matrix.write(json.dumps(row, ensure_ascii=False) + '\n')
                        if row['category'] == 'measurement':
                            historical.counts.update((ref['historicalEvidence'] or {})['status'] for ref in row['references'])
                            missing = [ref for ref in row['references'] if (ref['historicalEvidence'] or {})['status'] != 'capture-consistent']
                            if missing:
                                missing_groups += 1
                                missing_file.write(json.dumps({k: row[k] for k in ('rowId', 'generation', 'ecuId', 'profileId', 'functionKey')} | {
                                    'fields': [{'sourceOffset': ref['sourceOffset'], 'recordSha256': ref['recordSha256'],
                                                'reason': ref['historicalEvidence'].get('reason', ref['historicalEvidence']['status'])} for ref in missing],
                                    'required': ['fresh-full-identity/version', 'confirmed-session/address', 'selected-fields-and-timestamps',
                                                 'request/response-raw-capture', 'independent-adapter-read'],
                                    'executionEnabled': False, 'liveVerified': False}, ensure_ascii=False) + '\n')
                        counts[f"{generation}:{row['category']}:{row['definitionStatus']}"] += 1
                        definition_gaps.update(row['definitionGaps'])
                        for ref in row['references']:
                            if not ref['nameResolved']:
                                name_key = (variant['profile_id'], row['category'], ref['sourceOffset'])
                                unresolved_names[name_key] = {'profileId': variant['profile_id'], 'category': row['category'],
                                    'sourceOffset': ref['sourceOffset'], 'nameId': ref['nameId'], 'source': ref['source'],
                                    'reason': ref['nameStatus'],
                                    'emptySourceNameIds': ref['emptySourceNameIds'],
                                    'sourceLanguageEntryPresent': True if ref['nameStatus'] in ('source-empty', 'source-component-empty') else
                                        False if ref['nameStatus'] in ('source-entry-missing', 'source-component-missing') else None,
                                    'inventedLabel': False}
                rows.append({'generation': generation, 'profileId': variant['profile_id'], 'module': variant['module'],
                    'variant': variant['name'], 'ecuIds': meta.get('ecuIds', []),
                    'menuBindingReferenceCount': meta['menuBindingReferenceCount'],
                    'identityDefinitions': len(pools.get('identity', {}).get('records', [])),
                    'measurementDefinitions': len(measurements), 'measurementReadRequests': len(requests),
                    'codingFields': coding['definitionFieldCount'], 'codingReadRequests': len(coding['requestGroups']),
                    'codingDefinitionGaps': len(coding['missing']), 'codingFullLengthQualified': False,
                    'dtcDefinitions': len(pools.get('dtc', {}).get('records', [])),
                    'routineDefinitions': len(pools.get('routine', {}).get('records', [])),
                    'transportPreparation': [transport_preparation(variant, generation, ecu, catalog)
                                             for ecu in meta['ecuIds'] or [None]],
                    'manufacturerLiveQualified': False, 'codingWriteQualified': False, 'firmwareQualified': False,
                    'qualificationGaps': ['fresh-vehicle-identity/version', 'exact-session/address', 'adapter-independent-read',
                        'full-coding-block-scope', 'feature-write/readback/recovery', 'OEM-firmware-authenticity/recipe/recovery'],
                    'sourceOffset': variant.get('off')})
    verify_source(variants, source['sha256'])
    verify_source(DEFAULT_COVERAGE, coverage_source['sha256'])
    verify_source(GGP_PATH, language_source['sha256'])
    verify_source(DEFAULT_CATALOG, catalog_source['sha256'])
    for item in audit_code:
        verify_source(Path(item['path']), item['sha256'])
    for item in historical.inputs:
        verify_source(Path(item['path']), item['sha256'])
    menu_rows = []
    for generation in ('981', '982'):
        for menu in coverage.get('menu_ecus', []):
            matches = [r['profileId'] for r in rows if r['generation'] == generation and menu['ecu_id'] in r['ecuIds']]
            menu_rows.append({'generation': generation, 'ecuId': menu['ecu_id'], 'label': menu.get('label'),
                'profileIds': matches, 'mappingStatus': 'candidate' if matches else 'missing', 'fittedClaim': False})
    names_path = output / 'unresolved-names.json'
    names_path.write_text(json.dumps(list(unresolved_names.values()), ensure_ascii=False, indent=2), encoding='utf8')
    result = {'schemaVersion': 4, 'kind': 'diagnostic-definition-readiness', 'noDeviceIO': True,
        'vehicleVerified': False, 'source': source, 'inputs': [source, coverage_source, language_source, catalog_source],
        'code': audit_code,
        'historicalEvidence': {'inputs': historical.inputs, 'fieldCounts': dict(historical.counts),
            'missingCaptureGroups': missing_groups, 'missingCaptures': verify_source(missing_path),
            'independentAdapterVerified': False, 'vehicleVerified': False,
            'scope': 'measurement source fields; captured identity remains in original qualification evidence'},
        'functionMatrix': verify_source(matrix_path), 'matrixRowCounts': dict(counts),
        'decoderAudit': {'source': verify_source(audit_path), **auditor.summary(), 'moduleCounts': dict(audit_counts)},
        'definitionGapCounts': dict(definition_gaps), 'unresolvedNameRecords': len(unresolved_names),
        'unresolvedNames': verify_source(names_path), 'menuMappings': menu_rows,
        'coverageComplete': False, 'missingEvidenceIsNotUnsupported': True,
        'sourceScope': 'X431 source; PIWIS feature recipes require separate coverage',
        'uniqueVariantCounts': {generation: sum(r['generation'] == generation for r in rows) for generation in ('981', '982')},
        'ecuVariantBindingCounts': {generation: sum(r['menuBindingReferenceCount'] for r in rows if r['generation'] == generation)
                                   for generation in ('981', '982')},
        'uniqueEcuVariantBindingCounts': {generation: sum(len(r['ecuIds']) for r in rows if r['generation'] == generation)
                                   for generation in ('981', '982')},
        'duplicateMenuModuleBindings': {module: ids for module, ids in modules.items() if len(ids) != len(set(ids))},
        'countBasis': 'unique source versions and separate ECU/version bindings; neither is installed ECUs or working functions', 'rows': rows}
    (output / 'coverage-readiness.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return {k: v for k, v in result.items() if k in ('schemaVersion', 'uniqueVariantCounts',
        'ecuVariantBindingCounts', 'uniqueEcuVariantBindingCounts', 'matrixRowCounts', 'unresolvedNameRecords', 'coverageComplete', 'noDeviceIO', 'decoderAudit')}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--variants', type=Path, default=Path(os.environ.get('PORSCHE981_VARIANTS') or DEFAULT_VARIANTS))
    parser.add_argument('--bundle', type=Path, default=Path(os.environ.get('PORSCHE981_REALTIME_PREPARATION') or DEFAULT_OUTPUT))
    parser.add_argument('--match', type=Path, default=DEFAULT_MATCH)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--original-source-root')
    args = parser.parse_args()
    result = build(args.output_dir, variants=args.variants, bundle=args.bundle, match=args.match,
                   source_root=args.source_root, original_source_root=args.original_source_root)
    audit = result['decoderAudit']
    print(json.dumps({**result, 'decoderAudit': {key: value for key, value in audit.items()
          if key not in ('moduleCounts', 'source')}}, ensure_ascii=False))
    if audit['sourceFields'].get('mismatch', 0):
        raise SystemExit(1)

if __name__ == '__main__': main()
