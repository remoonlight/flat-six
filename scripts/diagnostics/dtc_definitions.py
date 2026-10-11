"""Exact-version DTC label references and offline application-response joins.

The pool's raw header word and per-row statusKind are not wire SID/status.
Raw response formats are existing observed KWP/UDS layouts, not qualifications
of every source version, link or actual installed ECU. No transport or writes.
"""
from __future__ import annotations
from collections import Counter
import argparse
import hashlib
import json
import re
from pathlib import Path

from .decode import decode_payload
from .offline_match import ANCHORS, FLAGS

GUARDS = {**FLAGS, 'vehicleVerified': False, 'ecuVersionQualified': False,
          'freshReadProven': False, 'perVariantDtcLayoutQualified': False}


def dtc_definition(variant):
    pool = variant.get('pool_records', {}).get('dtc') or {}
    rows = []
    for record in pool.get('records', []):
        source = record.get('source') or {}
        if (source.get('sha256', '').lower() != ANCHORS['data']
                or source.get('variant') != variant['name'] or source.get('module') != variant['module']):
            raise ValueError('dtc-definition-source-mismatch')
        numeric = record.get('numeric_dtc_hex')
        valid = isinstance(numeric, str) and re.fullmatch(r'[0-9A-Fa-f]{8}', numeric) is not None
        status = record.get('statusKind')
        status_valid = type(status) is int and 0 <= status <= 255
        text = record.get('text')
        rows.append({'sourceOffset': record.get('at'), 'numericRawHex': numeric,
            'numericKey': int(numeric, 16) if valid else None, 'displayCode': record.get('code'),
            'text': text, 'textState': 'missing' if text is None else 'source-empty' if text == '' else 'available',
            'textIdRawHex': record.get('text_id_hex'), 'sourceStatusKind': status,
            'sourceStatusKindByteValid': status_valid, 'liveStatusByte': None,
            'statusKindIsLiveStatusQualified': False, 'source': source,
            'definitionIssue': None if valid and status_valid else 'dtc-table-field-unresolved', **GUARDS})
    numeric_counts = Counter(row['numericKey'] for row in rows if row['numericKey'] is not None)
    result = {'kind': 'x431-source-dtc-definition', 'schemaVersion': 1,
        'profileId': variant['profile_id'], 'module': variant['module'], 'variant': variant['name'],
        'sourcePoolPointer': pool.get('pointer'), 'sourcePoolEnd': pool.get('end'),
        'sourceHeaderWord': pool.get('service_header'), 'poolHeaderIsWireReadSidQualified': False,
        'sourcePoolStatus': pool.get('status'), 'sourceDeclaredCount': pool.get('count'),
        'definitionCount': len(rows), 'records': rows,
        'duplicateNumericKeyCount': sum(count > 1 for count in numeric_counts.values()),
        'unresolvedRecordCount': sum(row['definitionIssue'] is not None for row in rows),
        'textStates': dict(Counter(row['textState'] for row in rows)), **GUARDS}
    result['definitionSha256'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return result


def decode_observation(profile, ecu_id, link_index, request_hex, response_hex, *, source_kind):
    """Source-bound offline decode; selected link is explicit analysis context.

Labels remain source candidates, even for a historical file. No fresh identity
or installed unit can be established from caller-supplied selectors/observations.
"""
    result = {'ok': False, 'kind': 'x431-offline-dtc-observation', 'sourceKind': source_kind,
        'profileId': profile.get('profileId'), 'requestedEcuId': ecu_id, 'offlineLinkIndex': link_index,
        'rawRequestHex': request_hex, 'rawResponseHex': response_hex, 'records': [],
        'dictionaryTextIsObservedVehicleText': False, **GUARDS}
    try:
        if source_kind not in ('simulation', 'historical-capture', 'imported-unqualified'):
            raise ValueError('dtc-offline-source-required')
        if type(ecu_id) is not int or ecu_id not in profile.get('ecuIds', []) or type(link_index) is not int:
            raise ValueError('dtc-offline-selector-invalid')
        definition = profile.get('dtcDefinition') or {}
        if (definition.get('kind') != 'x431-source-dtc-definition'
                or definition.get('profileId') != profile.get('profileId')
                or definition.get('module') != profile.get('module')
                or definition.get('variant') != profile.get('variant')):
            raise ValueError('dtc-profile-definition-mismatch')
        canonical = dict(definition);digest = canonical.pop('definitionSha256', None)
        if hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=True).encode()).hexdigest() != digest:
            raise ValueError('dtc-definition-hash-mismatch')
        refs = [ref for ref in profile.get('diagnosticSources', []) if ref.get('requestedEcuId') == ecu_id
                and ref.get('variantName') == profile['variant']]
        if len(refs) != 1:
            raise ValueError('dtc-diagnostic-source-reference-not-unique')
        links = [link for link in refs[0]['links'] if link.get('linkIndex') == link_index]
        if len(links) != 1:
            raise ValueError('dtc-offline-link-not-unique')
        if not isinstance(request_hex, str) or not re.fullmatch(r'[0-9A-Fa-f]{2,128}', request_hex) or len(request_hex) % 2:
            raise ValueError('dtc-request-hex-invalid')
        request = bytes.fromhex(request_hex)
        if not any(row.get('role') == 'dtcRead' and row.get('wireCandidateHex') == request.hex().upper()
                   for row in links[0]['dtc']['readCommandSlots']):
            raise ValueError('dtc-request-outside-exact-source-link')
        kind = ('kwp-dtc-18' if request[0] == 0x18 else 'uds-dtc-19-02'
                if len(request) == 3 and request[:2] == b'\x19\x02' else None)
        if kind is None:
            raise ValueError('dtc-response-layout-unimplemented')
        if (not isinstance(response_hex, str) or not re.fullmatch(r'[0-9A-Fa-f]{2,2097152}', response_hex)
                or len(response_hex) % 2):
            raise ValueError('dtc-response-hex-invalid')
        parsed = decode_payload(response_hex, {'type': kind})
        result['decodedApplicationResponse'] = parsed
        if not parsed['ok']:
            raise ValueError(parsed['reason'])
        result['responseLayoutBasis'] = 'existing observed application format; not per-variant runtime qualification'
        for observed in parsed['records']:
            key = int(observed['dtcHex'], 16)
            candidates = [row for row in definition['records'] if row['numericKey'] == key]
            valid = len(candidates) == 1 and candidates[0]['definitionIssue'] is None
            row = candidates[0] if valid else None
            result['records'].append({**observed, 'definitionMatches': candidates,
                'definitionBindingStatus': 'exact-numeric-source-candidate' if valid else
                    'source-number-missing' if not candidates else 'source-number-ambiguous-or-unresolved',
                'sourceDisplayCode': row['displayCode'] if row else None,
                'sourceText': row['text'] if row else None,
                'sourceTextState': row['textState'] if row else None,
                'liveStatusMeaningQualified': False, **GUARDS})
        result['ok'] = True
    except (ValueError, KeyError, TypeError) as error:
        result['error'] = str(error)
    return result


