"""Hash-bound identity capture gaps for every source menu/version; no transport.

An address reference and a decoded field do not qualify an ECU version. Original
negative, pending and malformed returns remain visible alongside positive PDUs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

from .decoder_reference import compare_reference, reference_value
from .offline_match import ANCHORS, explicit_address_pairs, verify_frames, verify_source
from .response_values import decode_application_response, request_from_record
from .source_catalog import GUARDS, _code_status, _name
from .x431_capture import paired_reads

FLAGS = {**GUARDS, 'noDeviceIO': True, 'completeIdentityQualified': False}
# Explicit author-reference routes only; never used as a live address allowlist.
ROUTE_REFERENCES = {
    1: 'dmitry-981-dme-kwp-read-dtc', 9: 'dmitry-981-module-mod-710-read-dtc',
    2: 'dmitry-981-module-pdk-read-dtc', 4: 'dmitry-981-module-airbag-read-dtc',
    32: 'dmitry-981-module-bcm-front-read-dtc', 35: 'dmitry-981-module-bcm-rear-read-dtc',
    70: 'dmitry-981-module-pcm-read-dtc',
}
AUDIT_SHA = '561a5409eec3a466cc03420f46ef071a2b887fd9999c31f50b531a9ac137e51c'
SUMMARY_SHA = '3eaaeeeaf0b29423a24e88b8db3ed1c1e6fc5433a4f8866dce0161e240cc0767'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_profiles(portable, expected_sha256):
    """Read the sole portable snapshot in memory; do not expand a second copy."""
    source = verify_source(Path(portable), expected_sha256)
    with zipfile.ZipFile(portable) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('identity-capture-duplicate-zip-entry')
        manifests = [n for n in names if n.startswith('.local/diagnostics/x431-source-references/')
                     and n.endswith('/manifest.json')]
        if len(manifests) != 1:
            raise ValueError('identity-capture-single-source-catalog-required')
        name = manifests[0]; prefix = name.removesuffix('manifest.json')
        raw = archive.read(name); catalog_id = prefix.rstrip('/').split('/')[-1]
        if sha(raw) != catalog_id:
            raise ValueError('identity-capture-catalog-hash-mismatch')
        manifest = json.loads(raw)
        if (manifest.get('schemaVersion') != 1 or manifest.get('kind') != 'x431-offline-source-catalog'
                or any(manifest.get(k) != v for k, v in GUARDS.items())
                or sum(r.get('sha256') == ANCHORS['variants'] for r in manifest.get('sourceFingerprints', [])) != 1):
            raise ValueError('identity-capture-catalog-unreviewed')
        status = _code_status(manifest)
        if not status['generatorCodeMatchesCurrent']:
            raise ValueError('identity-capture-generator-drift')
        documents = {}
        for row in manifest['files']:
            member = row.get('name')
            if (not _name(member) or member in documents or type(row.get('bytes')) is not int
                    or not 0 <= row['bytes'] <= 32*1024**2):
                raise ValueError('identity-capture-catalog-register-invalid')
            info = archive.getinfo(prefix + member)
            if info.file_size != row['bytes']:
                raise ValueError('identity-capture-payload-size-mismatch')
            data = archive.read(info)
            if sha(data) != row['sha256']:
                raise ValueError('identity-capture-payload-hash-mismatch')
            documents[member] = json.loads(data) if member.endswith('.json') else None
        index = documents['all-units.json']
        rows, seen = [], set()
        for unit in index['units']:
            for ref in unit['variants']:
                profile = documents[ref['file']]
                key = (unit['generation'], unit['ecuId'], ref['profileId'])
                if (key in seen or unit['generation'] not in ('981', '982')
                        or profile['profileId'] != ref['profileId'] or unit['ecuId'] not in profile['ecuIds']
                        or unit['generation'] not in profile['generations']):
                    raise ValueError('identity-capture-profile-binding-invalid')
                seen.add(key); rows.append((unit, profile))
    return rows, dict(portable=source, sourceCatalogId=catalog_id,
                      sourceFiles=len(documents), **status)


def reference_routes(definitions):
    pairs = explicit_address_pairs(definitions)
    routes = {}
    for ecu_id, source_id in ROUTE_REFERENCES.items():
        matches = [row for row in definitions['definitions'] if row.get('id') == source_id]
        if len(matches) != 1:
            raise ValueError('identity-capture-address-reference-not-unique')
        row = matches[0]
        try:
            tx, rx = int(row['requestAddress'], 16), int(row['responseAddress'], 16)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('identity-capture-address-reference-invalid') from error
        if row.get('definitionCategory') != 'author-read-command' or pairs.get(tx) != rx:
            raise ValueError('identity-capture-address-reference-conflict')
        routes[ecu_id] = dict(txId=tx, rxId=rx, addressReferenceId=source_id,
                              liveAddressQualified=False)
    return routes, pairs


def observations(frames, pairs, boundary):
    by_id = {frame['frameId']: frame for frame in frames}
    out = []
    for row in paired_reads(frames, pairs):
        request = by_id[row['requestFrameId']]
        reply = by_id.get(row.get('responseFrameId'))
        ret = row.get('returnShape') or {}
        pdu = ret.get('pduHex')
        out.append({k: row.get(k) for k in ('txId', 'requestHex', 'requestFrameId', 'responseFrameId',
            'requestRecord', 'responseRecord', 'status', 'nrc')} | {
            'observationId': row['requestFrameId'], 'rxId': ret.get('rxId'),
            'adapterStream': [request['stream'][0], request['stream'][2]],
            'capturePhase': 'before-coding' if row['requestRecord'] <= boundary else 'coding-workflow',
            'requestFrameSha256': request['frameSha256'],
            'responseFrameSha256': reply['frameSha256'] if reply else None,
            'pduHex': pdu, 'pduSha256': sha(bytes.fromhex(pdu)) if pdu else None,
            'returnShape': ret.get('shape'), 'captureGeneration': '981', **FLAGS})
    return out


def field_result(field, request_hex, observed):
    record = field['decodingDefinition']
    request = request_from_record(record)
    if not request.get('ok') or request['request'].hex().upper() != request_hex:
        raise ValueError('identity-capture-field-request-conflict')
    actual = decode_application_response(record, bytes.fromhex(observed['pduHex']), 'pdu')
    agreement, issue = False, None
    try:
        expected = reference_value(record, bytes.fromhex(observed['pduHex'])[len(request['request']):])
        agreement = bool(actual.get('ok') and expected.get('ok') and not compare_reference(expected, actual))
        if not agreement: issue = 'independent-decode-disagreement-or-failure'
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        issue = 'independent-reference-unsupported:' + type(error).__name__
    return dict(sourceOffset=field['sourceOffset'], observationId=observed['observationId'],
                value={k: v for k, v in actual.items() if k not in ('evidence', 'labelTexts')}, independentDecoderAgreement=agreement, issue=issue, **FLAGS)


def profile_evidence(unit, profile, all_observations, routes):
    route = routes.get(unit['ecuId']) if unit['generation'] == '981' else None
    groups = []
    for source_group in profile['identityGroups']:
        hx = source_group['requestHex']
        found = [o for o in all_observations if route and o['txId'] == route['txId'] and o['requestHex'] == hx]
        positive = [o for o in found if o['status'] == 'positive-candidate' and o['rxId'] == route['rxId']]
        values = [field_result(f, hx, o) for f in source_group['fields'] for o in positive]
        conflicts = []
        for field in source_group['fields']:
            decoded = [v for v in values if v['sourceOffset'] == field['sourceOffset'] and v['value'].get('ok')]
            signatures = {json.dumps({k: v['value'].get(k) for k in ('value', 'phys', 'text', 'hex', 'enumText')},
                                      sort_keys=True) for v in decoded}
            if len(signatures) > 1: conflicts.append(field['sourceOffset'])
        status = ('historical-value-conflict' if conflicts else 'historical-decode-gap' if any(not v['independentDecoderAgreement'] for v in values)
                  else 'historical-source-fields-decoded' if values else 'observed-without-positive-identity' if found
                  else 'capture-generation-unobserved' if unit['generation'] != '981'
                  else 'address-reference-missing' if route is None else 'response-missing')
        groups.append(dict(requestHex=hx, minimumKnownBytes=source_group['minimumKnownBytes'],
            expectedTotalBytes=source_group.get('expectedTotalBytes'), sourceFieldOffsets=[f['sourceOffset'] for f in source_group['fields']],
            status=status, observations=[o['observationId'] for o in found], observationStatuses=dict(Counter(o['status'] for o in found)),
            values=values, conflictingFieldOffsets=conflicts, **FLAGS))
    return dict(generation=unit['generation'], ecuId=unit['ecuId'], profileId=profile['profileId'],
        module=profile['module'], variant=profile['variant'], addressReference=route,
        identityGroups=groups, identityMissing=profile['identityMissing'],
        sourceGroupCount=len(groups), sourceGroupsWithPositiveFields=sum(bool(g['values']) for g in groups),
        missingEvidence=['fresh-complete-identity-and-version', 'native-version-selection-and-session',
                         'independent-adapter-response', 'full-response-length-contract'], **FLAGS)


def build(portable, expected_sha256, capture_root, output):
    output = Path(output)
    if output.exists(): raise ValueError('identity-capture-fresh-output-required')
    profiles, sources = source_profiles(portable, expected_sha256)
    root = Path(capture_root)
    paths = [(root/'.local/vehicle-analysis/981-x431-20261001'/name, digest)
             for name, digest in [('app-frames.jsonl', ANCHORS['frames']), ('transport-audit.json', AUDIT_SHA),
                                  ('app-frame-summary.json', SUMMARY_SHA)]]
    paths += [(root/'docs/research/can-data/diagnostic/definitions.json', ANCHORS['addresses']),
              (root/'.local/vehicle-runs/981-x431-coding-20261001T095850Z/final/btsnoop_hci.log', ANCHORS['hci'])]
    inputs = [verify_source(path, digest) for path, digest in paths]
    audit = json.loads(paths[1][0].read_text(encoding='utf8'))
    summary = json.loads(paths[2][0].read_text(encoding='utf8'))
    if audit['sha256'] != ANCHORS['hci'] or summary['sourceTransportSha256'] != ANCHORS['hci']:
        raise ValueError('identity-capture-transport-chain-mismatch')
    frames = [json.loads(line) for line in paths[0][0].read_text(encoding='utf8').splitlines()]
    verify_frames(frames)
    routes, pairs = reference_routes(json.loads(paths[3][0].read_text(encoding='utf8')))
    observed = observations(frames, pairs, audit['checkpoints'][0]['record'])
    counts = Counter(); group_counts = Counter(); profile_counts = Counter()
    output.mkdir(parents=True)
    def write(name, value):
        (output/name).write_bytes((json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2)+'\n').encode())
    with (output/'identity-profiles.jsonl').open('wb') as stream:
        for unit, profile in profiles:
            result = profile_evidence(unit, profile, observed, routes)
            stream.write((json.dumps(result, ensure_ascii=True, sort_keys=True)+'\n').encode())
            profile_counts[unit['generation']] += 1
            group_counts.update(g['status'] for g in result['identityGroups'])
            for group in result['identityGroups']:
                counts.update('independent-decoder-agreement' if v['independentDecoderAgreement'] else 'decode-gap' for v in group['values'])
    write('observations.json', observed)
    report = dict(kind='x431-historical-all-unit-identity-evidence', sourceCatalogId=sources['sourceCatalogId'],
        profileBindings=len(profiles), bindingsByGeneration=dict(profile_counts), uniqueProfiles=len({p['profileId'] for _, p in profiles}),
        menuUnitsByGeneration=dict(Counter(u['generation'] for u in { (u['generation'],u['ecuId']):u for u,_ in profiles}.values())),
        observations=len(observed), observationStatuses=dict(Counter(o['status'] for o in observed)),
        identityGroupStatuses=dict(group_counts), fieldObservationChecks=dict(counts),
        countBasis='source menu/generation/version candidates and repeated field observations; not fitted or qualified ECUs', **FLAGS)
    write('report.json', report)
    code_names = ('identity_capture.py', 'offline_match.py', 'x431_capture.py', 'response_values.py',
                  'decoder_reference.py', 'x431_values.py', 'x431_formula.py', 'source_catalog.py')
    code = [dict(name='scripts/diagnostics/'+name, sha256=sha(Path(__file__).with_name(name).read_bytes())) for name in code_names]
    write('manifest.json', dict(schemaVersion=1, sources=sources, inputs=inputs, code=code,
        outputs=[dict(name=name, bytes=(output/name).stat().st_size, sha256=sha((output/name).read_bytes()))
                 for name in ('identity-profiles.jsonl', 'observations.json', 'report.json')], **FLAGS))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--definitions-zip', required=True, type=Path)
    parser.add_argument('--definitions-sha256', required=True)
    parser.add_argument('--capture-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.definitions_zip, args.definitions_sha256, args.capture_root, args.output), sort_keys=True))


if __name__ == '__main__': main()
