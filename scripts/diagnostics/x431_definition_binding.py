"""Bind explicit offline version candidates to exact source definitions; no transport."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from scripts.x431_re.index_parse import (
    parse_9x1_index, parse_dsn_index, parse_l2_u16unk_id_name_off,
    parse_l2_u32count_id_name_off_extra,
)
from scripts.x431_re.generate import extract_pools
from .coding_read_plan import coding_read_plan
from .x431_variant_selection import REVIEWED_DSN_SHA256, _load_version_language, parse_version_definition
from .x431_version_response import SYS_DATA_LOGICAL_SHA256, SYSP_SHA256, FILE_SHA256
from scripts.x431_re.sysdata import parse_family_index, parse_systems

REVIEWED_ALLDATA_SHA256 = 'f32173d768ca9f8d2caf109bc7159fa43bde816f924fea5a91f1f202a2a76a2e'
GUARDS = {'executionEnabled': False, 'liveVerified': False, 'writePayload': None,
          'ecuIdentityQualified': False, 'ecuVersionQualified': False, 'freshReadProven': False,
          'completeVehicleCodingScopeQualified': False, 'selectedVehicleVariant': None}
REVIEWED_DSN_CALLERS = {
    'PorscheCodeMain': (0x29568, 0x296f4, 0x2970c, 0x2970e),
    'PorscheSpeMain': (0x24f6c, 0x250e0, 0x250f8, 0x250fa),
    'GetOdxAddForExtern': (0x298c8, 0x2990e, 0x29926, 0x29928),
    'PorscheVOSysEnter': (0x29398, 0x29452, 0x2946a, 0x2946c),
}


def audit_service_name_loop(service: dict, requested_name: str):
    """Family13's normal FILE name loop, assuming source reads completed.

    Native code copies six pointers before strcmp and returns one even when no
    name matched. A last copied pointer snapshot is never a qualified binding.
    An empty table leaves the previous six pointers unobserved. Copy callbacks,
    current TOKEN fields, file-open and cleanup effects remain unqualified.
    """
    if (not isinstance(service, dict) or not isinstance(service.get('variants'), list)
            or len(service['variants']) > 255 or not isinstance(requested_name, str)
            or not requested_name or not requested_name.isascii() or '\0' in requested_name
            or len(requested_name) >= 200):
        raise ValueError('service-name-loop-input-invalid')
    variants = service['variants']
    for row in variants:
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str)
                or not row['name'] or not row['name'].isascii() or '\0' in row['name']
                or len(row['name']) >= 100 or not isinstance(row.get('slots'), list)
                or len(row['slots']) != 6
                or any(type(value) is not int or not 0 <= value <= 0xffffffff for value in row['slots'])):
            raise ValueError('service-name-loop-layout-unqualified')
    matches = [index for index, row in enumerate(variants) if row['name'] == requested_name]
    read_index = matches[0] if matches else len(variants) - 1 if variants else None
    snapshot = list(variants[read_index]['slots']) if read_index is not None else None
    unique = len(matches) == 1
    status = ('exact-service-name-match-candidate' if unique else
              'service-name-ambiguous-first-slots-unqualified' if matches else
              'service-name-missing-last-slots-unqualified' if variants else
              'empty-service-table-prior-slots-unqualified')
    return {'kind': 'x431-offline-service-name-loop-audit', 'fileSourceSha256': FILE_SHA256,
        'sourceEntry': 0x15c18, 'reviewedFamilyId': 13, 'requestedName': requested_name,
        'sourceServiceCount': len(variants), 'sourceExactMatchCountCandidate': len(matches),
        'sourceFirstMatchIndexCandidate': matches[0] if matches else None,
        'sourceLastReadIndexCandidate': read_index, 'sourceSlotSnapshotCandidate': snapshot,
        'sourceSlotsCopiedBeforeNameCompare': True, 'sourceNormalReturnCandidate': 1,
        'sourceReturnOneProvesNameMatch': False,
        'nativeMismatchRetainsLastSlotsCandidate': bool(variants) and not matches,
        'nativeEmptyTablePriorSlotsUnknown': not variants,
        'qualifiedServiceSlotsCandidate': snapshot if unique else None,
        'projectFallbackUsed': False, 'actualCopyResultsQualified': False,
        'actualFileOpenQualified': False, 'actualTokenStateQualified': False,
        'normalCleanupRuntimeQualified': False, 'status': status, **GUARDS}


def bind_source_service_candidates(route_audit: dict, candidate_names: list):
    """Keep calculated version candidates separate from exact SYS service binds."""
    if (not isinstance(route_audit, dict) or route_audit.get('kind') != 'x431-offline-system-route-audit'
            or route_audit.get('dsnSourceSha256') != REVIEWED_DSN_SHA256
            or route_audit.get('logicalSourceSha256') != SYS_DATA_LOGICAL_SHA256
            or not isinstance(route_audit.get('versionServiceBindings'), list)):
        raise ValueError('service-candidate-route-audit-invalid')
    if (not isinstance(candidate_names, list) or len(candidate_names) > 255
            or any(not isinstance(name, str) or not name for name in candidate_names)
            or len(set(candidate_names)) != len(candidate_names)):
        raise ValueError('service-candidate-names-invalid')
    rows = []
    for name in candidate_names:
        matches = [row for row in route_audit['versionServiceBindings'] if row['variant'] == name]
        if not matches:
            raise ValueError('service-candidate-outside-version-definition')
        binding = matches[0] if len(matches) == 1 else None
        complete = binding is not None and binding['status'] == 'exact-source-service-name-match'
        loop = binding['serviceNameLoopAudit'] if binding is not None else None
        slots = loop['qualifiedServiceSlotsCandidate'] if complete else None
        rows.append({'variant': name, 'status': binding['status'] if binding else 'version-name-ambiguous',
            'serviceNameLoopAudit': loop, 'sourceBindingCompleteCandidate': complete and slots is not None,
            'qualifiedServiceSlotsCandidate': slots, 'projectFallbackUsed': False, **GUARDS})
    return {'kind': 'x431-offline-calculated-service-binding',
        'definitionOffset': route_audit['definitionOffset'],
        'explicitSourceSelection': route_audit['explicitSourceSelection'],
        'bindings': rows, 'sourceBindingsCompleteCandidate': bool(rows) and
            all(row['sourceBindingCompleteCandidate'] for row in rows), **GUARDS}


def audit_dsn_route_context(route_id: int, flags: int, system_key: int, context=None):
    """Family13's SYSP selector predicate; never infer the two runtime bytes.

    FILE GetOdxXmlDsnInfo has a different selector (including an ECU1 prompt).
    This predicate applies only to SYSP GetOdxXmlDsnInfoEx, not that FILE entry.
    """
    if (type(route_id) is not int or not 0 <= route_id <= 255
            or type(flags) is not int or not 0 <= flags <= 65535
            or type(system_key) is not int or not 0 <= system_key <= 65535
            or system_key >> 8 != 13):
        raise ValueError('system-route-family13-fields-invalid')
    caller_audit = None
    argument = None
    if context is not None:
        plain = {'secondArgumentByte', 'token63dByte'}
        named = {'caller', 'iComCmtByte', 'token63dByte'}
        if not isinstance(context, dict) or set(context) not in (plain, named):
            raise ValueError('system-route-context-fields-invalid')
        byte_fields = plain if set(context) == plain else {'iComCmtByte', 'token63dByte'}
        if any(type(context[key]) is not int or not 0 <= context[key] <= 255 for key in byte_fields):
            raise ValueError('system-route-context-fields-invalid')
        if set(context) == named:
            caller = context['caller']
            if not isinstance(caller, str) or caller not in REVIEWED_DSN_CALLERS:
                raise ValueError('system-route-caller-unreviewed')
            entry, read, move, call = REVIEWED_DSN_CALLERS[caller]
            caller_audit = {'caller': caller, 'sourceEntry': entry, 'syspSourceSha256': SYSP_SHA256,
                'sourceByteReadSite': read, 'sourceArgumentMoveSite': move,
                'sourceSelectorCallSite': call, 'sourceSelectorPltTarget': 0x17c50,
                'sourceSelectorDefinitionCandidate': 0x2c644,
                'sourceGlobalName': 'iComCmt', 'sourceGlobalBytes': 1,
                'sourceGlobalGotSlot': 0x64ae8, 'sourceGlobalObjectAddress': 0x66110,
                'sourceGlobalIsBss': True, 'onDiskGotWordIsRuntimeByte': False,
                'actualLoaderBindingQualified': False, 'actualGlobalByteQualified': False}
            argument = context['iComCmtByte']
        else:
            argument = context['secondArgumentByte']
    low, high = flags & 255, flags >> 8
    id_match = route_id == system_key & 255
    eligible = None if context is None else (id_match and low == argument
        and (high == 0 or high == context['token63dByte']))
    if not id_match:
        eligible = False
    return {'sourceEntry': 0x2c644, 'syspSourceSha256': SYSP_SHA256,
            'routeFlagLowByte': low, 'routeFlagHighByte': high,
            'systemIdMatches': id_match, 'explicitContext': context,
            'sourceCallerAudit': caller_audit, 'selectorSecondArgumentByteCandidate': argument,
            'sourceRouteEligibleCandidate': eligible,
            'status': 'source-context-unobserved' if eligible is None else
                      'source-context-eligible' if eligible else 'source-context-rejected',
            'fileEntryPredicateApplicable': False, 'actualContextQualified': False, **GUARDS}


def audit_source_system_route(dsn: bytes, logical: bytes, offset: int,
                              system_name, selection: dict, context=None, *, requested_service_name=None):
    """Join a pinned DSN route to an explicit SYS_DATA system/link, offline.

    A same request or same version label does not bind a different system ID.
    Context absence, multiple eligible routes and unmatched service names remain
    explicit. Exact source names are candidates, never a fitted vehicle claim.
    """
    if (not isinstance(dsn, bytes) or hashlib.sha256(dsn).hexdigest() != REVIEWED_DSN_SHA256
            or not isinstance(logical, bytes) or hashlib.sha256(logical).hexdigest() != SYS_DATA_LOGICAL_SHA256):
        raise ValueError('system-route-source-unreviewed')
    fields = {'familyId', 'systemKey', 'linkIndex'}
    if (not isinstance(selection, dict) or set(selection) != fields
            or any(type(value) is not int for value in selection.values())):
        raise ValueError('system-route-selection-fields-invalid')
    if selection['familyId'] != 13 or selection['systemKey'] >> 8 != 13:
        raise ValueError('system-route-family-unreviewed')
    if type(offset) is not int or (system_name is not None and
            (not isinstance(system_name, str) or not system_name)):
        raise ValueError('system-route-definition-selector-invalid')
    families = parse_dsn_index(dsn)['entries']
    family = next(row for row in families if row['id'] == 13 and row['name'] == '9x1')
    limit = min((row['off'] for row in families if row['off'] > family['off']), default=len(dsn))
    routes = parse_l2_u16unk_id_name_off(dsn, family['off'], limit)['entries']
    matches = [row for row in routes if row['off'] == offset and
               (system_name is None or row['name'] == system_name)]
    if not matches:
        raise ValueError('system-route-definition-not-unique')
    matching_id = [row for row in matches if row['id'] == selection['systemKey'] & 255]
    if not matching_id:
        raise ValueError('system-route-system-id-mismatch')
    # The native ID check legitimately separates shared definition offsets.
    # Multiple flag rows for the same ID remain candidates; never take the first.
    names = {row['name'] for row in matching_id}
    if len(names) != 1:
        raise ValueError('system-route-definition-name-ambiguous')
    route_name = next(iter(names))
    predicates = [audit_dsn_route_context(row['id'], row['unk'], selection['systemKey'], context)
                  for row in matching_id]
    predicate = predicates[0] if len(predicates) == 1 else None
    sf = next(row for row in parse_family_index(logical) if row['familyId'] == 13)
    systems = [row for row in parse_systems(logical, sf['off']) if row['systemKey'] == selection['systemKey']]
    if len(systems) != 1:
        raise ValueError('system-route-system-not-unique')
    links = systems[0]['links']; index = selection['linkIndex']
    if not 0 <= index < len(links):
        raise ValueError('system-route-link-index-out-of-range')
    link = links[index]
    definition = parse_version_definition(dsn, offset)
    service = link['service'].get('variants', [])
    variants = []
    for variant in definition['variants']:
        named = [{'serviceVariantIndex': i, 'sourceSlots': row['slots']}
                 for i, row in enumerate(service) if row['name'] == variant['name']]
        variants.append({'variant': variant['name'], 'ruleOffset': variant['ruleOffset'],
            'status': 'exact-source-service-name-match' if len(named) == 1 else
                      'service-name-missing' if not named else 'service-name-ambiguous',
            'serviceMatches': named, 'serviceNameLoopAudit': audit_service_name_loop(link['service'], variant['name']),
            'fallbackUsed': False, **GUARDS})
    candidates = [{'routeName': row['name'], 'routeIndexOffset': row['at'], 'definitionOffset': row['off'],
        **audit_dsn_route_context(row['id'], row['unk'], selection['systemKey'], context)}
        for row in routes if row['id'] == selection['systemKey'] & 255]
    return {'kind': 'x431-offline-system-route-audit', 'dsnSourceSha256': REVIEWED_DSN_SHA256,
        'logicalSourceSha256': SYS_DATA_LOGICAL_SHA256, 'explicitSourceSelection': dict(selection),
        'definitionOffset': offset, 'systemName': route_name,
        'routeIndexOffset': matching_id[0]['at'] if len(matching_id) == 1 else None,
        'matchingSourceRouteOffsets': [row['at'] for row in matching_id],
        'sourceRoutePairUnique': len(matching_id) == 1,
        'routeId': selection['systemKey'] & 255, 'sourcePredicateAudit': predicate,
        'sourcePredicateAudits': predicates,
        'sameIdRouteCandidates': candidates,
        'eligibleRouteCountCandidate': None if context is None else
            sum(row['sourceRouteEligibleCandidate'] for row in candidates),
        'communicationSourceOffset': link['commOff'], 'serviceSourceOffset': link['serviceOff'],
        'explicitServiceNameAudit': audit_service_name_loop(link['service'], requested_service_name)
            if requested_service_name is not None else None,
        'versionServiceBindings': variants, 'byStatus': dict(Counter(row['status'] for row in variants)),
        'sourceSelectedSystemTokenLowByteCandidate': selection['systemKey'] & 255,
        'actualFamilySystemBindingQualified': False, 'actualDsnRouteSelectionQualified': False,
        'sourceGeneration': None, **GUARDS}


def bind_version_candidates(definition: dict, system_name: str, catalog: list[dict], candidate_names=None):
    """Exact module/name joins only. Missing names never inherit a base or last table."""
    if (not isinstance(definition, dict) or not isinstance(definition.get('variants'), list)
            or not isinstance(system_name, str) or not system_name or not isinstance(catalog, list)):
        raise ValueError('definition-binding-input-invalid')
    if candidate_names is not None:
        if (not isinstance(candidate_names, list) or len(candidate_names) > 255
                or any(not isinstance(name, str) or not name for name in candidate_names)):
            raise ValueError('definition-binding-candidates-invalid')
        known = {variant['name'] for variant in definition['variants']}
        if set(candidate_names) - known:
            raise ValueError('definition-binding-candidate-outside-version-definition')
    modules = [module for module in catalog if module['module'] == system_name]
    rows = []
    for variant in definition['variants']:
        if candidate_names is not None and variant['name'] not in candidate_names:
            continue
        matches = [entry for module in modules for entry in module['variants'] if entry['name'] == variant['name']]
        if not modules:
            status = 'module-missing'
        elif len(modules) != 1:
            status = 'module-ambiguous'
        elif modules[0].get('indexError'):
            status = 'module-index-unsupported'
        elif len(matches) != 1:
            status = 'variant-missing' if not matches else 'variant-ambiguous'
        else:
            status = 'exact-source-name-match'
        rows.append({'variant': variant['name'], 'ruleOffset': variant['ruleOffset'], 'status': status,
                     'matchedDefinitionOffsets': [entry['off'] for entry in matches],
                     'variantOffset': matches[0]['off'] if status == 'exact-source-name-match' else None,
                     'fallbackUsed': False, **GUARDS})
    return {'kind': 'x431-offline-definition-binding', 'definitionOffset': definition['definitionOffset'],
            'systemName': system_name, 'candidateSource': 'explicit-offline-candidate-subset' if candidate_names is not None
            else 'all-source-declared-version-candidates', 'bindings': rows,
            'byStatus': dict(Counter(row['status'] for row in rows)), **GUARDS}


def audit_source_binding(dsn: bytes, offset: int, system_name: str, alldata: bytes,
                         language_path: Path, candidate_names=None):
    """Reviewed source bytes and route pair are required before resolving five pools.

    Plans remain offline, without a vehicle generation or qualified full block
    length. Candidate names may come from offline rule matches; none is selected.
    """
    dsn_sha = hashlib.sha256(dsn).hexdigest()
    alldata_sha = hashlib.sha256(alldata).hexdigest()
    if dsn_sha != REVIEWED_DSN_SHA256 or alldata_sha != REVIEWED_ALLDATA_SHA256:
        raise ValueError('definition-binding-source-unreviewed')
    families = parse_dsn_index(dsn)['entries']
    family = next(row for row in families if row['id'] == 13 and row['name'] == '9x1')
    limit = min((row['off'] for row in families if row['off'] > family['off']), default=len(dsn))
    routes = parse_l2_u16unk_id_name_off(dsn, family['off'], limit)['entries']
    matching_routes = [row for row in routes if row['name'] == system_name and row['off'] == offset]
    if not matching_routes:
        raise ValueError('definition-binding-route-pair-mismatch')
    definition = parse_version_definition(dsn, offset)
    catalog = []
    for group in parse_9x1_index(alldata)['entries']:
        try:
            entries = parse_l2_u32count_id_name_off_extra(alldata, group['off'])['entries']
            error = None
        except ValueError as exc:
            entries, error = [], str(exc)
        catalog.append({'module': group['name'], 'variants': entries, 'indexError': error})
    result = bind_version_candidates(definition, system_name, catalog, candidate_names)
    language = _load_version_language(language_path)
    starts = sorted({entry['off'] for module in catalog for entry in module['variants']})
    result.update(sourceSha256=dsn_sha, allDataSha256=alldata_sha,
                  languageSha256=hashlib.sha256(language.raw).hexdigest(),
                  matchingSourceRouteOffsets=[row['at'] for row in matching_routes],
                  sourceGeneration=None, fullCodingBlockLengthsQualified=False)
    for binding in result['bindings']:
        if binding['variantOffset'] is None:
            continue
        start = binding['variantOffset']
        end = next((other for other in starts if other > start), len(alldata))
        source = {'file': '9X1_ALLDATA.BIN.dec', 'sha256': alldata_sha, 'variantOffset': start,
                  'module': system_name, 'variant': binding['variant'],
                  'languageSha256': result['languageSha256']}
        decoded = extract_pools(alldata, start, end, language, source)
        if decoded.get('pointer_error'):
            binding.update(poolBindingStatus='unsupported', poolError=decoded['pointer_error'])
            continue
        pools = decoded['pools']
        binding['fivePoolPointers'] = decoded['five']['named']
        binding['poolSummaries'] = {name: {key: pool.get(key) for key in
            ('status', 'pointer', 'end', 'count', 'decoded', 'unsupported', 'missing', 'join', 'error')}
            for name, pool in pools.items()}
        variant = {'profile_id': f'x431-source-{start}', 'module': system_name, 'name': binding['variant'],
                   'generation': None, 'pool_records': pools}
        binding['codingReadPlan'] = coding_read_plan(variant)
        binding['codingFields'] = pools['coding'].get('records', [])
        binding['poolBindingStatus'] = 'source-pointers-resolved'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decoded-dsn', type=Path, required=True)
    parser.add_argument('--definition-offset', type=lambda value: int(value, 0), required=True)
    parser.add_argument('--system-name', required=True)
    parser.add_argument('--decoded-alldata', type=Path, required=True)
    parser.add_argument('--language-ggp', type=Path, required=True)
    parser.add_argument('--candidate-name', action='append')
    parser.add_argument('--logical-sys-data', type=Path)
    parser.add_argument('--source-system-selection-json', type=Path,
                        help='Explicit familyId/systemKey/linkIndex; no automatic vehicle selection')
    parser.add_argument('--route-context-json', type=Path,
                        help='Explicit offline SYSP secondArgumentByte/token63dByte; never inferred')
    parser.add_argument('--requested-service-name',
                        help='Audit an explicit selected name against the source SYS service loop')
    args = parser.parse_args()
    if bool(args.logical_sys_data) != bool(args.source_system_selection_json):
        parser.error('--logical-sys-data and --source-system-selection-json are required together')
    if args.route_context_json and not args.source_system_selection_json:
        parser.error('--route-context-json requires --source-system-selection-json')
    if args.requested_service_name is not None and not args.source_system_selection_json:
        parser.error('--requested-service-name requires --source-system-selection-json')
    try:
        dsn = args.decoded_dsn.read_bytes()
        route = None
        if args.source_system_selection_json:
            context = json.loads(args.route_context_json.read_text(encoding='utf-8-sig')) if args.route_context_json else None
            if args.route_context_json and context is None:
                raise ValueError('system-route-context-fields-invalid')
            route = audit_source_system_route(dsn, args.logical_sys_data.read_bytes(), args.definition_offset,
                args.system_name, json.loads(args.source_system_selection_json.read_text(encoding='utf-8-sig')),
                context, requested_service_name=args.requested_service_name)
        result = audit_source_binding(dsn, args.definition_offset, args.system_name,
                                      args.decoded_alldata.read_bytes(), args.language_ggp, args.candidate_name)
        if route is not None:
            result.update(systemRouteAudit=route, actualDsnRouteSelectionQualified=False)
            if args.candidate_name is not None:
                result['sourceServiceCandidateBinding'] = bind_source_service_candidates(route, args.candidate_name)
    except (OSError, ValueError) as error:
        parser.exit(2, f'{error}\n')
    # ASCII JSON preserves Unicode text and avoids Windows code-page dependence
    # for callers reading stdout as UTF-8. Decoding restores the original labels.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