def prepared_profile(root, profile_id):
    root = Path(root)
    if root.is_symlink() or (root/'manifest.json').is_symlink() or (root/'all-units.json').is_symlink():
        raise ValueError('dtc-prepared-profile-path-invalid')
    root = root.resolve()
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf8'))
    original = [row for row in manifest['inputs'] if row.get('sha256') == ANCHORS['variants']]
    if len(original) != 1:
        raise ValueError('dtc-prepared-source-unqualified')
    report_file = root/'all-units.json';report_bytes = report_file.read_bytes()
    report_pins = [row for row in manifest['outputs'] if Path(row['path']).name == 'all-units.json']
    if len(report_pins) != 1 or hashlib.sha256(report_bytes).hexdigest() != report_pins[0]['sha256']:
        raise ValueError('dtc-prepared-register-hash-mismatch')
    report = json.loads(report_bytes)
    files = {ref['file'] for unit in report['units'] for ref in unit['variants'] if ref['profileId'] == profile_id}
    if len(files) != 1:
        raise ValueError('dtc-prepared-profile-not-unique')
    relative = next(iter(files))
    if re.fullmatch(r'profiles/[a-f0-9]{64}\.json', relative) is None:
        raise ValueError('dtc-prepared-profile-path-invalid')
    path = root/relative
    if (root/'profiles').is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('dtc-prepared-profile-path-invalid')
    pins = [row for row in manifest['outputs'] if Path(row['path']).name == path.name]
    data = path.read_bytes()
    if len(pins) != 1 or len(data) != pins[0]['bytes'] or hashlib.sha256(data).hexdigest() != pins[0]['sha256']:
        raise ValueError('dtc-prepared-profile-hash-mismatch')
    result = json.loads(data)
    if result.get('profileId') != profile_id:
        raise ValueError('dtc-prepared-profile-identity-mismatch')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-root', type=Path, required=True)
    parser.add_argument('--profile-id', required=True)
    parser.add_argument('--ecu-id', type=int, required=True)
    parser.add_argument('--link-index', type=int, required=True)
    parser.add_argument('--observation', type=Path, required=True, help='offline JSON: requestHex/responseHex/sourceKind')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():raise ValueError('output-already-exists')
    profile = prepared_profile(args.prepared_root, args.profile_id)
    observation_bytes = args.observation.read_bytes()
    if len(observation_bytes) > 3 * 1024 * 1024:raise ValueError('dtc-observation-file-too-large')
    observation = json.loads(observation_bytes)
    result = decode_observation(profile, args.ecu_id, args.link_index, observation.get('requestHex'),
        observation.get('responseHex'), source_kind=observation.get('sourceKind'))
    result['observationFileSha256'] = hashlib.sha256(observation_bytes).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=True, indent=2)+'\n', encoding='utf8')
    print(json.dumps({'ok':result['ok'],'recordCount':len(result['records']),'error':result.get('error'), **GUARDS}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':raise SystemExit(main())
